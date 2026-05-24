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

from qgis.PyQt.QtCore import QPointF, QRectF, QSize
from qgis.PyQt.QtGui import QColor, QImage, QImageWriter, QPainter
from qgis.core import Qgis, QgsMessageLog
from qgis.gui import QgsMessageBar

from . import gdal_utils, utils
from .utils import debug_log


class ExportGeorefRasterCommand(object):
    def __init__(self, iface):
        self.iface = iface

    def exportGeorefRaster(
        self, layer, rasterPath, isPutRotationInWorldFile, isExportOnlyWorldFile
    ):
        baseRasterFilePath, _ = os.path.splitext(rasterPath)
        rasterFormat = utils.imageFormat(rasterPath)
        source_path = layer.getAbsoluteFilepath()

        try:
            crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            crs_wkt = crs.toWkt()

            if layer.hasGcpExport():
                if isExportOnlyWorldFile:
                    raise RuntimeError(
                        "Ground control point mode requires exporting a new GeoTIFF. "
                        "Disable 'Only export world file'."
                    )
                if utils.imageFormat(rasterPath) != "tif":
                    raise RuntimeError(
                        "Ground control point export requires a GeoTIFF (.tif) output file."
                    )
                gdal_utils.export_with_gcps(
                    source_path,
                    rasterPath,
                    layer.gcps,
                    crs_wkt,
                    use_tps=len(layer.gcps) >= 4,
                )
                widget = QgsMessageBar.createMessage(
                    "Raster Geoferencer",
                    "Raster exported successfully with ground control points.",
                )
                self.iface.messageBar().pushWidget(widget, Qgis.MessageLevel.Info, 2)
                return

            use_gdal_source = self._should_use_gdal_source(
                layer, source_path, rasterPath, isExportOnlyWorldFile
            )

            if use_gdal_source:
                src_ds = gdal_utils.open_dataset(source_path)
                originalWidth = src_ds.RasterXSize
                originalHeight = src_ds.RasterYSize
                src_ds = None
            else:
                originalWidth = layer.image.width()
                originalHeight = layer.image.height()

            affine, img = self._compute_export_affine_and_image(
                layer,
                originalWidth,
                originalHeight,
                isPutRotationInWorldFile,
                isExportOnlyWorldFile,
            )
            a, d, b, e, c, f = affine

            if not isExportOnlyWorldFile:
                if use_gdal_source:
                    if rasterFormat != "tif":
                        raise RuntimeError(
                            "Full-quality export from the original raster requires "
                            "a GeoTIFF (.tif) output file."
                        )

                    geotransform = gdal_utils.world_file_to_geotransform(
                        a, d, b, e, c, f
                    )
                    if isPutRotationInWorldFile:
                        gdal_utils.export_georeferenced_raster(
                            source_path, rasterPath, geotransform, crs_wkt
                        )
                    else:
                        gdal_utils.warp_georeferenced_raster(
                            source_path,
                            rasterPath,
                            geotransform,
                            crs_wkt,
                            layer.extent(),
                            img.width(),
                            img.height(),
                        )
                elif rasterFormat == "tif":
                    writer = QImageWriter()
                    writer.setCompression(1)
                    writer.setFormat("TIFF")
                    writer.setFileName(rasterPath)
                    writer.write(img)
                else:
                    img.save(rasterPath, rasterFormat)

            self._write_sidecar_files(
                baseRasterFilePath, rasterFormat, rasterPath, a, d, b, e, c, f, crs
            )

            widget = QgsMessageBar.createMessage(
                "Raster Geoferencer", "Raster exported successfully."
            )
            self.iface.messageBar().pushWidget(widget, Qgis.MessageLevel.Info, 2)
        except Exception as ex:
            QgsMessageLog.logMessage(repr(ex))
            widget = QgsMessageBar.createMessage(
                "Raster Geoferencer",
                "There was an error performing this command. "
                "See QGIS Message log for details.",
            )
            self.iface.messageBar().pushWidget(widget, Qgis.MessageLevel.Critical, 5)

    def _should_use_gdal_source(
        self, layer, source_path, raster_path, is_export_only_world_file
    ):
        if not os.path.exists(source_path):
            return False
        if gdal_utils.open_dataset(source_path) is None:
            return False
        if getattr(layer, "imageTransformedForDisplay", False):
            return True
        if is_export_only_world_file:
            return utils.imageFormat(source_path) == "tif"
        return (
            utils.imageFormat(source_path) == "tif"
            and utils.imageFormat(raster_path) == "tif"
        )

    def _compute_export_affine_and_image(
        self,
        layer,
        originalWidth,
        originalHeight,
        isPutRotationInWorldFile,
        isExportOnlyWorldFile,
    ):
        radRotation = layer.rotation * math.pi / 180

        if isPutRotationInWorldFile or isExportOnlyWorldFile:
            a = layer.xScale * math.cos(radRotation)
            b = -layer.yScale * math.sin(radRotation)
            d = layer.xScale * -math.sin(radRotation)
            e = -layer.yScale * math.cos(radRotation)
            c = layer.center.x() - (
                a * (originalWidth - 1) / 2 + b * (originalHeight - 1) / 2
            )
            f = layer.center.y() - (
                d * (originalWidth - 1) / 2 + e * (originalHeight - 1) / 2
            )
            return (a, d, b, e, c, f), layer.image

        ratio = layer.xScale / layer.yScale
        if ratio > 1:
            scaleX = ratio
            scaleY = 1
        else:
            scaleX = 1
            scaleY = 1.0 / ratio

        width = abs(scaleX * originalWidth * math.cos(radRotation)) + abs(
            scaleY * originalHeight * math.sin(radRotation)
        )
        height = abs(scaleX * originalWidth * math.sin(radRotation)) + abs(
            scaleY * originalHeight * math.cos(radRotation)
        )

        debug_log("wh %f,%f" % (width, height))

        img = QImage(
            QSize(math.ceil(width), math.ceil(height)), QImage.Format.Format_ARGB32
        )
        img.fill(QColor(0, 0, 0, 0))

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(
            QPointF(-layer.image.width() / 2.0, -layer.image.height() / 2.0),
            QPointF(layer.image.width() / 2.0, layer.image.height() / 2.0),
        )

        painter.translate(QPointF(width / 2.0, height / 2.0))
        painter.rotate(layer.rotation)
        painter.scale(scaleX, scaleY)
        painter.drawImage(rect, layer.image)
        painter.end()

        extent = layer.extent()
        a = extent.width() / width
        e = -extent.height() / height
        c = extent.xMinimum() + a / 2
        f = extent.yMaximum() + e / 2
        b = d = 0.0

        return (a, d, b, e, c, f), img

    def _write_sidecar_files(
        self, baseRasterFilePath, rasterFormat, rasterPath, a, d, b, e, c, f, crs
    ):
        worldFilePath = baseRasterFilePath + "."
        if rasterFormat == "jpg":
            worldFilePath += "jgw"
        elif rasterFormat == "png":
            worldFilePath += "pgw"
        elif rasterFormat == "bmp":
            worldFilePath += "bpw"
        elif rasterFormat == "tif":
            worldFilePath += "tfw"

        with open(worldFilePath, "w") as writer:
            writer.write(
                "%.13f\n%.13f\n%.13f\n%.13f\n%.13f\n%.13f" % (a, d, b, e, c, f)
            )

        crsFilePath = rasterPath + ".aux.xml"
        with open(crsFilePath, "w") as writer:
            writer.write(self.auxContent(crs))

    def auxContent(self, crs):
        content = """<PAMDataset>
  <Metadata domain="xml:ESRI" format="xml">
    <GeodataXform xsi:type="typens:IdentityXform" 
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
      xmlns:xs="http://www.w3.org/2001/XMLSchema" 
      xmlns:typens="http://www.esri.com/schemas/ArcGIS/9.2">
      <SpatialReference xsi:type="typens:%sCoordinateSystem">
        <WKT>%s</WKT>
      </SpatialReference>
    </GeodataXform>
  </Metadata>
</PAMDataset>"""  # noqa
        geogOrProj = "Geographic" if crs.isGeographic() else "Projected"
        return content % (geogOrProj, crs.toWkt())
