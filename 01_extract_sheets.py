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
    python 01_extract_sheets.py                          # all sheets
    python 01_extract_sheets.py --limit 1                # smoke test on one sheet
    python 01_extract_sheets.py --res 0.25               # faster, coarser trial
    python 01_extract_sheets.py --profile profiles/austin_1921.json
    python 01_extract_sheets.py --start 0 --end 10       # split across shells

Run 00_calibrate.py first on an atlas you have not processed before.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("GDAL_CACHEMAX", "512")

import numpy as np
import rasterio
from rasterio import features
from rasterio.warp import transform_geom
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from classify import CLASSES, classify
from sheetio import WGS84, find_sheets, sheet_grid

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


def trace_sheet(path, res_m=None, min_area_m2=None, min_split_m2=None,
                classes=None, sat_k=None, sat_floor=None):
    """Trace one sheet. Returns (features, lonlat_bounds, pixel_area_m2) or None.

    Output geometry is always lon/lat (EPSG:4326), whatever the sheet's own CRS.
    """
    res_m = config.RES_M if res_m is None else res_m
    min_area_m2 = config.MIN_AREA_M2 if min_area_m2 is None else min_area_m2
    min_split_m2 = config.MIN_SPLIT_M2 if min_split_m2 is None else min_split_m2
    classes = config.CLASSES_USED if classes is None else classes
    active = list(classes) if classes else CLASSES

    with rasterio.open(path) as s:
        src_crs = s.crs
        out_w, out_h, transform, px_area, ll_bounds = sheet_grid(s, res_m)
        a = s.read(out_shape=(min(s.count, 3), out_h, out_w))
    rgb = np.transpose(a[:3], (1, 2, 0))
    if rgb.max() == 0:
        return None

    min_px = max(1, int(min_area_m2 / px_area))
    split_px = max(1, int(min_split_m2 / px_area))

    lab = classify(rgb, classes=active, sat_k=sat_k, sat_floor=sat_floor)
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

    warp = not src_crs == WGS84          # sheets in projected CRSs need converting
    out = []
    for ci, cname in enumerate(CLASSES, start=1):
        cm = (lab == ci) & mask
        if cm.sum() < min_px:
            continue
        for geom, val in features.shapes(cm.astype(np.uint8), mask=cm,
                                         transform=transform, connectivity=8):
            if val != 1 or len(geom["coordinates"][0]) < 4:
                continue
            if warp:
                geom = transform_geom(src_crs, WGS84, geom)
            out.append({"geom": geom, "material": cname})
    return out, ll_bounds, px_area


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, default=None,
                    help="JSON profile written by 00_calibrate.py; anything given "
                         "explicitly on the command line still wins")
    ap.add_argument("--sheet-dir", default=None, type=Path)
    ap.add_argument("--work-dir", default=None, type=Path)
    ap.add_argument("--pattern", default=None,
                    help=f"glob for sheet rasters (default: {config.SHEET_GLOB})")
    ap.add_argument("--res", type=float, default=None,
                    help=f"metres per pixel (default: {config.RES_M})")
    ap.add_argument("--min-area", type=float, default=None)
    ap.add_argument("--min-split", type=float, default=None)
    ap.add_argument("--sat-k", type=float, default=None,
                   help="how far above the paper tone a colour fill must sit, in "
                        "paper-spreads (default 3.0). Lower it if fills are being "
                        "eaten, raise it if paper leaks in.")
    ap.add_argument("--sat-floor", type=float, default=None,
                   help="fixed saturation floor, bypassing the per-sheet estimate")
    ap.add_argument("--classes", default=None,
                   help="comma-separated materials to keep, e.g. "
                        "'brick,frame,special,iron_fireproof' for an atlas with "
                        f"no adobe. Default: all of {','.join(CLASSES)}")
    ap.add_argument("--start", type=int, default=0, help="first sheet index")
    ap.add_argument("--end", type=int, default=None, help="stop before this index")
    ap.add_argument("--limit", type=int, default=None, help="trace at most N sheets")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-trace sheets that already have a JSON")
    args = ap.parse_args()

    prof = config.load_profile(args.profile)
    pick = config.resolver(args, prof)

    sheet_dir = Path(pick("sheet_dir", config.SHEET_DIR))
    work_dir = Path(pick("work_dir", config.WORK_DIR))
    pattern = pick("pattern", config.SHEET_GLOB)
    res = float(pick("res", config.RES_M))
    min_area = float(pick("min_area", config.MIN_AREA_M2))
    min_split = float(pick("min_split", config.MIN_SPLIT_M2))
    sat_k = pick("sat_k", None)
    sat_floor = pick("sat_floor", None)
    skip = prof.get("skip_sheets", config.SKIP_SHEETS)

    classes = pick("classes", config.CLASSES_USED)
    if isinstance(classes, str):
        classes = [c.strip() for c in classes.split(",") if c.strip()]
    active = list(classes) if classes else list(CLASSES)

    files = find_sheets(sheet_dir, pattern, skip)
    if not files:
        sys.exit(f"no sheets matching {pattern} in {sheet_dir}")
    files = files[args.start:args.end]
    if args.limit:
        files = files[:args.limit]

    work_dir.mkdir(parents=True, exist_ok=True)
    if prof:
        print(f"profile {args.profile} ({prof.get('name', 'unnamed')})")
    print(f"{len(files)} sheet(s) to consider at {res} m/px -> {work_dir}")

    with rasterio.open(files[0]) as s:
        print(f"sheet CRS {s.crs.to_string() if s.crs else 'MISSING'}"
              f" - polygons are written as lon/lat either way")
    print(f"materials: {', '.join(active)}"
          + (f" | sat_k {sat_k}" if sat_k else "")
          + (f" | sat_floor {sat_floor}" if sat_floor else ""))

    t0 = time.time()
    failed = []
    for i, path in enumerate(files, 1):
        name = os.path.basename(path)
        dst = work_dir / (Path(name).stem + ".json")
        if dst.exists() and not args.overwrite:
            print(f"[{i}/{len(files)}] {name} - already traced, skipping")
            continue
        try:
            result = trace_sheet(path, res, min_area, min_split,
                                 classes=active, sat_k=sat_k,
                                 sat_floor=sat_floor)
        except Exception as exc:                     # keep going; report at the end
            failed.append((name, exc))
            print(f"[{i}/{len(files)}] {name} - FAILED: {exc}", flush=True)
            continue
        if not result:
            print(f"[{i}/{len(files)}] {name} - empty, skipped")
            continue
        feats, bounds, px_area = result
        with open(dst, "w") as fh:
            json.dump({"sheet": name, "bounds": bounds, "px_area": px_area,
                       "res_m": res, "classes": active, "feats": feats}, fh)
        print(f"[{i}/{len(files)}] {name} - {len(feats)} polygons "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)

    n = len(list(work_dir.glob("*.json")))
    print(f"done in {time.time() - t0:.0f}s - {n} sheet JSON files in {work_dir}")
    if failed:
        print(f"{len(failed)} sheet(s) failed:")
        for name, exc in failed:
            print(f"  {name}: {exc}")
    print("next: python 02_build_footprints.py")


if __name__ == "__main__":
    main()
