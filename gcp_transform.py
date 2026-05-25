import json
import math

import numpy as np
from qgis.PyQt.QtGui import QTransform
from qgis.core import QgsPointXY


def _affine_device_transform(width, height, pixel_to_device_fn):
    if width <= 0 or height <= 0:
        return None

    sample_points = [
        (0, 0),
        (width, 0),
        (0, height),
        (width, height),
    ]
    matrix = np.array([[px, py, 1.0] for px, py in sample_points], dtype=float)
    device_x = np.array([pixel_to_device_fn(px, py)[0] for px, py in sample_points])
    device_y = np.array([pixel_to_device_fn(px, py)[1] for px, py in sample_points])
    coeff_x = np.linalg.lstsq(matrix, device_x, rcond=None)[0]
    coeff_y = np.linalg.lstsq(matrix, device_y, rcond=None)[0]
    return QTransform(
        coeff_x[0],
        coeff_y[0],
        coeff_x[1],
        coeff_y[1],
        coeff_x[2],
        coeff_y[2],
    )


def device_transform_for_geotransform(width, height, geotransform, map2pixel):
    def pixel_to_device(column, row):
        map_x = geotransform[0] + column * geotransform[1] + row * geotransform[2]
        map_y = geotransform[3] + column * geotransform[4] + row * geotransform[5]
        device_point = map2pixel.transform(QgsPointXY(map_x, map_y))
        return device_point.x(), device_point.y()

    return _affine_device_transform(width, height, pixel_to_device)


def device_transform_for_pixel_map(width, height, pixel_to_map, map2pixel):
    def pixel_to_device(px, py):
        mx, my = pixel_to_map.map(px, py)
        device_point = map2pixel.transform(QgsPointXY(mx, my))
        return device_point.x(), device_point.y()

    return _affine_device_transform(width, height, pixel_to_device)


def gcps_from_json(raw):
    if not raw:
        return []
    data = json.loads(raw)
    return [
        ((item["px"], item["py"]), (item["mx"], item["my"]))
        for item in data
    ]


def gcps_to_json(gcps):
    return json.dumps(
        [
            {"px": px, "py": py, "mx": mx, "my": my}
            for (px, py), (mx, my) in gcps
        ]
    )


def fit_qtransform_from_gcps(gcps):
    if len(gcps) < 3:
        return None

    matrix = np.array([[px, py, 1.0] for (px, py), _ in gcps], dtype=float)
    map_x = np.array([mx for _, (mx, my) in gcps], dtype=float)
    map_y = np.array([my for _, (mx, my) in gcps], dtype=float)
    coeff_x = np.linalg.lstsq(matrix, map_x, rcond=None)[0]
    coeff_y = np.linalg.lstsq(matrix, map_y, rcond=None)[0]
    return QTransform(
        coeff_x[0],
        coeff_y[0],
        coeff_x[1],
        coeff_y[1],
        coeff_x[2],
        coeff_y[2],
    )


def gcp_rms_error(gcps, pixel_to_map):
    if not gcps or pixel_to_map is None:
        return 0.0

    total = 0.0
    for (px, py), (mx, my) in gcps:
        predicted_x, predicted_y = pixel_to_map.map(px, py)
        dx = predicted_x - mx
        dy = predicted_y - my
        total += dx * dx + dy * dy
    return math.sqrt(total / len(gcps))


def map_to_pixel_affine(layer, map_point):
    dx = map_point.x() - layer.center.x()
    dy = map_point.y() - layer.center.y()
    rad = layer.rotation * math.pi / 180.0
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    rx = dx * cos_r - dy * sin_r
    ry = dx * sin_r + dy * cos_r
    px = rx / layer.xScale + layer.image.width() / 2.0
    py = -ry / layer.yScale + layer.image.height() / 2.0
    return px, py


def map_to_pixel(layer, map_point):
    if layer.gcp_display_transform is not None and len(layer.gcps) >= 3:
        inverse, ok = layer.gcp_display_transform.inverted()
        if ok:
            return inverse.map(map_point.x(), map_point.y())

    return map_to_pixel_affine(layer, map_point)


def pixel_to_map_affine(layer, px, py):
    dx = (px - layer.image.width() / 2.0) * layer.xScale
    dy = -(py - layer.image.height() / 2.0) * layer.yScale
    rad = -layer.rotation * math.pi / 180.0
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    mx = dx * cos_r - dy * sin_r + layer.center.x()
    my = dx * sin_r + dy * cos_r + layer.center.y()
    return QgsPointXY(mx, my)


def generate_grid_gcps(layer, rows, cols):
    width = layer.image.width()
    height = layer.image.height()
    gcps = []
    for row in range(rows):
        py = row * height / (rows - 1) if rows > 1 else height / 2.0
        for col in range(cols):
            px = col * width / (cols - 1) if cols > 1 else width / 2.0
            map_point = pixel_to_map_affine(layer, px, py)
            gcps.append(((px, py), (map_point.x(), map_point.y())))
    return gcps


def nearest_gcp_index(gcps, mx, my, tolerance):
    if not gcps or tolerance <= 0:
        return None

    tolerance_sq = tolerance * tolerance
    best_index = None
    best_dist_sq = tolerance_sq
    for index, (_, (gmx, gmy)) in enumerate(gcps):
        dx = gmx - mx
        dy = gmy - my
        dist_sq = dx * dx + dy * dy
        if dist_sq <= best_dist_sq:
            best_dist_sq = dist_sq
            best_index = index
    return best_index


def nearest_gcp_index_by_source(gcps, px, py, tolerance):
    if not gcps or tolerance <= 0:
        return None

    tolerance_sq = tolerance * tolerance
    best_index = None
    best_dist_sq = tolerance_sq
    for index, ((gpx, gpy), _) in enumerate(gcps):
        dx = gpx - px
        dy = gpy - py
        dist_sq = dx * dx + dy * dy
        if dist_sq <= best_dist_sq:
            best_dist_sq = dist_sq
            best_index = index
    return best_index


def pixel_to_map_point(layer, px, py):
    if layer.gcp_display_transform is not None and len(layer.gcps) >= 3:
        mx, my = layer.gcp_display_transform.map(px, py)
        return QgsPointXY(mx, my)

    return pixel_to_map_affine(layer, px, py)


def transformed_image_corners(layer):
    if getattr(layer, "gcp_preview_ready", False) and layer.gcp_preview_geotransform is not None:
        return corners_from_geotransform(
            layer.gcp_preview_geotransform,
            layer.gcp_preview_image.width(),
            layer.gcp_preview_image.height(),
        )

    width = layer.image.width()
    height = layer.image.height()
    corners = [
        pixel_to_map_point(layer, 0, 0),
        pixel_to_map_point(layer, width, 0),
        pixel_to_map_point(layer, width, height),
        pixel_to_map_point(layer, 0, height),
    ]
    return tuple(corners)


def corners_from_geotransform(geotransform, width, height):
    def map_point(column, row):
        x = geotransform[0] + column * geotransform[1] + row * geotransform[2]
        y = geotransform[3] + column * geotransform[4] + row * geotransform[5]
        return QgsPointXY(x, y)

    return (
        map_point(0, 0),
        map_point(width, 0),
        map_point(width, height),
        map_point(0, height),
    )
