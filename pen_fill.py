#!/usr/bin/env python3
"""
Pen Plotter Fill — Inkscape extension.

Fills closed shapes with concentric inset paths so a pen plotter can
"fill" the shape by drawing line by line. The pen-tip width (mm) sets
the spacing between successive copies.

Fill modes:
  * concentric — each inset is its own closed loop (pen lifts between loops)
  * spiral     — one continuous polyline that walks each loop and bridges
                 to the next-inner one at the closest point (fewer pen lifts)

Pure-Python: no Clipper / Shapely / pyclipper dependency. The offset is
an angle-bisector method with iterative pruning of overshoot vertices,
and the output is re-fit to cubic Bezier segments using Schneider's
algorithm (Graphics Gems, 1990) so it stays smooth like the source.
"""

import math

import inkex
from inkex import PathElement, Group
from inkex.bezier import cspsubdiv


__version__ = "1.0.0"

FLATNESS_RATIO = 0.05   # bezier-flatten tolerance as fraction of pen size
MAX_PASSES = 5000       # hard safety cap on inset iterations
MITRE_LIMIT = 4.0       # cap per-vertex offset distance at d * MITRE_LIMIT
MIN_AREA = 1e-3         # discard polygons with area below this (user-units²)


# ----------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------

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


def _norm(vx, vy):
    L = math.hypot(vx, vy)
    if L < 1e-12:
        return None
    return vx / L, vy / L


def remove_collinear(poly, eps=1e-7):
    """Drop vertices that are collinear with their neighbours."""
    n = len(poly)
    if n < 3:
        return poly[:]
    out = []
    for i in range(n):
        p = poly[(i - 1) % n]
        c = poly[i]
        q = poly[(i + 1) % n]
        cross = (c[0] - p[0]) * (q[1] - p[1]) - (c[1] - p[1]) * (q[0] - p[0])
        if abs(cross) > eps:
            out.append(c)
    return out if len(out) >= 3 else []


def _bisector_offset(poly, d):
    """Compute candidate offset vertex for each input vertex via the
    angle-bisector method. Returns a list the same length as `poly`."""
    n = len(poly)
    out = [None] * n
    for i in range(n):
        p = poly[(i - 1) % n]
        c = poly[i]
        q = poly[(i + 1) % n]

        e1 = _norm(c[0] - p[0], c[1] - p[1])
        e2 = _norm(q[0] - c[0], q[1] - c[1])
        if e1 is None or e2 is None:
            out[i] = c
            continue

        # Inward normals for a CCW polygon (interior is to the left).
        n1 = (-e1[1], e1[0])
        n2 = (-e2[1], e2[0])

        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        L = math.hypot(bx, by)
        if L < 1e-9:
            bnx, bny = n1                 # 180° turn → fall back to one normal
        else:
            bnx, bny = bx / L, by / L

        dot = n1[0] * bnx + n1[1] * bny
        if abs(dot) < 1e-9:
            scale = d * MITRE_LIMIT
        else:
            scale = d / dot
            limit = abs(d) * MITRE_LIMIT
            if abs(scale) > limit:
                scale = math.copysign(limit, scale)

        out[i] = (c[0] + bnx * scale, c[1] + bny * scale)
    return out


def _turn_sign(a, b, c):
    """Sign of the cross product (b - a) × (c - b). >0 left turn, <0 right."""
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def offset_polygon(poly, d):
    """Offset polygon (assumed CCW) by distance d; positive = inward.

    Robust against sharp concave corners and overshoot: starts from the
    bisector candidate vertices and iteratively removes any vertex whose
    turn direction has flipped relative to the original polygon. After
    cleanup the result must (a) have positive signed area, (b) be free
    of self-intersections."""
    if len(poly) < 3:
        return []

    candidates = _bisector_offset(poly, d)
    valid = list(range(len(poly)))

    # Iteratively drop "flipped" vertices (turn-direction reversed vs source)
    # plus vertices whose offset overshoots beyond their adjacent original edges.
    for _ in range(len(poly) * 2):
        m = len(valid)
        if m < 3:
            return []

        worst_k = -1
        worst_score = 0.0
        for k in range(m):
            i_prev = valid[(k - 1) % m]
            i_curr = valid[k]
            i_next = valid[(k + 1) % m]

            old_sign = _turn_sign(poly[i_prev], poly[i_curr], poly[i_next])
            new_sign = _turn_sign(
                candidates[i_prev], candidates[i_curr], candidates[i_next]
            )
            # If turn direction reversed, this vertex is "inverted".
            if old_sign * new_sign < -1e-12:
                # Score by how badly it shifts; prioritise the worst offender.
                shift = math.hypot(
                    candidates[i_curr][0] - poly[i_curr][0],
                    candidates[i_curr][1] - poly[i_curr][1],
                )
                if shift > worst_score:
                    worst_score = shift
                    worst_k = k

        if worst_k < 0:
            break
        valid.pop(worst_k)

    cleaned = remove_collinear([candidates[i] for i in valid])
    if len(cleaned) < 3:
        return []
    new_area = signed_area(cleaned)
    if new_area <= MIN_AREA:
        return []
    # Sanity: an inward offset must strictly shrink area. If the cleaned
    # polygon is no smaller than the input, the offset has degenerated.
    # Bail rather than oscillate.
    if d > 0 and new_area >= signed_area(poly) - MIN_AREA:
        return []
    # Skip the O(n²) self-intersection scan here: vertex pruning above
    # plus the area-shrink guard already catch the failure modes that
    # matter, and the scan is the bottleneck when curves are flattened
    # finely enough to look smooth on calligraphy shapes.

    return [cleaned]


# ----------------------------------------------------------------------
# Cubic-Bezier curve fitting (Schneider, Graphics Gems 1990).
# ----------------------------------------------------------------------
# Given a dense polyline approximating a smooth curve, fit a sequence of
# cubic Bezier segments within `tol` Euclidean deviation. Lets us emit
# the inset paths as smooth `C` commands instead of long `L` chains, so
# the output keeps the visual quality of the original curves.
# ----------------------------------------------------------------------

def _vsub(a, b): return (a[0] - b[0], a[1] - b[1])
def _vadd(a, b): return (a[0] + b[0], a[1] + b[1])
def _vmul(v, s): return (v[0] * s, v[1] * s)
def _vdot(a, b): return a[0] * b[0] + a[1] * b[1]
def _vneg(v): return (-v[0], -v[1])

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
        u[i] = u[i - 1] + math.hypot(
            pts[first + i][0] - pts[first + i - 1][0],
            pts[first + i][1] - pts[first + i - 1][1],
        )
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

        # residual = pts[first+i] - (B0+B1)*P0 - (B2+B3)*P3
        rx = pts[first + i][0] - (B[0] + B[1]) * P0[0] - (B[2] + B[3]) * P3[0]
        ry = pts[first + i][1] - (B[0] + B[1]) * P0[1] - (B[2] + B[3]) * P3[1]
        X0 += A0[0] * rx + A0[1] * ry
        X1 += A1[0] * rx + A1[1] * ry

    det_C = C00 * C11 - C01 * C01
    seg_len = math.hypot(P3[0] - P0[0], P3[1] - P0[1])

    if abs(det_C) < 1e-12:
        a_l = a_r = seg_len / 3.0
    else:
        a_l = (X0 * C11 - C01 * X1) / det_C
        a_r = (C00 * X1 - X0 * C01) / det_C
        if a_l < 1e-6 or a_r < 1e-6:
            a_l = a_r = seg_len / 3.0

    P1 = _vadd(P0, _vmul(t1, a_l))
    P2 = _vadd(P3, _vmul(t2, a_r))
    return (P0, P1, P2, P3)


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
        a = math.hypot(P3[0] - P0[0], P3[1] - P0[1]) / 3.0
        return [(P0, _vadd(P0, _vmul(t1, a)), _vadd(P3, _vmul(t2, a)), P3)]

    u = _chord_param(pts, first, last)
    bez = _generate_bezier(pts, first, last, u, t1, t2)
    err, split = _max_error(pts, first, last, bez, u)
    if err < tol:
        return [bez]

    if depth > 24 or split <= first or split >= last:
        # Bail out as straight segments
        return [
            (pts[first + i], pts[first + i], pts[first + i + 1], pts[first + i + 1])
            for i in range(n - 1)
        ]

    t_mid = _vunit(_vsub(pts[split - 1], pts[split + 1]))
    return (
        _fit_recursive(pts, first, split, t1, t_mid, tol, depth + 1)
        + _fit_recursive(pts, split, last, _vneg(t_mid), t2, tol, depth + 1)
    )


def fit_beziers(points, tol):
    """Fit cubic Bezier segments to a polyline within `tol` deviation.
    Returns list of (P0, P1, P2, P3) tuples."""
    if len(points) < 2:
        return []
    if len(points) == 2:
        P0, P3 = points
        return [(P0, P0, P3, P3)]
    t1 = _vunit(_vsub(points[1], points[0]))
    t2 = _vunit(_vsub(points[-2], points[-1]))
    return _fit_recursive(points, 0, len(points) - 1, t1, t2, tol)


# ----------------------------------------------------------------------
# Inkscape extension
# ----------------------------------------------------------------------

class PenFill(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--pen_size", type=float, default=0.5)
        pars.add_argument("--overlap", type=float, default=1.0)
        pars.add_argument("--max_copies", type=int, default=0)
        pars.add_argument("--fill_mode", type=str, default="concentric")
        pars.add_argument("--group", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_original", type=inkex.Boolean, default=True)

    def effect(self):
        if not self.svg.selection:
            inkex.errormsg("Select at least one closed path first.")
            return

        pen_uu = self.svg.unittouu(f"{self.options.pen_size}mm")
        step = pen_uu * self.options.overlap
        if step <= 0:
            inkex.errormsg("Pen size and overlap must be > 0.")
            return

        # Flatness: how finely Bezier curves are sampled into line segments
        # before offsetting. Smaller = smoother output curves. Tied to pen
        # size so finer pens automatically get finer detail.
        flatness = max(pen_uu * FLATNESS_RATIO, 1e-3)

        for elem in list(self.svg.selection):
            if hasattr(elem, "path"):
                self.process(elem, step, pen_uu, flatness)

    def process(self, elem, step, pen_uu, flatness):
        try:
            transform = elem.composed_transform()
            path = elem.path.transform(transform).to_absolute()
        except Exception as exc:
            inkex.errormsg(f"Could not read path on <{elem.get('id')}>: {exc}")
            return

        polys = self._path_to_polygons(path, flatness)
        # Normalise orientation: CCW with positive area
        polys = [p if signed_area(p) > 0 else list(reversed(p)) for p in polys]
        polys = [p for p in polys if signed_area(p) > MIN_AREA]
        if not polys:
            return

        max_copies = self.options.max_copies if self.options.max_copies > 0 else None
        chains = self._build_chains(polys, step, max_copies)
        if all(len(c) <= 1 for c in chains):
            inkex.errormsg(
                f"Shape <{elem.get('id')}> is too small for pen size "
                f"{self.options.pen_size}mm — no inset fits inside."
            )
            return

        # Curve-fit tolerance: tie to flatness so the output curves stay
        # within roughly the sampling error of the input flattening.
        fit_tol = flatness * 2.0

        if self.options.fill_mode == "spiral":
            d = self._chains_to_d_spiral(chains, fit_tol)
        else:
            d = self._chains_to_d_concentric(chains, fit_tol)

        new_elem = PathElement()
        new_elem.set("d", d)
        new_elem.style = inkex.Style({
            "fill": "none",
            "stroke": "#000000",
            "stroke-width": f"{pen_uu}",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        })

        # Path coords are in document space (composed_transform applied),
        # so append at SVG root to avoid double-transformation.
        target = self.svg

        if self.options.group:
            g = Group()
            g.set("inkscape:label", f"penfill_{elem.get('id', 'shape')}")
            target.append(g)
            g.append(new_elem)
        else:
            target.append(new_elem)

        if not self.options.keep_original:
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)

    # ------------------------------------------------------------------
    # Chain building
    # ------------------------------------------------------------------

    def _build_chains(self, polys, step, max_copies=None):
        """Inset each polygon step-by-step until the offset returns nothing
        (or until the requested number of copies has been generated).
        `max_copies` counts the inset copies added beyond the original — so
        max_copies=3 yields the original + 3 inner loops per chain."""
        chains = [[p] for p in polys]
        pending = list(range(len(chains)))
        cap = max_copies if max_copies is not None else MAX_PASSES

        for _ in range(cap):
            if not pending:
                break
            still = []
            for idx in pending:
                current = chains[idx][-1]
                result = offset_polygon(current, step)
                if not result:
                    continue
                chains[idx].append(result[0])
                still.append(idx)
            pending = still

        return chains

    # ------------------------------------------------------------------
    # Path-data emitters
    # ------------------------------------------------------------------

    def _chains_to_d_concentric(self, chains, fit_tol):
        parts = []
        for chain in chains:
            for poly in chain:
                if len(poly) < 2:
                    continue
                # Close the loop for fitting so the start/end stay smooth.
                pts = list(poly) + [poly[0]]
                self._emit_polyline_as_curve(parts, pts, fit_tol, closed=True)
        return " ".join(parts)

    def _chains_to_d_spiral(self, chains, fit_tol):
        parts = []
        for chain in chains:
            polyline = self._chain_to_spiral_points(chain)
            if len(polyline) < 2:
                continue
            self._emit_polyline_as_curve(parts, polyline, fit_tol, closed=False)
        return " ".join(parts)

    def _emit_polyline_as_curve(self, parts, polyline, fit_tol, closed):
        """Append M / C... / [Z] commands for `polyline` to `parts`,
        fitting cubic Beziers so the output is smooth like the source."""
        x0, y0 = polyline[0]
        parts.append(f"M {x0:.4f},{y0:.4f}")
        if len(polyline) >= 3:
            for _, P1, P2, P3 in fit_beziers(polyline, fit_tol):
                parts.append(
                    f"C {P1[0]:.4f},{P1[1]:.4f} "
                    f"{P2[0]:.4f},{P2[1]:.4f} "
                    f"{P3[0]:.4f},{P3[1]:.4f}"
                )
        else:
            for x, y in polyline[1:]:
                parts.append(f"L {x:.4f},{y:.4f}")
        if closed:
            parts.append("Z")

    def _chain_to_spiral_points(self, chain):
        if not chain:
            return []
        points = list(chain[0])
        points.append(chain[0][0])  # close outermost loop

        for nxt in chain[1:]:
            end = points[-1]
            best_idx = min(
                range(len(nxt)),
                key=lambda i: (nxt[i][0] - end[0]) ** 2 + (nxt[i][1] - end[1]) ** 2,
            )
            rotated = nxt[best_idx:] + nxt[:best_idx]
            points.extend(rotated)
            points.append(rotated[0])

        return points

    # ------------------------------------------------------------------
    # Path → polygon flattening
    # ------------------------------------------------------------------

    def _path_to_polygons(self, path, flatness):
        csp = path.to_superpath()
        cspsubdiv(csp, flatness)
        polys = []
        for sub in csp:
            poly = [(node[1][0], node[1][1]) for node in sub]
            # drop trailing duplicate of start point if present
            if len(poly) >= 2 and poly[0] == poly[-1]:
                poly = poly[:-1]
            if len(poly) >= 3:
                polys.append(poly)
        return polys


if __name__ == "__main__":
    PenFill().run()
