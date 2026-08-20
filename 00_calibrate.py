#!/usr/bin/env python3
"""Step 0 - measure a new atlas and work out what settings it needs.

Every Sanborn atlas was printed and scanned differently, and the colour rules in
classify.py are relative to the paper, so a new city needs ten minutes of
checking before a full run. This script does that checking: it samples a few
sheets, measures the paper, reports what colour fills are present and whether
the rules can see them, and writes side-by-side previews you can eyeball.

    python 00_calibrate.py --sheet-dir data/sheets
    python 00_calibrate.py --sheet-dir data/sheets --sample 5 --save-profile profiles/my_city.json

What to look at, in order:

  1. The CRS line. Anything is fine, but "MISSING" means the sheet is not
     georeferenced and cannot be used.
  2. The paper line. Saturation tells you whether the hue route is on (tinted
     paper) or off (near-neutral paper).
  3. The fill table. Every hue band with a real share should correspond to
     colours you can see on the sheet. A band with a big share that you cannot
     see on the sheet means the rules are reading paper as a fill.
  4. The previews in calibration/. The label image should look like the sheet
     with the buildings picked out and everything else blank.

The recommendation at the end is a starting point, not an answer. Look at the
previews before trusting it.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import classify as CL
from sheetio import find_sheets, read_rgb

# Hue bands, in the order classify.py tests them. Reporting only.
BANDS = [("pink/red", "brick", lambda h: (h < 22) | (h >= 330)),
         ("olive-brown", "adobe", lambda h: (h >= 22) & (h < 42)),
         ("yellow", "frame", lambda h: (h >= 42) & (h < 70)),
         ("green", "special", lambda h: (h >= 70) & (h < 175)),
         ("blue-teal", "iron_fireproof", lambda h: (h >= 175) & (h < 270)),
         ("purple (unused)", None, lambda h: (h >= 270) & (h < 330))]

MIN_BLOB_M2 = 6.0        # a fill this big counts as a real building
PRESENT_BLOBS = 3        # this many blobs on a sheet means the class is in use


def measure(path, res_m, classes=None, sat_k=None):
    """Measure one sheet. Returns a dict of findings plus the label raster."""
    rgb, info = read_rgb(path, res_m)
    if rgb.max() == 0:
        return None
    h, s, v = CL.rgb2hsv(rgb)
    p_hue, p_sat = CL.paper_hue(h, s, v)
    floor = CL.paper_tone(s, v, sat_k)
    hue_route = p_sat >= CL.PAPER_TINT_MIN

    ink = v < CL.INK_V
    lit = (~ink) & (v > CL.CAND_V)
    lab = CL.classify(rgb, classes=classes, sat_k=sat_k)

    min_px = max(1, int(MIN_BLOB_M2 / info["px_area_m2"]))
    # What the classifier decided, per material.
    rows = []
    for i, cname in enumerate(CL.CLASSES, start=1):
        sel = lab == i
        n = int(sel.sum())
        blobs = 0
        if n:
            opened = ndi.binary_opening(sel, np.ones((3, 3), bool))
            if opened.any():
                comp, nc = ndi.label(opened, structure=np.ones((3, 3), bool))
                if nc:
                    sizes = np.bincount(comp.ravel())[1:]
                    blobs = int((sizes >= min_px).sum())
        rows.append({"cls": cname, "n": n, "pct": 100.0 * n / lab.size,
                     "sat": float(np.median(s[sel])) if n else None,
                     "hue": float(np.median(h[sel])) if n else None,
                     "blobs": blobs})

    # Possible misses: coloured pixels clearly off the paper's hue that the
    # rules nonetheless rejected. This is the signature of a wash the classifier
    # cannot see - exactly how 1921 Austin's pale pink brick went missing.
    dh_all = np.abs((h - p_hue + 180) % 360 - 180)
    missed = lit & (lab == 0) & (dh_all >= CL.HUE_DELTA) & (s >= CL.HUE_ROUTE_S_MIN)
    miss_bands = []
    if missed.any():
        for label, cname, test in BANDS:
            sel = missed & test(h)
            pct = 100.0 * sel.sum() / lab.size
            if pct >= 0.02:
                miss_bands.append({"label": label, "cls": cname, "pct": pct,
                                   "sat": float(np.median(s[sel]))})

    return {"path": path, "info": info, "paper_hue": p_hue, "paper_sat": p_sat,
            "floor": floor, "hue_route": hue_route, "rows": rows,
            "blobs": {r["cls"]: r["blobs"] for r in rows},
            "missed": miss_bands, "missed_pct": 100.0 * missed.sum() / lab.size,
            "fill_frac": float((lab > 0).mean()),
            "labels": lab, "rgb": rgb}


def report(m):
    info = m["info"]
    gw, gh = info["ground_m"]
    print(f"\n=== {Path(m['path']).name}")
    print(f"    CRS {info['crs'].to_string() if info['crs'] else 'MISSING'}"
          f" | native {info['native'][0]} x {info['native'][1]} px"
          f" (~{info['native_res_m']:.2f} m/px)"
          f" | ground {gw:.0f} x {gh:.0f} m")
    print(f"    paper: hue {m['paper_hue']:.0f} deg, saturation {m['paper_sat']:.2f}"
          f" -> hue route {'ON (tinted paper)' if m['hue_route'] else 'off (neutral paper)'}")
    print(f"    saturation floor {m['floor']:.3f}"
          f" | classified as fill: {100 * m['fill_frac']:.2f}% of sheet")
    print(f"    {'material':16s} {'% of sheet':>10s} {'med hue':>8s} {'med sat':>8s}"
          f" {'buildings':>10s}")
    for r in m["rows"]:
        if not r["n"]:
            print(f"    {r['cls']:16s} {'-':>10s} {'-':>8s} {'-':>8s} {0:>10d}")
        else:
            print(f"    {r['cls']:16s} {r['pct']:9.2f}% {r['hue']:8.0f}"
                  f" {r['sat']:8.2f} {r['blobs']:10d}")
    if m["missed"]:
        print("    possible misses (off-paper-hue colour the rules rejected):")
        for b in m["missed"]:
            print(f"      {b['label']:16s} {b['pct']:5.2f}% of sheet,"
                  f" median saturation {b['sat']:.2f}"
                  f"  -> would be {b['cls'] or 'no class'}")


def recommend(measurements):
    """Turn the per-sheet findings into suggested settings."""
    present, absent = [], []
    for cname in CL.CLASSES:
        total = sum(m["blobs"].get(cname, 0) for m in measurements)
        (present if total >= PRESENT_BLOBS else absent).append((cname, total))

    print("\n" + "=" * 72)
    print("RECOMMENDATION")
    print("=" * 72)
    print(f"sampled {len(measurements)} sheet(s) - a material used only in a part"
          " of the city")
    print("can be missed, so raise --sample before trusting the list below.\n")
    print("materials in use:  " + ", ".join(f"{c} ({n})" for c, n in present))
    if absent:
        print("not found:         " + ", ".join(f"{c} ({n})" for c, n in absent))
        print("  -> restrict the run to what is actually there; this removes a"
              " whole category")
        print("     of false positives, especially for adobe:")
        print("     --classes " + ",".join(c for c, _ in present))

    tinted = [m for m in measurements if m["hue_route"]]
    if tinted:
        print(f"\npaper is tinted on {len(tinted)}/{len(measurements)} sampled sheets,"
              " so pale washes are")
        print("found by hue rather than saturation. If pale fills are still being"
              " missed,")
        print("lower HUE_DELTA in classify.py; if line work is being traced,"
              " raise HUE_ROUTE_S_MIN.")

    misses = {}
    for m in measurements:
        for b in m["missed"]:
            misses.setdefault(b["label"], []).append(b["pct"])
    if misses:
        print("\npossible unrecognised washes:")
        for label, pcts in sorted(misses.items(), key=lambda kv: -max(kv[1])):
            print(f"  {label:16s} up to {max(pcts):.2f}% of a sheet")
        print("  -> compare the label preview against the RGB preview for those")
        print("     colours. If real buildings are missing, lower HUE_DELTA or")
        print("     --sat-k; if it is only line work and scan edges, ignore it.")

    # A class whose median colour matches the paper's is the classic false
    # positive: it is the paper, read as a wash. Cannot be settled automatically,
    # because a genuine neutral wash also sits near the paper hue - hence the
    # instruction to look.
    suspect = []
    for m in measurements:
        for r in m["rows"]:
            if not r["n"] or r["pct"] < 5.0:
                continue
            if abs((r["hue"] - m["paper_hue"] + 180) % 360 - 180) < 10:
                suspect.append((r["cls"], r["pct"], Path(m["path"]).name))
    if suspect:
        worst = {}
        for cls, pct, sheet in suspect:
            if pct > worst.get(cls, (0, ""))[0]:
                worst[cls] = (pct, sheet)
        print("\nCLASSES THAT MIGHT JUST BE PAPER:")
        for cls, (pct, sheet) in sorted(worst.items(), key=lambda kv: -kv[1][0]):
            print(f"  {cls:16s} {pct:5.1f}% of a sheet, at the paper's own hue")
        print("  Open the label preview next to the RGB preview and check that")
        print("  these areas are buildings. If the whole sheet is coloured in, they")
        print("  are not - drop the class with --classes, or raise SAT_K.")

    fills = [m["fill_frac"] for m in measurements]
    if max(fills) > 0.20:
        print(f"\nWARNING: up to {100 * max(fills):.0f}% of a sheet is classified"
              " as building fill.")
        print("  A dense city block is 10-20%; well above that means paper is being")
        print("  read as a material. Check the previews before running.")
    return [c for c, _ in present]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet-dir", type=Path, default=None)
    ap.add_argument("--pattern", default=None)
    ap.add_argument("--sample", type=int, default=3,
                    help="how many sheets to measure, spread across the set "
                         "(default: %(default)s)")
    ap.add_argument("--sheet", action="append", default=None,
                    help="measure this sheet specifically; repeatable")
    ap.add_argument("--res", type=float, default=0.40,
                    help="metres per pixel for the check - coarse is fine and "
                         "fast (default: %(default)s)")
    ap.add_argument("--classes", default=None,
                    help="restrict the check to these materials")
    ap.add_argument("--sat-k", type=float, default=None)
    ap.add_argument("--out-dir", type=Path, default=Path("calibration"),
                    help="where to write preview images (default: %(default)s)")
    ap.add_argument("--save-profile", type=Path, default=None,
                    help="write the findings and recommendation to a JSON profile")
    ap.add_argument("--profile", type=Path, default=None,
                    help="start from an existing profile")
    args = ap.parse_args()

    prof = config.load_profile(args.profile)
    pick = config.resolver(args, prof)
    sheet_dir = Path(pick("sheet_dir", config.SHEET_DIR))
    pattern = pick("pattern", config.SHEET_GLOB)
    classes = pick("classes", config.CLASSES_USED)
    if isinstance(classes, str):
        classes = [c.strip() for c in classes.split(",") if c.strip()]

    if args.sheet:
        files = [str(Path(s)) for s in args.sheet]
    else:
        files = find_sheets(sheet_dir, pattern, prof.get("skip_sheets",
                                                        config.SKIP_SHEETS))
        if not files:
            sys.exit(f"no sheets matching {pattern} in {sheet_dir}")
        if args.sample < len(files):      # spread the sample across the atlas
            idx = np.linspace(0, len(files) - 1, args.sample).round().astype(int)
            files = [files[i] for i in sorted(set(idx))]

    print(f"calibrating on {len(files)} of the sheets in {sheet_dir} "
          f"at {args.res} m/px")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    measurements = []
    for path in files:
        try:
            m = measure(path, args.res, classes=classes, sat_k=args.sat_k)
        except Exception as exc:
            print(f"\n=== {Path(path).name}\n    FAILED: {exc}")
            continue
        if m is None:
            print(f"\n=== {Path(path).name}\n    empty (all nodata)")
            continue
        report(m)
        stem = Path(path).stem[:48]
        try:
            from PIL import Image
            Image.fromarray(m["rgb"]).save(args.out_dir / f"{stem}_rgb.png")
            CL.preview(m["labels"], args.out_dir / f"{stem}_labels.png")
            print(f"    previews -> {args.out_dir / (stem + '_rgb.png')}")
        except ImportError:
            print("    (install pillow to get preview images)")
        measurements.append(m)

    if not measurements:
        sys.exit("nothing could be measured")

    suggested = recommend(measurements)

    if args.save_profile:
        out = dict(prof)
        out.setdefault("name", args.save_profile.stem)
        out["pattern"] = pattern
        out["classes"] = suggested
        if args.sat_k is not None:
            out["sat_k"] = args.sat_k
        out["measured"] = {
            "sheets_sampled": [Path(m["path"]).name for m in measurements],
            "calibration_res_m": args.res,
            "paper_hue_deg": round(float(np.mean([m["paper_hue"] for m in measurements])), 1),
            "paper_saturation": round(float(np.mean([m["paper_sat"] for m in measurements])), 3),
            "saturation_floor": round(float(np.mean([m["floor"] for m in measurements])), 3),
            "hue_route": bool(any(m["hue_route"] for m in measurements)),
            "crs": str(measurements[0]["info"]["crs"]),
        }
        out.setdefault("notes", "written by 00_calibrate.py; edit freely")
        args.save_profile.parent.mkdir(parents=True, exist_ok=True)
        with open(args.save_profile, "w") as fh:
            json.dump(out, fh, indent=2)
            fh.write("\n")
        print(f"\nprofile written to {args.save_profile}")
        print(f"run it with:  python 01_extract_sheets.py --profile {args.save_profile}")
    else:
        print("\nsave these findings with --save-profile profiles/<city>.json")

    print("\nLook at the label previews before starting a full run.")


if __name__ == "__main__":
    main()
