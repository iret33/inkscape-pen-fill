#!/usr/bin/env python3
"""
penfill_core — pure-geometry engine for the Pen Plotter Fill extension.

This module has NO Inkscape / inkex dependency, so it can be unit-tested
and reused standalone (e.g. by the demo generator). All coordinates are
plain (x, y) tuples in user units; the inkex glue in pen_fill.py handles
SVG parsing, transforms and unit conversion.

Contents
--------
* Region        — a filled area described by flattened rings + a fill rule
                  (even-odd or nonzero). Subpaths stay together, so holes
                  (donuts, letter "O") are respected.
* Hatch family  — parallel lines at any angle, cross-hatch, sine waves,
                  with optional serpentine connection of neighbouring rows
                  into long continuous strokes (fewer pen lifts).
* DistanceField — sampled signed distance to the region boundary
                  (scanline mask + exact near-boundary band + 5x7x11
                  chamfer transform; numpy-accelerated when available).
* Marching squares contour extraction → robust concentric fills that
  survive holes, splits at narrow waists, and merges — where a naive
  polygon inset gives up.
* Continuous spiral linking of concentric rings, true Archimedean spiral
  fill centred on the pole of inaccessibility, Hilbert-curve fill.
* Plot optimizer — greedy nearest-neighbour ordering with endpoint
  reversal and chain merging; reports pen-lift / travel statistics.
* Schneider cubic-Bezier fitting (Graphics Gems 1990) so smooth fills
  are emitted as real `C` curves, not chunky polylines.

Compatible with Python 3.8+ (the oldest interpreter shipped by any
supported Inkscape 1.x). numpy is optional but used when present —
Inkscape always bundles it.
"""

import math

try:
    import numpy as _np
except ImportError:          # pragma: no cover - numpy ships with Inkscape
    _np = None

__version__ = "2.0.0"

EPS = 1e-9
INF = float("inf")

# Chamfer 5x5 weights (Borgefors-style optimized real weights, unit grid).
# Max radial error ~1.4% — invisible after contour smoothing, and isotropic
# enough that deep concentric rings stay round instead of going octagonal.
_CH_A = 0.9866   # orthogonal step
_CH_B = 1.4141   # diagonal step
_CH_C = 2.2062   # knight's-move step


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def polyline_length(pts):
    total = 0.0
    for i in range(len(pts) - 1):
        total += _dist(pts[i], pts[i + 1])
    return total


def signed_area(poly):
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def rotate_points(pts, angle_rad):
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def shorten_polyline(pts, trim_start, trim_end):
    """Cut `trim_start` / `trim_end` of arc length off the ends of an open
    polyline. Returns [] if nothing is left."""
    if trim_start <= 0 and trim_end <= 0:
        return list(pts)
    total = polyline_length(pts)
    if total <= trim_start + trim_end + EPS:
        return []

    def walk(points, trim):
        if trim <= 0:
            return list(points)
        out = []
        remaining = trim
        i = 0
        while i < len(points) - 1:
            seg = _dist(points[i], points[i + 1])
            if seg > remaining:
                t = remaining / seg
                x = points[i][0] + (points[i + 1][0] - points[i][0]) * t
                y = points[i][1] + (points[i + 1][1] - points[i][1]) * t
                out.append((x, y))
                out.extend(points[i + 1:])
                return out
            remaining -= seg
            i += 1
        return []

    pts = walk(pts, trim_start)
    if not pts:
        return []
    pts = walk(list(reversed(pts)), trim_end)
    return list(reversed(pts)) if pts else []


def _resample_dense(pts, closed, max_step):
    """Insert points so no segment is longer than max_step."""
    if len(pts) < 2:
        return list(pts)
    src = list(pts) + ([pts[0]] if closed else [])
    out = [src[0]]
    for i in range(len(src) - 1):
        a, b = src[i], src[i + 1]
        seg = _dist(a, b)
        if seg > max_step:
            n = int(math.ceil(seg / max_step))
            for k in range(1, n):
                t = k / n
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    if closed:
        out.pop()
    return out


def simplify_polyline(pts, tol, closed=False):
    """Iterative Douglas-Peucker (stack based, no recursion limit)."""
    if tol <= 0 or len(pts) < 3:
        return list(pts)
    if closed:
        # anchor at two far-apart points to keep the loop shape
        far = max(range(len(pts)), key=lambda i: _dist(pts[0], pts[i]))
        if far == 0:
            return list(pts)
        a = simplify_polyline(pts[: far + 1], tol, False)
        b = simplify_polyline(pts[far:] + [pts[0]], tol, False)
        return a[:-1] + b[:-1]

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        ax, ay = pts[i0]
        bx, by = pts[i1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        worst, worst_d2 = -1, tol * tol
        for i in range(i0 + 1, i1):
            px, py = pts[i][0] - ax, pts[i][1] - ay
            if seg2 < EPS:
                d2 = px * px + py * py
            else:
                t = (px * dx + py * dy) / seg2
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                ex, ey = px - t * dx, py - t * dy
                d2 = ex * ex + ey * ey
            if d2 > worst_d2:
                worst_d2 = d2
                worst = i
        if worst >= 0:
            keep[worst] = True
            stack.append((i0, worst))
            stack.append((worst, i1))
    return [p for p, k in zip(pts, keep) if k]


# ----------------------------------------------------------------------
# Region — polygons + fill rule
# ----------------------------------------------------------------------

class Region:
    """A filled area: a list of flattened closed rings plus a fill rule.

    Rings keep their original orientation; insideness is decided by the
    fill rule (SVG semantics), so holes work exactly like they render.
    """

    def __init__(self, rings, fill_rule="evenodd"):
        self.rings = [list(r) for r in rings if len(r) >= 3]
        self.fill_rule = "nonzero" if fill_rule == "nonzero" else "evenodd"
        xs = [p[0] for r in self.rings for p in r]
        ys = [p[1] for r in self.rings for p in r]
        if xs:
            self.xmin, self.xmax = min(xs), max(xs)
            self.ymin, self.ymax = min(ys), max(ys)
        else:
            self.xmin = self.xmax = self.ymin = self.ymax = 0.0

    def is_empty(self):
        return not self.rings

    def rotated(self, angle_rad):
        return Region([rotate_points(r, angle_rad) for r in self.rings],
                      self.fill_rule)

    # -- scanline machinery ------------------------------------------------

    def crossings(self, y):
        """All edge crossings of the horizontal line at `y`, as a list of
        (x, direction) with direction +1 for upward edges, -1 for downward.
        Uses the half-open rule [ymin, ymax) so vertices count once."""
        out = []
        for ring in self.rings:
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]
                x2, y2 = ring[(i + 1) % n]
                if y1 == y2:
                    continue
                if (y1 <= y < y2):
                    t = (y - y1) / (y2 - y1)
                    out.append((x1 + t * (x2 - x1), 1))
                elif (y2 <= y < y1):
                    t = (y - y1) / (y2 - y1)
                    out.append((x1 + t * (x2 - x1), -1))
        out.sort(key=lambda c: c[0])
        return out

    def intervals(self, y):
        """Inside intervals [(x0, x1), ...] along the horizontal at `y`."""
        cr = self.crossings(y)
        out = []
        if self.fill_rule == "evenodd":
            for k in range(0, len(cr) - 1, 2):
                out.append((cr[k][0], cr[k + 1][0]))
        else:
            w = 0
            start = 0.0
            for x, d in cr:
                prev = w
                w += d
                if prev == 0 and w != 0:
                    start = x
                elif prev != 0 and w == 0:
                    out.append((start, x))
        return [(a, b) for a, b in out if b - a > EPS]

    def contains(self, x, y):
        cr = self.crossings(y)
        if self.fill_rule == "evenodd":
            cnt = sum(1 for cx, _ in cr if cx > x)
            return cnt % 2 == 1
        w = sum(d for cx, d in cr if cx > x)
        return w != 0

    def segment_inside(self, a, b, samples=5):
        """True if the open segment a-b stays inside (sampled test)."""
        for k in range(1, samples + 1):
            t = k / (samples + 1)
            if not self.contains(a[0] + (b[0] - a[0]) * t,
                                 a[1] + (b[1] - a[1]) * t):
                return False
        return True


# ----------------------------------------------------------------------
# Generic polyline clipping against an inside-predicate
# ----------------------------------------------------------------------

def clip_polyline(pts, inside_fn, refine_iters=20):
    """Split a dense polyline into the runs that satisfy `inside_fn`.
    Boundary crossings are refined by bisection between samples."""

    def crossing(p_in, p_out):
        a, b = p_in, p_out
        for _ in range(refine_iters):
            m = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            if inside_fn(m[0], m[1]):
                a = m
            else:
                b = m
        return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)

    runs = []
    cur = []
    prev = None
    prev_in = False
    for p in pts:
        p_in = inside_fn(p[0], p[1])
        if p_in:
            if not prev_in and prev is not None:
                cur.append(crossing(p, prev))
            cur.append(p)
        else:
            if prev_in and prev is not None:
                cur.append(crossing(prev, p))
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
        prev, prev_in = p, p_in
    if len(cur) >= 2:
        runs.append(cur)
    return runs


# ----------------------------------------------------------------------
# Hatch family
# ----------------------------------------------------------------------

def _scanline_ys(ymin, ymax, spacing):
    """Scanline positions with a tiny irrational phase nudge so lines never
    align exactly with horizontal geometry (keeps parity counts stable)."""
    nudge = spacing * 0.017632
    y = ymin + spacing * 0.5 + nudge
    out = []
    while y < ymax:
        out.append(y)
        y += spacing
    return out


def _serpentine_connect(rows, region, max_bridge):
    """Connect per-row polylines into serpentine chains.

    `rows` is a list (in scan order) of lists of open polylines whose
    endpoints sit on that row. Consecutive rows are joined when a straight
    bridge between endpoints is short enough and stays inside the region.
    Returns a flat list of polylines.
    """
    unused = [[True] * len(row) for row in rows]
    chains = []

    for i0 in range(len(rows)):
        for j0 in range(len(rows[i0])):
            if not unused[i0][j0]:
                continue
            unused[i0][j0] = False
            chain = list(rows[i0][j0])
            row = i0 + 1
            while row < len(rows):
                end = chain[-1]
                best = None      # (dist, idx, reversed)
                for j, ok in enumerate(unused[row]):
                    if not ok:
                        continue
                    seg = rows[row][j]
                    d_start = _dist(end, seg[0])
                    d_end = _dist(end, seg[-1])
                    if d_start <= d_end:
                        cand = (d_start, j, False)
                    else:
                        cand = (d_end, j, True)
                    if cand[0] <= max_bridge and (best is None or cand[0] < best[0]):
                        best = cand
                if best is None:
                    break
                _, j, rev = best
                seg = rows[row][j]
                entry = seg[-1] if rev else seg[0]
                if not region.segment_inside(end, entry):
                    break
                unused[row][j] = False
                chain.extend(reversed(seg) if rev else seg)
                row += 1
            chains.append(chain)

    return chains


def hatch_fill(region, spacing, angle_deg, edge_gap=0.0,
               connect=False, connect_factor=3.0, min_len=1e-4):
    """Parallel straight-line fill.

    spacing   — distance between lines (user units)
    angle_deg — line direction, 0 = horizontal
    edge_gap  — shorten each line end by this much (keeps ink inside)
    connect   — serpentine-join neighbouring lines into long chains
    Returns list of open polylines in world coordinates.
    """
    if region.is_empty() or spacing <= 0:
        return []
    theta = math.radians(angle_deg)
    rot = region.rotated(-theta)

    rows = []
    for y in _scanline_ys(rot.ymin, rot.ymax, spacing):
        row = []
        for x0, x1 in rot.intervals(y):
            x0 += edge_gap
            x1 -= edge_gap
            if x1 - x0 >= max(min_len, EPS):
                row.append([(x0, y), (x1, y)])
        rows.append(row)

    if connect:
        chains = _serpentine_connect(rows, rot, spacing * connect_factor)
    else:
        chains = [seg for row in rows for seg in row]

    return [rotate_points(c, theta) for c in chains]


def cross_hatch_fill(region, spacing, angle_deg, cross_angle_deg=90.0,
                     edge_gap=0.0, connect=False):
    """Two hatch passes: angle and angle+cross_angle."""
    first = hatch_fill(region, spacing, angle_deg, edge_gap, connect)
    second = hatch_fill(region, spacing, angle_deg + cross_angle_deg,
                        edge_gap, connect)
    return first + second


def sine_fill(region, spacing, angle_deg, amplitude, wavelength,
              edge_gap=0.0, connect=False, connect_factor=3.0):
    """Sine-wave hatch: each scan row is a sine stroke clipped to the shape.
    Rows alternate phase by half a period so neighbouring waves interlock."""
    if region.is_empty() or spacing <= 0 or wavelength <= 0:
        return []
    theta = math.radians(angle_deg)
    rot = region.rotated(-theta)
    inside = rot.contains

    sample = max(wavelength / 24.0, 1e-3)
    x_start = rot.xmin - wavelength
    x_end = rot.xmax + wavelength
    n = max(2, int(math.ceil((x_end - x_start) / sample)))

    rows = []
    for i, y in enumerate(_scanline_ys(rot.ymin - amplitude,
                                       rot.ymax + amplitude, spacing)):
        phase = math.pi * (i % 2)
        wave = []
        for k in range(n + 1):
            x = x_start + (x_end - x_start) * k / n
            wave.append((x, y + amplitude *
                         math.sin(2 * math.pi * x / wavelength + phase)))
        runs = clip_polyline(wave, inside)
        row = []
        for run in runs:
            run = shorten_polyline(run, edge_gap, edge_gap)
            if len(run) >= 2:
                row.append(run)
        rows.append(row)

    if connect:
        # wave crests swing endpoints up to ±amplitude, so allow for it
        chains = _serpentine_connect(
            rows, rot, spacing * connect_factor + 2 * amplitude)
    else:
        chains = [r for row in rows for r in row]

    return [rotate_points(c, theta) for c in chains]


# ----------------------------------------------------------------------
# Distance field
# ----------------------------------------------------------------------

class DistanceField:
    """Sampled signed distance to the region boundary (inside positive).

    Built from a scanline inside-mask, an exact distance band along the
    boundary (point-to-edge distances), and a two-sweep 5x5 chamfer
    transform to propagate the band across the grid.
    """

    def __init__(self, region, cell, x0, y0, nx, ny, values, coarsened):
        self.region = region
        self.cell = cell
        self.x0 = x0
        self.y0 = y0
        self.nx = nx
        self.ny = ny
        self.values = values          # row-major list/ndarray, len nx*ny
        self.coarsened = coarsened

    # -- construction ------------------------------------------------------

    @classmethod
    def build(cls, region, cell, max_cells=None):
        if max_cells is None:
            max_cells = 4_000_000 if _np is not None else 700_000
        margin_cells = 3
        w = region.xmax - region.xmin
        h = region.ymax - region.ymin
        if w <= 0 or h <= 0:
            raise ValueError("empty region")

        coarsened = False
        need = ((w / cell) + 2 * margin_cells) * ((h / cell) + 2 * margin_cells)
        if need > max_cells:
            cell = cell * math.sqrt(need / max_cells) * 1.01
            coarsened = True

        x0 = region.xmin - margin_cells * cell
        y0 = region.ymin - margin_cells * cell
        nx = int(math.ceil(w / cell)) + 2 * margin_cells + 1
        ny = int(math.ceil(h / cell)) + 2 * margin_cells + 1

        mask = cls._build_mask(region, x0, y0, nx, ny, cell)
        seed = cls._build_band(region, x0, y0, nx, ny, cell)
        dist = cls._chamfer(seed, nx, ny, cell)

        if _np is not None:
            values = _np.where(mask, dist, -dist)
        else:
            values = [d if m else -d for m, d in zip(mask, dist)]
        return cls(region, cell, x0, y0, nx, ny, values, coarsened)

    @staticmethod
    def _build_mask(region, x0, y0, nx, ny, cell):
        if _np is not None:
            mask = _np.zeros(nx * ny, dtype=bool)
        else:
            mask = [False] * (nx * ny)
        for j in range(ny):
            y = y0 + j * cell
            base = j * nx
            for xa, xb in region.intervals(y):
                ia = int(math.ceil((xa - x0) / cell))
                ib = int(math.floor((xb - x0) / cell))
                if ia < 0:
                    ia = 0
                if ib > nx - 1:
                    ib = nx - 1
                if ib >= ia:
                    if _np is not None:
                        mask[base + ia: base + ib + 1] = True
                    else:
                        for i in range(ia, ib + 1):
                            mask[base + i] = True
        return mask

    @staticmethod
    def _build_band(region, x0, y0, nx, ny, cell):
        """Exact point-to-edge distances for cells within ~2 cells of the
        boundary; INF elsewhere."""
        if _np is not None:
            seed = _np.full(nx * ny, INF)
        else:
            seed = [INF] * (nx * ny)
        reach = 2
        for ring in region.rings:
            n = len(ring)
            for k in range(n):
                ax, ay = ring[k]
                bx, by = ring[(k + 1) % n]
                i_lo = int((min(ax, bx) - x0) / cell) - reach
                i_hi = int((max(ax, bx) - x0) / cell) + reach
                j_lo = int((min(ay, by) - y0) / cell) - reach
                j_hi = int((max(ay, by) - y0) / cell) + reach
                if i_lo < 0:
                    i_lo = 0
                if j_lo < 0:
                    j_lo = 0
                if i_hi > nx - 1:
                    i_hi = nx - 1
                if j_hi > ny - 1:
                    j_hi = ny - 1
                ex, ey = bx - ax, by - ay
                ee = ex * ex + ey * ey
                for j in range(j_lo, j_hi + 1):
                    py = y0 + j * cell
                    base = j * nx
                    for i in range(i_lo, i_hi + 1):
                        px = x0 + i * cell
                        if ee < EPS:
                            dx, dy = px - ax, py - ay
                        else:
                            t = ((px - ax) * ex + (py - ay) * ey) / ee
                            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                            dx = px - (ax + t * ex)
                            dy = py - (ay + t * ey)
                        d = math.hypot(dx, dy)
                        idx = base + i
                        if d < seed[idx]:
                            seed[idx] = d
        return seed

    # -- chamfer propagation ----------------------------------------------

    @staticmethod
    def _chamfer(seed, nx, ny, cell):
        a = _CH_A * cell
        b = _CH_B * cell
        c = _CH_C * cell
        if _np is not None:
            return DistanceField._chamfer_np(seed, nx, ny, a, b, c)
        return DistanceField._chamfer_py(seed, nx, ny, a, b, c)

    @staticmethod
    def _chamfer_np(seed, nx, ny, a, b, c):
        d = seed.reshape(ny, nx).copy()
        idx = _np.arange(nx, dtype=float)

        def scan_row(row):
            # in-row propagation both directions via the cummin trick:
            # min_k<=i (v_k + (i-k)*a)  ==  i*a + cummin(v_k - k*a)
            t = _np.minimum.accumulate(row - idx * a) + idx * a
            u = _np.minimum.accumulate((row - (nx - 1 - idx) * a)[::-1])[::-1] \
                + (nx - 1 - idx) * a
            return _np.minimum(t, u)

        def shift(row, k, fill):
            out = _np.full_like(row, fill)
            if k > 0:
                out[k:] = row[:-k]
            elif k < 0:
                out[:k] = row[-k:]
            else:
                out[:] = row
            return out

        for sweep in (1, -1):
            rows = range(ny) if sweep == 1 else range(ny - 1, -1, -1)
            for j in rows:
                row = d[j]
                j1 = j - sweep
                j2 = j - 2 * sweep
                if 0 <= j1 < ny:
                    p = d[j1]
                    row = _np.minimum(row, p + a)
                    row = _np.minimum(row, shift(p, 1, INF) + b)
                    row = _np.minimum(row, shift(p, -1, INF) + b)
                    row = _np.minimum(row, shift(p, 2, INF) + c)
                    row = _np.minimum(row, shift(p, -2, INF) + c)
                if 0 <= j2 < ny:
                    p2 = d[j2]
                    row = _np.minimum(row, shift(p2, 1, INF) + c)
                    row = _np.minimum(row, shift(p2, -1, INF) + c)
                d[j] = scan_row(row)
        return d.reshape(nx * ny)

    @staticmethod
    def _chamfer_py(seed, nx, ny, a, b, c):
        d = list(seed)

        def scan_row(base):
            run = INF
            for i in range(nx):
                v = d[base + i]
                run = v if v < run else run
                if run < v:
                    d[base + i] = run
                run += a
            run = INF
            for i in range(nx - 1, -1, -1):
                v = d[base + i]
                run = v if v < run else run
                if run < v:
                    d[base + i] = run
                run += a

        for sweep in (1, -1):
            rows = range(ny) if sweep == 1 else range(ny - 1, -1, -1)
            for j in rows:
                base = j * nx
                j1 = j - sweep
                j2 = j - 2 * sweep
                if 0 <= j1 < ny:
                    b1 = j1 * nx
                    for i in range(nx):
                        best = d[b1 + i] + a
                        if i > 0:
                            v = d[b1 + i - 1] + b
                            if v < best:
                                best = v
                            if i > 1:
                                v = d[b1 + i - 2] + c
                                if v < best:
                                    best = v
                        if i < nx - 1:
                            v = d[b1 + i + 1] + b
                            if v < best:
                                best = v
                            if i < nx - 2:
                                v = d[b1 + i + 2] + c
                                if v < best:
                                    best = v
                        if best < d[base + i]:
                            d[base + i] = best
                if 0 <= j2 < ny:
                    b2 = j2 * nx
                    for i in range(nx):
                        best = d[base + i]
                        if i > 0:
                            v = d[b2 + i - 1] + c
                            if v < best:
                                best = v
                        if i < nx - 1:
                            v = d[b2 + i + 1] + c
                            if v < best:
                                best = v
                        d[base + i] = best
                scan_row(base)
        return d

    # -- queries -----------------------------------------------------------

    def value(self, i, j):
        return self.values[j * self.nx + i]

    def sample(self, x, y):
        """Bilinear interpolation; far outside the grid returns -INF-ish."""
        fx = (x - self.x0) / self.cell
        fy = (y - self.y0) / self.cell
        i = int(math.floor(fx))
        j = int(math.floor(fy))
        if i < 0 or j < 0 or i >= self.nx - 1 or j >= self.ny - 1:
            return -1e30
        tx = fx - i
        ty = fy - j
        v00 = self.value(i, j)
        v10 = self.value(i + 1, j)
        v01 = self.value(i, j + 1)
        v11 = self.value(i + 1, j + 1)
        return (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
                + v01 * (1 - tx) * ty + v11 * tx * ty)

    def max_point(self):
        """Deepest interior point (pole of inaccessibility) and its depth."""
        if _np is not None:
            k = int(_np.argmax(self.values))
            best = float(self.values[k])
        else:
            k = max(range(len(self.values)), key=lambda t: self.values[t])
            best = self.values[k]
        j, i = divmod(k, self.nx)
        return (self.x0 + i * self.cell, self.y0 + j * self.cell, best)

    def max_value(self):
        return self.max_point()[2]


# ----------------------------------------------------------------------
# Marching squares
# ----------------------------------------------------------------------

def _cells_crossing(df, level_values):
    """For each level, the grid cells its contour passes through.
    One classification pass over the grid instead of one per level —
    this is what makes dense concentric fills fast."""
    nx, ny = df.nx, df.ny
    buckets = [[] for _ in level_values]
    if _np is not None:
        v = _np.asarray(df.values, dtype=float).reshape(ny, nx)
        c00 = v[:-1, :-1]
        c10 = v[:-1, 1:]
        c01 = v[1:, :-1]
        c11 = v[1:, 1:]
        cmin = _np.minimum(_np.minimum(c00, c10), _np.minimum(c01, c11))
        cmax = _np.maximum(_np.maximum(c00, c10), _np.maximum(c01, c11))
        lv = _np.asarray(level_values, dtype=float)
        lo = _np.searchsorted(lv, cmin, side="right")
        hi = _np.searchsorted(lv, cmax, side="right")
        js, is_ = _np.nonzero(hi > lo)
        los = lo[js, is_].tolist()
        his = hi[js, is_].tolist()
        for j, i, l0, l1 in zip(js.tolist(), is_.tolist(), los, his):
            for k in range(l0, l1):
                buckets[k].append((i, j))
    else:
        import bisect as _bisect
        vals = df.values
        for j in range(ny - 1):
            base = j * nx
            for i in range(nx - 1):
                v00 = vals[base + i]
                v10 = vals[base + i + 1]
                v01 = vals[base + nx + i]
                v11 = vals[base + nx + i + 1]
                cmin = min(v00, v10, v01, v11)
                cmax = max(v00, v10, v01, v11)
                l0 = _bisect.bisect_right(level_values, cmin)
                l1 = _bisect.bisect_right(level_values, cmax)
                for k in range(l0, l1):
                    buckets[k].append((i, j))
    return buckets


def marching_squares(df, level, cells=None):
    """Extract iso-contours `field == level` as closed loops.

    Returns a list of loops (each a list of points, implicitly closed).
    Segment endpoints are keyed by grid edge, so loops stitch exactly.
    `cells` optionally restricts processing to a pre-computed list of
    (i, j) cells the contour crosses (see _cells_crossing).
    """
    nx, ny = df.nx, df.ny
    vals = df.values
    cell = df.cell
    x0, y0 = df.x0, df.y0

    # edge key: (j * nx + i) * 2 + orient   orient 0: (i,j)-(i+1,j)
    #                                       orient 1: (i,j)-(i,j+1)
    pts = {}

    def edge_point(i, j, orient, va, vb):
        key = (j * nx + i) * 2 + orient
        p = pts.get(key)
        if p is None:
            t = (level - va) / (vb - va)
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            if orient == 0:
                p = (x0 + (i + t) * cell, y0 + j * cell)
            else:
                p = (x0 + i * cell, y0 + (j + t) * cell)
            pts[key] = p
        return key

    if cells is None:
        cells = ((i, j) for j in range(ny - 1) for i in range(nx - 1))

    segs = []
    for i, j in cells:
        base = j * nx
        v00 = vals[base + i]
        v10 = vals[base + i + 1]
        v01 = vals[base + nx + i]
        v11 = vals[base + nx + i + 1]
        case = ((1 if v00 >= level else 0)
                | (2 if v10 >= level else 0)
                | (4 if v11 >= level else 0)
                | (8 if v01 >= level else 0))
        if case == 0 or case == 15:
            continue

        top = lambda: edge_point(i, j, 0, v00, v10)          # noqa: E731
        bottom = lambda: edge_point(i, j + 1, 0, v01, v11)   # noqa: E731
        left = lambda: edge_point(i, j, 1, v00, v01)         # noqa: E731
        right = lambda: edge_point(i + 1, j, 1, v10, v11)    # noqa: E731

        if case == 1:
            segs.append((top(), left()))
        elif case == 2:
            segs.append((right(), top()))
        elif case == 3:
            segs.append((right(), left()))
        elif case == 4:
            segs.append((bottom(), right()))
        elif case == 5:
            # saddle — disambiguate with centre sample
            centre = (v00 + v10 + v01 + v11) * 0.25
            if centre >= level:
                segs.append((bottom(), left()))
                segs.append((top(), right()))
            else:
                segs.append((top(), left()))
                segs.append((bottom(), right()))
        elif case == 6:
            segs.append((bottom(), top()))
        elif case == 7:
            segs.append((bottom(), left()))
        elif case == 8:
            segs.append((left(), bottom()))
        elif case == 9:
            segs.append((top(), bottom()))
        elif case == 10:
            centre = (v00 + v10 + v01 + v11) * 0.25
            if centre >= level:
                segs.append((left(), top()))
                segs.append((right(), bottom()))
            else:
                segs.append((left(), bottom()))
                segs.append((right(), top()))
        elif case == 11:
            segs.append((right(), bottom()))
        elif case == 12:
            segs.append((left(), right()))
        elif case == 13:
            segs.append((top(), right()))
        elif case == 14:
            segs.append((left(), top()))

    # stitch segments into loops (undirected walk; every contour key has
    # exactly two incident segments, so cycles are unambiguous)
    adj = {}
    for a, b in segs:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    loops = []
    visited = set()
    for start, nbrs in adj.items():
        if start in visited or len(nbrs) != 2:
            continue
        loop_keys = [start]
        visited.add(start)
        prev, cur = start, nbrs[0]
        ok = True
        while cur != start:
            loop_keys.append(cur)
            visited.add(cur)
            nb = adj.get(cur)
            if not nb or len(nb) != 2:
                ok = False
                break
            step = nb[0] if nb[1] == prev else (nb[1] if nb[0] == prev else None)
            if step is None:
                ok = False
                break
            prev, cur = cur, step
        if ok and len(loop_keys) >= 3:
            loops.append([pts[k] for k in loop_keys])
    return loops


# ----------------------------------------------------------------------
# Concentric / spiral fills on top of the distance field
# ----------------------------------------------------------------------

def _ring_perimeter(loop):
    return polyline_length(loop) + _dist(loop[-1], loop[0])


def concentric_rings(df, spacing, start, min_perimeter=0.0, simplify_tol=None):
    """All iso-rings at levels start, start+spacing, ... grouped per level.
    Returns list of levels, each a list of loops.

    Near the medial axis the iso-band gets thinner than a grid cell and
    marching squares shatters into confetti; we detect that (raw loop
    count exploding) and stop, then bisect the leftover gap with one
    final level so the unfilled sliver stays under a pen width."""
    dmax = df.max_value()
    if simplify_tol is None:
        # keep enough vertices that the later Bezier fit is well-sampled
        simplify_tol = df.cell * 0.2

    def extract(level, cells):
        raw = marching_squares(df, level, cells)
        loops = []
        for loop in raw:
            if _ring_perimeter(loop) < min_perimeter:
                continue
            loop = simplify_polyline(loop, simplify_tol, closed=True)
            if len(loop) >= 3:
                loops.append(loop)
        return len(raw), loops

    level_values = []
    level = start
    while level <= dmax - df.cell * 0.4:
        level_values.append(level)
        level += spacing
    if not level_values:
        return []
    buckets = _cells_crossing(df, level_values)

    levels = []
    prev_raw = None
    last_level = None
    for level, cells in zip(level_values, buckets):
        raw_count, loops = extract(level, cells)
        if prev_raw is not None and raw_count > prev_raw * 3 + 2:
            break                      # sub-cell band shattered — stop here
        if loops:
            levels.append(loops)
            prev_raw = raw_count
            last_level = level
        elif levels:
            break

    # one gap-filling pass halfway between the last ring and the ridge
    if last_level is not None and dmax - last_level > spacing * 0.75:
        mid = (last_level + dmax) / 2.0
        cells = _cells_crossing(df, [mid])[0]
        raw_count, loops = extract(mid, cells)
        if loops and raw_count <= (prev_raw or 1) * 3 + 2:
            levels.append(loops)

    return levels


def _point_in_poly(poly, x, y):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 <= y < y2) or (y2 <= y < y1):
            t = (y - y1) / (y2 - y1)
            if x1 + t * (x2 - x1) > x:
                inside = not inside
    return inside


def link_rings_spiral(levels, region):
    """Link nested concentric rings into continuous spiral chains.

    Rings at level k+1 attach to the ring at level k that contains them.
    The first child continues the parent's chain via a short bridge; other
    children (splits at narrow waists) start fresh chains.
    Returns a list of open polylines.
    """
    if not levels:
        return []

    children = [[[] for _ in lvl] for lvl in levels]
    for k in range(1, len(levels)):
        for ci, child in enumerate(levels[k]):
            cx, cy = child[0]
            parent = None
            for pi, cand in enumerate(levels[k - 1]):
                if _point_in_poly(cand, cx, cy):
                    parent = pi
                    break
            if parent is not None:
                children[k - 1][parent].append(ci)

    chains = []
    visited = [[False] * len(lvl) for lvl in levels]
    seeds = [(0, i) for i in range(len(levels[0]) - 1, -1, -1)]

    while seeds:
        k, idx = seeds.pop()
        if visited[k][idx]:
            continue
        chain = []
        entry = levels[k][idx][0]
        # descend outer→inner, always continuing into the first unvisited
        # child; siblings become fresh seeds (their own chains)
        while True:
            visited[k][idx] = True
            ring = levels[k][idx]
            s = min(range(len(ring)),
                    key=lambda t: (ring[t][0] - entry[0]) ** 2
                                + (ring[t][1] - entry[1]) ** 2)
            walk_pts = ring[s:] + ring[:s]
            walk_pts.append(walk_pts[0])
            if chain and not region.segment_inside(chain[-1], walk_pts[0], 3):
                chains.append(chain)
                chain = []
            chain.extend(walk_pts)
            kids = []
            if k + 1 < len(levels):
                kids = [ci for ci in children[k][idx]
                        if not visited[k + 1][ci]]
            if not kids:
                break
            # continue into the child nearest to where the pen is now;
            # the rest become fresh chains (splits at narrow waists)
            end = chain[-1]
            kids.sort(key=lambda ci: (levels[k + 1][ci][0][0] - end[0]) ** 2
                                   + (levels[k + 1][ci][0][1] - end[1]) ** 2)
            for ci in kids[1:]:
                seeds.append((k + 1, ci))
            entry = end
            k, idx = k + 1, kids[0]
        if chain:
            chains.append(chain)

    return chains


def archimedean_spiral_fill(df, spacing, edge_gap):
    """A single Archimedean spiral centred on the pole of inaccessibility,
    clipped to the region eroded by `edge_gap`. Concave shapes yield
    multiple arcs; the optimizer orders them."""
    cx, cy, dmax = df.max_point()
    if dmax <= edge_gap:
        return []
    region = df.region
    r_need = 0.0
    for corner in ((region.xmin, region.ymin), (region.xmax, region.ymin),
                   (region.xmin, region.ymax), (region.xmax, region.ymax)):
        r_need = max(r_need, _dist((cx, cy), corner))

    a = spacing / (2 * math.pi)
    ds = max(spacing / 2.0, 0.02)
    pts = [(cx, cy)]
    theta = 0.0
    r = 0.0
    while r <= r_need:
        r = a * theta
        pts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
        theta += ds / max(r, ds)

    gap = edge_gap
    return clip_polyline(pts, lambda x, y: df.sample(x, y) >= gap)


def _hilbert_d2xy(order, d):
    """Map index d to (x, y) on a 2^order x 2^order Hilbert curve."""
    rx = ry = 0
    x = y = 0
    t = d
    s = 1
    n = 1 << order
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


def hilbert_fill(df, spacing, edge_gap, max_points=600_000):
    """Hilbert space-filling curve clipped to the eroded region."""
    region = df.region
    w = region.xmax - region.xmin
    h = region.ymax - region.ymin
    side = max(w, h)
    if side <= 0 or spacing <= 0:
        return []
    order = max(1, int(math.ceil(math.log(side / spacing, 2))))
    while (1 << (2 * order)) > max_points and order > 1:
        order -= 1
    n = 1 << order
    step = side / (n - 1) if n > 1 else side
    ox = region.xmin + (w - side) / 2.0
    oy = region.ymin + (h - side) / 2.0

    pts = []
    for d in range(n * n):
        hx, hy = _hilbert_d2xy(order, d)
        pts.append((ox + hx * step, oy + hy * step))

    gap = edge_gap
    return clip_polyline(pts, lambda x, y: df.sample(x, y) >= gap)


def eroded_region(region, depth, cell_hint, max_cells=None):
    """Region shrunk inward by `depth` (a true lateral inset, computed as
    the distance-field iso-contour). Returns None when nothing is left,
    or the original region if the field cannot be built."""
    cell = max(min(cell_hint, depth * 0.9), 1e-3)
    try:
        df = DistanceField.build(region, cell, max_cells)
    except ValueError:
        return region
    cells = _cells_crossing(df, [depth])[0]
    loops = []
    for loop in marching_squares(df, depth, cells):
        loop = simplify_polyline(loop, df.cell * 0.3, closed=True)
        if len(loop) >= 3:
            loops.append(loop)
    if not loops:
        return None
    return Region(loops, "evenodd")


# ----------------------------------------------------------------------
# Plot-order optimizer
# ----------------------------------------------------------------------

def path_stats(chains):
    """(draw_length, travel_length, pen_lifts) for chains plotted in order."""
    draw = 0.0
    travel = 0.0
    for i, ch in enumerate(chains):
        draw += polyline_length(ch)
        if i > 0:
            travel += _dist(chains[i - 1][-1], ch[0])
    return draw, travel, len(chains)


class _EndpointIndex:
    """Spatial hash over chain endpoints for nearest-neighbour queries."""

    def __init__(self, chains, bucket):
        self.bucket = max(bucket, EPS)
        self.grid = {}
        self.alive = [True] * len(chains)
        self.ends = []
        for idx, ch in enumerate(chains):
            for end in (0, 1):
                p = ch[0] if end == 0 else ch[-1]
                self.ends.append((idx, end, p))
                key = self._key(p)
                self.grid.setdefault(key, []).append(len(self.ends) - 1)

    def _key(self, p):
        return (int(math.floor(p[0] / self.bucket)),
                int(math.floor(p[1] / self.bucket)))

    def kill(self, idx):
        self.alive[idx] = False

    def nearest(self, p):
        """Nearest endpoint of any living chain. Returns (idx, end, pt)."""
        kx, ky = self._key(p)
        best = None
        best_d2 = INF
        for radius in range(0, 64):
            if best is not None and (radius - 1) * self.bucket > math.sqrt(best_d2):
                break
            found_any = False
            for gx in range(kx - radius, kx + radius + 1):
                for gy in range(ky - radius, ky + radius + 1):
                    if max(abs(gx - kx), abs(gy - ky)) != radius:
                        continue
                    for ei in self.grid.get((gx, gy), ()):
                        idx, end, q = self.ends[ei]
                        if not self.alive[idx]:
                            continue
                        found_any = True
                        d2 = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
                        if d2 < best_d2:
                            best_d2 = d2
                            best = (idx, end, q)
            if radius > 40 and not found_any and best is None:
                # sparse fallback: brute force the stragglers
                for idx, ch in enumerate(self._chains_ref):
                    if not self.alive[idx]:
                        continue
                    for end in (0, 1):
                        q = ch[0] if end == 0 else ch[-1]
                        d2 = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
                        if d2 < best_d2:
                            best_d2 = d2
                            best = (idx, end, q)
                return best
        return best


def optimize_order(chains, join_tol=0.0):
    """Greedy nearest-neighbour re-ordering (with reversal), optionally
    merging chains whose gap is <= join_tol into one stroke.

    Returns (new_chains, stats_before, stats_after)."""
    chains = [list(c) for c in chains if len(c) >= 2]
    before = path_stats(chains)
    if len(chains) <= 1:
        return chains, before, before

    diag = 0.0
    xs = [p[0] for c in chains for p in (c[0], c[-1])]
    ys = [p[1] for c in chains for p in (c[0], c[-1])]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    index = _EndpointIndex(chains, diag / 48.0 if diag > 0 else 1.0)
    index._chains_ref = chains

    # start from the chain whose endpoint is closest to the top-left
    start_corner = (min(xs), min(ys))
    first = index.nearest(start_corner)
    ordered = []
    idx, end, _ = first
    cur = chains[idx] if end == 0 else list(reversed(chains[idx]))
    index.kill(idx)
    ordered.append(cur)

    for _ in range(len(chains) - 1):
        tail = ordered[-1][-1]
        found = index.nearest(tail)
        if found is None:
            break
        idx, end, q = found
        index.kill(idx)
        nxt = chains[idx] if end == 0 else list(reversed(chains[idx]))
        gap = _dist(tail, nxt[0])
        if join_tol > 0 and gap <= join_tol:
            # continue the same stroke: bridge (if any gap) then the chain
            if gap > EPS:
                ordered[-1].append(nxt[0])
            ordered[-1].extend(nxt[1:])
        else:
            ordered.append(nxt)

    after = path_stats(ordered)
    # Some fills (serpentine hatch, Hilbert) already come out in a
    # near-optimal order; keep whichever order costs less overall. A pen
    # lift costs real time on hardware — count it like ~8 uu of travel.
    lift_cost = 8.0
    if after[1] + after[2] * lift_cost >= before[1] + before[2] * lift_cost:
        return chains, before, before
    return ordered, before, after


# ----------------------------------------------------------------------
# Cubic-Bezier curve fitting (Schneider, Graphics Gems 1990)
# ----------------------------------------------------------------------

def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _vadd(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _vmul(v, s):
    return (v[0] * s, v[1] * s)


def _vdot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _vneg(v):
    return (-v[0], -v[1])


def _vunit(v):
    L = math.hypot(v[0], v[1])
    return (v[0] / L, v[1] / L) if L > 1e-12 else (0.0, 0.0)


def _bernstein(u):
    om = 1.0 - u
    return (om * om * om, 3 * om * om * u, 3 * om * u * u, u * u * u)


def _bezier_eval(P, u):
    B = _bernstein(u)
    return (
        P[0][0] * B[0] + P[1][0] * B[1] + P[2][0] * B[2] + P[3][0] * B[3],
        P[0][1] * B[0] + P[1][1] * B[1] + P[2][1] * B[2] + P[3][1] * B[3],
    )


def _chord_param(pts, first, last):
    n = last - first + 1
    u = [0.0] * n
    for i in range(1, n):
        u[i] = u[i - 1] + _dist(pts[first + i], pts[first + i - 1])
    total = u[-1]
    if total < 1e-12:
        return [i / max(n - 1, 1) for i in range(n)]
    return [v / total for v in u]


def _generate_bezier(pts, first, last, u, t1, t2):
    P0 = pts[first]
    P3 = pts[last]
    n = last - first + 1

    C00 = C01 = C11 = X0 = X1 = 0.0
    for i in range(n):
        B = _bernstein(u[i])
        A0 = _vmul(t1, B[1])
        A1 = _vmul(t2, B[2])
        C00 += _vdot(A0, A0)
        C01 += _vdot(A0, A1)
        C11 += _vdot(A1, A1)
        rx = pts[first + i][0] - (B[0] + B[1]) * P0[0] - (B[2] + B[3]) * P3[0]
        ry = pts[first + i][1] - (B[0] + B[1]) * P0[1] - (B[2] + B[3]) * P3[1]
        X0 += A0[0] * rx + A0[1] * ry
        X1 += A1[0] * rx + A1[1] * ry

    det_C = C00 * C11 - C01 * C01
    seg_len = _dist(P3, P0)

    if abs(det_C) < 1e-12:
        a_l = a_r = seg_len / 3.0
    else:
        a_l = (X0 * C11 - C01 * X1) / det_C
        a_r = (C00 * X1 - X0 * C01) / det_C
        # Guard the classic Schneider blow-up: near-parallel tangents make
        # the normal equations ill-conditioned and the alphas explode,
        # which draws a huge spike between two sample points.
        cap = seg_len * 2.5 if seg_len > EPS else 1e-6
        if not (1e-6 < a_l < cap) or not (1e-6 < a_r < cap):
            a_l = a_r = seg_len / 3.0

    return (P0, _vadd(P0, _vmul(t1, a_l)), _vadd(P3, _vmul(t2, a_r)), P3)


def _max_error(pts, first, last, bez, u):
    max_d2 = 0.0
    split = (first + last) // 2
    for i in range(1, last - first):
        s = _bezier_eval(bez, u[i])
        d2 = (pts[first + i][0] - s[0]) ** 2 + (pts[first + i][1] - s[1]) ** 2
        if d2 > max_d2:
            max_d2 = d2
            split = first + i
    return math.sqrt(max_d2), split


def _fit_recursive(pts, first, last, t1, t2, tol, depth=0):
    n = last - first + 1
    if n < 2:
        return []
    if n == 2:
        P0, P3 = pts[first], pts[last]
        a = _dist(P3, P0) / 3.0
        return [(P0, _vadd(P0, _vmul(t1, a)), _vadd(P3, _vmul(t2, a)), P3)]

    u = _chord_param(pts, first, last)
    bez = _generate_bezier(pts, first, last, u, t1, t2)
    err, split = _max_error(pts, first, last, bez, u)
    if err < tol:
        return [bez]

    if depth > 24 or split <= first or split >= last:
        return [
            (pts[first + i], pts[first + i], pts[first + i + 1], pts[first + i + 1])
            for i in range(n - 1)
        ]

    t_mid = _vunit(_vsub(pts[split - 1], pts[split + 1]))
    return (
        _fit_recursive(pts, first, split, t1, t_mid, tol, depth + 1)
        + _fit_recursive(pts, split, last, _vneg(t_mid), t2, tol, depth + 1)
    )


def fit_beziers(points, tol, closed=False):
    """Fit cubic Beziers to a polyline within `tol`; list of (P0,P1,P2,P3).
    For closed loops (first point == last), tangents wrap around the seam
    so the join stays smooth."""
    if len(points) < 2:
        return []
    if len(points) == 2:
        P0, P3 = points
        return [(P0, P0, P3, P3)]
    if closed and len(points) >= 3:
        t1 = _vunit(_vsub(points[1], points[-2]))
        t2 = _vneg(t1)
    else:
        t1 = _vunit(_vsub(points[1], points[0]))
        t2 = _vunit(_vsub(points[-2], points[-1]))
    return _fit_recursive(points, 0, len(points) - 1, t1, t2, tol)


# ----------------------------------------------------------------------
# Path-data emission
# ----------------------------------------------------------------------

def chains_to_path_d(chains, smooth=False, fit_tol=0.05, closed_flags=None):
    """Serialize polyline chains to an SVG path `d` string.

    smooth=True re-fits each chain with cubic Beziers (Schneider) so the
    output is compact and silky; otherwise plain L polylines are emitted.
    """
    parts = []
    for ci, ch in enumerate(chains):
        if len(ch) < 2:
            continue
        closed = bool(closed_flags[ci]) if closed_flags else False
        pts = list(ch)
        if closed and _dist(pts[0], pts[-1]) > EPS:
            pts.append(pts[0])
        elif not closed and len(pts) > 3 and _dist(pts[0], pts[-1]) <= EPS:
            # chain physically returns to its start — treat as a loop so
            # the Bezier fit wraps tangents smoothly across the seam
            closed = True
        parts.append("M {:.4f},{:.4f}".format(pts[0][0], pts[0][1]))
        if smooth and len(pts) >= 3:
            for _, P1, P2, P3 in fit_beziers(pts, fit_tol, closed=closed):
                parts.append(
                    "C {:.4f},{:.4f} {:.4f},{:.4f} {:.4f},{:.4f}".format(
                        P1[0], P1[1], P2[0], P2[1], P3[0], P3[1]))
        else:
            for x, y in pts[1:]:
                parts.append("L {:.4f},{:.4f}".format(x, y))
        if closed:
            parts.append("Z")
    return " ".join(parts)


# ----------------------------------------------------------------------
# High-level entry point
# ----------------------------------------------------------------------

MODES = ("hatch", "crosshatch", "sine", "concentric", "spiral",
         "archimedean", "hilbert")

_DF_MODES = {"concentric", "spiral", "archimedean", "hilbert"}


class FillResult:
    def __init__(self, chains, closed_flags, smooth, warnings):
        self.chains = chains              # list of point lists
        self.closed_flags = closed_flags  # list of bool, same length
        self.smooth = smooth              # emit as fitted curves?
        self.warnings = warnings          # list of str


def generate_fill(region, mode, pen_width, spacing, angle=45.0,
                  cross_angle=90.0, sine_amplitude=1.5, sine_wavelength=6.0,
                  edge_gap=None, connect=True, max_cells=None):
    """Produce fill chains for `region`.

    pen_width / spacing / lengths are in user units. `edge_gap` defaults
    to pen_width / 2 so the ink stays inside the outline; pass 0 to run
    the pen right up to (and onto) the boundary.
    """
    warnings = []
    if region.is_empty():
        return FillResult([], [], False, ["selection contains no closed area"])
    if edge_gap is None:
        edge_gap = pen_width * 0.5

    closed_flags = None
    smooth = False

    if mode in ("hatch", "crosshatch", "sine"):
        # True lateral inset: hatch the region eroded by edge_gap, so ink
        # stays inside even where a line runs parallel to the boundary.
        # Stroke ends then land ON the eroded outline and the round pen
        # tip just kisses the original edge.
        work = region
        if edge_gap > 0:
            work = eroded_region(region, edge_gap, spacing / 2.0, max_cells)
            if work is None:
                return FillResult([], [], False, [
                    "shape is too small for this pen size — nothing fits "
                    "inside"])
        if mode == "hatch":
            chains = hatch_fill(work, spacing, angle, 0.0, connect)
        elif mode == "crosshatch":
            chains = cross_hatch_fill(work, spacing, angle, cross_angle,
                                      0.0, connect)
        else:
            chains = sine_fill(work, spacing, angle, sine_amplitude,
                               sine_wavelength, 0.0, connect)
            smooth = True
    elif mode in _DF_MODES:
        cell = max(min(spacing / 3.0, pen_width / 1.5), 1e-3)
        df = DistanceField.build(region, cell, max_cells)
        if df.coarsened:
            warnings.append(
                "shape is large relative to the pen size — distance grid "
                "was coarsened (cell {:.3f} uu); fine detail may soften"
                .format(df.cell))
        start = edge_gap if edge_gap > 0 else spacing * 0.5
        if df.max_value() <= start:
            return FillResult([], [], False, [
                "shape is too small for this pen size — nothing fits inside"])
        if mode == "concentric":
            levels = concentric_rings(df, spacing, start,
                                      min_perimeter=pen_width * 2.0)
            # bake the closure into the points so rings survive
            # re-ordering / reversal in the optimizer intact
            chains = [loop + [loop[0]] for lvl in levels for loop in lvl]
            closed_flags = [True] * len(chains)
            smooth = True
        elif mode == "spiral":
            levels = concentric_rings(df, spacing, start,
                                      min_perimeter=pen_width * 2.0)
            chains = link_rings_spiral(levels, region)
            smooth = True
        elif mode == "archimedean":
            chains = archimedean_spiral_fill(df, spacing, start)
            smooth = True
        else:  # hilbert
            chains = hilbert_fill(df, spacing, start)
    else:
        raise ValueError("unknown fill mode: {!r}".format(mode))

    chains = [c for c in chains if len(c) >= 2
              and polyline_length(c) > pen_width * 0.25]
    if closed_flags is not None:
        closed_flags = closed_flags[: len(chains)]
    else:
        closed_flags = [False] * len(chains)

    if not chains:
        warnings.append("no fill fits — try a smaller pen or tighter spacing")
    return FillResult(chains, closed_flags, smooth, warnings)
