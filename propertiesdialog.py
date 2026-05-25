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

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import gdal_utils
from .ui_propertiesdialog import Ui_Dialog


class PropertiesDialog(QDialog, Ui_Dialog):
    def __init__(self, layer):
        QDialog.__init__(self)
        self.setupUi(self)
        self.setWindowTitle("%s - %s" % (self.tr("Layer Properties"), layer.name()))

        self.layer = layer
        self._loading = True
        self.horizontalSlider_Transparency.valueChanged.connect(self.sliderChanged)
        self.spinBox_Transparency.valueChanged.connect(self.spinBoxChanged)

        self._buildNodataControls()
        layer.refreshFileNodataValue()
        self.textEdit_Properties.setText(layer.layerPropertiesText())
        self.spinBox_Transparency.setValue(layer.transparency)
        self._loadNodataControls()
        self._loading = False

    def _buildNodataControls(self):
        container = QWidget(self.groupBox_Style)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.checkBox_UseNodata = QCheckBox(self.tr("Use NoData value"), container)
        layout.addWidget(self.checkBox_UseNodata)

        value_row = QHBoxLayout()
        value_row.addWidget(QLabel(self.tr("NoData value"), container))
        self.spinBox_Nodata = QDoubleSpinBox(container)
        self.spinBox_Nodata.setDecimals(6)
        self.spinBox_Nodata.setMinimum(-999999999999.0)
        self.spinBox_Nodata.setMaximum(999999999999.0)
        self.spinBox_Nodata.setEnabled(False)
        value_row.addWidget(self.spinBox_Nodata)
        layout.addLayout(value_row)

        self.checkBox_HideNodata = QCheckBox(
            self.tr("Hide NoData (transparent)"), container
        )
        self.checkBox_HideNodata.setEnabled(False)
        layout.addWidget(self.checkBox_HideNodata)

        if self.layer.file_nodata_value is not None:
            file_label = QLabel(
                self.tr("File NoData: %s")
                % gdal_utils.nodata_value_to_string(self.layer.file_nodata_value),
                container,
            )
            file_label.setWordWrap(True)
            layout.addWidget(file_label)

        self.verticalLayout.addWidget(container)

        self.checkBox_UseNodata.toggled.connect(self._useNodataToggled)
        self.spinBox_Nodata.valueChanged.connect(self._nodataValueChanged)
        self.checkBox_HideNodata.toggled.connect(self._nodataTransparentChanged)

    def _loadNodataControls(self):
        use_nodata = self.layer.nodata_use
        nodata_value = self.layer.nodata_value
        if nodata_value is None and self.layer.file_nodata_value is not None:
            nodata_value = self.layer.file_nodata_value
            use_nodata = True

        self.checkBox_UseNodata.setChecked(use_nodata)
        self.checkBox_HideNodata.setChecked(self.layer.nodata_transparent)
        self._setNodataControlsEnabled(use_nodata)
        if nodata_value is not None and not (
            isinstance(nodata_value, float) and math.isnan(nodata_value)
        ):
            self.spinBox_Nodata.setValue(float(nodata_value))
        elif isinstance(nodata_value, float) and math.isnan(nodata_value):
            self.spinBox_Nodata.setSpecialValueText("nan")
            self.spinBox_Nodata.setValue(self.spinBox_Nodata.minimum())

    def _setNodataControlsEnabled(self, enabled):
        self.spinBox_Nodata.setEnabled(enabled)
        self.checkBox_HideNodata.setEnabled(enabled)

    def _useNodataToggled(self, checked):
        self._setNodataControlsEnabled(checked)
        if self._loading:
            return
        if checked and self.spinBox_Nodata.specialValueText() != "nan":
            value = self.spinBox_Nodata.value()
        elif checked and self.layer.file_nodata_value is not None:
            value = self.layer.file_nodata_value
        else:
            value = None
        self.layer.nodataSettingsChanged(
            checked, value, self.checkBox_HideNodata.isChecked()
        )
        self.textEdit_Properties.setText(self.layer.layerPropertiesText())

    def _nodataValueChanged(self, value):
        if self._loading or not self.checkBox_UseNodata.isChecked():
            return
        self.layer.nodataSettingsChanged(
            True, value, self.checkBox_HideNodata.isChecked()
        )
        self.textEdit_Properties.setText(self.layer.layerPropertiesText())

    def _nodataTransparentChanged(self, checked):
        if self._loading or not self.checkBox_UseNodata.isChecked():
            return
        value = self.spinBox_Nodata.value()
        if self.spinBox_Nodata.specialValueText() == "nan":
            value = float("nan")
        self.layer.nodataSettingsChanged(True, value, checked)
        self.textEdit_Properties.setText(self.layer.layerPropertiesText())

    def sliderChanged(self, val):
        s = self.spinBox_Transparency
        s.blockSignals(True)
        s.setValue(val)
        s.blockSignals(False)

    def spinBoxChanged(self, val):
        s = self.horizontalSlider_Transparency
        s.blockSignals(True)
        s.setValue(val)
        s.blockSignals(False)
