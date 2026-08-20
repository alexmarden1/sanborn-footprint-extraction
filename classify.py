"""Map Sanborn fill colours to construction-material classes.

The 1908 Sanborn key colours every building by what it is made of. This module
turns an RGB image into a label raster using hand-tuned HSV rules:

    1 brick           pink / red
    2 frame           yellow
    3 adobe           grey to olive-brown wash
    4 iron_fireproof  blue-grey
    5 special         green (brick special / frame special)
    0 nothing         paper, black line work, text

Rules rather than a trained model, on purpose: the palette is a published
standard, the rules are auditable, and nobody has to label training data. Old
paper is uneven, so paper brightness is estimated per tile from the 80th
percentile of value instead of assumed.

If your sheets are from a different printing or a different scanning run, this
is the file to tune. `preview()` writes a colour-coded PNG of the labels so you
can see what the rules are actually doing.
"""
import numpy as np

CLASSES = ["brick", "frame", "adobe", "iron_fireproof", "special"]

# Display colours for the label raster, index 0 = unclassified.
PALETTE = np.array([[255, 255, 255], [214, 96, 96], [240, 214, 92],
                    [150, 120, 90], [90, 150, 190], [90, 170, 90]], np.uint8)

INK_V = 0.35         # below this value it is black line work or text
CAND_V = 0.40        # a fill must be at least this bright to be a colour wash

# Blank paper is measured per sheet rather than assumed, because a fill is only
# recognisable relative to the paper it sits on. Most of a Sanborn sheet is paper,
# so the median saturation of non-ink pixels is the paper tone, and the gap from
# the 10th percentile up to the median gives its spread. Fills must sit several
# spreads above the paper. Only the lower tail is used, since the fills
# themselves inflate the upper one.
#
# White 1908 El Paso paper measures ~0.10 and yields a floor near 0.13-0.15;
# cream-aged 1921 Austin paper measures ~0.29 and yields a floor near 0.37.
SAT_K = 3.0          # how many paper-spreads above the paper a fill must sit
SAT_MIN_GAP = 0.02   # ...but at least this far above it
SAT_FLOOR_MIN = 0.11 # never accept fills below this saturation
SAT_FLOOR_MAX = 0.45 # never demand more than this, however stained the paper

ADOBE_S_SPAN = 0.20 # olive-brown stays adobe rather than frame up to floor+this
ADOBE_V_REL = 0.90  # a neutral wash is darker than this fraction of paper value

# On a strongly tinted sheet, a pale wash can be *less* saturated than the paper
# it sits on -- 1921 Austin's pink brick measures 0.11 against tan paper at 0.20 --
# so no saturation floor can find it. What marks it out is hue: the paper is tan
# (~37 deg) while brick is pink (~350) and iron is teal (~185). Below a tinted
# enough paper this route stays off, because the hue of near-neutral paper is
# noise and would let everything through.
PAPER_TINT_MIN = 0.15  # paper saturation above which its hue is meaningful
HUE_DELTA = 25.0       # degrees from the paper hue that mark a distinct wash
HUE_ROUTE_S_MIN = 0.08 # ...with at least this much saturation, to exclude noise


def rgb2hsv(a):
    """Vectorised RGB -> (hue in degrees, saturation, value) for a uint8 HxWx3 array."""
    a = a.astype(np.float32) / 255.0
    mx = a.max(2)
    mn = a.min(2)
    d = mx - mn
    h = np.zeros_like(mx)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    nz = d > 1e-6
    idx = nz & (mx == r)
    h[idx] = ((g - b)[idx] / d[idx]) % 6
    idx = nz & (mx == g) & (mx != r)
    h[idx] = ((b - r)[idx] / d[idx]) + 2
    idx = nz & (mx == b) & (mx != r) & (mx != g)
    h[idx] = ((r - g)[idx] / d[idx]) + 4
    h *= 60
    s = np.zeros_like(mx)
    s[mx > 0] = d[mx > 0] / mx[mx > 0]
    return h, s, mx


def paper_tone(s, v, sat_k=None):
    """Saturation floor separating colour fills from this sheet's blank paper."""
    k = SAT_K if sat_k is None else float(sat_k)
    lit = (v > 0.30) & (v >= INK_V)
    if not lit.any():
        return SAT_FLOOR_MIN
    p10, p50 = np.percentile(s[lit], [10, 50])
    spread = max(float(p50 - p10), SAT_MIN_GAP / max(k, 0.1))
    return float(np.clip(p50 + k * spread, SAT_FLOOR_MIN, SAT_FLOOR_MAX))


def paper_hue(h, s, v):
    """Circular mean hue of this sheet's paper, and its median saturation.

    Paper outnumbers fills several times over on any Sanborn sheet, so a plain
    average over non-ink pixels lands on the paper.
    """
    lit = (v > 0.30) & (v >= INK_V)
    if not lit.any():
        return 0.0, 0.0
    mean_vec = np.exp(1j * np.radians(h[lit].astype(np.float64))).mean()
    return float(np.degrees(np.angle(mean_vec)) % 360), float(np.median(s[lit]))


def classify(rgb, sat_floor=None, classes=None, sat_k=None):
    """rgb uint8 HxWx3 -> uint8 label raster, 0 = none, 1..5 = CLASSES.

    Pass `sat_floor` to override the per-sheet paper estimate, e.g. when a sheet
    is so heavily stained that the estimate itself goes wrong.

    Pass `classes` (an iterable of names from CLASSES) to restrict the output to
    the materials an atlas actually uses. This matters most for `adobe`: it is a
    neutral wash, so it is the one class that overlaps with aged paper and grey
    line work, and switching it off removes that noise outright for a city that
    never built in adobe.
    """
    h, s, v = rgb2hsv(rgb)
    out = np.zeros(rgb.shape[:2], np.uint8)
    ink = v < INK_V
    lit = (v > 0.30) & (~ink)
    ref = np.percentile(v[lit], 80) if lit.any() else 0.9   # paper brightness
    floor = paper_tone(s, v, sat_k) if sat_floor is None else float(sat_floor)

    # A colour fill is brighter than ink and either more saturated than its
    # paper, or -- on a tinted sheet - a clearly different hue from it.
    p_hue, p_sat = paper_hue(h, s, v)
    if p_sat >= PAPER_TINT_MIN:
        off_hue = np.abs((h - p_hue + 180) % 360 - 180) >= HUE_DELTA
        distinct = (s >= floor) | (off_hue & (s >= HUE_ROUTE_S_MIN))
    else:
        distinct = s >= floor
    cand = (~ink) & (v > CAND_V) & distinct

    # brick: pink/red
    out[cand & ((h < 22) | (h >= 330))] = 1
    # adobe: olive-brown, where saturated enough to be a wash rather than paper
    out[cand & (h >= 22) & (h < 42) & (s < floor + ADOBE_S_SPAN)] = 3
    # frame: yellow (whatever in that hue band the adobe rule did not take)
    out[cand & (h >= 22) & (h < 70) & (out == 0)] = 2
    # special (brick special / frame special): green
    out[cand & (h >= 70) & (h < 175)] = 5
    # iron / fireproof: blue-grey
    out[cand & (h >= 175) & (h < 270)] = 4

    # Adobe (and stone) is often a neutral grey wash, no more saturated than the
    # paper it sits on, so saturation cannot find it. What separates the two is
    # brightness: a wash is visibly darker than blank paper, a paper tint is not.
    grey = ((~ink) & (v > CAND_V) & (s < floor) & (v < ADOBE_V_REL * ref)
            & (h >= 15) & (h < 60) & (out == 0))
    out[grey] = 3

    if classes is not None:
        wanted = set(classes)
        unknown = wanted - set(CLASSES)
        if unknown:
            raise ValueError(f"unknown class(es) {sorted(unknown)}; "
                             f"choose from {CLASSES}")
        for i, name in enumerate(CLASSES, start=1):
            if name not in wanted:
                out[out == i] = 0
    return out


def preview(labels, path):
    """Write a label raster to a PNG using PALETTE. Needs Pillow or imageio."""
    img = PALETTE[labels]
    try:
        from PIL import Image
        Image.fromarray(img).save(path)
    except ImportError:
        import imageio.v3 as iio
        iio.imwrite(path, img)
    return path


if __name__ == "__main__":
    # Sanity check on one sheet: python classify.py sheet.tif preview.png [downsample]
    import sys
    import rasterio

    src, dst = sys.argv[1], sys.argv[2]
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    with rasterio.open(src) as s:
        a = s.read(indexes=[1, 2, 3],
                   out_shape=(3, s.height // step, s.width // step))
    lab = classify(np.transpose(a, (1, 2, 0)))
    preview(lab, dst)
    total = lab.size
    for i, name in enumerate(CLASSES, start=1):
        print(f"{name:16s} {100 * (lab == i).sum() / total:5.2f}% of pixels")
    print("wrote", dst)
