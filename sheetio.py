"""Reading Sanborn sheets: finding them, and putting them on a metric grid.

Shared by 00_calibrate.py and 01_extract_sheets.py so both see a sheet exactly
the same way.
"""
import glob
import math
import os

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

WGS84 = CRS.from_epsg(4326)
M_PER_DEG = 111320.0         # metres per degree of latitude
MAX_PIXELS = 400_000_000     # refuse to allocate a grid larger than this


def find_sheets(sheet_dir, pattern="*.tif", skip=()):
    """Sheet paths in sorted order, minus any whose name contains a skip string."""
    files = sorted(glob.glob(os.path.join(str(sheet_dir), pattern)))
    return [f for f in files if not any(s in os.path.basename(f) for s in skip)]


def sheet_grid(src, res_m):
    """Work out the read grid for one sheet, whatever CRS it happens to be in.

    Sheets arrive in lat/lon degrees from some archives and in projected metres
    (Web Mercator, UTM, state plane) from others, so the pixel size is derived
    from the sheet's *ground* extent rather than from its coordinate units. The
    read grid stays in the sheet's own CRS; polygons traced on it are converted
    to lon/lat afterwards.

    Returns (out_w, out_h, transform, px_area_m2, lonlat_bounds).
    """
    if src.crs is None:
        raise ValueError("sheet has no CRS - it is not georeferenced")

    b = src.bounds
    west, south, east, north = transform_bounds(src.crs, WGS84, *b)
    lat0 = (south + north) / 2
    ground_w = (east - west) * M_PER_DEG * math.cos(math.radians(lat0))
    ground_h = (north - south) * M_PER_DEG

    out_w = max(1, int(round(ground_w / res_m)))
    out_h = max(1, int(round(ground_h / res_m)))
    if out_w * out_h > MAX_PIXELS:
        raise ValueError(
            f"grid would be {out_w} x {out_h} px at {res_m} m/px "
            f"({ground_w:.0f} x {ground_h:.0f} m of ground). Either the sheet "
            f"covers a whole city (a mosaic rather than one sheet) or its "
            f"georeferencing is wrong. Try a coarser --res, or exclude it.")

    transform = Affine((b.right - b.left) / out_w, 0, b.left,
                       0, -(b.top - b.bottom) / out_h, b.top)
    px_area = (ground_w / out_w) * (ground_h / out_h)
    return out_w, out_h, transform, px_area, (west, south, east, north)


def read_rgb(path, res_m):
    """Read one sheet as an RGB array on a res_m grid.

    Returns (rgb, info) where info carries the grid, the pixel area in m2, the
    lon/lat bounds, the source CRS and the ground extent.
    """
    with rasterio.open(path) as s:
        out_w, out_h, transform, px_area, ll = sheet_grid(s, res_m)
        a = s.read(out_shape=(min(s.count, 3), out_h, out_w))
        info = {"crs": s.crs, "native": (s.width, s.height),
                "native_res_m": None, "transform": transform,
                "px_area_m2": px_area, "lonlat_bounds": ll,
                "grid": (out_w, out_h)}
    rgb = np.transpose(a[:3], (1, 2, 0))
    lat0 = (ll[1] + ll[3]) / 2
    ground_w = (ll[2] - ll[0]) * M_PER_DEG * math.cos(math.radians(lat0))
    ground_h = (ll[3] - ll[1]) * M_PER_DEG
    info["ground_m"] = (ground_w, ground_h)
    info["native_res_m"] = ground_w / max(info["native"][0], 1)
    return rgb, info
