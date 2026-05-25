# About

This project is a plugin for QGIS 3 and QGIS 4 (Qt6) to perform interactive raster georeferencing. The plugin was originally made to replace a workflow where digitizers would use Google Earth to interactively georeference a raster and the tools (move, rotate, scale...) found in that software have been reimplemented. Compared to the standard raster georeferencer tool of QGIS, which needs control points and an export, this plugin allows the visualization of the result immediately, on top of the other layers of the map.

This QGIS 4 fork adds grid GCP (3x3 through 6x6), manual GCP with TPS preview/export, and NoData transparency.

# Install

## From the QGIS plugin registry

In QGIS, open the "Plugins" > "Manage and install plugin" dialog. Install the "Freehand raster georeferencer" plugin.

## Manual install (QGIS 4)

1. Copy the `FreehandRasterGeoreferencer` folder into the QGIS plugins directory, for example:
   - Windows: `%AppData%\QGIS\QGIS4\profiles\default\python\plugins\`
   - Linux: `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/`
2. Restart QGIS and enable the plugin in **Plugins > Manage and Install Plugins**.

A legacy version for QGIS 2 is in the upstream `qgis2` branch.

# Usage

플러그인을 켜면 **Raster > Freehand Raster Georeferencer** 메뉴와 전용 툴바가 나타납니다.

## 1. 래스터 추가

1. **Add raster** 로 TIFF, PNG, JPEG, BMP 등을 불러옵니다.
2. 배경 지도(참조 레이어)가 보이도록 CRS를 맞춥니다.
3. 16bit·다밴드 TIFF는 미리보기용으로 변환될 수 있습니다. 원본 품질을 유지하려면 내보낼 때 **Only export world file** 옵션을 사용하세요.

## 2. 대략 맞추기 (affine)

아래 도구로 영상을 대략 맞춘 뒤 세부 조정으로 넘어갑니다.

| 도구 | 설명 |
|------|------|
| **Move** | 영상을 드래그하여 이동 |
| **Rotate** | 회전 (Ctrl: 클릭한 점 기준) |
| **Scale** | 크기 조절 (Ctrl: X/Y 비율 유지) |
| **Adjust** | 모서리·변을 드래그해 기울기 조정 |
| **2 Points** | 래스터 위 2점 → 지도 위 2점으로 맞춤 |

**Undo** 로 직전 작업을 되돌릴 수 있습니다.

## 3. Grid GCP (넓은 영역 맞추기)

한쪽만 맞고 반대쪽이 틀어지는 경우, 격자 GCP를 사용합니다.

1. **Georeference with grid ground control points** 선택
2. 격자 크기 선택: **3×3 ~ 6×6**
3. 파란 격자 점을 클릭한 뒤, 지도에서 올바른 위치를 클릭해 수정
4. 4점 이상이면 **TPS 미리보기**가 자동으로 표시됩니다
5. **Move** 로 격자와 영상을 함께 이동할 수 있습니다. Move를 끄면 **격자가 잠겨** rotate/scale 등 affine 도구가 비활성화됩니다
6. 우클릭: 선택 해제

## 4. 수동 GCP

격자 대신 점을 직접 찍을 때:

1. **Georeference with multiple ground control points** 선택
2. 좌클릭: 래스터 위 점 → 지도 위 목표 위치 (순서대로 2번 클릭)
3. 우클릭: 마지막 GCP 삭제
4. 3점 이상: 다항식 미리보기, 4점 이상: TPS 미리보기

## 5. NoData 투명 (선택)

레이어 **Properties** 에서 NoData 값을 지정하고 **Hide NoData** 를 켜면 해당 픽셀이 투명하게 보입니다.

## 6. 내보내기

1. 플러그인 레이어를 선택한 상태에서 **Export raster with world file** 클릭
2. 옵션 선택:

| 옵션 | 용도 |
|------|------|
| **Put rotation in world file** (기본) | 원본 픽셀 유지, GeoTIFF/world file에 좌표·회전 기록 |
| **Only export world file** | 원본 파일은 그대로, `.tfw` 등 좌표 파일만 생성 |
| **Put rotation in world file** 해제 | GDAL warp로 회전을 픽셀에 반영 (16bit TIFF 등) |

16bit·다밴드 원본을 그대로 두고 좌표만 붙이려면 **Only export world file** 을 체크하고 **원본 TIFF 경로**를 지정하세요.

내보낸 파일은 QGIS **Add Raster Layer** 로 일반 래스터처럼 불러올 수 있습니다.

# Documentation

Upstream documentation: http://gvellut.github.io/FreehandRasterGeoreferencer/

# Limitations

- The plugin uses Qt to read and manipulate a raster and is therefore limited to the formats supported by that library. Very large rasters should be avoided. BMP, JPEG, PNG, and TIFF can be loaded directly.
- Affine tools (move, rotate, scale) do not model rubber-sheeting. Use GCP/TPS for that.
- There is limited support for changing CRS: if the map CRS changes, georeferencing may need to be adjusted again.
- The plugin layer is for interactive editing and preview. Export produces a normal georeferenced raster for use elsewhere.
- Some TIFF rasters (non-Byte, unusual band count) are converted for display. Prefer **Only export world file** with the original file when quality matters.

# Issues

Report issues to the maintainer of your installed copy, or at the upstream tracker: https://github.com/gvellut/FreehandRasterGeoreferencer/issues

---

# Authors

**Maintainer (QGIS 4 fork, v0.9.1+)**  
Taeyeon Won (원태연) — teadone@konkuk.ac.kr, teadone@naver.com

**Original author**  
Guilhem Vellut — g@vellut.com  
Project: http://gvellut.github.io/FreehandRasterGeoreferencer/

Contributors to the original plugin include Takayuki Mizutani, Sebastien Barre, and others (see changelog in `metadata.txt`).

# License

This program is free software; you can redistribute it and/or modify it under the terms of the **GNU General Public License version 2** (or later). See [LICENSE.txt](LICENSE.txt).

- Copyright (C) 2018 **Guilhem Vellut** — original FreehandRasterGeoreferencer
- Copyright (C) 2026 **Taeyeon Won (원태연)** — QGIS 4 port, grid GCP, TPS preview/export, NoData display
