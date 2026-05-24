import math
import os
import uuid

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtGui import QImage


def format(filepath):
    dataset = gdal.Open(filepath, gdal.GA_ReadOnly)
    cols = dataset.RasterXSize
    rows = dataset.RasterYSize
    bands = dataset.RasterCount
    if bands == 0:
        return None
    band = dataset.GetRasterBand(1)
    bandtype = gdal.GetDataTypeName(band.DataType)

    return bands, bandtype, cols, rows


def pixels(filepath):
    dataset = gdal.Open(filepath, gdal.GA_ReadOnly)
    cols = dataset.RasterXSize
    rows = dataset.RasterYSize
    data = dataset.ReadAsArray(0, 0, cols, rows)
    if len(data.shape) == 2:
        # monoband
        data = data.reshape((1, *data.shape))
    return data


def read_nodata_value(filepath):
    dataset = gdal.Open(filepath, gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        return None

    value = dataset.GetRasterBand(1).GetNoDataValue()
    dataset = None
    return value


def nodata_value_from_string(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, str) and raw.lower() == "nan":
        return float("nan")
    return float(raw)


def nodata_value_to_string(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return str(value)


def _nodata_mask(array, nodata_value):
    if nodata_value is None:
        return None
    if isinstance(nodata_value, float) and math.isnan(nodata_value):
        return np.isnan(array)
    if np.issubdtype(array.dtype, np.floating):
        return np.isclose(array, nodata_value, rtol=1e-5, atol=1e-8, equal_nan=False)
    return array == nodata_value


def _to_byte_with_mask(data, mask=None):
    if mask is not None and np.any(mask):
        valid = data[~mask]
        if valid.size == 0:
            return np.zeros_like(data, dtype=np.uint8)
        min_ = np.min(valid)
        max_ = np.max(valid)
    else:
        min_ = np.min(data)
        max_ = np.max(data)

    if max_ == min_:
        return np.zeros_like(data, dtype=np.uint8)

    scaled = 255.0 * (data - min_) / (max_ - min_)
    return scaled.astype(np.uint8)


def load_display_image(filepath, nodata_value=None, nodata_transparent=False):
    dataset = gdal.Open(filepath, gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        return None, False

    width = dataset.RasterXSize
    height = dataset.RasterYSize
    band_count = dataset.RasterCount
    first_band = dataset.GetRasterBand(1)
    first_data = first_band.ReadAsArray()
    mask = _nodata_mask(first_data, nodata_value) if nodata_transparent else None
    is_byte = gdal.GetDataTypeName(first_band.DataType) == "Byte"
    transformed = not is_byte or band_count not in (1, 3) or mask is not None

    if band_count >= 3:
        channels = []
        for index in range(3):
            data = dataset.GetRasterBand(index + 1).ReadAsArray()
            if not is_byte:
                data = _to_byte_with_mask(data, mask)
            else:
                data = data.astype(np.uint8)
            channels.append(data)
        rgb = np.stack(channels, axis=-1)
        rgb = np.ascontiguousarray(rgb)
        if mask is not None:
            alpha = np.where(mask, 0, 255).astype(np.uint8)
            argb = np.dstack([rgb, alpha])
            argb = np.ascontiguousarray(argb)
            image = QImage(
                argb.data,
                width,
                height,
                4 * width,
                QImage.Format.Format_ARGB32,
            ).copy()
        else:
            image = QImage(
                rgb.data,
                width,
                height,
                3 * width,
                QImage.Format.Format_RGB888,
            ).copy()
        dataset = None
        return image, transformed

    data = first_data
    if not is_byte:
        data = _to_byte_with_mask(data, mask)
    else:
        data = data.astype(np.uint8)
    data = np.ascontiguousarray(data)
    if mask is not None:
        alpha = np.where(mask, 0, 255).astype(np.uint8)
        argb = np.dstack(
            [
                data,
                data,
                data,
                alpha,
            ]
        )
        argb = np.ascontiguousarray(argb)
        image = QImage(
            argb.data,
            width,
            height,
            4 * width,
            QImage.Format.Format_ARGB32,
        ).copy()
    else:
        image = QImage(
            data.data,
            width,
            height,
            width,
            QImage.Format.Format_Grayscale8,
        ).copy()
    dataset = None
    return image, transformed


def to_byte(data):
    min_ = np.min(data)
    max_ = np.max(data)
    data = 255.0 * (data - min_) / (max_ - min_)
    data = data.astype(np.uint8)
    return data


def open_dataset(filepath):
    return gdal.Open(filepath, gdal.GA_ReadOnly)


def _gcps_to_gdal(gcps):
    gdal_gcps = []
    for (px, py), (mx, my) in gcps:
        gdal_gcps.append(gdal.GCP(float(mx), float(my), 0.0, float(px), float(py)))
    return gdal_gcps


def _warp_kwargs_for_gcps(gcps, use_tps=None):
    if use_tps is None:
        use_tps = len(gcps) >= 4
    warp_kwargs = {}
    if use_tps and len(gcps) >= 4:
        warp_kwargs["tps"] = True
    elif len(gcps) >= 3:
        warp_kwargs["polynomialOrder"] = 2 if len(gcps) >= 6 else 1
    return warp_kwargs


def _open_source_with_gcps(source_path, gcps, crs_wkt):
    src = gdal.Open(source_path, gdal.GA_ReadOnly)
    if src is None:
        raise RuntimeError("Cannot open source raster: %s" % source_path)

    mem_driver = gdal.GetDriverByName("MEM")
    temp = mem_driver.CreateCopy("", src)
    temp.SetGCPs(_gcps_to_gdal(gcps), crs_wkt or "")
    src = None
    return temp


def warp_with_gcps(source_path, gcps, crs_wkt, dest_path, use_tps=None):
    """Warp a raster with GCPs to a dataset file path (use /vsimem/ for memory)."""
    temp = _open_source_with_gcps(source_path, gcps, crs_wkt)
    warp_kwargs = _warp_kwargs_for_gcps(gcps, use_tps=use_tps)
    if not dest_path.startswith("/vsimem/"):
        warp_kwargs["creationOptions"] = ["COMPRESS=LZW"]

    result = gdal.Warp(dest_path, temp, options=gdal.WarpOptions(**warp_kwargs))
    temp = None
    if result is None:
        raise RuntimeError("GDAL GCP warp failed.")
    return result


def _unlink_vsimem(path):
    if path and path.startswith("/vsimem/"):
        try:
            gdal.Unlink(path)
        except Exception:
            pass


def _scale_geotransform(geotransform, width, height, buf_width, buf_height):
    gt = list(geotransform)
    if width != buf_width:
        gt[1] = gt[1] * width / float(buf_width)
        gt[2] = gt[2] * width / float(buf_width)
    if height != buf_height:
        gt[4] = gt[4] * height / float(buf_height)
        gt[5] = gt[5] * height / float(buf_height)
    return tuple(gt)


def dataset_to_preview_qimage(
    dataset, max_dimension=2048, nodata_value=None, nodata_transparent=False
):
    """Convert a GDAL dataset to a QImage, optionally downsampled for preview."""
    width = dataset.RasterXSize
    height = dataset.RasterYSize
    scale = min(1.0, max_dimension / float(max(width, height)))
    buf_width = max(1, int(round(width * scale)))
    buf_height = max(1, int(round(height * scale)))
    geotransform = _scale_geotransform(
        dataset.GetGeoTransform(), width, height, buf_width, buf_height
    )

    band_count = dataset.RasterCount
    if band_count == 0:
        return None, geotransform

    first_band = dataset.GetRasterBand(1)
    first_data = first_band.ReadAsArray(
        0, 0, width, height, buf_width, buf_height
    )
    mask = _nodata_mask(first_data, nodata_value) if nodata_transparent else None
    is_byte = gdal.GetDataTypeName(first_band.DataType) == "Byte"

    if band_count >= 3:
        channels = []
        for index in range(3):
            data = dataset.GetRasterBand(index + 1).ReadAsArray(
                0, 0, width, height, buf_width, buf_height
            )
            if not is_byte:
                data = _to_byte_with_mask(data, mask)
            else:
                data = data.astype(np.uint8)
            channels.append(data)
        rgb = np.stack(channels, axis=-1)
        rgb = np.ascontiguousarray(rgb)
        if mask is not None:
            alpha = np.where(mask, 0, 255).astype(np.uint8)
            argb = np.dstack([rgb, alpha])
            argb = np.ascontiguousarray(argb)
            image = QImage(
                argb.data,
                buf_width,
                buf_height,
                4 * buf_width,
                QImage.Format.Format_ARGB32,
            ).copy()
        else:
            image = QImage(
                rgb.data,
                buf_width,
                buf_height,
                3 * buf_width,
                QImage.Format.Format_RGB888,
            ).copy()
        return image, geotransform

    data = first_data
    if not is_byte:
        data = _to_byte_with_mask(data, mask)
    else:
        data = data.astype(np.uint8)
    data = np.ascontiguousarray(data)
    if mask is not None:
        alpha = np.where(mask, 0, 255).astype(np.uint8)
        argb = np.dstack([data, data, data, alpha])
        argb = np.ascontiguousarray(argb)
        image = QImage(
            argb.data,
            buf_width,
            buf_height,
            4 * buf_width,
            QImage.Format.Format_ARGB32,
        ).copy()
    else:
        image = QImage(
            data.data,
            buf_width,
            buf_height,
            buf_width,
            QImage.Format.Format_Grayscale8,
        ).copy()
    return image, geotransform


def create_gcp_preview(
    source_path,
    gcps,
    crs_wkt,
    max_dimension=2048,
    use_tps=None,
    nodata_value=None,
    nodata_transparent=False,
):
    if len(gcps) < 3:
        return None

    vsimem_path = "/vsimem/frgr_gcp_preview_%s.tif" % uuid.uuid4().hex
    dataset = None
    try:
        dataset = warp_with_gcps(
            source_path,
            gcps,
            crs_wkt,
            dest_path=vsimem_path,
            use_tps=use_tps,
        )
        return dataset_to_preview_qimage(
            dataset,
            max_dimension=max_dimension,
            nodata_value=nodata_value,
            nodata_transparent=nodata_transparent,
        )
    finally:
        dataset = None
        _unlink_vsimem(vsimem_path)


def world_file_to_geotransform(a, d, b, e, c, f):
    """Convert world-file affine (pixel center origin) to GDAL geotransform."""
    return (
        c - 0.5 * a - 0.5 * b,
        a,
        b,
        f - 0.5 * d - 0.5 * e,
        d,
        e,
    )


def export_georeferenced_raster(source_path, dest_path, geotransform, crs_wkt):
    """Copy the source raster and embed georeferencing in the output GeoTIFF."""
    source_path = os.path.abspath(source_path)
    dest_path = os.path.abspath(dest_path)

    src = gdal.Open(source_path, gdal.GA_ReadOnly)
    if src is None:
        raise RuntimeError("Cannot open source raster: %s" % source_path)

    if source_path == dest_path:
        dst = gdal.Open(dest_path, gdal.GA_Update)
        if dst is None:
            raise RuntimeError("Cannot update raster: %s" % dest_path)
    else:
        driver = gdal.GetDriverByName("GTiff")
        dst = driver.CreateCopy(dest_path, src, options=["COMPRESS=LZW"])
        if dst is None:
            raise RuntimeError("Cannot create raster: %s" % dest_path)

    dst.SetGeoTransform(geotransform)
    if crs_wkt:
        dst.SetProjection(crs_wkt)
    dst.FlushCache()
    dst = None
    src = None


def warp_georeferenced_raster(
    source_path,
    dest_path,
    geotransform,
    crs_wkt,
    output_bounds,
    width,
    height,
):
    """Warp the source raster using full-quality resampling (rotation baked in)."""
    src = gdal.Open(source_path, gdal.GA_ReadOnly)
    if src is None:
        raise RuntimeError("Cannot open source raster: %s" % source_path)

    mem_driver = gdal.GetDriverByName("MEM")
    temp = mem_driver.CreateCopy("", src)
    temp.SetGeoTransform(geotransform)
    if crs_wkt:
        temp.SetProjection(crs_wkt)

    warp_options = gdal.WarpOptions(
        format="GTiff",
        outputBounds=[
            output_bounds.xMinimum(),
            output_bounds.yMinimum(),
            output_bounds.xMaximum(),
            output_bounds.yMaximum(),
        ],
        width=int(math.ceil(width)),
        height=int(math.ceil(height)),
        resampleAlg=gdal.GRA_Bilinear,
        creationOptions=["COMPRESS=LZW"],
    )
    result = gdal.Warp(dest_path, temp, options=warp_options)
    if result is None:
        raise RuntimeError("GDAL warp failed for: %s" % dest_path)
    result = None
    temp = None
    src = None


def export_with_gcps(source_path, dest_path, gcps, crs_wkt, use_tps=True):
    """Export the source raster using GCPs and thin-plate spline or polynomial."""
    warp_with_gcps(
        source_path,
        gcps,
        crs_wkt,
        dest_path=dest_path,
        use_tps=use_tps,
    )
