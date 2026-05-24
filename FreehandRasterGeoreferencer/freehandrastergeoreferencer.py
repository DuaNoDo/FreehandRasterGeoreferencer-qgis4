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

import os.path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDialog, QDoubleSpinBox
from qgis.core import QgsApplication, QgsMapLayer, QgsProject

from . import resources_rc  # noqa
from .exportgeorefrasterdialog import ExportGeorefRasterDialog
from .freehandrastergeoreferencer_commands import ExportGeorefRasterCommand
from .freehandrastergeoreferencer_layer import (
    FreehandRasterGeoreferencerLayer,
    FreehandRasterGeoreferencerLayerType,
)
from .freehandrastergeoreferencer_maptools import (
    AdjustRasterMapTool,
    GeorefRasterBy2PointsMapTool,
    GeorefRasterByGcpMapTool,
    GeorefRasterByGridGcpMapTool,
    MoveRasterMapTool,
    RotateRasterMapTool,
    ScaleRasterMapTool,
)
from .freehandrastergeoreferencerdialog import FreehandRasterGeoreferencerDialog


class FreehandRasterGeoreferencer(object):

    PLUGIN_MENU = "&Freehand Raster Georeferencer"

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.layers = {}
        self.layer = None
        self.currentTool = None
        self.toolbar = None
        self.layerType = None
        self.dialogAddLayer = None
        self.dialogExportGeorefRaster = None
        self.moveTool = None
        self.rotateTool = None
        self.scaleTool = None
        self.adjustTool = None
        self.georef2PTool = None
        self.georefGcpTool = None
        self.georefGridGcpTool = None
        QgsProject.instance().layerRemoved.connect(self.layerRemoved)
        self.iface.currentLayerChanged.connect(self.currentLayerChanged)

    def initGui(self):
        self.dialogAddLayer = FreehandRasterGeoreferencerDialog()
        self.dialogExportGeorefRaster = ExportGeorefRasterDialog()

        self.moveTool = MoveRasterMapTool(self.iface)
        self.rotateTool = RotateRasterMapTool(self.iface)
        self.scaleTool = ScaleRasterMapTool(self.iface)
        self.adjustTool = AdjustRasterMapTool(self.iface)
        self.georef2PTool = GeorefRasterBy2PointsMapTool(self.iface)
        self.currentTool = None

        # Create actions
        self.actionAddLayer = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconAdd.png"),
            "Add raster for interactive georeferencing",
            self.iface.mainWindow(),
        )
        self.actionAddLayer.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_AddLayer"
        )
        self.actionAddLayer.triggered.connect(self.addLayer)

        self.actionMoveRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconMove.png"),
            "Move raster",
            self.iface.mainWindow(),
        )
        self.actionMoveRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_MoveRaster"
        )
        self.actionMoveRaster.triggered.connect(self.moveRaster)
        self.actionMoveRaster.setCheckable(True)
        self.moveTool.setAction(self.actionMoveRaster)

        self.actionRotateRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconRotate.png"),
            "Rotate raster",
            self.iface.mainWindow(),
        )
        self.actionRotateRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_RotateRaster"
        )
        self.actionRotateRaster.triggered.connect(self.rotateRaster)
        self.actionRotateRaster.setCheckable(True)
        self.rotateTool.setAction(self.actionRotateRaster)

        self.actionScaleRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconScale.png"),
            "Scale raster",
            self.iface.mainWindow(),
        )
        self.actionScaleRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_ScaleRaster"
        )
        self.actionScaleRaster.triggered.connect(self.scaleRaster)
        self.actionScaleRaster.setCheckable(True)
        self.scaleTool.setAction(self.actionScaleRaster)

        self.actionAdjustRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconAdjust.png"),
            "Adjust sides of raster",
            self.iface.mainWindow(),
        )
        self.actionAdjustRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_AdjustRaster"
        )
        self.actionAdjustRaster.triggered.connect(self.adjustRaster)
        self.actionAdjustRaster.setCheckable(True)
        self.adjustTool.setAction(self.actionAdjustRaster)

        self.actionGeoref2PRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/icon2Points.png"),
            "Georeference raster with 2 points",
            self.iface.mainWindow(),
        )
        self.actionGeoref2PRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_Georef2PRaster"
        )
        self.actionGeoref2PRaster.triggered.connect(self.georef2PRaster)
        self.actionGeoref2PRaster.setCheckable(True)
        self.georef2PTool.setAction(self.actionGeoref2PRaster)

        self.actionGeorefGcpRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/icon2Points.png"),
            "Georeference with multiple ground control points",
            self.iface.mainWindow(),
        )
        self.actionGeorefGcpRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_GeorefGcpRaster"
        )
        self.actionGeorefGcpRaster.setToolTip(
            "Place GCPs on the raster, then click the matching map location. "
            "Right click removes the last point."
        )
        self.actionGeorefGcpRaster.triggered.connect(self.georefGcpRaster)
        self.actionGeorefGcpRaster.setCheckable(True)
        self.georefGcpTool = GeorefRasterByGcpMapTool(self.iface)
        self.georefGcpTool.setAction(self.actionGeorefGcpRaster)

        self.actionGeorefGridGcpRaster = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/icon2Points.png"),
            "Georeference with grid ground control points",
            self.iface.mainWindow(),
        )
        self.actionGeorefGridGcpRaster.setObjectName(
            "FreehandRasterGeoreferencingLayerPlugin_GeorefGridGcpRaster"
        )
        self.actionGeorefGridGcpRaster.setToolTip(
            "Create a grid of GCPs (3x3 to 6x6) and adjust each point on the map."
        )
        self.actionGeorefGridGcpRaster.triggered.connect(self.georefGridGcpRaster)
        self.actionGeorefGridGcpRaster.setCheckable(True)
        self.georefGridGcpTool = GeorefRasterByGridGcpMapTool(self.iface)
        self.georefGridGcpTool.setAction(self.actionGeorefGridGcpRaster)

        self.actionIncreaseTransparency = QAction(
            QIcon(
                ":/plugins/freehandrastergeoreferencer/" "iconTransparencyIncrease.png"
            ),
            "Increase transparency",
            self.iface.mainWindow(),
        )
        self.actionIncreaseTransparency.triggered.connect(self.increaseTransparency)
        self.actionIncreaseTransparency.setShortcut("Alt+Ctrl+N")

        self.actionDecreaseTransparency = QAction(
            QIcon(
                ":/plugins/freehandrastergeoreferencer/" "iconTransparencyDecrease.png"
            ),
            "Decrease transparency",
            self.iface.mainWindow(),
        )
        self.actionDecreaseTransparency.triggered.connect(self.decreaseTransparency)
        self.actionDecreaseTransparency.setShortcut("Alt+Ctrl+B")

        self.actionExport = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconExport.png"),
            "Export raster with world file",
            self.iface.mainWindow(),
        )
        self.actionExport.triggered.connect(self.exportGeorefRaster)

        self.actionUndo = QAction(
            QIcon(":/plugins/freehandrastergeoreferencer/iconUndo.png"),
            u"Undo",
            self.iface.mainWindow(),
        )
        self.actionUndo.triggered.connect(self.undo)

        # Add toolbar button and menu item for AddLayer
        self.iface.layerToolBar().addAction(self.actionAddLayer)
        self.iface.insertAddLayerAction(self.actionAddLayer)
        self.iface.addPluginToRasterMenu(
            FreehandRasterGeoreferencer.PLUGIN_MENU, self.actionAddLayer
        )

        self.spinBoxRotate = QDoubleSpinBox(self.iface.mainWindow())
        self.spinBoxRotate.setDecimals(3)
        self.spinBoxRotate.setMinimum(-180)
        self.spinBoxRotate.setMaximum(180)
        self.spinBoxRotate.setSingleStep(0.1)
        self.spinBoxRotate.setValue(0.0)
        self.spinBoxRotate.setToolTip("Rotation value (-180 to 180)")
        self.spinBoxRotate.setObjectName("FreehandRasterGeoreferencer_spinbox")
        self.spinBoxRotate.setKeyboardTracking(False)
        self.spinBoxRotate.valueChanged.connect(self.spinBoxRotateValueChangeEvent)
        self.spinBoxRotate.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spinBoxRotate.focusInEvent = self.spinBoxRotateFocusInEvent

        # create toolbar for this plugin
        self.toolbar = self.iface.addToolBar("Freehand raster georeferencing")
        self.toolbar.addAction(self.actionAddLayer)
        self.toolbar.addAction(self.actionMoveRaster)
        self.toolbar.addAction(self.actionRotateRaster)
        self.toolbar.addWidget(self.spinBoxRotate)
        self.toolbar.addAction(self.actionScaleRaster)
        self.toolbar.addAction(self.actionAdjustRaster)
        self.toolbar.addAction(self.actionGeoref2PRaster)
        self.toolbar.addAction(self.actionGeorefGcpRaster)
        self.toolbar.addAction(self.actionGeorefGridGcpRaster)
        self.toolbar.addAction(self.actionDecreaseTransparency)
        self.toolbar.addAction(self.actionIncreaseTransparency)
        self.toolbar.addAction(self.actionExport)
        self.toolbar.addAction(self.actionUndo)

        # Register plugin layer type
        registry = QgsApplication.pluginLayerRegistry()
        if registry.pluginLayerType(FreehandRasterGeoreferencerLayer.LAYER_TYPE) is None:
            self.layerType = FreehandRasterGeoreferencerLayerType(self)
            registry.addPluginLayerType(self.layerType)

        # default state for toolbar
        self.checkCurrentLayerIsPluginLayer()

    def unload(self):
        # Remove the plugin menu item and icon
        if hasattr(self, "actionAddLayer"):
            self.iface.layerToolBar().removeAction(self.actionAddLayer)
            self.iface.removeAddLayerAction(self.actionAddLayer)
            self.iface.removePluginRasterMenu(
                FreehandRasterGeoreferencer.PLUGIN_MENU, self.actionAddLayer
            )

        # Unregister plugin layer type
        registry = QgsApplication.pluginLayerRegistry()
        if registry.pluginLayerType(FreehandRasterGeoreferencerLayer.LAYER_TYPE) is not None:
            registry.removePluginLayerType(
                FreehandRasterGeoreferencerLayer.LAYER_TYPE
            )

        QgsProject.instance().layerRemoved.disconnect(self.layerRemoved)
        self.iface.currentLayerChanged.disconnect(self.currentLayerChanged)

        if self.toolbar is not None:
            del self.toolbar
            self.toolbar = None

    def layerRemoved(self, layerId):
        if layerId in self.layers:
            del self.layers[layerId]
            self.checkCurrentLayerIsPluginLayer()

    def currentLayerChanged(self, layer):
        self.checkCurrentLayerIsPluginLayer()

    def checkCurrentLayerIsPluginLayer(self):
        layer = self.iface.activeLayer()
        if (
            layer
            and layer.type() == QgsMapLayer.LayerType.PluginLayer
            and layer.pluginLayerType() == FreehandRasterGeoreferencerLayer.LAYER_TYPE
        ):
            self.actionMoveRaster.setEnabled(True)
            self.actionRotateRaster.setEnabled(True)
            self.actionScaleRaster.setEnabled(True)
            self.actionAdjustRaster.setEnabled(True)
            self.actionGeoref2PRaster.setEnabled(True)
            self.actionGeorefGcpRaster.setEnabled(True)
            self.actionGeorefGridGcpRaster.setEnabled(True)
            self.actionDecreaseTransparency.setEnabled(True)
            self.actionIncreaseTransparency.setEnabled(True)
            self.actionExport.setEnabled(True)
            self.spinBoxRotate.setEnabled(True)
            self.spinBoxRotateValueSetValue(layer.rotation)
            try:
                # self.layer is the previously selected layer
                # in case it was a FRGR layer, disconnect the spinBox
                self.layer.transformParametersChanged.disconnect()
            except Exception:
                pass
            layer.transformParametersChanged.connect(self.spinBoxRotateUpdate)
            dialog_add_layer = getattr(self, "dialogAddLayer", None)
            if dialog_add_layer is not None:
                dialog_add_layer.toolButtonAdvanced.setEnabled(True)
            self.actionUndo.setEnabled(True)
            self.layer = layer

            if self.currentTool:
                self.currentTool.reset()
                self.currentTool.setLayer(layer)
        else:
            self.actionMoveRaster.setEnabled(False)
            self.actionRotateRaster.setEnabled(False)
            self.actionScaleRaster.setEnabled(False)
            self.actionAdjustRaster.setEnabled(False)
            self.actionGeoref2PRaster.setEnabled(False)
            self.actionGeorefGcpRaster.setEnabled(False)
            self.actionGeorefGridGcpRaster.setEnabled(False)
            self.actionDecreaseTransparency.setEnabled(False)
            self.actionIncreaseTransparency.setEnabled(False)
            self.actionExport.setEnabled(False)
            self.spinBoxRotate.setEnabled(False)
            self.spinBoxRotateValueSetValue(0)
            try:
                self.layer.transformParametersChanged.disconnect()
            except Exception:
                pass
            dialog_add_layer = getattr(self, "dialogAddLayer", None)
            if dialog_add_layer is not None:
                dialog_add_layer.toolButtonAdvanced.setEnabled(False)
            self.actionUndo.setEnabled(False)
            self.layer = None

            if self.currentTool:
                self.currentTool.reset()
                self.currentTool.setLayer(None)
                self._uncheckCurrentTool()

    def addLayer(self):
        dialog_add_layer = getattr(self, "dialogAddLayer", None)
        if dialog_add_layer is None:
            return

        dialog_add_layer.clear(self.layer)
        dialog_add_layer.show()
        result = dialog_add_layer.exec()
        if result == QDialog.DialogCode.Accepted:
            self.createFreehandRasterGeoreferencerLayer()
        elif result == FreehandRasterGeoreferencerDialog.REPLACE:
            self.replaceImage()
        elif result == FreehandRasterGeoreferencerDialog.DUPLICATE:
            self.duplicateLayer()

    def replaceImage(self):
        dialog_add_layer = getattr(self, "dialogAddLayer", None)
        if dialog_add_layer is None:
            return

        imagepath = dialog_add_layer.lineEditImagePath.text()
        imagename, _ = os.path.splitext(os.path.basename(imagepath))
        self.layer.replaceImage(imagepath, imagename)

    def duplicateLayer(self):
        layer = self.iface.activeLayer().clone()
        QgsProject.instance().addMapLayer(layer)
        self.layers[layer.id()] = layer

    def createFreehandRasterGeoreferencerLayer(self):
        dialog_add_layer = getattr(self, "dialogAddLayer", None)
        if dialog_add_layer is None:
            return

        imagePath = dialog_add_layer.lineEditImagePath.text()
        imageName, _ = os.path.splitext(os.path.basename(imagePath))
        screenExtent = self.iface.mapCanvas().extent()

        layer = FreehandRasterGeoreferencerLayer(
            self, imagePath, imageName, screenExtent
        )
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.layers[layer.id()] = layer
            self.iface.setActiveLayer(layer)

    def _toggleTool(self, tool):
        layer = self.iface.activeLayer()
        if (
            layer
            and hasattr(layer, "affineTransformBlocked")
            and layer.affineTransformBlocked()
            and tool
            in (
                self.rotateTool,
                self.scaleTool,
                self.adjustTool,
                self.georef2PTool,
                self.georefGcpTool,
            )
        ):
            self.iface.mainWindow().statusBar().showMessage(
                "Grid is locked. Turn on Move to shift the grid and image together, then turn Move off.",
                6000,
            )
            return

        if self.currentTool is tool:
            # Toggle
            self._uncheckCurrentTool()
        else:
            self.currentTool = tool
            layer = self.iface.activeLayer()
            tool.setLayer(layer)
            self.iface.mapCanvas().setMapTool(tool)

    def _uncheckCurrentTool(self):
        # Toggle
        self.iface.mapCanvas().unsetMapTool(self.currentTool)
        # replace tool with Pan
        self.iface.actionPan().trigger()
        self.currentTool = None

    def moveRaster(self):
        self._toggleTool(self.moveTool)

    def rotateRaster(self):
        self._toggleTool(self.rotateTool)

    def scaleRaster(self):
        self._toggleTool(self.scaleTool)

    def adjustRaster(self):
        self._toggleTool(self.adjustTool)

    def georef2PRaster(self):
        self._toggleTool(self.georef2PTool)

    def georefGcpRaster(self):
        self._toggleTool(self.georefGcpTool)

    def georefGridGcpRaster(self):
        self._toggleTool(self.georefGridGcpTool)

    def increaseTransparency(self):
        layer = self.iface.activeLayer()
        # clamp to 100
        tr = min(layer.transparency + 10, 100)
        layer.transparencyChanged(tr)

    def decreaseTransparency(self):
        layer = self.iface.activeLayer()
        # clamp to 0
        tr = max(layer.transparency - 10, 0)
        layer.transparencyChanged(tr)

    def exportGeorefRaster(self):
        layer = self.iface.activeLayer()
        self.dialogExportGeorefRaster.clear(layer)
        self.dialogExportGeorefRaster.show()
        result = self.dialogExportGeorefRaster.exec()
        if result == QDialog.DialogCode.Accepted:
            exportCommand = ExportGeorefRasterCommand(self.iface)
            exportCommand.exportGeorefRaster(
                layer,
                self.dialogExportGeorefRaster.imagePath,
                self.dialogExportGeorefRaster.isPutRotationInWorldFile,
                self.dialogExportGeorefRaster.isExportOnlyWorldFile,
            )

    def spinBoxRotateUpdate(self, newParameters):
        self.spinBoxRotateValueSetValue(self.layer.rotation)

    def spinBoxRotateValueChangeEvent(self, val):
        layer = self.layer
        if layer is not None and layer.affineTransformBlocked():
            self.spinBoxRotateValueSetValue(layer.rotation)
            return
        layer.history.append(
            {"action": "rotation", "rotation": layer.rotation, "center": layer.center}
        )
        layer.setRotation(val)
        layer.repaint()
        layer.commitTransformParameters()

    def spinBoxRotateValueSetValue(self, val):
        # for changing only the spinbox value
        self.spinBoxRotate.valueChanged.disconnect()
        self.spinBoxRotate.setValue(val)
        self.spinBoxRotate.valueChanged.connect(self.spinBoxRotateValueChangeEvent)

    def spinBoxRotateFocusInEvent(self, event):
        # for clear 2point rubberband
        if self.currentTool:
            layer = self.iface.activeLayer()
            self.currentTool.reset()
            self.currentTool.setLayer(layer)

    def undo(self):
        layer = self.iface.activeLayer()
        if self.currentTool:
            self.currentTool.reset()  # for clear 2point rubberband
            self.currentTool.setLayer(layer)
        if len(layer.history) > 0:
            act = layer.history.pop()
            if act["action"] == "move":
                layer.setCenter(act["center"])
            elif act["action"] == "move_gcp":
                layer.setCenter(act["center"])
                layer.restoreGcpHistoryState(act["gcp_state"])
            elif act["action"] == "scale":
                layer.setScale(act["xScale"], act["yScale"])
            elif act["action"] == "rotation":
                layer.setRotation(act["rotation"])
                layer.setCenter(act["center"])
            elif act["action"] == "adjust":
                layer.setCenter(act["center"])
                layer.setScale(act["xScale"], act["yScale"])
            elif act["action"] == "2pointsA":
                layer.setCenter(act["center"])
            elif act["action"] == "2pointsB":
                layer.setRotation(act["rotation"])
                layer.setCenter(act["center"])
                layer.setScale(act["xScale"], act["yScale"])
                layer.setScale(act["xScale"], act["yScale"])
            elif act["action"] == "gcp":
                layer.restoreGcpHistoryState(act)
            layer.repaint()
            layer.commitTransformParameters()
