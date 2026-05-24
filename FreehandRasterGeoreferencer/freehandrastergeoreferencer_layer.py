"""
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

import math
import os

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtCore import (
    pyqtSignal,
    QFileInfo,
    QPointF,
    QRectF,
    QSettings,
    QSize,
    Qt,
)
from qgis.PyQt.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPolygonF, QTransform
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataProvider,
    QgsMapLayerRenderer,
    QgsMessageLog,
    QgsPluginLayer,
    QgsPluginLayerType,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

from . import gdal_utils, utils
from .gcp_transform import (
    device_transform_for_geotransform,
    device_transform_for_pixel_map,
    fit_qtransform_from_gcps,
    gcps_from_json,
    gcps_to_json,
    gcp_rms_error,
    transformed_image_corners,
)
from .utils import debug_log
from .loaderrordialog import LoadErrorDialog


class LayerDefaultSettings:
    TRANSPARENCY = 30
    BLEND_MODE = "SourceOver"


class FreehandRasterGeoreferencerLayer(QgsPluginLayer):

    LAYER_TYPE = "FreehandRasterGeoreferencerLayer"
    transformParametersChanged = pyqtSignal(tuple)

    def __init__(self, plugin, filepath, title, screenExtent):
        QgsPluginLayer.__init__(
            self, FreehandRasterGeoreferencerLayer.LAYER_TYPE, title
        )
        self.plugin = plugin
        self.iface = plugin.iface

        self.title = title
        self.filepath = filepath
        self.screenExtent = screenExtent
        self.history = []
        # set custom properties
        self.setCustomProperty("title", title)
        self.setCustomProperty("filepath", self.filepath)

        self.setValid(True)

        self.setTransparency(LayerDefaultSettings.TRANSPARENCY)
        self.setBlendModeByName(LayerDefaultSettings.BLEND_MODE)

        # dummy data: real init is done in intializeLayer
        self.center = QgsPointXY(0, 0)
        self.rotation = 0.0
        self.xScale = 1.0
        self.yScale = 1.0

        self.error = False
        self.initializing = False
        self.initialized = False
        self.imageTransformedForDisplay = False
        self.gcps = []
        self.gcp_grid_rows = 0
        self.gcp_grid_cols = 0
        self.transform_mode = "affine"
        self.gcp_display_transform = None
        self.gcp_preview_image = None
        self.gcp_preview_geotransform = None
        self.gcp_preview_ready = False
        self.file_nodata_value = None
        self.nodata_use = False
        self.nodata_value = None
        self.nodata_transparent = True
        self.grid_georef_locked = False
        self.initializeLayer(screenExtent)
        self._extent = None

        self.provider = FreehandRasterGeoreferencerLayerProvider(self)

    def dataProvider(self):
        # issue with DBManager if the dataProvider of the QgsLayerPlugin
        # returns None
        return self.provider

    def setScale(self, xScale, yScale):
        self.xScale = xScale
        self.yScale = yScale

    def setRotation(self, rotation):
        # 3 decimals ought to be enough for everybody
        rotation = round(rotation, 3)
        # keep in -180,180 interval
        if rotation < -180:
            rotation += 360
        if rotation > 180:
            rotation -= 360
        self.rotation = rotation

    def setCenter(self, center):
        self.center = center

    def commitTransformParameters(self):
        QgsProject.instance().setDirty(True)
        self._extent = None
        self.setCustomProperty("xScale", self.xScale)
        self.setCustomProperty("yScale", self.yScale)
        self.setCustomProperty("rotation", self.rotation)
        self.setCustomProperty("xCenter", self.center.x())
        self.setCustomProperty("yCenter", self.center.y())
        self.setCustomProperty("transformMode", self.transform_mode)
        self.setCustomProperty("gcps", gcps_to_json(self.gcps))
        self.setCustomProperty("gcpGridRows", self.gcp_grid_rows)
        self.setCustomProperty("gcpGridCols", self.gcp_grid_cols)
        self.setCustomProperty("gridGeorefLocked", int(self.grid_georef_locked))
        self.commitDisplaySettings()
        self.transformParametersChanged.emit(
            (self.xScale, self.yScale, self.rotation, self.center)
        )

    def addGcp(self, px, py, mx, my):
        self.gcp_grid_rows = 0
        self.gcp_grid_cols = 0
        self.gcps.append(((px, py), (mx, my)))
        self.transform_mode = "gcp_tps"
        self.updateGcpDisplayTransform()
        self.commitTransformParameters()
        self.repaint()

    def initializeGridGcps(self, rows, cols):
        from .gcp_transform import generate_grid_gcps

        self.gcps = generate_grid_gcps(self, rows, cols)
        self.gcp_grid_rows = rows
        self.gcp_grid_cols = cols
        self.transform_mode = "gcp_tps"
        self.updateGcpDisplayTransform()
        self.setGridGeorefLocked(True)
        self.commitTransformParameters()
        self.repaint()

    def updateGcpTarget(self, index, mx, my):
        if index < 0 or index >= len(self.gcps):
            return False

        px, py = self.gcps[index][0]
        self.gcps[index] = ((px, py), (mx, my))
        self.updateGcpDisplayTransform()
        self.commitTransformParameters()
        self.repaint()
        return True

    def hasGridGcps(self):
        expected = self.gcp_grid_rows * self.gcp_grid_cols
        return expected > 0 and len(self.gcps) == expected

    def isGridGeorefLocked(self):
        return self.hasGridGcps() and self.grid_georef_locked

    def setGridGeorefLocked(self, locked):
        if not self.hasGridGcps():
            self.grid_georef_locked = False
            return
        self.grid_georef_locked = bool(locked)
        self.setCustomProperty("gridGeorefLocked", int(self.grid_georef_locked))
        QgsProject.instance().setDirty(True)

    def translateGcps(self, dx, dy):
        if not self.gcps or (dx == 0 and dy == 0):
            return

        self.gcps = [
            ((px, py), (mx + dx, my + dy))
            for (px, py), (mx, my) in self.gcps
        ]
        self.updateGcpDisplayTransform()
        self.commitTransformParameters()

    def affineTransformBlocked(self):
        return self.isGridGeorefLocked()

    def gcpHistoryState(self):
        return {
            "action": "gcp",
            "gcps": list(self.gcps),
            "gcp_grid_rows": self.gcp_grid_rows,
            "gcp_grid_cols": self.gcp_grid_cols,
        }

    def restoreGcpHistoryState(self, state):
        self.gcps = state["gcps"]
        self.gcp_grid_rows = state.get("gcp_grid_rows", 0)
        self.gcp_grid_cols = state.get("gcp_grid_cols", 0)
        if self.gcps:
            self.transform_mode = "gcp_tps"
            self.updateGcpDisplayTransform()
        else:
            self.transform_mode = "affine"
            self.gcp_display_transform = None
            self._clearGcpPreview()
        self._extent = None
        self.commitTransformParameters()

    def removeLastGcp(self):
        if not self.gcps:
            return False
        self.gcps.pop()
        if not self.gcps:
            self.transform_mode = "affine"
            self.gcp_display_transform = None
            self.gcp_grid_rows = 0
            self.gcp_grid_cols = 0
            self._clearGcpPreview()
        else:
            self.updateGcpDisplayTransform()
        self._extent = None
        self.commitTransformParameters()
        self.repaint()
        return True

    def clearGcps(self):
        self.gcps = []
        self.gcp_grid_rows = 0
        self.gcp_grid_cols = 0
        self.transform_mode = "affine"
        self.gcp_display_transform = None
        self._clearGcpPreview()
        self._extent = None
        self.commitTransformParameters()
        self.repaint()

    def _clearGcpPreview(self):
        self.gcp_preview_image = None
        self.gcp_preview_geotransform = None
        self.gcp_preview_ready = False

    def updateGcpDisplayTransform(self):
        self.gcp_display_transform = fit_qtransform_from_gcps(self.gcps)
        self._extent = None
        self._clearGcpPreview()
        if len(self.gcps) >= 3 and self.initialized:
            self._buildGcpPreview()

    def _buildGcpPreview(self):
        self._clearGcpPreview()
        if len(self.gcps) < 3:
            return

        source_path = self.getAbsoluteFilepath()
        if not os.path.exists(source_path):
            return

        try:
            preview = gdal_utils.create_gcp_preview(
                source_path,
                self.gcps,
                self.crs().toWkt(),
                max_dimension=2048,
                use_tps=len(self.gcps) >= 4,
                nodata_value=self.effectiveNodataValue(),
                nodata_transparent=self.nodataTransparentForDisplay(),
            )
            if preview is None:
                return
            self.gcp_preview_image, self.gcp_preview_geotransform = preview
            self.gcp_preview_ready = True
            self._extent = None
        except Exception as ex:
            QgsMessageLog.logMessage(
                "GCP preview failed: %s" % repr(ex),
                "FreehandRasterGeoreferencer",
                Qgis.MessageLevel.Warning,
            )

    def gcpResidualMeters(self):
        return gcp_rms_error(self.gcps, self.gcp_display_transform)

    def hasGcpExport(self):
        return self.transform_mode == "gcp_tps" and len(self.gcps) >= 3

    def reprojectTransformParameters(self, oldCrs, newCrs):
        transform = QgsCoordinateTransform(oldCrs, newCrs, QgsProject.instance())

        newCenter = transform.transform(self.center)
        newExtent = transform.transform(self.extent())

        # transform the parameters except rotation
        # TODO rotation could be better handled (maybe check rotation between
        # old and new extent)
        # but not really worth the effort ?
        self.setCrs(newCrs)
        self.setCenter(newCenter)
        self.resetScale(newExtent.width(), newExtent.height())

    def resetTransformParametersToNewCrs(self):
        """
        Attempts to keep the layer on the same region of the map when
        the map CRS is changed
        """
        oldCrs = self.crs()
        newCrs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self.reprojectTransformParameters(oldCrs, newCrs)
        self.commitTransformParameters()

    def setupCrsEvents(self):
        layerId = self.id()

        def removeCrsChangeHandler(layerIds):
            if layerId in layerIds:
                try:
                    self.iface.mapCanvas().destinationCrsChanged.disconnect(
                        self.resetTransformParametersToNewCrs
                    )
                except Exception:
                    pass
                try:
                    QgsProject.instance().disconnect(removeCrsChangeHandler)
                except Exception:
                    pass

        self.iface.mapCanvas().destinationCrsChanged.connect(
            self.resetTransformParametersToNewCrs
        )
        QgsProject.instance().layersRemoved.connect(removeCrsChangeHandler)

    def setupCrs(self):
        mapCrs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self.setCrs(mapCrs)

        self.setupCrsEvents()

    def repaint(self):
        self.repaintRequested.emit()

    def transformParameters(self):
        return (self.center, self.rotation, self.xScale, self.yScale)

    def initializeLayer(self, screenExtent=None):
        if self.error or self.initialized or self.initializing:
            return

        if self.filepath is not None:
            # not safe...
            self.initializing = True
            filepath = self.getAbsoluteFilepath()

            if not os.path.exists(filepath):
                # TODO integrate with BadLayerHandler ?
                loadErrorDialog = LoadErrorDialog(filepath)
                result = loadErrorDialog.exec()
                if result == 1:
                    # absolute
                    filepath = loadErrorDialog.lineEditImagePath.text()
                    # to relative if needed
                    self.filepath = utils.toRelativeToQGS(filepath)
                    self.setCustomProperty("filepath", self.filepath)
                    QgsProject.instance().setDirty(True)
                else:
                    self.error = True

                del loadErrorDialog

            imageFormat = utils.imageFormat(filepath)
            if imageFormat == "pdf":
                s = QSettings()
                oldValidation = s.value("/Projections/defaultBehavior")
                s.setValue(
                    "/Projections/defaultBehavior", "useGlobal"
                )  # for not asking about crs
                fileInfo = QFileInfo(filepath)
                path = fileInfo.filePath()
                baseName = fileInfo.baseName()
                layer = QgsRasterLayer(path, baseName)
                self.image = layer.previewAsImage(QSize(layer.width(), layer.height()))
                s.setValue("/Projections/defaultBehavior", oldValidation)
            else:
                self.refreshFileNodataValue()
                self._loadDisplayImage()
                if self.image is None or self.image.isNull():
                    QgsMessageLog.logMessage(
                        "Failed to load raster image: %s" % filepath,
                        "FreehandRasterGeoreferencer",
                        Qgis.MessageLevel.Critical,
                    )
                    self.error = True

            if self.error:
                self.initializing = False
                return

            self.initialized = True
            self.initializing = False

            self.setupCrs()

            if screenExtent:
                # constructor called from AddLayer action
                # if not, layer loaded from QGS project file

                # check if image already has georef info
                # use GDAL
                dataset = gdal.Open(filepath, gdal.GA_ReadOnly)
                georef = None
                if dataset:
                    georef = dataset.GetGeoTransform()

                if georef and not self.is_default_geotransform(georef):
                    self.initializeExistingGeoreferencing(dataset, georef)
                else:
                    # init to default params
                    self.setCenter(screenExtent.center())
                    self.setRotation(0.0)

                    sw = screenExtent.width()
                    sh = screenExtent.height()

                    self.resetScale(sw, sh)

                    self.commitTransformParameters()

            if len(self.gcps) >= 3:
                self._buildGcpPreview()

    def refreshFileNodataValue(self):
        filepath = self.getAbsoluteFilepath()
        if os.path.exists(filepath):
            self.file_nodata_value = gdal_utils.read_nodata_value(filepath)
        else:
            self.file_nodata_value = None

    def effectiveNodataValue(self):
        if self.nodata_use and self.nodata_value is not None:
            return self.nodata_value
        return None

    def nodataTransparentForDisplay(self):
        return self.nodata_use and self.nodata_transparent

    def commitDisplaySettings(self):
        QgsProject.instance().setDirty(True)
        self.setCustomProperty("nodataUse", int(self.nodata_use))
        self.setCustomProperty(
            "nodataValue", gdal_utils.nodata_value_to_string(self.nodata_value)
        )
        self.setCustomProperty("nodataTransparent", int(self.nodata_transparent))

    def setNodataSettings(self, use_nodata, nodata_value, transparent):
        self.nodata_use = bool(use_nodata)
        self.nodata_value = nodata_value
        self.nodata_transparent = bool(transparent)
        self.commitDisplaySettings()
        if self.initialized:
            self._loadDisplayImage()
            if len(self.gcps) >= 3:
                self._buildGcpPreview()
            self.repaint()

    def nodataSettingsChanged(self, use_nodata, nodata_value, transparent):
        self.setNodataSettings(use_nodata, nodata_value, transparent)

    def _needsGdalDisplayConversion(self, filepath):
        if self.nodataTransparentForDisplay() and self.effectiveNodataValue() is not None:
            return True

        format_info = gdal_utils.format(filepath)
        if format_info is None:
            return True

        nbands, datatype, _, _ = format_info
        if nbands not in (1, 3) or datatype != "Byte":
            return True
        return False

    def _loadDisplayImage(self):
        filepath = self.getAbsoluteFilepath()
        imageFormat = utils.imageFormat(filepath)
        self.refreshFileNodataValue()

        if imageFormat == "tif" and self._needsGdalDisplayConversion(filepath):
            image, transformed = gdal_utils.load_display_image(
                filepath,
                nodata_value=self.effectiveNodataValue(),
                nodata_transparent=self.nodataTransparentForDisplay(),
            )
            if image is not None and not image.isNull():
                self.image = image
                self.imageTransformedForDisplay = transformed
                return

        reader = QImageReader(filepath)
        image = reader.read()
        if image.isNull() and imageFormat == "tif":
            image, transformed = gdal_utils.load_display_image(filepath, None, False)
            if image is not None and not image.isNull():
                self.image = image
                self.imageTransformedForDisplay = transformed
                return

        self.image = image
        self.imageTransformedForDisplay = False

    def preCheckImage(self):
        filepath = self.getAbsoluteFilepath()
        if not self._needsGdalDisplayConversion(filepath):
            return False

        image, transformed = gdal_utils.load_display_image(
            filepath,
            nodata_value=self.effectiveNodataValue(),
            nodata_transparent=self.nodataTransparentForDisplay(),
        )
        if image is None or image.isNull():
            return False

        self.image = image
        self.imageTransformedForDisplay = transformed
        return True

    def initializeExistingGeoreferencing(self, dataset, georef):
        # georef can have scaling, rotation or translation
        rotation = 180 / math.pi * -math.atan2(georef[4], georef[1])
        sx = math.sqrt(georef[1] ** 2 + georef[4] ** 2)
        sy = math.sqrt(georef[2] ** 2 + georef[5] ** 2)
        i_center_x = self.image.width() / 2
        i_center_y = self.image.height() / 2
        center = QgsPointXY(
            georef[0] + georef[1] * i_center_x + georef[2] * i_center_y,
            georef[3] + georef[4] * i_center_x + georef[5] * i_center_y,
        )

        debug_log(repr(rotation) + " " + repr((sx, sy)) + " " + repr(center))

        self.setRotation(rotation)
        self.setCenter(center)
        # keep yScale positive
        self.setScale(sx, sy)
        self.commitTransformParameters()

        crs_wkt = dataset.GetProjection()
        message_shown = False
        if crs_wkt:
            qcrs = QgsCoordinateReferenceSystem(crs_wkt)
            if qcrs != self.crs():
                # reproject
                try:
                    self.reprojectTransformParameters(qcrs, self.crs())
                    self.commitTransformParameters()
                    self.showBarMessage(
                        "Transform parameters changed",
                        "Found existing georeferencing in raster but "
                        "its CRS does not match the CRS of the map. "
                        "Reprojected the extent.",
                        Qgis.MessageLevel.Warning,
                        5,
                    )
                    message_shown = True
                except Exception as ex:
                    QgsMessageLog.logMessage(repr(ex))
                    self.showBarMessage(
                        "CRS does not match",
                        "Found existing georeferencing in raster but "
                        "its CRS does not match the CRS of the map. "
                        "Unable to reproject.",
                        Qgis.MessageLevel.Warning,
                        5,
                    )
                    message_shown = True
        # if no projection info, assume it is the same CRS
        # as the map and no warning
        if not message_shown:
            self.showBarMessage(
                "Georeferencing loaded",
                "Found existing georeferencing in raster",
                Qgis.MessageLevel.Info,
                3,
            )

        # zoom (assume the user wants to work on the image)
        self.iface.mapCanvas().setExtent(self.extent())

    def is_default_geotransform(self, georef):
        """
        Check if there is really a transform or if it is just the default
        made up by GDAL
        """
        return georef[0] == 0 and georef[3] == 0 and georef[1] == 1 and georef[5] == 1

    def resetScale(self, sw, sh):
        iw = self.image.width()
        ih = self.image.height()
        if iw <= 0 or ih <= 0:
            return
        wratio = sw / iw
        hratio = sh / ih

        if wratio > hratio:
            # takes all height of current extent
            self.setScale(hratio, hratio)
        else:
            # all width
            self.setScale(wratio, wratio)

    def replaceImage(self, filepath, title):
        self.title = title
        self.filepath = filepath

        # set custom properties
        self.setCustomProperty("title", title)
        self.setCustomProperty("filepath", self.filepath)
        self.setName(title)

        fileInfo = QFileInfo(filepath)
        ext = fileInfo.suffix()
        if ext == "pdf":
            s = QSettings()
            oldValidation = s.value("/Projections/defaultBehavior")
            s.setValue(
                "/Projections/defaultBehavior", "useGlobal"
            )  # for not asking about crs
            path = fileInfo.filePath()
            baseName = fileInfo.baseName()
            layer = QgsRasterLayer(path, baseName)
            self.image = layer.previewAsImage(QSize(layer.width(), layer.height()))
            s.setValue("/Projections/defaultBehavior", oldValidation)
        else:
            self._loadDisplayImage()
        self.repaint()

    def clone(self):
        layer = FreehandRasterGeoreferencerLayer(
            self.plugin, self.filepath, self.title, self.screenExtent
        )
        layer.center = self.center
        layer.rotation = self.rotation
        layer.xScale = self.xScale
        layer.yScale = self.yScale
        layer.commitTransformParameters()
        return layer

    def getAbsoluteFilepath(self):
        if not os.path.isabs(self.filepath):
            # relative to QGS file
            qgsPath = QgsProject.instance().fileName()
            qgsFolder, _ = os.path.split(qgsPath)
            filepath = os.path.join(qgsFolder, self.filepath)
        else:
            filepath = self.filepath

        return filepath

    def extent(self):
        self.initializeLayer()
        if not self.initialized:
            debug_log("Not Initialized")
            return QgsRectangle(0, 0, 1, 1)

        if self._extent:
            return self._extent

        topLeft, topRight, bottomRight, bottomLeft = self.cornerCoordinates()

        left = min(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        right = max(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        top = max(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())
        bottom = min(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())

        # recenter + create rectangle
        self._extent = QgsRectangle(left, bottom, right, top)
        return self._extent

    def cornerCoordinates(self):
        if self.hasGcpExport() and self.gcp_display_transform is not None:
            return transformed_image_corners(self)
        return self.transformedCornerCoordinates(
            self.center, self.rotation, self.xScale, self.yScale
        )

    def transformedCornerCoordinates(self, center, rotation, xScale, yScale):
        # scale
        topLeft = QgsPointXY(
            -self.image.width() / 2.0 * xScale, self.image.height() / 2.0 * yScale
        )
        topRight = QgsPointXY(
            self.image.width() / 2.0 * xScale, self.image.height() / 2.0 * yScale
        )
        bottomLeft = QgsPointXY(
            -self.image.width() / 2.0 * xScale, -self.image.height() / 2.0 * yScale
        )
        bottomRight = QgsPointXY(
            self.image.width() / 2.0 * xScale, -self.image.height() / 2.0 * yScale
        )

        # rotate
        # minus sign because rotation is CW in this class and Qt)
        rotationRad = -rotation * math.pi / 180
        cosRot = math.cos(rotationRad)
        sinRot = math.sin(rotationRad)

        topLeft = self._rotate(topLeft, cosRot, sinRot)
        topRight = self._rotate(topRight, cosRot, sinRot)
        bottomRight = self._rotate(bottomRight, cosRot, sinRot)
        bottomLeft = self._rotate(bottomLeft, cosRot, sinRot)

        topLeft.set(topLeft.x() + center.x(), topLeft.y() + center.y())
        topRight.set(topRight.x() + center.x(), topRight.y() + center.y())
        bottomRight.set(bottomRight.x() + center.x(), bottomRight.y() + center.y())
        bottomLeft.set(bottomLeft.x() + center.x(), bottomLeft.y() + center.y())

        return (topLeft, topRight, bottomRight, bottomLeft)

    def transformedCornerCoordinatesFromPoint(
        self, startPoint, rotation, xScale, yScale
    ):
        # startPoint is a fixed point for this new movement (rotation and
        # scale)
        # rotation is the global rotation of the image
        # xScale is the new xScale factor to be multiplied by self.xScale
        # idem for yScale
        # Calculate the coordinate of the center in a startPoint origin
        # coordinate system and apply scales
        dX = (self.center.x() - startPoint.x()) * xScale
        dY = (self.center.y() - startPoint.y()) * yScale
        # Half width and half height in the current transformation
        hW = (self.image.width() / 2.0) * self.xScale * xScale
        hH = (self.image.height() / 2.0) * self.yScale * yScale
        # Actual rectangle coordinates :
        pt1 = QgsPointXY(-hW, hH)
        pt2 = QgsPointXY(hW, hH)
        pt3 = QgsPointXY(hW, -hH)
        pt4 = QgsPointXY(-hW, -hH)
        # Actual rotation from the center
        # minus sign because rotation is CW in this class and Qt)
        rotationRad = -self.rotation * math.pi / 180
        cosRot = math.cos(rotationRad)
        sinRot = math.sin(rotationRad)
        pt1 = self._rotate(pt1, cosRot, sinRot)
        pt2 = self._rotate(pt2, cosRot, sinRot)
        pt3 = self._rotate(pt3, cosRot, sinRot)
        pt4 = self._rotate(pt4, cosRot, sinRot)
        # Second transformation
        # displacement of the origin
        pt1 = QgsPointXY(pt1.x() + dX, pt1.y() + dY)
        pt2 = QgsPointXY(pt2.x() + dX, pt2.y() + dY)
        pt3 = QgsPointXY(pt3.x() + dX, pt3.y() + dY)
        pt4 = QgsPointXY(pt4.x() + dX, pt4.y() + dY)
        # Rotation
        # minus sign because rotation is CW in this class and Qt)
        rotationRad = -rotation * math.pi / 180
        cosRot = math.cos(rotationRad)
        sinRot = math.sin(rotationRad)
        pt1 = self._rotate(pt1, cosRot, sinRot)
        pt2 = self._rotate(pt2, cosRot, sinRot)
        pt3 = self._rotate(pt3, cosRot, sinRot)
        pt4 = self._rotate(pt4, cosRot, sinRot)
        # translate to startPoint
        pt1 = QgsPointXY(pt1.x() + startPoint.x(), pt1.y() + startPoint.y())
        pt2 = QgsPointXY(pt2.x() + startPoint.x(), pt2.y() + startPoint.y())
        pt3 = QgsPointXY(pt3.x() + startPoint.x(), pt3.y() + startPoint.y())
        pt4 = QgsPointXY(pt4.x() + startPoint.x(), pt4.y() + startPoint.y())

        return (pt1, pt2, pt3, pt4)

    def moveCenterFromPointRotate(self, startPoint, rotation, xScale, yScale):
        cornerPoints = self.transformedCornerCoordinatesFromPoint(
            startPoint, rotation, xScale, yScale
        )
        self.center = QgsPointXY(
            (cornerPoints[0].x() + cornerPoints[2].x()) / 2,
            (cornerPoints[0].y() + cornerPoints[2].y()) / 2,
        )

    def _rotate(self, point, cosRot, sinRot):
        return QgsPointXY(
            point.x() * cosRot - point.y() * sinRot,
            point.x() * sinRot + point.y() * cosRot,
        )

    def createMapRenderer(self, rendererContext):
        return FreehandRasterGeoreferencerLayerRenderer(self, rendererContext)

    def setBlendModeByName(self, modeName):
        self.blendModeName = modeName
        blendMode = getattr(QPainter, "CompositionMode_" + modeName, None)
        if blendMode is None:
            blendMode = getattr(
                QPainter.CompositionMode,
                "CompositionMode_" + modeName,
                None,
            )
        if blendMode is None:
            blendMode = getattr(
                QPainter.CompositionMode,
                modeName,
                QPainter.CompositionMode.CompositionMode_SourceOver,
            )
        self.setBlendMode(blendMode)
        self.setCustomProperty("blendMode", modeName)

    def setTransparency(self, transparency):
        self.transparency = transparency
        self.setCustomProperty("transparency", transparency)

    def draw(self, renderContext):
        if renderContext.extent().isEmpty():
            debug_log("Drawing is skipped because map extent is empty.")
            return True

        self.initializeLayer()
        if not self.initialized:
            debug_log("Drawing is skipped because nothing to draw.")
            return True

        painter = renderContext.painter()
        painter.save()
        self.prepareStyle(painter)
        self.drawRaster(renderContext)
        painter.restore()

        return True

    def drawRaster(self, renderContext):
        painter = renderContext.painter()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self.hasGcpExport():
            if not self.gcp_preview_ready and len(self.gcps) >= 3:
                self._buildGcpPreview()
            if self.gcp_preview_ready and self.gcp_preview_image is not None:
                if self._drawRasterWithGcpPreview(renderContext, painter):
                    return
            if self.gcp_display_transform is not None and not self.image.isNull():
                if self._drawRasterWithGcpTransform(renderContext, painter):
                    return

        if self.image is None or self.image.isNull():
            return

        self.map2pixel = renderContext.mapToPixel()
        scaleX = self.xScale / self.map2pixel.mapUnitsPerPixel()
        scaleY = self.yScale / self.map2pixel.mapUnitsPerPixel()

        rect = QRectF(
            QPointF(-self.image.width() / 2.0, -self.image.height() / 2.0),
            QPointF(self.image.width() / 2.0, self.image.height() / 2.0),
        )
        mapCenter = self.map2pixel.transform(self.center)

        # draw the image on the map canvas
        painter.translate(QPointF(mapCenter.x(), mapCenter.y()))
        painter.rotate(self.rotation)
        painter.scale(scaleX, scaleY)
        painter.drawImage(rect, self.image)

        painter.setOpacity(1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen()
        pen.setColor(QColor(0, 0, 0))
        pen.setWidth(3)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect)

    def _drawRasterWithGcpTransform(self, renderContext, painter):
        map2pixel = renderContext.mapToPixel()
        width = self.image.width()
        height = self.image.height()
        transform = device_transform_for_pixel_map(
            width, height, self.gcp_display_transform, map2pixel
        )
        if transform is None:
            return False

        painter.save()
        painter.setTransform(transform)
        painter.drawImage(0, 0, self.image)
        painter.restore()
        return True

    def _drawRasterWithGcpPreview(self, renderContext, painter):
        map2pixel = renderContext.mapToPixel()
        image = self.gcp_preview_image
        if image is None or image.isNull():
            return False

        transform = device_transform_for_geotransform(
            image.width(),
            image.height(),
            self.gcp_preview_geotransform,
            map2pixel,
        )
        if transform is None:
            return False

        painter.save()
        painter.setTransform(transform)
        painter.drawImage(0, 0, image)
        painter.restore()
        return True

    def prepareStyle(self, painter):
        painter.setOpacity(1.0 - self.transparency / 100.0)

    def readXml(self, node, context):
        self.readCustomProperties(node)
        self.title = self.customProperty("title", "")
        self.filepath = self.customProperty("filepath", "")
        self.xScale = float(self.customProperty("xScale", 1.0))
        self.yScale = float(self.customProperty("yScale", 1.0))
        self.rotation = float(self.customProperty("rotation", 0.0))
        xCenter = float(self.customProperty("xCenter", 0.0))
        yCenter = float(self.customProperty("yCenter", 0.0))
        self.center = QgsPointXY(xCenter, yCenter)
        self.transform_mode = self.customProperty("transformMode", "affine")
        self.gcps = gcps_from_json(self.customProperty("gcps", ""))
        self.gcp_grid_rows = int(self.customProperty("gcpGridRows", 0))
        self.gcp_grid_cols = int(self.customProperty("gcpGridCols", 0))
        self.grid_georef_locked = bool(int(self.customProperty("gridGeorefLocked", 0)))
        self.nodata_use = bool(int(self.customProperty("nodataUse", 0)))
        self.nodata_value = gdal_utils.nodata_value_from_string(
            self.customProperty("nodataValue", "")
        )
        self.nodata_transparent = bool(
            int(self.customProperty("nodataTransparent", 1))
        )
        if self.nodata_use and self.nodata_value is None:
            self.nodata_use = False
        if self.gcps:
            self.transform_mode = "gcp_tps"
            self.updateGcpDisplayTransform()
        else:
            self.transform_mode = "affine"
            self.gcp_display_transform = None
        if self.initialized and not self.error:
            self._loadDisplayImage()
            if len(self.gcps) >= 3:
                self._buildGcpPreview()
        self.setTransparency(
            int(self.customProperty("transparency", LayerDefaultSettings.TRANSPARENCY))
        )
        self.setBlendModeByName(
            self.customProperty("blendMode", LayerDefaultSettings.BLEND_MODE)
        )
        return True

    def writeXml(self, node, doc, context):
        element = node.toElement()
        self.writeCustomProperties(node, doc)
        element.setAttribute("type", "plugin")
        element.setAttribute("name", FreehandRasterGeoreferencerLayer.LAYER_TYPE)
        return True

    def layerPropertiesText(self):
        lines = []
        fmt = "%s:\t%s"
        lines.append(fmt % (self.tr("Title"), self.title))
        filepath = self.getAbsoluteFilepath()
        filepath = os.path.normpath(filepath)
        lines.append(fmt % (self.tr("Path"), filepath))
        lines.append(fmt % (self.tr("Image Width"), str(self.image.width())))
        lines.append(fmt % (self.tr("Image Height"), str(self.image.height())))
        lines.append(fmt % (self.tr("Rotation (CW)"), str(self.rotation)))
        lines.append(fmt % (self.tr("X center"), str(self.center.x())))
        lines.append(fmt % (self.tr("Y center"), str(self.center.y())))
        lines.append(fmt % (self.tr("X scale"), str(self.xScale)))
        lines.append(fmt % (self.tr("Y scale"), str(self.yScale)))
        if self.gcps:
            lines.append(fmt % (self.tr("GCP count"), str(len(self.gcps))))
            if self.hasGridGcps():
                lines.append(
                    fmt
                    % (
                        self.tr("Grid GCP mode"),
                        "%d x %d" % (self.gcp_grid_rows, self.gcp_grid_cols),
                    )
                )
            if self.isGridGeorefLocked():
                lines.append(fmt % (self.tr("Grid georef lock"), self.tr("Locked")))
            lines.append(
                fmt
                % (
                    self.tr("GCP fit error (map units)"),
                    str(round(self.gcpResidualMeters(), 3)),
                )
            )
            if self.gcp_preview_ready:
                preview_mode = (
                    "TPS preview"
                    if len(self.gcps) >= 4
                    else "Polynomial preview"
                )
                lines.append(fmt % (self.tr("Preview mode"), preview_mode))
        if self.file_nodata_value is not None:
            lines.append(
                fmt
                % (
                    self.tr("File NoData value"),
                    gdal_utils.nodata_value_to_string(self.file_nodata_value),
                )
            )
        if self.nodata_use and self.nodata_value is not None:
            lines.append(
                fmt
                % (
                    self.tr("Display NoData value"),
                    gdal_utils.nodata_value_to_string(self.nodata_value),
                )
            )
            lines.append(
                fmt
                % (
                    self.tr("Hide NoData"),
                    self.tr("Yes") if self.nodata_transparent else self.tr("No"),
                )
            )

        return "\n".join(lines)

    def log(self, msg):
        debug_log(msg)

    def dump(self, detail=False, bbox=None):
        pass

    def showStatusMessage(self, msg, timeout):
        self.iface.mainWindow().statusBar().showMessage(msg, timeout)

    def showBarMessage(self, title, text, level, duration):
        self.iface.messageBar().pushMessage(title, text, level, duration)

    def transparencyChanged(self, val):
        QgsProject.instance().setDirty(True)
        self.setTransparency(val)
        self.repaintRequested.emit()

    def setTransformContext(self, transformContext):
        pass


class FreehandRasterGeoreferencerLayerType(QgsPluginLayerType):
    def __init__(self, plugin):
        QgsPluginLayerType.__init__(self, FreehandRasterGeoreferencerLayer.LAYER_TYPE)
        self.plugin = plugin

    def createLayer(self):
        return FreehandRasterGeoreferencerLayer(self.plugin, None, "", None)

    def showLayerProperties(self, layer):
        from .propertiesdialog import PropertiesDialog

        dialog = PropertiesDialog(layer)
        dialog.horizontalSlider_Transparency.valueChanged.connect(
            layer.transparencyChanged
        )
        dialog.spinBox_Transparency.valueChanged.connect(layer.transparencyChanged)

        dialog.exec()

        dialog.horizontalSlider_Transparency.valueChanged.disconnect(
            layer.transparencyChanged
        )
        dialog.spinBox_Transparency.valueChanged.disconnect(layer.transparencyChanged)
        return True


class FreehandRasterGeoreferencerLayerProvider(QgsDataProvider):
    def __init__(self, layer):
        QgsDataProvider.__init__(self, "dummyURI")

    def name(self):
        # doesn't matter
        return "FreehandRasterGeoreferencerLayerProvider"


class FreehandRasterGeoreferencerLayerRenderer(QgsMapLayerRenderer):
    """
    Custom renderer: in QGIS3 no implementation is provided for
    QgsPluginLayers
    """

    def __init__(self, layer, rendererContext):
        QgsMapLayerRenderer.__init__(self, layer.id())
        self.layer = layer
        self.rendererContext = rendererContext

    def render(self):
        # same implementation as for QGIS2
        return self.layer.draw(self.rendererContext)

    def forceRasterRender(self):
        image = getattr(self.layer, "image", None)
        if image is not None and not image.isNull() and image.hasAlphaChannel():
            return True
        return False
