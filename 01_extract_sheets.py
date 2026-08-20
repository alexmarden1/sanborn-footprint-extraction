#!/usr/bin/env python3
"""Step 1 - trace building outlines, one sheet at a time.

Each sheet is read whole at RES_M metres per pixel, so no building is ever cut
in half by a processing tile. Objects are found on the union of all material
classes, which keeps the outer wall of a building clean, and each object is then
partitioned by material. That way a frame porch separates from its brick host
without the ragged colour-transition fringe you get from tracing each class on
its own.

One JSON per sheet is written to WORK_DIR, recording each polygon in WGS84
degrees plus the sheet it came from and that sheet's bounds. Sheets that already
have a JSON are skipped, so an interrupted run resumes where it stopped.

Usage:
    python 01_extract_sheets.py                     # all sheets
    python 01_extract_sheets.py --limit 1           # smoke test on one sheet
    python 01_extract_sheets.py --res 0.25          # faster, coarser trial
    python 01_extract_sheets.py --start 0 --end 10  # split the work across shells
"""
import argparse
import glob
import json
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("GDAL_CACHEMAX", "512")

import numpy as np
import rasterio
from affine import Affine
from rasterio import features
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from classify import CLASSES, classify

ST = np.ones((3, 3), bool)   # 8-connectivity


def nearest_fill(lab, holes):
    """Give every pixel in `holes` the label of the nearest labelled pixel.

    Ink lines and anti-aliased edges inside a building come back unclassified.
    Left alone they would punch fake courtyards through the footprint, so they
    inherit the material next to them.
    """
    _, idx = ndi.distance_transform_edt(lab == 0, return_indices=True)
    out = lab.copy()
    out[holes] = lab[idx[0][holes], idx[1][holes]]
    return out


def trace_sheet(path, res_m=None, min_area_m2=None, min_split_m2=None):
    """Trace one sheet. Returns (features, bounds, pixel_area_m2) or None."""
    res_m = config.RES_M if res_m is None else res_m
    min_area_m2 = config.MIN_AREA_M2 if min_area_m2 is None else min_area_m2
    min_split_m2 = config.MIN_SPLIT_M2 if min_split_m2 is None else min_split_m2

    res = res_m / 111320.0            # metres -> degrees of latitude

    with rasterio.open(path) as s:
        b = s.bounds
        out_w = int(round((b.right - b.left) / res))
        out_h = int(round((b.top - b.bottom) / res))
        a = s.read(out_shape=(min(s.count, 3), out_h, out_w))
    rgb = np.transpose(a[:3], (1, 2, 0))
    if rgb.max() == 0:
        return None

    lat = (b.top + b.bottom) / 2
    px_area = (res * 111320.0 * math.cos(math.radians(lat))) * (res * 111320.0)
    min_px = max(1, int(min_area_m2 / px_area))
    split_px = max(1, int(min_split_m2 / px_area))
    transform = Affine(res, 0, b.left, 0, -res, b.top)

    lab = classify(rgb)
    del rgb

    # Objects come from the union of all classes -> clean outer boundaries.
    mask = ndi.binary_opening(lab > 0, ST, iterations=2)
    if not mask.any():
        return None
    lab = np.where(mask, lab, 0).astype(np.uint8)

    holes = mask & (lab == 0)
    if holes.any():
        lab = nearest_fill(lab, holes)

    # Drop material slivers (colour fringes), then re-fill so the material
    # partition of each object stays gap-free.
    small = np.zeros_like(mask)
    for ci in range(1, len(CLASSES) + 1):
        c = lab == ci
        if not c.any():
            continue
        comp, n = ndi.label(c, structure=ST)
        if n == 0:
            continue
        sizes = np.bincount(comp.ravel())
        tiny = np.where(sizes < split_px)[0][1:] if len(sizes) > 1 else []
        small |= np.isin(comp, tiny) & c
    if small.any():
        lab = np.where(small, 0, lab)
        holes = mask & (lab == 0)
        if holes.any() and (lab > 0).any():
            lab = nearest_fill(lab, holes)

    out = []
    for ci, cname in enumerate(CLASSES, start=1):
        cm = (lab == ci) & mask
        if cm.sum() < min_px:
            continue
        for geom, val in features.shapes(cm.astype(np.uint8), mask=cm,
                                         transform=transform, connectivity=8):
            if val != 1 or len(geom["coordinates"][0]) < 4:
                continue
            out.append({"geom": geom, "material": cname})
    return out, (b.left, b.bottom, b.right, b.top), px_area


def find_sheets(sheet_dir, pattern, skip):
    files = sorted(glob.glob(os.path.join(str(sheet_dir), pattern)))
    return [f for f in files if not any(s in os.path.basename(f) for s in skip)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet-dir", default=config.SHEET_DIR, type=Path)
    ap.add_argument("--work-dir", default=config.WORK_DIR, type=Path)
    ap.add_argument("--pattern", default=config.SHEET_GLOB,
                    help="glob for sheet rasters (default: %(default)s)")
    ap.add_argument("--res", type=float, default=config.RES_M,
                    help="metres per pixel (default: %(default)s)")
    ap.add_argument("--min-area", type=float, default=config.MIN_AREA_M2)
    ap.add_argument("--min-split", type=float, default=config.MIN_SPLIT_M2)
    ap.add_argument("--start", type=int, default=0, help="first sheet index")
    ap.add_argument("--end", type=int, default=None, help="stop before this index")
    ap.add_argument("--limit", type=int, default=None, help="trace at most N sheets")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-trace sheets that already have a JSON")
    args = ap.parse_args()

    files = find_sheets(args.sheet_dir, args.pattern, config.SKIP_SHEETS)
    if not files:
        sys.exit(f"no sheets matching {args.pattern} in {args.sheet_dir}")
    files = files[args.start:args.end]
    if args.limit:
        files = files[:args.limit]

    args.work_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(files)} sheet(s) to consider at {args.res} m/px -> {args.work_dir}")

    t0 = time.time()
    for i, path in enumerate(files, 1):
        name = os.path.basename(path)
        dst = args.work_dir / (Path(name).stem + ".json")
        if dst.exists() and not args.overwrite:
            print(f"[{i}/{len(files)}] {name} - already traced, skipping")
            continue
        result = trace_sheet(path, args.res, args.min_area, args.min_split)
        if not result:
            print(f"[{i}/{len(files)}] {name} - empty, skipped")
            continue
        feats, bounds, px_area = result
        with open(dst, "w") as fh:
            json.dump({"sheet": name, "bounds": bounds, "px_area": px_area,
                       "res_m": args.res, "feats": feats}, fh)
        print(f"[{i}/{len(files)}] {name} - {len(feats)} polygons "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)

    n = len(list(args.work_dir.glob("*.json")))
    print(f"done in {time.time() - t0:.0f}s - {n} sheet JSON files in {args.work_dir}")
    print("next: python 02_build_footprints.py")


if __name__ == "__main__":
    main()
