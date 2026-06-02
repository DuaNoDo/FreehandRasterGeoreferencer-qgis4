import os
import re
import xml.etree.ElementTree as ET
from osgeo import gdal

try:
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
        QLabel, QLineEdit, QFileDialog, QTextEdit,
        QProgressBar, QCheckBox, QGroupBox
    )
    from PyQt6.QtCore import QThread, pyqtSignal
    from PyQt6.QtGui import QFont
except ImportError:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
        QLabel, QLineEdit, QFileDialog, QTextEdit,
        QProgressBar, QCheckBox, QGroupBox
    )
    from PyQt5.QtCore import QThread, pyqtSignal
    from PyQt5.QtGui import QFont

from qgis.core import QgsProject, QgsRasterLayer


class CompositeWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(int, int)

    def __init__(self, root_dir, output_dir, load_to_qgis):
        super().__init__()
        self.root_dir = root_dir
        self.output_dir = output_dir
        self.load_to_qgis = load_to_qgis

    def run(self):
        groups = self.find_groups()
        total = len(groups)
        success, skipped = 0, 0
        self.log.emit(f"총 {total}개 그룹 발견\n")
        for idx, (base_key, files) in enumerate(groups.items()):
            self.progress.emit(int((idx / total) * 100) if total else 100)
            if self.process_group(base_key, files):
                success += 1
            else:
                skipped += 1
        self.progress.emit(100)
        self.finished.emit(success, skipped)

    def find_groups(self):
        groups = {}
        band_pat = re.compile(r'^(.+)_(R|G|B|N)\.tif$', re.IGNORECASE)
        aux_pat  = re.compile(r'^(.+)_Aux\.xml$',        re.IGNORECASE)
        for dirpath, _, filenames in os.walk(self.root_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                m = band_pat.match(fname)
                if m:
                    key = os.path.join(dirpath, m.group(1))
                    groups.setdefault(key, {})
                    groups[key][m.group(2).upper()] = full
                    continue
                m = aux_pat.match(fname)
                if m:
                    key = os.path.join(dirpath, m.group(1))
                    groups.setdefault(key, {})
                    groups[key]['AUX'] = full
        return groups

    def process_group(self, base_key, files):
        base_name = os.path.basename(base_key)
        src_dir   = os.path.dirname(base_key)
        out_dir   = self.output_dir if self.output_dir else src_dir
        os.makedirs(out_dir, exist_ok=True)

        missing = [b for b in ['R', 'G', 'B', 'N'] if b not in files]
        if missing:
            self.log.emit(f"⚠  스킵 [{base_name}] — 밴드 없음: {missing}")
            return False

        output_path = os.path.join(out_dir, f"{base_name}_RGBN_composite.tif")
        self.log.emit(f"▶  처리 중: {base_name}")

        try:
            ref_ds = gdal.Open(files['R'])
            cols   = ref_ds.RasterXSize
            rows   = ref_ds.RasterYSize
            geo    = ref_ds.GetGeoTransform()
            proj   = ref_ds.GetProjection()
            dtype  = ref_ds.GetRasterBand(1).DataType
            nodata = ref_ds.GetRasterBand(1).GetNoDataValue()
            ref_ds = None

            driver = gdal.GetDriverByName('GTiff')
            out_ds = driver.Create(
                output_path, cols, rows, 4, dtype,
                options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER']
            )
            out_ds.SetGeoTransform(geo)
            out_ds.SetProjection(proj)

            for i, band_key in enumerate(['R', 'G', 'B', 'N'], start=1):
                src_ds   = gdal.Open(files[band_key])
                data     = src_ds.GetRasterBand(1).ReadAsArray()
                out_band = out_ds.GetRasterBand(i)
                out_band.WriteArray(data)
                out_band.SetDescription(band_key)
                if nodata is not None:
                    out_band.SetNoDataValue(nodata)
                out_band.FlushCache()
                src_ds = None

            aux_meta = self.parse_aux(files.get('AUX'))
            if aux_meta:
                out_ds.SetMetadata(aux_meta)

            out_ds.FlushCache()
            out_ds = None
            self.log.emit(f"   저장완료: {output_path}")

            if self.load_to_qgis:
                layer = QgsRasterLayer(output_path, f"{base_name}_RGBN")
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log.emit("   QGIS 레이어 추가됨")
            return True

        except Exception as e:
            self.log.emit(f"   오류: {e}")
            return False

    def parse_aux(self, aux_path):
        if not aux_path or not os.path.exists(aux_path):
            return {}
        try:
            root = ET.parse(aux_path).getroot()
            tags = {}
            general = root.find('General')
            if general is not None:
                for child in general:
                    if child.text and child.text.strip():
                        tags[f"AUX_{child.tag}"] = child.text.strip()
            return tags
        except Exception:
            return {}


class RGBNCompositeDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RGBN Composite Builder")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.worker = None
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)

        title = QLabel("RGBN Composite Builder")
        font = QFont("Arial", 14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        desc = QLabel("지정 디렉터리 하위의 _R/_G/_B/_N.tif 파일을 자동 탐색해\n_RGBN_composite.tif 로 합성합니다.")
        layout.addWidget(desc)

        input_group  = QGroupBox("입력 디렉터리")
        input_layout = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("탐색할 루트 디렉터리 선택...")
        browse_input = QPushButton("찾아보기")
        browse_input.clicked.connect(self.browse_input)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(browse_input)
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        output_group  = QGroupBox("출력 디렉터리")
        output_layout = QVBoxLayout()
        self.same_dir_cb = QCheckBox("원본과 같은 디렉터리에 저장")
        self.same_dir_cb.setChecked(True)
        self.same_dir_cb.toggled.connect(self.toggle_output)
        output_layout.addWidget(self.same_dir_cb)
        out_path_layout = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("출력 디렉터리 선택...")
        self.output_edit.setEnabled(False)
        self.browse_output_btn = QPushButton("찾아보기")
        self.browse_output_btn.clicked.connect(self.browse_output)
        self.browse_output_btn.setEnabled(False)
        out_path_layout.addWidget(self.output_edit)
        out_path_layout.addWidget(self.browse_output_btn)
        output_layout.addLayout(out_path_layout)
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        self.load_cb = QCheckBox("완료 후 QGIS에 레이어 자동 추가")
        self.load_cb.setChecked(True)
        layout.addWidget(self.load_cb)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Courier New", 9))
        self.log_edit.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        self.log_edit.setMinimumHeight(160)
        layout.addWidget(self.log_edit)

        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("실행")
        self.run_btn.setStyleSheet("background:#2d7d46; color:white; font-weight:bold; padding:6px;")
        self.run_btn.clicked.connect(self.run_composite)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def browse_input(self):
        d = QFileDialog.getExistingDirectory(self, "입력 디렉터리 선택")
        if d:
            self.input_edit.setText(d)

    def browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "출력 디렉터리 선택")
        if d:
            self.output_edit.setText(d)

    def toggle_output(self, checked):
        self.output_edit.setEnabled(not checked)
        self.browse_output_btn.setEnabled(not checked)

    def append_log(self, msg):
        self.log_edit.append(msg)

    def run_composite(self):
        root_dir = self.input_edit.text().strip()
        if not root_dir or not os.path.isdir(root_dir):
            self.append_log("유효한 입력 디렉터리를 선택해주세요.")
            return

        output_dir = None
        if not self.same_dir_cb.isChecked():
            output_dir = self.output_edit.text().strip() or None

        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_edit.clear()
        self.append_log(f"탐색 시작: {root_dir}\n")

        self.worker = CompositeWorker(root_dir, output_dir, self.load_cb.isChecked())
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, success, skipped):
        self.append_log(f"\n완료!  성공: {success}개 / 스킵: {skipped}개")
        self.run_btn.setEnabled(True)


dlg = RGBNCompositeDialog()
dlg.show()