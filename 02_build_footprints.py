#!/usr/bin/env python3
"""Step 2 - deduplicate, regularize, and write the footprint layer.

Sanborn sheets overlap heavily, so the same building is traced on two or three
of them, and the copies near a sheet margin are clipped. The fix is geometric:
sort every candidate polygon by how far its centroid sits from its own sheet's
edge and greedily accept the most interior copy first, discarding anything that
overlaps it. The complete version of a building survives; the cut ones do not.

What happens here, in order:

  1. load every per-sheet JSON from step 1, project to a local metric frame
  2. drop duplicates across overlapping sheets (see above)
  3. regularize each outline - square up corners, remove pixel staircase
     (see regularize.py; churches and curved fronts skip orthogonalization)
  4. link porches and wings to the larger building they touch (host_id)
  5. write GeoJSON + shapefile in WGS84, with per-building attributes

This reads only the JSON files, never the rasters, so it takes seconds and is
cheap to re-run while tuning thresholds.

Usage:
    python 02_build_footprints.py
    python 02_build_footprints.py --name el_paso_1908_footprints
    python 02_build_footprints.py --dup-min 0.6 --touch 0.3
"""
import argparse
import collections
import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np
import shapefile
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import transform as shp_transform
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import regularize as RG

WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
             '6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
             'UNIT["Degree",0.0174532925199433]]')

FIELDS = [("bldg_id", "N", 9, 0), ("material", "C", 20, 0), ("area_m2", "N", 12, 1),
          ("perim_m", "N", 12, 1), ("bbox_w_m", "N", 9, 1), ("bbox_h_m", "N", 9, 1),
          ("azimuth", "N", 6, 1), ("regular", "N", 1, 0), ("n_holes", "N", 4, 0),
          ("n_sheets", "N", 3, 0), ("edge_m", "N", 9, 1), ("host_id", "N", 9, 0),
          ("src_sheet", "C", 60, 0)]


def metric_frame(lat0):
    """Local equirectangular transforms: degrees <-> metres about latitude lat0.

    Areas and lengths only need to be right to a few centimetres over a city,
    and this keeps the whole pipeline free of a projection dependency.
    """
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 111320.0
    to_m = lambda x, y: (np.asarray(x) * kx, np.asarray(y) * ky)
    to_deg = lambda x, y: (np.asarray(x) / kx, np.asarray(y) / ky)
    return to_m, to_deg


def drop_small_holes(g, min_hole_m2):
    """Remove interior rings smaller than min_hole_m2 - those are label text."""
    parts = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
    out, n = [], 0
    for pg in parts:
        keep = []
        for ring in pg.interiors:
            if Polygon(ring).area >= min_hole_m2:
                keep.append(ring.coords)
            else:
                n += 1
        out.append(Polygon(pg.exterior.coords, keep))
    return (out[0] if len(out) == 1 else MultiPolygon(out)), n


def load_candidates(work_dir, min_area_m2):
    """Read per-sheet JSON, split multipart traces, record distance to sheet edge."""
    files = sorted(Path(work_dir).glob("*.json"))
    if not files:
        sys.exit(f"no sheet JSON in {work_dir} - run 01_extract_sheets.py first")

    sheets = []
    for f in files:
        with open(f) as fh:
            sheets.append(json.load(fh))

    # One metric frame for the whole city, centred on the sheets' mean latitude.
    lat0 = st.mean([(d["bounds"][1] + d["bounds"][3]) / 2 for d in sheets])
    to_m, to_deg = metric_frame(lat0)

    recs = []
    for d in sheets:
        sheet_box = shp_transform(to_m, box(*d["bounds"]))
        for ft in d["feats"]:
            g = shp_transform(to_m, shape(ft["geom"]))
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty or g.area < min_area_m2:
                continue
            for pg in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                if pg.area < min_area_m2:
                    continue
                c = pg.centroid
                recs.append({
                    "g": pg,
                    "material": ft["material"],
                    "sheet": d["sheet"],
                    "edge_m": c.distance(sheet_box.exterior) if sheet_box.contains(c) else 0.0,
                })
    return recs, lat0, to_deg, len(files)


def dedupe(recs, dup_min, dup_iou):
    """Keep the most interior copy of each building; count how many sheets saw it."""
    recs.sort(key=lambda r: -r["edge_m"])          # most interior first
    tree = STRtree([r["g"] for r in recs])
    alive = np.ones(len(recs), bool)
    dup_of = collections.Counter()

    for i, r in enumerate(recs):
        if not alive[i]:
            continue
        for j in tree.query(r["g"]):
            j = int(j)
            if j <= i or not alive[j]:
                continue
            other = recs[j]
            try:
                inter = r["g"].intersection(other["g"]).area
            except Exception:
                continue
            if inter <= 0:
                continue
            smaller = min(r["g"].area, other["g"].area)
            iou = inter / (r["g"].area + other["g"].area - inter)
            same_material = r["material"] == other["material"]
            if (same_material and inter / smaller > dup_min) or iou > dup_iou:
                alive[j] = False
                dup_of[i] += 1

    keep = []
    for i, r in enumerate(recs):
        if alive[i]:
            r["n_sheets"] = dup_of[i] + 1
            keep.append(r)
    return keep


def regularize_all(keep, min_area_m2, min_hole_m2, simplify_m):
    modes = collections.Counter()
    n_holes = 0
    final = []
    for r in keep:
        g, n = drop_small_holes(r["g"], min_hole_m2)
        n_holes += n
        if not g.is_valid:
            g = g.buffer(0)
        # Collapse the pixel staircase first, otherwise the dominant-axis
        # estimator locks onto the raster grid instead of the building's bearing.
        g = g.simplify(simplify_m, preserve_topology=True)
        if not g.is_valid:
            g = g.buffer(0)
        if g.is_empty or g.area < min_area_m2:
            continue
        res, mode = RG.regularize(g, want_mode=True)
        if res is None or res.is_empty or res.area < min_area_m2:
            res, mode = g, 0
        res, n = drop_small_holes(res, min_hole_m2)
        n_holes += n
        if not res.is_valid:
            res = res.buffer(0)
        if res.is_empty or res.area < min_area_m2:
            continue
        r["g"], r["mode"] = res, mode
        modes[mode] += 1
        final.append(r)
    return final, modes, n_holes


def link_hosts(final, touch_m):
    """Attach each polygon to the largest different-material neighbour it touches."""
    final.sort(key=lambda r: (-r["g"].centroid.y, r["g"].centroid.x))
    for i, r in enumerate(final, 1):
        r["id"] = i
    tree = STRtree([r["g"] for r in final])
    for r in final:
        r["host"] = 0
        best = 0.0
        for j in tree.query(r["g"].buffer(touch_m)):
            o = final[int(j)]
            if o is r or o["material"] == r["material"] or o["g"].area <= r["g"].area:
                continue
            if r["g"].distance(o["g"]) <= touch_m and o["g"].area > best:
                best = o["g"].area
                r["host"] = o["id"]


def write_layer(final, to_deg, out_dir, name):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = str(out_dir / name)

    gj = {"type": "FeatureCollection", "name": name,
          "crs": {"type": "name",
                  "properties": {"name": "urn:ogc:def:crs:OGC:1.3/CRS84"}},
          "features": []}
    w = shapefile.Writer(stem, shapeType=shapefile.POLYGON)
    for fname, ftype, size, dec in FIELDS:
        w.field(fname, ftype, size, dec) if ftype == "N" else w.field(fname, ftype, size)

    n_vert = 0
    for r in final:
        gm = r["g"]
        gd = shp_transform(to_deg, gm)
        parts = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
        dparts = list(gd.geoms) if gd.geom_type == "MultiPolygon" else [gd]
        rings = []
        for pg in dparts:
            rings.append(list(pg.exterior.coords))
            rings += [list(x.coords) for x in pg.interiors]
        n_vert += sum(len(x) for x in rings)

        biggest = max(parts, key=lambda p: p.area)
        az = math.degrees(RG.dominant_axis(np.array(biggest.exterior.coords))) % 90
        mnx, mny, mxx, mxy = gm.bounds
        props = {"bldg_id": r["id"], "material": r["material"],
                 "area_m2": round(gm.area, 1), "perim_m": round(gm.length, 1),
                 "bbox_w_m": round(mxx - mnx, 1), "bbox_h_m": round(mxy - mny, 1),
                 "azimuth_deg": round(az, 1), "regularized": r["mode"],
                 "n_holes": sum(len(p.interiors) for p in parts),
                 "n_sheets": r.get("n_sheets", 1), "edge_m": round(r["edge_m"], 1),
                 "host_id": r["host"], "source_sheet": r["sheet"]}
        gj["features"].append({"type": "Feature", "id": r["id"],
                               "geometry": mapping(gd), "properties": props})
        w.poly(rings)
        w.record(r["id"], r["material"], props["area_m2"], props["perim_m"],
                 props["bbox_w_m"], props["bbox_h_m"], props["azimuth_deg"],
                 r["mode"], props["n_holes"], props["n_sheets"], props["edge_m"],
                 r["host"], r["sheet"])
    w.close()

    with open(stem + ".geojson", "w") as fh:
        json.dump(gj, fh)
    open(stem + ".prj", "w").write(WGS84_PRJ)
    open(stem + ".cpg", "w").write("UTF-8")
    return gj, n_vert, stem


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=config.WORK_DIR, type=Path)
    ap.add_argument("--out-dir", default=config.OUT_DIR, type=Path)
    ap.add_argument("--name", default=config.LAYER_NAME, help="output layer name")
    ap.add_argument("--min-area", type=float, default=config.MIN_AREA_M2)
    ap.add_argument("--min-hole", type=float, default=config.MIN_HOLE_M2)
    ap.add_argument("--simplify", type=float, default=config.SIMPLIFY_M)
    ap.add_argument("--dup-min", type=float, default=config.DUP_MIN)
    ap.add_argument("--dup-iou", type=float, default=config.DUP_IOU)
    ap.add_argument("--touch", type=float, default=config.TOUCH_M)
    args = ap.parse_args()

    recs, lat0, to_deg, n_sheets = load_candidates(args.work_dir, args.min_area)
    print(f"{n_sheets} sheets | {len(recs)} candidate polygons | "
          f"metric frame at lat {lat0:.4f}")

    keep = dedupe(recs, args.dup_min, args.dup_iou)
    print(f"after dedupe {len(keep)} | removed {len(recs) - len(keep)} "
          f"({100 * (len(recs) - len(keep)) / max(len(recs), 1):.0f}% were repeat traces)")

    final, modes, n_holes = regularize_all(keep, args.min_area, args.min_hole,
                                           args.simplify)
    print(f"regularized: {modes.get(2, 0)} squared up, {modes.get(1, 0)} smoothed, "
          f"{modes.get(0, 0)} left as traced | {n_holes} text holes removed")

    link_hosts(final, args.touch)
    gj, n_vert, stem = write_layer(final, to_deg, args.out_dir, args.name)

    n = len(final)
    props = [f["properties"] for f in gj["features"]]
    print(f"\nFINAL {n} buildings -> {stem}.geojson (+ .shp/.dbf/.shx/.prj/.cpg)")
    print(f"vertices {n_vert}, avg {n_vert / max(n, 1):.1f} per building")
    for k, v in collections.Counter(p["material"] for p in props).most_common():
        print(f"  {k:16s} {v:5d}  {100 * v / max(n, 1):5.1f}%")
    areas = [p["area_m2"] for p in props]
    if areas:
        print(f"area m2: median {st.median(areas):.1f} | "
              f"total built {sum(areas) / 1e4:.2f} ha")
    print("courtyards kept:", sum(1 for p in props if p["n_holes"] > 0))
    print("porches/wings linked to a host:", sum(1 for p in props if p["host_id"] > 0))
    print("traced on more than one sheet:", sum(1 for p in props if p["n_sheets"] > 1))
    r = shapefile.Reader(stem)
    print(f"verify: shapefile has {len(r)} records, shapeType {r.shapeType}")


if __name__ == "__main__":
    main()
