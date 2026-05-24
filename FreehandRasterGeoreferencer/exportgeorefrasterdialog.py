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

from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QMessageBox

from .ui_exportgeorefrasterdialog import Ui_ExportGeorefRasterDialog


class ExportGeorefRasterDialog(QDialog, Ui_ExportGeorefRasterDialog):
    def __init__(self):
        QDialog.__init__(self)
        self.setupUi(self)

        self.pushButtonBrowse.clicked.connect(self.showBrowserDialog)
        self.checkBoxOnlyWorldFile.stateChanged.connect(self.setupOnlyWorldFile)

    def clear(self, layer):
        self.checkBoxRotationMode.setChecked(False)
        self.checkBoxRotationMode.setEnabled(True)

        if getattr(layer, "imageTransformedForDisplay", False):
            self.checkBoxOnlyWorldFile.setChecked(False)
            self.checkBoxRotationMode.setChecked(True)
            defaultPath, _ = os.path.splitext(layer.getAbsoluteFilepath())
            self.defaultPath = defaultPath + "_georeferenced.tif"
            self.lineEditImagePath.setText(self.defaultPath)
        elif layer.hasGcpExport():
            self.checkBoxOnlyWorldFile.setChecked(False)
            self.checkBoxRotationMode.setChecked(True)
            self.checkBoxRotationMode.setEnabled(False)
            defaultPath, _ = os.path.splitext(layer.getAbsoluteFilepath())
            self.defaultPath = defaultPath + "_gcp_georeferenced.tif"
            self.lineEditImagePath.setText(self.defaultPath)
        else:
            self.checkBoxOnlyWorldFile.setChecked(False)
            self.lineEditImagePath.setText("")
            defaultPath, _ = os.path.splitext(layer.filepath)
            self.defaultPath = defaultPath + "_georeferenced.png"

    def setupOnlyWorldFile(self):
        if self.checkBoxOnlyWorldFile.isChecked():
            self._originalCheckBoxRotationModeChecked = (
                self.checkBoxRotationMode.isChecked()
            )
            self.checkBoxRotationMode.setChecked(True)
            self.checkBoxRotationMode.setEnabled(False)

        else:
            self.checkBoxRotationMode.setChecked(
                self._originalCheckBoxRotationModeChecked
            )
            self.checkBoxRotationMode.setEnabled(True)

    def showBrowserDialog(self):
        if self.lineEditImagePath.text():
            filepathDialog = self.lineEditImagePath.text()
        else:
            filepathDialog = self.defaultPath

        if not self.checkBoxOnlyWorldFile.isChecked():
            filepath, _ = QFileDialog.getSaveFileName(
                None,
                "Export georeferenced raster",
                filepathDialog,
                "Images (*.png *.bmp *.jpg *.tif *.tiff)",
            )
        else:
            filepath, _ = QFileDialog.getOpenFileName(
                None,
                "Export world file for raster",
                filepathDialog,
                "Images (*.png *.bmp *.jpg *.tif *.tiff)",
            )

        if filepath:
            self.lineEditImagePath.setText(filepath)

    def accept(self):
        result, message, details = self.validate()
        if result:
            self.done(QDialog.DialogCode.Accepted)
        else:
            msgBox = QMessageBox()
            msgBox.setWindowTitle("Error")
            msgBox.setText(message)
            msgBox.setDetailedText(details)
            msgBox.setStandardButtons(QMessageBox.StandardButton.Ok)
            msgBox.exec()

    def validate(self):
        result = True
        message = ""
        details = ""

        self.isPutRotationInWorldFile = self.checkBoxRotationMode.isChecked()
        self.isExportOnlyWorldFile = self.checkBoxOnlyWorldFile.isChecked()

        self.imagePath = self.lineEditImagePath.text()
        if not self.imagePath:
            result = False
            details += "A file must be selected"

        if result:
            _, extension = os.path.splitext(self.imagePath)
            extension = extension.lower()
            if extension not in [".jpg", ".bmp", ".png", ".tif", ".tiff"]:
                result = False
                if len(details) > 0:
                    details += "\n"
                details += "The file must be an image"

        if not result:
            message = "There were errors in the form"

        return result, message, details
