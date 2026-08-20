"""Settings for the Sanborn footprint pipeline.

Every path and threshold used by the scripts lives here. Edit this file for a
permanent change, override on the command line for a one-off run, or set the
environment variables below.

Environment overrides:
    SANBORN_SHEET_DIR   folder of georeferenced sheet GeoTIFFs
    SANBORN_WORK_DIR    folder for intermediate per-sheet JSON
    SANBORN_OUT_DIR     folder for the final GeoJSON + shapefile
"""
import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def _path(env_var: str, default: Path) -> Path:
    return Path(os.environ.get(env_var, str(default))).expanduser()


# ---------------------------------------------------------------- paths
# Input: one georeferenced GeoTIFF per Sanborn sheet, in EPSG:4326.
SHEET_DIR = _path("SANBORN_SHEET_DIR", REPO_DIR / "data" / "sheets")
SHEET_GLOB = "*.tif"

# Intermediates: one JSON of traced polygons per sheet. Safe to delete; step 1
# skips sheets that already have a JSON here, so this doubles as a resume cache.
WORK_DIR = _path("SANBORN_WORK_DIR", REPO_DIR / "work" / "sheets")

# Output: <LAYER_NAME>.geojson plus a matching .shp/.shx/.dbf/.prj/.cpg set.
OUT_DIR = _path("SANBORN_OUT_DIR", REPO_DIR / "output")
LAYER_NAME = "sanborn_footprints"

# Filename substrings to ignore. The 1908 El Paso set includes one oversized
# non-map sheet (an index/key plate) that traces into noise.
SKIP_SHEETS = ["a93372bb"]

# ---------------------------------------------------------------- step 1: tracing
# Resolution the sheets are resampled to before classification, in metres per
# pixel. 0.10 m is close to the native scan resolution of a 1:600 Sanborn sheet.
# Coarser values (0.20-0.40) run much faster and are good for a trial run.
RES_M = 0.10

MIN_AREA_M2 = 6.0    # smallest object kept at all
MIN_SPLIT_M2 = 8.0   # smallest single-material sub-region kept as its own polygon

# ---------------------------------------------------------------- step 2: assembly
MIN_HOLE_M2 = 5.0    # interior rings smaller than this are label text, not courtyards
SIMPLIFY_M = 0.30    # collapse the pixel staircase before estimating building bearing

DUP_MIN = 0.50       # same material and this much of the smaller copy overlapped -> duplicate
DUP_IOU = 0.50       # or this much intersection-over-union regardless of material
TOUCH_M = 0.40       # gap under which a porch or wing counts as attached to its host
