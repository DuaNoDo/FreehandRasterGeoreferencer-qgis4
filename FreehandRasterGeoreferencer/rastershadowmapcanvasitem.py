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

from qgis.PyQt.QtCore import QPointF, QRectF
from qgis.PyQt.QtGui import QPainter
from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvasItem


class RasterShadowMapCanvasItem(QgsMapCanvasItem):
    def __init__(self, canvas):
        QgsMapCanvasItem.__init__(self, canvas)

        self.canvas = canvas
        self.reset()

    def reset(self, layer=None):
        self.layer = layer
        self.setVisible(False)

        self.dx = 0
        self.dy = 0
        self.drotation = 0
        self.fxscale = 1
        self.fyscale = 1

    def setDeltaDisplacement(self, dx, dy, doUpdate):
        self.dx = dx
        self.dy = dy
        if doUpdate:
            self.setVisible(self.layer is None)
            self.updateRect()
            self.update()

    def setDeltaRotation(self, rotation, doUpdate):
        self.drotation = rotation
        if doUpdate:
            self.updateRect()
            self.update()

    def setDeltaRotationFromPoint(self, rotation, startPoint, doUpdate):
        # Rotation around a point other than center of raster
        self.drotation = rotation
        if doUpdate:
            self.updateRectFromPoint(startPoint)
            self.update()

    def setDeltaScale(self, xscale, yscale, doUpdate):
        self.fxscale = xscale
        self.fyscale = yscale
        if doUpdate:
            self.updateRect()
            self.update()

    def updateRect(self):
        topLeft, topRight, bottomRight, bottomLeft = self.cornerCoordinates()

        left = min(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        right = max(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        top = max(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())
        bottom = min(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())

        self.setRect(QgsRectangle(left, bottom, right, top))

    def updateRectFromPoint(self, startPoint):
        topLeft, topRight, bottomRight, bottomLeft = self.cornerCoordinatesFromPoint(
            startPoint
        )

        left = min(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        right = max(topLeft.x(), topRight.x(), bottomRight.x(), bottomLeft.x())
        top = max(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())
        bottom = min(topLeft.y(), topRight.y(), bottomRight.y(), bottomLeft.y())

        self.setRect(QgsRectangle(left, bottom, right, top))

    def cornerCoordinates(self):
        center = QgsPointXY(
            self.layer.center.x() + self.dx, self.layer.center.y() + self.dy
        )
        return self.layer.transformedCornerCoordinates(
            center,
            self.layer.rotation + self.drotation,
            self.layer.xScale * self.fxscale,
            self.layer.yScale * self.fyscale,
        )

    def cornerCoordinatesFromPoint(self, startPoint):
        return self.layer.transformedCornerCoordinatesFromPoint(
            startPoint, self.drotation, 1, 1
        )

    def paint(self, painter, options, widget):
        painter.save()
        self.prepareStyle(painter)
        self.drawRaster(painter)
        painter.restore()

    def drawRaster(self, painter):
        mapUPerPixel = self.canvas.mapUnitsPerPixel()

        scaleX = self.layer.xScale * self.fxscale / mapUPerPixel
        scaleY = self.layer.yScale * self.fyscale / mapUPerPixel

        rect = QRectF(
            QPointF(-self.layer.image.width() / 2.0, -self.layer.image.height() / 2.0),
            QPointF(self.layer.image.width() / 2.0, self.layer.image.height() / 2.0),
        )
        targetRect = self.boundingRect()

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # draw the image on the canvas item rectangle
        # center displacement already taken into account in canvas
        # item rectangle so no update
        painter.translate(targetRect.center())
        painter.rotate(self.layer.rotation + self.drotation)
        painter.scale(scaleX, scaleY)
        painter.drawImage(rect, self.layer.image)

    def prepareStyle(self, painter):
        painter.setOpacity(min(0.5, 1 - self.layer.transparency / 100.0))
