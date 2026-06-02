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
import re
from operator import itemgetter

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication, QInputDialog, QMessageBox
from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand

from .gcp_transform import (
    map_to_pixel,
    nearest_gcp_index,
    nearest_gcp_index_by_source,
    transformed_image_corners,
)
from .rastershadowmapcanvasitem import RasterShadowMapCanvasItem
from .utils import tryfloat


GRID_GCP_SIZES = tuple(range(3, 16))


def _grid_layout_label(size):
    return "%d x %d (%d points)" % (size, size, size * size)


def _grid_layout_choices(replace=False):
    labels = [_grid_layout_label(size) for size in GRID_GCP_SIZES]
    if replace:
        return ["Keep existing grid"] + [
            "Replace with " + label for label in labels
        ]
    return labels


def _grid_size_from_choice(choice):
    if choice.startswith("Keep"):
        return None
    match = re.search(r"(\d+)\s*x\s*(\d+)", choice)
    if match is None:
        return None
    return int(match.group(1))


def isLayerVisible(iface, layer):
    # TODO Really ???? See if there is something simpler
    vl = iface.layerTreeView().layerTreeModel().rootGroup().findLayer(layer)
    return vl.itemVisibilityChecked()


def setLayerVisible(iface, layer, visible):
    vl = iface.layerTreeView().layerTreeModel().rootGroup().findLayer(layer)
    vl.setItemVisibilityChecked(visible)


class MoveRasterMapTool(QgsMapToolEmitPoint):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.rasterShadow = RasterShadowMapCanvasItem(self.canvas)

        self.rubberBandDisplacement = QgsRubberBand(
            self.canvas, QgsWkbTypes.GeometryType.LineGeometry
        )
        self.rubberBandDisplacement.setColor(Qt.GlobalColor.red)
        self.rubberBandDisplacement.setWidth(1)

        self.rubberBandExtent = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.setColor(Qt.GlobalColor.red)
        self.rubberBandExtent.setWidth(1)

        self.isLayerVisible = True

        self.reset()

    def setLayer(self, layer):
        self.layer = layer

    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()
        self.layer = None

    def activate(self):
        QgsMapToolEmitPoint.activate(self)
        if self.layer is not None and self.layer.hasGridGcps():
            self.layer.setGridGeorefLocked(False)

    def deactivate(self):
        if self.layer is not None and self.layer.hasGridGcps():
            self.layer.setGridGeorefLocked(True)
        QgsMapToolEmitPoint.deactivate(self)
        self.reset()

    def canvasPressEvent(self, e):
        if self.layer is None:
            return

        self.startPoint = self.toMapCoordinates(e.pos())
        self.endPoint = self.startPoint
        self.isEmittingPoint = True
        self.originalCenter = self.layer.center
        if self.layer.hasGridGcps():
            self.originalCornerPoints = transformed_image_corners(self.layer)
        else:
            self.originalCornerPoints = self.layer.transformedCornerCoordinates(
                *self.layer.transformParameters()
            )

        self.isLayerVisible = isLayerVisible(self.iface, self.layer)
        setLayerVisible(self.iface, self.layer, False)

        self.showDisplacement(self.startPoint, self.endPoint)
        if self.layer.hasGridGcps():
            self.layer.history.append(
                {
                    "action": "move_gcp",
                    "center": self.layer.center,
                    "gcp_state": self.layer.gcpHistoryState(),
                }
            )
        else:
            self.layer.history.append({"action": "move", "center": self.layer.center})

    def canvasReleaseEvent(self, e):
        self.isEmittingPoint = False

        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()

        dx = self.endPoint.x() - self.startPoint.x()
        dy = self.endPoint.y() - self.startPoint.y()
        x = self.originalCenter.x() + dx
        y = self.originalCenter.y() + dy
        if self.layer.hasGridGcps():
            self.layer.translateGcps(dx, dy)
        self.layer.setCenter(QgsPointXY(x, y))

        setLayerVisible(self.iface, self.layer, self.isLayerVisible)
        self.layer.repaint()

        self.layer.commitTransformParameters()

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return

        self.endPoint = self.toMapCoordinates(e.pos())
        self.showDisplacement(self.startPoint, self.endPoint)

    def showDisplacement(self, startPoint, endPoint):
        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        point1 = QgsPointXY(startPoint.x(), startPoint.y())
        point2 = QgsPointXY(endPoint.x(), endPoint.y())
        self.rubberBandDisplacement.addPoint(point1, False)
        self.rubberBandDisplacement.addPoint(point2, True)  # true to update canvas
        self.rubberBandDisplacement.show()

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in self.originalCornerPoints:
            self._addDisplacementToPoint(self.rubberBandExtent, point, False)
        # for closing
        self._addDisplacementToPoint(
            self.rubberBandExtent, self.originalCornerPoints[0], True
        )
        self.rubberBandExtent.show()

        self.rasterShadow.reset(self.layer)
        self.rasterShadow.setDeltaDisplacement(
            self.endPoint.x() - self.startPoint.x(),
            self.endPoint.y() - self.startPoint.y(),
            True,
        )
        self.rasterShadow.show()

    def _addDisplacementToPoint(self, rubberBand, point, doUpdate):
        x = point.x() + self.endPoint.x() - self.startPoint.x()
        y = point.y() + self.endPoint.y() - self.startPoint.y()
        self.rubberBandExtent.addPoint(QgsPointXY(x, y), doUpdate)


# move the mouse in the Y axis to rotate


class RotateRasterMapTool(QgsMapToolEmitPoint):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.rasterShadow = RasterShadowMapCanvasItem(self.canvas)

        self.rubberBandExtent = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.setColor(Qt.GlobalColor.red)
        self.rubberBandExtent.setWidth(1)

        # In case of rotation around pressed point (ctrl)
        # Use rubberBand for displaying an horizontal line.
        self.rubberBandDisplacement = QgsRubberBand(
            self.canvas, QgsWkbTypes.GeometryType.LineGeometry
        )
        self.rubberBandDisplacement.setColor(Qt.GlobalColor.red)
        self.rubberBandDisplacement.setWidth(1)

        self.reset()

    def setLayer(self, layer):
        self.layer = layer

    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()
        self.layer = None

    def canvasPressEvent(self, e):
        self.startY = e.pos().y()
        self.endY = self.startY
        self.isEmittingPoint = True
        self.height = self.canvas.height()

        modifiers = QApplication.keyboardModifiers()
        self.isRotationAroundPoint = bool(
            modifiers & Qt.KeyboardModifier.ControlModifier
        )
        self.startPoint = self.toMapCoordinates(e.pos())
        self.endPoint = self.startPoint

        self.isLayerVisible = isLayerVisible(self.iface, self.layer)
        setLayerVisible(self.iface, self.layer, False)

        rotation = self.computeRotation()
        self.showRotation(rotation)

        self.layer.history.append(
            {
                "action": "rotation",
                "rotation": self.layer.rotation,
                "center": self.layer.center,
            }
        )  # rotation set

    def canvasReleaseEvent(self, e):
        self.isEmittingPoint = False

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()

        rotation = self.computeRotation()
        if self.isRotationAroundPoint:
            self.layer.moveCenterFromPointRotate(self.startPoint, rotation, 1, 1)
        val = self.layer.rotation + rotation

        self.layer.setRotation(val)

        setLayerVisible(self.iface, self.layer, self.isLayerVisible)
        self.layer.repaint()

        self.layer.commitTransformParameters()

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return

        self.endY = e.pos().y()
        rotation = self.computeRotation()
        self.showRotation(rotation)

        self.endPoint = self.toMapCoordinates(e.pos())

    def computeRotation(self):
        if self.isRotationAroundPoint:
            dX = self.endPoint.x() - self.startPoint.x()
            dY = self.endPoint.y() - self.startPoint.y()
            return math.degrees(math.atan2(-dY, dX))
        else:
            dY = self.endY - self.startY
            return 90.0 * dY / self.height

    def showRotation(self, rotation):
        if self.isRotationAroundPoint:
            cornerPoints = self.layer.transformedCornerCoordinatesFromPoint(
                self.startPoint, rotation, 1, 1
            )

            self.rasterShadow.reset(self.layer)
            self.rasterShadow.setDeltaRotationFromPoint(rotation, self.startPoint, True)
            self.rasterShadow.show()

            self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
            point0 = QgsPointXY(self.startPoint.x() + 10, self.startPoint.y())
            point1 = QgsPointXY(self.startPoint.x(), self.startPoint.y())
            point2 = QgsPointXY(self.endPoint.x(), self.endPoint.y())
            self.rubberBandDisplacement.addPoint(point0, False)
            self.rubberBandDisplacement.addPoint(point1, False)
            self.rubberBandDisplacement.addPoint(point2, True)  # true to update canvas
            self.rubberBandDisplacement.show()
        else:
            center, originalRotation, xScale, yScale = self.layer.transformParameters()
            newRotation = rotation + originalRotation
            cornerPoints = self.layer.transformedCornerCoordinates(
                center, newRotation, xScale, yScale
            )

            self.rasterShadow.reset(self.layer)
            self.rasterShadow.setDeltaRotation(rotation, True)
            self.rasterShadow.show()

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in cornerPoints:
            self.rubberBandExtent.addPoint(point, False)
        # for closing
        self.rubberBandExtent.addPoint(cornerPoints[0], True)
        self.rubberBandExtent.show()


# move the map in x or y axis to scale in x or y dimensions of the
# image (no rotation of the coordinate system)
class ScaleRasterMapTool(QgsMapToolEmitPoint):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.rasterShadow = RasterShadowMapCanvasItem(self.canvas)

        self.rubberBandExtent = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.setColor(Qt.GlobalColor.red)
        self.rubberBandExtent.setWidth(1)

        self.reset()

    def setLayer(self, layer):
        self.layer = layer

    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()
        self.layer = None

    def canvasPressEvent(self, e):
        pressed_button = e.button()
        if pressed_button == 1:
            self.startPoint = e.pos()
            self.endPoint = self.startPoint
            self.isEmittingPoint = True
            self.height = float(self.canvas.height())
            self.width = float(self.canvas.width())

            modifiers = QApplication.keyboardModifiers()
            self.isKeepRelativeScale = bool(
                modifiers & Qt.KeyboardModifier.ControlModifier
            )

            self.isLayerVisible = isLayerVisible(self.iface, self.layer)
            setLayerVisible(self.iface, self.layer, False)

            scaling = self.computeScaling()
            self.showScaling(*scaling)
        self.layer.history.append(
            {
                "action": "scale",
                "xScale": self.layer.xScale,
                "yScale": self.layer.yScale,
            }
        )

    def canvasReleaseEvent(self, e):
        pressed_button = e.button()
        if pressed_button == 1:
            self.isEmittingPoint = False

            self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self.rasterShadow.reset()

            xScale, yScale = self.computeScaling()
            self.layer.setScale(xScale * self.layer.xScale, yScale * self.layer.yScale)

            setLayerVisible(self.iface, self.layer, self.isLayerVisible)
        elif pressed_button == 2:
            number, ok = QInputDialog.getText(
                None, "Scale & DPI", "Enter scale,dpi (e.g. 3000,96)"
            )
            if not ok:
                self.layer.history.pop()
                return
            scales = number.split(",")
            if len(scales) != 2:
                self.layer.history.pop()
                QMessageBox.information(
                    self.iface.mainWindow(), "Error", "Must be 2 numbers"
                )
                return
            scale = tryfloat(scales[0])
            dpi = tryfloat(scales[1])
            if scale and dpi:
                xScale = scale / (dpi / 0.0254)
                yScale = xScale
            else:
                self.layer.history.pop()
                QMessageBox.information(
                    self.iface.mainWindow(),
                    "Error",
                    "Bad format: Must be scale,dpi (e.g. 3000,96)",
                )
                return

            self.layer.setScale(xScale, yScale)

        self.layer.repaint()
        self.layer.commitTransformParameters()

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return

        self.endPoint = e.pos()
        scaling = self.computeScaling()
        self.showScaling(*scaling)

    def computeScaling(self):
        dX = -(self.endPoint.x() - self.startPoint.x())
        dY = self.endPoint.y() - self.startPoint.y()
        xScale = 1.0 - (dX / (self.width * 1.1))
        yScale = 1.0 - (dY / (self.height * 1.1))

        if self.isKeepRelativeScale:
            # keep same scale in both dimensions
            return (xScale, xScale)
        else:
            return (xScale, yScale)

    def showScaling(self, xScale, yScale):
        if xScale == 0 and yScale == 0:
            return

        center, rotation, originalXScale, originalYScale = (
            self.layer.transformParameters()
        )
        newXScale = xScale * originalXScale
        newYScale = yScale * originalYScale
        cornerPoints = self.layer.transformedCornerCoordinates(
            center, rotation, newXScale, newYScale
        )

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in cornerPoints:
            self.rubberBandExtent.addPoint(point, False)
        # for closing
        self.rubberBandExtent.addPoint(cornerPoints[0], True)
        self.rubberBandExtent.show()

        self.rasterShadow.reset(self.layer)
        self.rasterShadow.setDeltaScale(xScale, yScale, True)
        self.rasterShadow.show()


class AdjustRasterMapTool(QgsMapToolEmitPoint):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.rasterShadow = RasterShadowMapCanvasItem(self.canvas)

        self.rubberBandExtent = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.setColor(Qt.GlobalColor.red)
        self.rubberBandExtent.setWidth(1)

        self.rubberBandAdjustSide = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandAdjustSide.setColor(Qt.GlobalColor.red)
        self.rubberBandAdjustSide.setWidth(3)

        self.reset()

    def setLayer(self, layer):
        self.layer = layer

    def reset(self):
        self.startPoint = self.endPoint = None
        self.isEmittingPoint = False
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandAdjustSide.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()
        self.layer = None

    def canvasPressEvent(self, e):
        # find the side of the rectangle closest to the click and some data
        # necessary to compute the new cneter and scale
        topLeft, topRight, bottomRight, bottomLeft = self.layer.cornerCoordinates()
        top = [topLeft, topRight]
        right = [bottomRight, topRight]
        bottom = [bottomRight, bottomLeft]
        left = [bottomLeft, topLeft]

        click = QgsGeometry.fromPointXY(self.toMapCoordinates(e.pos()))

        # order is important (for referenceSide)
        sides = [top, right, bottom, left]
        distances = [click.distance(QgsGeometry.fromPolylineXY(side)) for side in sides]
        self.indexSide = self.minDistance(distances)
        self.side = sides[self.indexSide]
        self.sidePoint = self.center(self.side)
        self.vector = self.directionVector(self.side)
        # side that does not move (opposite of indexSide)
        self.referenceSide = sides[(self.indexSide + 2) % 4]
        self.referencePoint = self.center(self.referenceSide)
        self.referenceDistance = self.distance(self.sidePoint, self.referencePoint)
        self.isXScale = self.indexSide % 2 == 1

        self.startPoint = click.asPoint()
        self.endPoint = self.startPoint
        self.isEmittingPoint = True

        self.isLayerVisible = isLayerVisible(self.iface, self.layer)
        setLayerVisible(self.iface, self.layer, False)

        adjustment = self.computeAdjustment()
        self.showAdjustment(*adjustment)
        self.layer.history.append(
            {
                "action": "adjust",
                "center": self.layer.center,
                "xScale": self.layer.xScale,
                "yScale": self.layer.yScale,
            }
        )

    def minDistance(self, distances):
        sortedDistances = [
            i[0] for i in sorted(enumerate(distances), key=itemgetter(1))
        ]
        # first is min
        return sortedDistances[0]

    def directionVector(self, side):
        sideCenter = self.center(side)
        layerCenter = self.layer.center
        vector = [sideCenter.x() - layerCenter.x(), sideCenter.y() - layerCenter.y()]
        norm = math.sqrt(vector[0] ** 2 + vector[1] ** 2)
        normedVector = [vector[0] / norm, vector[1] / norm]
        return normedVector

    def center(self, side):
        return QgsPointXY(
            (side[0].x() + side[1].x()) / 2, (side[0].y() + side[1].y()) / 2
        )

    def distance(self, pt1, pt2):
        return math.sqrt((pt1.x() - pt2.x()) ** 2 + (pt1.y() - pt2.y()) ** 2)

    def canvasReleaseEvent(self, e):
        self.isEmittingPoint = False

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandAdjustSide.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()

        center, xScale, yScale = self.computeAdjustment()
        self.layer.setCenter(center)
        self.layer.setScale(xScale * self.layer.xScale, yScale * self.layer.yScale)

        setLayerVisible(self.iface, self.layer, self.isLayerVisible)
        self.layer.repaint()

        self.layer.commitTransformParameters()

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return

        self.endPoint = self.toMapCoordinates(e.pos())

        adjustment = self.computeAdjustment()
        self.showAdjustment(*adjustment)

    def computeAdjustment(self):
        dX = self.endPoint.x() - self.startPoint.x()
        dY = self.endPoint.y() - self.startPoint.y()
        # project on vector
        dp = dX * self.vector[0] + dY * self.vector[1]

        # do not go beyond 5% of the current size of side
        if dp < -0.95 * self.referenceDistance:
            dp = -0.95 * self.referenceDistance

        updatedSidePoint = QgsPointXY(
            self.sidePoint.x() + dp * self.vector[0],
            self.sidePoint.y() + dp * self.vector[1],
        )

        center = self.center([self.referencePoint, updatedSidePoint])
        scaleFactor = self.distance(self.referencePoint, updatedSidePoint)
        if self.isXScale:
            xScale = scaleFactor / self.referenceDistance
            yScale = 1.0
        else:
            xScale = 1.0
            yScale = scaleFactor / self.referenceDistance

        return (center, xScale, yScale)

    def showAdjustment(self, center, xScale, yScale):
        _, rotation, originalXScale, originalYScale = self.layer.transformParameters()
        newXScale = xScale * originalXScale
        newYScale = yScale * originalYScale
        cornerPoints = self.layer.transformedCornerCoordinates(
            center, rotation, newXScale, newYScale
        )

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in cornerPoints:
            self.rubberBandExtent.addPoint(point, False)
        # for closing
        self.rubberBandExtent.addPoint(cornerPoints[0], True)
        self.rubberBandExtent.show()

        # show rubberband for side
        # see def of indexSide in init:
        # cornerpoints are (topLeft, topRight, bottomRight, bottomLeft)
        self.rubberBandAdjustSide.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandAdjustSide.addPoint(cornerPoints[self.indexSide % 4], False)
        self.rubberBandAdjustSide.addPoint(cornerPoints[(self.indexSide + 1) % 4], True)
        self.rubberBandAdjustSide.show()

        self.rasterShadow.reset(self.layer)
        dx = center.x() - self.layer.center.x()
        dy = center.y() - self.layer.center.y()
        self.rasterShadow.setDeltaDisplacement(dx, dy, False)
        self.rasterShadow.setDeltaScale(xScale, yScale, True)
        self.rasterShadow.show()


class GeorefRasterBy2PointsMapTool(QgsMapToolEmitPoint):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.rasterShadow = RasterShadowMapCanvasItem(self.canvas)

        self.firstPoint = None

        self.rubberBandOrigin = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self.rubberBandOrigin.setColor(Qt.GlobalColor.red)
        self.rubberBandOrigin.setIcon(
            getattr(
                getattr(QgsRubberBand, "Icon", QgsRubberBand),
                "ICON_CIRCLE",
                QgsRubberBand.ICON_CIRCLE,
            )
        )
        self.rubberBandOrigin.setIconSize(7)
        self.rubberBandOrigin.setWidth(2)

        self.rubberBandDisplacement = QgsRubberBand(
            self.canvas, QgsWkbTypes.GeometryType.LineGeometry
        )
        self.rubberBandDisplacement.setColor(Qt.GlobalColor.red)
        self.rubberBandDisplacement.setWidth(1)

        self.rubberBandExtent = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.setColor(Qt.GlobalColor.red)
        self.rubberBandExtent.setWidth(2)

        self.isLayerVisible = True

        self.reset()

    def setLayer(self, layer):
        self.layer = layer

    def reset(self):
        self.startPoint = self.endPoint = self.firstPoint = None
        self.isEmittingPoint = False
        self.rubberBandOrigin.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()
        self.layer = None

    def deactivate(self):
        QgsMapToolEmitPoint.deactivate(self)
        self.reset()

    def canvasPressEvent(self, e):
        if self.firstPoint is None:
            self.startPoint = self.toMapCoordinates(e.pos())
            self.endPoint = self.startPoint
            self.isEmittingPoint = True
            self.originalCenter = self.layer.center
            # this tool do the displacement itself TODO update so it is done by
            # transformed coordinates + new center)
            self.originalCornerPoints = self.layer.transformedCornerCoordinates(
                *self.layer.transformParameters()
            )

            self.isLayerVisible = isLayerVisible(self.iface, self.layer)
            setLayerVisible(self.iface, self.layer, False)

            self.showDisplacement(self.startPoint, self.endPoint)
            self.layer.history.append(
                {"action": "2pointsA", "center": self.layer.center}
            )
        else:
            self.startPoint = self.toMapCoordinates(e.pos())
            self.endPoint = self.startPoint

            self.startY = e.pos().y()
            self.endY = self.startY
            self.isEmittingPoint = True
            self.height = self.canvas.height()

            self.isLayerVisible = isLayerVisible(self.iface, self.layer)
            setLayerVisible(self.iface, self.layer, False)

            rotation = self.computeRotation()
            xScale = yScale = self.computeScale()
            self.showRotationScale(rotation, xScale, yScale)
            self.layer.history.append(
                {
                    "action": "2pointsB",
                    "center": self.layer.center,
                    "xScale": self.layer.xScale,
                    "yScale": self.layer.yScale,
                    "rotation": self.layer.rotation,
                }
            )

    def canvasReleaseEvent(self, e):
        self.isEmittingPoint = False

        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.rasterShadow.reset()

        if self.firstPoint is None:
            x = self.originalCenter.x() + self.endPoint.x() - self.startPoint.x()
            y = self.originalCenter.y() + self.endPoint.y() - self.startPoint.y()
            self.layer.setCenter(QgsPointXY(x, y))
            self.firstPoint = self.endPoint

            setLayerVisible(self.iface, self.layer, self.isLayerVisible)
            self.layer.repaint()

            self.layer.commitTransformParameters()
        else:
            rotation = self.computeRotation()
            xScale = yScale = self.computeScale()
            self.layer.moveCenterFromPointRotate(
                self.firstPoint, rotation, xScale, yScale
            )
            self.layer.setRotation(self.layer.rotation + rotation)
            self.layer.setScale(self.layer.xScale * xScale, self.layer.yScale * yScale)

            setLayerVisible(self.iface, self.layer, self.isLayerVisible)
            self.layer.repaint()

            self.layer.commitTransformParameters()

            self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self.rubberBandOrigin.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self.rasterShadow.reset()

            self.firstPoint = None
            self.startPoint = self.endPoint = None

    def canvasMoveEvent(self, e):
        if not self.isEmittingPoint:
            return

        self.endPoint = self.toMapCoordinates(e.pos())

        if self.firstPoint is None:
            self.showDisplacement(self.startPoint, self.endPoint)
        else:
            self.endY = e.pos().y()
            rotation = self.computeRotation()
            xScale = yScale = self.computeScale()
            self.showRotationScale(rotation, xScale, yScale)

    def computeRotation(self):
        # The angle is the difference between angle
        # horizontal/endPoint-firstPoint and horizontal/startPoint-firstPoint.
        dX0 = self.startPoint.x() - self.firstPoint.x()
        dY0 = self.startPoint.y() - self.firstPoint.y()
        dX = self.endPoint.x() - self.firstPoint.x()
        dY = self.endPoint.y() - self.firstPoint.y()
        return math.degrees(math.atan2(-dY, dX) - math.atan2(-dY0, dX0))

    def computeScale(self):
        # The scale is the ratio between endPoint-firstPoint and
        # startPoint-firstPoint.
        dX0 = self.startPoint.x() - self.firstPoint.x()
        dY0 = self.startPoint.y() - self.firstPoint.y()
        dX = self.endPoint.x() - self.firstPoint.x()
        dY = self.endPoint.y() - self.firstPoint.y()
        return math.sqrt((dX * dX + dY * dY) / (dX0 * dX0 + dY0 * dY0))

    def showRotationScale(self, rotation, xScale, yScale):
        center, _, _, _ = self.layer.transformParameters()
        # newRotation = rotation + originalRotation
        cornerPoints = self.layer.transformedCornerCoordinatesFromPoint(
            self.firstPoint, rotation, xScale, yScale
        )

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in cornerPoints:
            self.rubberBandExtent.addPoint(point, False)
        self.rubberBandExtent.addPoint(cornerPoints[0], True)
        self.rubberBandExtent.show()

        # Calculate the displacement of the center due to the rotation from
        # another point.
        newCenterDX = (cornerPoints[0].x() + cornerPoints[2].x()) / 2 - center.x()
        newCenterDY = (cornerPoints[0].y() + cornerPoints[2].y()) / 2 - center.y()
        self.rasterShadow.reset(self.layer)
        self.rasterShadow.setDeltaDisplacement(newCenterDX, newCenterDY, False)
        self.rasterShadow.setDeltaScale(xScale, yScale, False)
        self.rasterShadow.setDeltaRotation(rotation, True)
        self.rasterShadow.show()

        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        point0 = QgsPointXY(self.startPoint.x(), self.startPoint.y())
        point1 = QgsPointXY(self.firstPoint.x(), self.firstPoint.y())
        point2 = QgsPointXY(self.endPoint.x(), self.endPoint.y())
        self.rubberBandDisplacement.addPoint(point0, False)
        self.rubberBandDisplacement.addPoint(point1, False)
        self.rubberBandDisplacement.addPoint(point2, True)  # true to update canvas
        self.rubberBandDisplacement.show()

    def showDisplacement(self, startPoint, endPoint):
        self.rubberBandOrigin.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.rubberBandOrigin.addPoint(endPoint, True)
        self.rubberBandOrigin.show()

        self.rubberBandDisplacement.reset(QgsWkbTypes.GeometryType.LineGeometry)
        point1 = QgsPointXY(startPoint.x(), startPoint.y())
        point2 = QgsPointXY(endPoint.x(), endPoint.y())
        self.rubberBandDisplacement.addPoint(point1, False)
        self.rubberBandDisplacement.addPoint(point2, True)  # true to update canvas
        self.rubberBandDisplacement.show()

        self.rubberBandExtent.reset(QgsWkbTypes.GeometryType.LineGeometry)
        for point in self.originalCornerPoints:
            self._addDisplacementToPoint(self.rubberBandExtent, point, False)
        # for closing
        self._addDisplacementToPoint(
            self.rubberBandExtent, self.originalCornerPoints[0], True
        )
        self.rubberBandExtent.show()

        self.rasterShadow.reset(self.layer)
        self.rasterShadow.setDeltaDisplacement(
            self.endPoint.x() - self.startPoint.x(),
            self.endPoint.y() - self.startPoint.y(),
            True,
        )
        self.rasterShadow.show()

    def _addDisplacementToPoint(self, rubberBand, point, doUpdate):
        x = point.x() + self.endPoint.x() - self.startPoint.x()
        y = point.y() + self.endPoint.y() - self.startPoint.y()
        self.rubberBandExtent.addPoint(QgsPointXY(x, y), doUpdate)


class GeorefRasterByGcpMapTool(QgsMapToolEmitPoint):

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.gcpMarkers = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self.gcpMarkers.setColor(Qt.GlobalColor.blue)
        self.gcpMarkers.setIcon(
            getattr(
                getattr(QgsRubberBand, "Icon", QgsRubberBand),
                "ICON_CIRCLE",
                QgsRubberBand.ICON_CIRCLE,
            )
        )
        self.gcpMarkers.setIconSize(8)

        self.pendingMarker = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self.pendingMarker.setColor(Qt.GlobalColor.green)
        self.pendingMarker.setIcon(
            getattr(
                getattr(QgsRubberBand, "Icon", QgsRubberBand),
                "ICON_BOX",
                QgsRubberBand.ICON_BOX,
            )
        )
        self.pendingMarker.setIconSize(10)

        self.linkBand = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.linkBand.setColor(Qt.GlobalColor.green)
        self.linkBand.setWidth(2)

        self.pending_source = None
        self.reset()

    def setLayer(self, layer):
        self.layer = layer
        self.refreshGcpMarkers()

    def reset(self):
        self.pending_source = None
        self.layer = None
        self.gcpMarkers.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.pendingMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)

    def deactivate(self):
        QgsMapToolEmitPoint.deactivate(self)
        self.reset()

    def canvasPressEvent(self, e):
        if self.layer is None:
            return

        map_point = self.toMapCoordinates(e.pos())

        if e.button() == Qt.MouseButton.RightButton:
            if self.pending_source is not None:
                self.pending_source = None
                self.pendingMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
                self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
                return

            self.layer.history.append(self.layer.gcpHistoryState())
            if self.layer.removeLastGcp():
                self.refreshGcpMarkers()
                self._showStatus("Removed last ground control point.")
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.pending_source is None:
            px, py = map_to_pixel(self.layer, map_point)
            if (
                px < 0
                or py < 0
                or px > self.layer.image.width()
                or py > self.layer.image.height()
            ):
                self._showStatus(
                    "Click a point on the raster image first, then click its target location."
                )
                return

            self.pending_source = (px, py, map_point.x(), map_point.y())
            self.pendingMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self.pendingMarker.addPoint(map_point, True)
            self._showStatus(
                "Now click the target location on the map for this ground control point."
            )
            return

        px, py, _, _ = self.pending_source
        self.layer.history.append(self.layer.gcpHistoryState())
        self.layer.addGcp(px, py, map_point.x(), map_point.y())
        self.pending_source = None
        self.pendingMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.refreshGcpMarkers()
        self._showStatus(self._statusMessage())

    def canvasMoveEvent(self, e):
        if self.pending_source is None:
            return

        source_map = QgsPointXY(self.pending_source[2], self.pending_source[3])
        target_map = self.toMapCoordinates(e.pos())
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.linkBand.addPoint(source_map, False)
        self.linkBand.addPoint(target_map, True)

    def refreshGcpMarkers(self):
        self.gcpMarkers.reset(QgsWkbTypes.GeometryType.PointGeometry)
        if self.layer is None:
            return

        for _, (mx, my) in self.layer.gcps:
            self.gcpMarkers.addPoint(QgsPointXY(mx, my), False)
        if self.layer.gcps:
            self.gcpMarkers.addPoint(
                QgsPointXY(self.layer.gcps[-1][1][0], self.layer.gcps[-1][1][1]), True
            )
            self.gcpMarkers.show()

    def _statusMessage(self):
        count = len(self.layer.gcps)
        message = "Added ground control point (%d total)." % count
        if count >= 3:
            message += " Fit error: %.3f map units." % self.layer.gcpResidualMeters()
            if self.layer.gcp_preview_ready:
                if count >= 4:
                    message += " TPS preview updated (matches export)."
                else:
                    message += " Polynomial preview updated."
            else:
                message += " Building preview..."
        if count >= 4:
            message += " Export with thin-plate spline is available."
        elif count >= 3:
            message += " Add one more point for thin-plate spline export."
        return message

    def _showStatus(self, message):
        self.iface.mainWindow().statusBar().showMessage(message, 5000)


class GeorefRasterByGridGcpMapTool(QgsMapToolEmitPoint):

    PICK_PIXEL_TOLERANCE = 20

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        QgsMapToolEmitPoint.__init__(self, self.canvas)

        self.gcpMarkers = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self.gcpMarkers.setColor(Qt.GlobalColor.blue)
        self.gcpMarkers.setIcon(
            getattr(
                getattr(QgsRubberBand, "Icon", QgsRubberBand),
                "ICON_CIRCLE",
                QgsRubberBand.ICON_CIRCLE,
            )
        )
        self.gcpMarkers.setIconSize(8)

        self.selectedMarker = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self.selectedMarker.setColor(Qt.GlobalColor.green)
        self.selectedMarker.setIcon(
            getattr(
                getattr(QgsRubberBand, "Icon", QgsRubberBand),
                "ICON_BOX",
                QgsRubberBand.ICON_BOX,
            )
        )
        self.selectedMarker.setIconSize(12)

        self.linkBand = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self.linkBand.setColor(Qt.GlobalColor.green)
        self.linkBand.setWidth(2)

        self.selected_index = None
        self.reset()

    def setLayer(self, layer):
        self.layer = layer
        self.selected_index = None
        if layer is not None and layer.hasGridGcps():
            self.refreshGcpMarkers()

    def reset(self):
        self.selected_index = None
        self.layer = None
        self.gcpMarkers.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.selectedMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)

    def deactivate(self):
        try:
            self.canvas.extentsChanged.disconnect(self.refreshGcpMarkers)
        except TypeError:
            pass
        if self.layer is not None and self.layer.hasGridGcps():
            self.layer.setGridGeorefLocked(True)
        QgsMapToolEmitPoint.deactivate(self)
        self.reset()

    def activate(self):
        QgsMapToolEmitPoint.activate(self)
        if self.layer is None:
            return
        self.canvas.extentsChanged.connect(self.refreshGcpMarkers)
        if not self._ensureGrid():
            action = self.action()
            if action is not None:
                action.setChecked(False)
            self.iface.actionPan().trigger()
            return
        self.refreshGcpMarkers()
        self._showStatus(
            self._editStatusMessage()
            + " Use Move to reposition the whole grid, then turn Move off to lock."
        )

    def _ensureGrid(self):
        layer = self.layer
        if layer.hasGridGcps():
            choice, ok = QInputDialog.getItem(
                self.iface.mainWindow(),
                "Grid GCP mode",
                "Choose grid layout:",
                _grid_layout_choices(replace=True),
                0,
                False,
            )
            if not ok:
                return False
            rows = _grid_size_from_choice(choice)
            if rows is None:
                return True
        else:
            if layer.gcps:
                reply = QMessageBox.question(
                    self.iface.mainWindow(),
                    "Grid GCP mode",
                    "Existing ground control points will be replaced by the grid. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return False

            choice, ok = QInputDialog.getItem(
                self.iface.mainWindow(),
                "Grid GCP mode",
                "Choose grid layout:",
                _grid_layout_choices(),
                0,
                False,
            )
            if not ok:
                return False
            rows = _grid_size_from_choice(choice)
            if rows is None:
                return False

        cols = rows
        layer.history.append(layer.gcpHistoryState())
        layer.initializeGridGcps(rows, cols)
        return True

    def canvasPressEvent(self, e):
        if self.layer is None or not self.layer.hasGridGcps():
            return

        map_point = self.toMapCoordinates(e.pos())

        if e.button() == Qt.MouseButton.RightButton:
            self.selected_index = None
            self.selectedMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
            self._showStatus("Selection cleared. " + self._editStatusMessage())
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.selected_index is None:
            index = self._pickGridIndex(map_point)
            if index is None:
                self._showStatus(
                    "Click a blue grid point on the map, or click the raster near a grid node."
                )
                return

            self.selected_index = index
            _, (mx, my) = self.layer.gcps[index]
            self.selectedMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self.selectedMarker.addPoint(QgsPointXY(mx, my), True)
            self._showStatus(
                "Grid point %d selected. Click its corrected map location."
                % (index + 1)
            )
            return

        self.layer.history.append(self.layer.gcpHistoryState())
        self.layer.updateGcpTarget(self.selected_index, map_point.x(), map_point.y())
        self.selected_index = None
        self.selectedMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.refreshGcpMarkers()
        self._showStatus(self._editStatusMessage())

    def canvasMoveEvent(self, e):
        if self.selected_index is None:
            return

        _, (mx, my) = self.layer.gcps[self.selected_index]
        target_map = self.toMapCoordinates(e.pos())
        self.linkBand.reset(QgsWkbTypes.GeometryType.LineGeometry)
        self.linkBand.addPoint(QgsPointXY(mx, my), False)
        self.linkBand.addPoint(target_map, True)

    def refreshGcpMarkers(self):
        self.gcpMarkers.reset(QgsWkbTypes.GeometryType.PointGeometry)
        if self.layer is None or not self.layer.gcps:
            return

        for _, (mx, my) in self.layer.gcps:
            self.gcpMarkers.addPoint(QgsPointXY(mx, my), False)
        self.gcpMarkers.show()

        if self.selected_index is not None:
            _, (mx, my) = self.layer.gcps[self.selected_index]
            self.selectedMarker.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self.selectedMarker.addPoint(QgsPointXY(mx, my), True)

    def _pickGridIndex(self, map_point):
        index = nearest_gcp_index(
            self.layer.gcps,
            map_point.x(),
            map_point.y(),
            self._mapTolerance(),
        )
        if index is not None:
            return index

        px, py = map_to_pixel(self.layer, map_point)
        width = self.layer.image.width()
        height = self.layer.image.height()
        if px < 0 or py < 0 or px > width or py > height:
            return None

        return nearest_gcp_index_by_source(
            self.layer.gcps,
            px,
            py,
            self.PICK_PIXEL_TOLERANCE,
        )

    def _mapTolerance(self):
        return self.canvas.mapSettings().mapUnitsPerPixel() * 15

    def _editStatusMessage(self):
        count = len(self.layer.gcps)
        message = (
            "Grid GCP (%d x %d): click a point, then click its corrected map location. "
            "Right click clears selection."
            % (self.layer.gcp_grid_rows, self.layer.gcp_grid_cols)
        )
        if count >= 3:
            message += " Fit error: %.3f map units." % self.layer.gcpResidualMeters()
        if self.layer.gcp_preview_ready:
            if count >= 4:
                message += " TPS preview active."
            else:
                message += " Polynomial preview active."
        return message

    def _showStatus(self, message):
        self.iface.mainWindow().statusBar().showMessage(message, 8000)
