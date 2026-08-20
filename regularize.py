"""Regularization for Sanborn footprint rings.

Pipeline per building (all geometry in metres, local equirectangular frame):
  1. morphological closing  -> dissolves zero-width pinch seams between body and porch
  2. despike                -> drops doubled-back vertices and near-duplicates
  3. orthogonalize          -> snaps edges to the building's own dominant axis,
                               rebuilding true square corners by line intersection
Non-rectilinear buildings (churches, curved fronts) skip step 3 automatically.
"""
import numpy as np
import shapely
from shapely.geometry import Polygon, MultiPolygon, LinearRing


def _fix(g):
    """Make a geometry topologically safe: snap to 1 mm and repair."""
    try:
        g = shapely.set_precision(g, 0.001)
    except Exception:
        pass
    if not g.is_valid:
        g = g.buffer(0)
    return g


def _symdif(a, b):
    """Symmetric-difference area, robust to non-noded intersections."""
    for x, y in ((a, b), (_fix(a), _fix(b))):
        try:
            return x.symmetric_difference(y).area
        except Exception:
            continue
    return float('inf')

TOL_DEG   = 22.0   # max deviation for an edge to count as axis-aligned
MIN_EDGE  = 0.50   # m  - axis-aligned edges shorter than this are pixel staircase
MIN_FREE  = 1.50   # m  - free (diagonal) edges shorter than this are bevels
SNAP_M    = 0.35   # m  - walls within this distance are made flush
CLOSE_M   = 0.20   # m  - closing radius for pinch seams
SMOOTH_M  = 0.60   # m  - simplify tolerance for non-rectilinear fallbacks
BEVEL_MAX = 4.00   # m  - a diagonal this short between two perpendicular walls
BEVEL_REL = 0.60   #      and this fraction of its neighbours is a traced corner cut
RECT_FRAC = 0.60   # min fraction of perimeter that must be axis-aligned
MAX_SYMDIF= 0.25   # reject regularization if it moves >25% of the area


def _seg(c):
    v = np.diff(c, axis=0)
    return v, np.hypot(v[:, 0], v[:, 1]), np.arctan2(v[:, 1], v[:, 0])


def dominant_axis(c):
    """Length-weighted 4-fold circular mean, refined to reject off-axis edges."""
    _, L, a = _seg(c)
    m = L >= 0.8
    if m.sum() < 2:
        m = L > 0
    if m.sum() < 2:
        return 0.0
    th = np.angle((L[m] * np.exp(4j * a[m])).sum()) / 4
    for _ in range(3):
        dev = np.abs(((a - th + np.pi / 4) % (np.pi / 2)) - np.pi / 4)
        m2 = m & (dev < np.radians(15))
        if m2.sum() < 2:
            break
        th = np.angle((L[m2] * np.exp(4j * a[m2])).sum()) / 4
    return th


def despike(c, tol_deg=12.0, dup=0.05):
    """Remove near-duplicate vertices and spikes where the path doubles back."""
    p = [np.asarray(x, float) for x in c[:-1]]
    for _ in range(12):
        n = len(p)
        if n < 4:
            break
        drop = set()
        for i in range(n):
            a, b, d = p[(i - 1) % n], p[i], p[(i + 1) % n]
            if np.hypot(*(b - a)) < dup:
                drop.add(i)
                continue
            u, w = a - b, d - b
            nu, nw = np.hypot(*u), np.hypot(*w)
            if nu < 1e-9 or nw < 1e-9:
                drop.add(i)
                continue
            cos = np.clip(u @ w / (nu * nw), -1, 1)
            if np.degrees(np.arccos(cos)) < tol_deg:   # doubles straight back
                drop.add(i)
        if not drop or len(p) - len(drop) < 4:
            break
        p = [q for i, q in enumerate(p) if i not in drop]
    p = np.array(p)
    return np.vstack([p, p[:1]])


def _smooth(c):
    """Fallback for non-rectilinear buildings: drop wobble, keep the true shape."""
    try:
        s = np.array(LinearRing(c).simplify(SMOOTH_M).coords)
        return s if len(s) >= 4 else c
    except Exception:
        return c


def _cluster(vals, w, tol):
    """Snap near-equal wall coordinates to a common length-weighted value."""
    if len(vals) == 0:
        return vals
    o = np.argsort(vals)
    out = np.array(vals, float)
    grp, start = [], 0
    for i in range(1, len(o) + 1):
        if i == len(o) or vals[o[i]] - vals[o[i - 1]] > tol:
            grp.append(o[start:i])
            start = i
    for g in grp:
        ww = w[g]
        out[g] = (vals[g] * ww).sum() / ww.sum() if ww.sum() > 0 else vals[g].mean()
    return out


def ortho_ring(c, th):
    """Snap edges to the frame defined by th, then rebuild corners by intersection."""
    R = np.array([[np.cos(-th), -np.sin(-th)], [np.sin(-th), np.cos(-th)]])
    f = c @ R.T
    v, L, a = _seg(f)
    k = np.round(a / (np.pi / 2))
    dev = np.abs(a - k * (np.pi / 2))
    axis = dev <= np.radians(TOL_DEG)
    if L.sum() == 0 or (L[axis].sum() / L.sum()) < RECT_FRAC:
        return None                                  # not a rectilinear building

    keep = list(np.where((axis & (L >= MIN_EDGE)) | (~axis & (L >= MIN_FREE)))[0])
    if len(keep) < 4:
        return None

    # Drop traced corner-cuts: a short diagonal wedged between two perpendicular
    # walls is a rasterization bevel, not a real chamfered corner. Long diagonals
    # (genuine angled walls on corner lots) survive.
    for _ in range(3):
        m = len(keep)
        if m <= 4:
            break
        cut = None
        for j, i in enumerate(keep):
            if axis[i] or L[i] > BEVEL_MAX:
                continue
            ip, inx = keep[(j - 1) % m], keep[(j + 1) % m]
            if not (axis[ip] and axis[inx]):
                continue
            if int(k[ip]) % 2 == int(k[inx]) % 2:      # neighbours parallel
                continue
            if L[i] <= BEVEL_REL * min(L[ip], L[inx]):
                cut = j
                break
        if cut is None:
            break
        keep.pop(cut)
    keep = np.array(keep)
    if len(keep) < 4:
        return None

    horiz = (k[keep].astype(int) % 2 == 0) & axis[keep]
    vert  = (k[keep].astype(int) % 2 == 1) & axis[keep]
    mid   = (f[keep] + f[keep + 1]) / 2
    yv, xv = mid[:, 1].copy(), mid[:, 0].copy()
    yv[horiz] = _cluster(yv[horiz], L[keep][horiz], SNAP_M)
    xv[vert]  = _cluster(xv[vert],  L[keep][vert],  SNAP_M)

    lines = []   # (nx, ny, c) with nx*x + ny*y = c
    wts = []
    for j, i in enumerate(keep):
        if horiz[j]:
            lines.append(np.array([0.0, 1.0, yv[j]]))
        elif vert[j]:
            lines.append(np.array([1.0, 0.0, xv[j]]))
        else:
            p, q = f[i], f[i + 1]
            d = q - p
            n = np.array([-d[1], d[0]])
            n /= np.hypot(*n)
            lines.append(np.array([n[0], n[1], n @ p]))
        wts.append(L[i])

    # Collapse consecutive parallel walls into one. Without this, a short step
    # dropped between two same-direction edges leaves a 180 degree joint --
    # the residual "jagged" look.
    par = np.sin(np.radians(8.0))
    while len(lines) > 4:
        m = len(lines)
        hit = None
        for j in range(m):
            a1, a2 = lines[j], lines[(j + 1) % m]
            s = 1.0 if a1[:2] @ a2[:2] >= 0 else -1.0
            if abs(a1[0] * s * a2[1] - s * a2[0] * a1[1]) < par:
                hit = (j, s)
                break
        if hit is None:
            break
        j, s = hit
        w1, w2 = wts[j], wts[(j + 1) % m]
        merged = (w1 * lines[j] + w2 * s * lines[(j + 1) % m]) / (w1 + w2)
        merged[:2] /= np.hypot(*merged[:2])
        lines[j] = merged
        wts[j] = w1 + w2
        lines.pop((j + 1) % m)
        wts.pop((j + 1) % m)
    if len(lines) < 4:
        return None

    pts = []
    m = len(lines)
    for j in range(m):
        a1, b1, c1 = lines[j]
        a2, b2, c2 = lines[(j + 1) % m]
        det = a1 * b2 - a2 * b1
        if abs(det) < 1e-9:                          # still parallel: skip
            continue
        else:
            pts.append(np.array([(c1 * b2 - c2 * b1) / det,
                                 (a1 * c2 - a2 * c1) / det]))
    pts = np.array(pts)
    if len(pts) < 3:
        return None
    Rb = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    out = pts @ Rb.T
    return np.vstack([out, out[:1]])


def regularize(g, want_mode=False):
    """g: shapely (Multi)Polygon in metres -> regularized (Multi)Polygon.

    With want_mode=True returns (geom, mode) where mode is 2 if the largest part
    was orthogonalized, 1 if it was only smoothed, 0 if it was left as traced.
    """
    modes = []
    g = g.buffer(CLOSE_M, join_style=2, mitre_limit=8.0) \
         .buffer(-CLOSE_M, join_style=2, mitre_limit=8.0)
    if g.is_empty:
        return None
    parts = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
    out = []
    for pg in parts:
        if pg.area < 1e-6:
            continue
        ext = despike(np.array(pg.exterior.coords))
        if len(ext) < 4:
            continue
        th = dominant_axis(ext)
        e2 = ortho_ring(ext, th)
        ext_f = e2 if e2 is not None else _smooth(ext)
        ints = []
        for r in pg.interiors:
            ir = despike(np.array(r.coords))
            if len(ir) < 4:
                continue
            i2 = ortho_ring(ir, th)
            ints.append(i2 if i2 is not None else _smooth(ir))
        try:
            cand = _fix(Polygon(ext_f, ints))
            if cand.is_empty or cand.area <= 0 or \
               _symdif(cand, pg) / pg.area > MAX_SYMDIF:
                cand = pg                              # too much drift: keep original
        except Exception:
            cand = pg
        modes.append((pg.area, 0 if cand is pg else (2 if e2 is not None else 1)))
        if cand.geom_type == 'MultiPolygon':
            out.extend([p for p in cand.geoms if p.area > 0])
        elif not cand.is_empty:
            out.append(cand)
    if not out:
        return (None, 0) if want_mode else None
    res = _fix(out[0] if len(out) == 1 else MultiPolygon(out))
    if not want_mode:
        return res
    return res, (max(modes)[1] if modes else 0)
