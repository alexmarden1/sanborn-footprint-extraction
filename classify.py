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

INK_V = 0.35        # below this value it is black line work or text
PAPER_S = 0.13      # below this saturation and bright -> blank paper
PAPER_V_REL = 0.87  # fraction of local paper brightness that still counts as paper
CAND_V = 0.40       # a fill must be at least this bright to be a colour wash


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


def classify(rgb):
    """rgb uint8 HxWx3 -> uint8 label raster, 0 = none, 1..5 = CLASSES."""
    h, s, v = rgb2hsv(rgb)
    out = np.zeros(rgb.shape[:2], np.uint8)
    ink = v < INK_V
    lit = v > 0.30
    ref = np.percentile(v[lit], 80) if lit.any() else 0.9   # local paper brightness
    paper = (s < PAPER_S) & (v > PAPER_V_REL * ref)
    cand = (~ink) & (~paper) & (v > CAND_V)

    # brick: pink/red
    out[cand & ((h < 22) | (h >= 330))] = 1
    # adobe: neutral grey to olive-brown wash (hue ~30 deg, weakly saturated)
    out[cand & (h >= 22) & (h < 42) & (s < 0.34)] = 3
    # frame: yellow (whatever in that hue band the adobe rule did not take)
    out[cand & (h >= 22) & (h < 70) & (out == 0)] = 2
    # special (brick special / frame special): green
    out[cand & (h >= 70) & (h < 175)] = 5
    # iron / fireproof: blue-grey
    out[cand & (h >= 175) & (h < 270)] = 4
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
