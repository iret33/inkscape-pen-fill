"""Unit tests for penfill_core — pure geometry, no Inkscape required."""

import math

import pytest

import penfill_core as core


# ---------------------------------------------------------------- shapes

def circle(cx, cy, r, n=180):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def brute_contains(rings, rule, x, y):
    """Reference point-in-region test without any acceleration."""
    winding = crossings = 0
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 <= y < y2) or (y2 <= y < y1):
                if x1 + (y - y1) / (y2 - y1) * (x2 - x1) > x:
                    crossings += 1
                    winding += 1 if y2 > y1 else -1
    return crossings % 2 == 1 if rule == "evenodd" else winding != 0


def dumbbell(n=240):
    """Two blobs joined by a waist narrower than the blob radius."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 30 * math.cos(t)
        y = 14 * math.sin(t) * (0.25 + 0.75 * abs(math.cos(t)))
        pts.append((x + 40, y + 25))
    return pts


@pytest.fixture
def disk():
    return core.Region([circle(40, 25, 20)])


@pytest.fixture
def donut():
    return core.Region([circle(40, 25, 20), circle(40, 25, 9)])


# ---------------------------------------------------------------- region

class TestRegion:
    def test_evenodd_hole(self, donut):
        assert donut.contains(40, 25 + 15)        # in the ring
        assert not donut.contains(40, 25)         # in the hole
        assert not donut.contains(40, 25 + 30)    # outside

    def test_nonzero_same_orientation_fills_hole(self):
        # both rings CCW → winding 2 inside → nonzero fills the "hole"
        region = core.Region([circle(40, 25, 20), circle(40, 25, 9)],
                             fill_rule="nonzero")
        assert region.contains(40, 25)

    def test_nonzero_opposite_orientation_keeps_hole(self):
        inner = list(reversed(circle(40, 25, 9)))
        region = core.Region([circle(40, 25, 20), inner],
                             fill_rule="nonzero")
        assert not region.contains(40, 25)
        assert region.contains(40, 25 + 15)

    def test_intervals_donut(self, donut):
        spans = donut.intervals(25.0)     # through the centre
        assert len(spans) == 2            # left arm + right arm
        (a0, a1), (b0, b1) = spans
        assert a0 < a1 <= 40 <= b0 < b1

    def test_segment_inside(self, donut):
        assert donut.segment_inside((40, 40), (40, 43))
        assert not donut.segment_inside((25, 25), (55, 25))  # crosses hole

    def test_edge_index_matches_brute_force(self):
        import random
        rng = random.Random(3)
        rings = [circle(40, 25, 20, n=97), circle(47, 25, 6, n=31),
                 circle(20, 30, 9, n=40)]
        for rule in ("evenodd", "nonzero"):
            region = core.Region(rings, rule)
            for _ in range(400):
                x, y = rng.uniform(15, 65), rng.uniform(0, 50)
                assert region.contains(x, y) == brute_contains(
                    rings, rule, x, y)

    def test_overlap_edges_are_not_boundary(self):
        # two overlapping discs, nonzero → one merged blob; the arcs
        # inside the overlap draw nothing and are not outline
        union = core.Region([circle(0, 0, 20), circle(25, 0, 20)],
                            "nonzero")
        assert not union.on_boundary((5.0, -1.0), (5.0, 1.0))
        assert union.on_boundary((-20.0, -1.0), (-20.0, 1.0))


# ---------------------------------------------------------------- hatch

class TestHatch:
    def test_lines_inside_and_spaced(self, disk):
        chains = core.hatch_fill(disk, 2.0, 0.0)
        assert chains
        ys = set()
        for ch in chains:
            for i in range(len(ch) - 1):
                mx = (ch[i][0] + ch[i + 1][0]) / 2
                my = (ch[i][1] + ch[i + 1][1]) / 2
                assert disk.contains(mx, my)
            ys.update(round(p[1], 6) for p in ch)
        ys = sorted(ys)
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        for g in gaps:
            assert g == pytest.approx(2.0, abs=1e-6)

    def test_angle_respected(self, disk):
        chains = core.hatch_fill(disk, 2.0, 30.0, connect=False)
        for ch in chains:
            dx = ch[-1][0] - ch[0][0]
            dy = ch[-1][1] - ch[0][1]
            ang = math.degrees(math.atan2(dy, dx)) % 180.0
            assert ang == pytest.approx(30.0, abs=0.5)

    def test_serpentine_reduces_lifts(self, disk):
        loose = core.hatch_fill(disk, 1.0, 0.0, connect=False)
        tight = core.hatch_fill(disk, 1.0, 0.0, connect=True)
        assert len(tight) < len(loose) / 3

    def test_hole_never_crossed(self, donut):
        chains = core.hatch_fill(donut, 1.0, 15.0, connect=True)
        for ch in chains:
            for i in range(len(ch) - 1):
                for t in (0.25, 0.5, 0.75):
                    x = ch[i][0] + (ch[i + 1][0] - ch[i][0]) * t
                    y = ch[i][1] + (ch[i + 1][1] - ch[i][1]) * t
                    assert donut.contains(x, y), "stroke crossed the hole"

    def test_crosshatch_two_directions(self, disk):
        chains = core.cross_hatch_fill(disk, 2.0, 0.0, 90.0, connect=False)
        angles = set()
        for ch in chains:
            dx, dy = ch[-1][0] - ch[0][0], ch[-1][1] - ch[0][1]
            angles.add(round(math.degrees(math.atan2(dy, dx)) % 180))
        assert angles == {0, 90}


class TestSine:
    def test_points_inside(self, disk):
        chains = core.sine_fill(disk, 2.5, 0.0, 0.8, 5.0)
        assert chains
        for ch in chains:
            for x, y in ch:
                # allow boundary rounding of the clip refinement
                assert disk.contains(x, y) or min(
                    abs(math.hypot(x - 40, y - 25) - 20), 0.1) <= 0.1

    def test_waves_never_cross(self, disk):
        # every row is the same sine shifted sideways, so rows can't cross
        # even when the wave is taller than the line spacing
        amp, wl = 1.5, 6.0
        chains = core.sine_fill(disk, 0.5, 0.0, amp, wl, connect=False)
        assert len(chains) > 20
        for ch in chains:
            bases = [y - amp * math.sin(2 * math.pi * x / wl) for x, y in ch]
            assert max(bases) - min(bases) < amp * 0.05


# ------------------------------------------------------------ clip

class TestClip:
    def test_crossing_refined(self):
        inside = lambda x, y: math.hypot(x, y) < 10.0   # noqa: E731
        pts = [(x, 0.0) for x in range(-20, 21, 4)]
        runs = core.clip_polyline(pts, inside)
        assert len(runs) == 1
        run = runs[0]
        assert run[0][0] == pytest.approx(-10.0, abs=1e-5)
        assert run[-1][0] == pytest.approx(10.0, abs=1e-5)


# ------------------------------------------------------------ distance

class TestDistanceField:
    def test_matches_analytic_circle(self, disk):
        df = core.DistanceField.build(disk, 0.25)
        for r_frac, ang in ((0.2, 0.3), (0.5, 2.0), (0.8, 4.0)):
            x = 40 + 20 * r_frac * math.cos(ang)
            y = 25 + 20 * r_frac * math.sin(ang)
            expect = 20 - 20 * r_frac
            assert df.sample(x, y) == pytest.approx(expect, abs=0.35)

    def test_pole_of_inaccessibility(self, donut):
        df = core.DistanceField.build(donut, 0.2)
        px, py, depth = df.max_point()
        # deepest point of an annulus lies mid-ring, depth (20-9)/2
        assert math.hypot(px - 40, py - 25) == pytest.approx(14.5, abs=0.4)
        assert depth == pytest.approx(5.5, abs=0.25)

    def test_outside_negative(self, disk):
        df = core.DistanceField.build(disk, 0.25)
        assert df.sample(40, 25 - 24) < 0

    def test_overlapping_subpaths_merge(self):
        # nonzero union of two overlapping discs: deep inside the overlap
        # the distance is to the merged outline (~15.6), not to the
        # hidden arcs running through it
        union = core.Region([circle(0, 0, 20), circle(25, 0, 20)],
                            "nonzero")
        df = core.DistanceField.build(union, 0.3)
        assert df.sample(12.5, 0) == pytest.approx(15.6, abs=0.5)
        assert df.sample(5.3, 0) > 14.0

    @pytest.mark.parametrize("rule", ["evenodd", "nonzero"])
    def test_shared_edge_is_not_boundary(self, rule):
        # two squares sharing an edge fill as one rectangle — whether the
        # shared edge is vertical or horizontal
        left = [(0, 0), (20, 0), (20, 20), (0, 20)]
        right = [(20, 0), (40, 0), (40, 20), (20, 20)]
        below = [(0, 20), (20, 20), (20, 40), (0, 40)]
        for pair, probe in (((left, right), (20, 10)),
                            ((left, below), (10, 20))):
            df = core.DistanceField.build(core.Region(pair, rule), 0.25)
            assert df.sample(*probe) == pytest.approx(10.0, abs=0.3)

    def test_pure_python_fallback_matches(self, disk):
        saved = core._np
        try:
            df_np = core.DistanceField.build(disk, 0.4)
            core._np = None
            df_py = core.DistanceField.build(disk, 0.4)
        finally:
            core._np = saved
        assert df_np.nx == df_py.nx and df_np.ny == df_py.ny
        worst = max(abs(float(a) - b)
                    for a, b in zip(df_np.values, df_py.values))
        assert worst < 1e-9


class TestMarchingSquares:
    def test_circle_single_loop(self, disk):
        df = core.DistanceField.build(disk, 0.25)
        loops = core.marching_squares(df, 5.0)
        assert len(loops) == 1
        peri = core.polyline_length(loops[0]) + core._dist(
            loops[0][-1], loops[0][0])
        assert peri == pytest.approx(2 * math.pi * 15, rel=0.02)

    def test_donut_two_loops(self, donut):
        df = core.DistanceField.build(donut, 0.2)
        loops = core.marching_squares(df, 2.0)
        assert len(loops) == 2


# ------------------------------------------------------------ concentric

class TestConcentric:
    def test_ring_count(self, disk):
        df = core.DistanceField.build(disk, 0.3)
        levels = core.concentric_rings(df, 2.0, 1.0)
        # rings at 1,3,5,...,19 → 10 levels, give or take the ridge guard
        assert 9 <= len(levels) <= 11

    def test_split_at_waist(self):
        region = core.Region([dumbbell()])
        res = core.generate_fill(region, "spiral", 0.8, 0.8)
        assert 2 <= len(res.chains) <= 3      # one chain per lobe

    def test_spiral_on_donut_is_two_strokes(self, donut):
        # rings around the hole follow the hole: one stroke spirals in
        # from the outer edge, one out from the hole (v2.0 gave 9)
        res = core.generate_fill(donut, "spiral", 0.7, 0.7)
        assert len(res.chains) <= 2

    def test_spiral_keeps_every_ring(self, donut):
        conc = core.generate_fill(donut, "concentric", 0.7, 0.7)
        spir = core.generate_fill(donut, "spiral", 0.7, 0.7)
        ink = lambda res: sum(core.polyline_length(c)   # noqa: E731
                              for c in res.chains)
        # same rings, plus short bridges between them
        assert ink(spir) == pytest.approx(ink(conc), rel=0.05)

    def test_spiral_single_chain_on_disk(self, disk):
        res = core.generate_fill(disk, "spiral", 0.8, 0.8)
        assert len(res.chains) == 1

    def test_no_giant_bridges(self, donut):
        res = core.generate_fill(donut, "spiral", 0.7, 0.7)
        for ch in res.chains:
            for i in range(len(ch) - 1):
                assert core._dist(ch[i], ch[i + 1]) < 5.0

    def test_chains_inside_region(self, donut):
        res = core.generate_fill(donut, "concentric", 0.7, 0.7)
        assert res.chains
        for ch in res.chains:
            for x, y in ch[::5]:
                assert donut.contains(x, y)


class TestSpirals:
    def test_archimedean_inside_eroded(self, disk):
        df = core.DistanceField.build(disk, 0.25)
        chains = core.archimedean_spiral_fill(df, 1.0, 0.4)
        assert chains
        for ch in chains:
            for x, y in ch[::7]:
                assert df.sample(x, y) >= 0.4 - 0.15

    def test_archimedean_no_repeated_points(self, disk):
        df = core.DistanceField.build(disk, 0.25)
        for ch in core.archimedean_spiral_fill(df, 1.0, 0.0):
            for a, b in zip(ch, ch[1:]):
                assert core._dist(a, b) > 1e-9

    @pytest.mark.parametrize("spacing", [1.0, 1.5, 2.2, 3.0])
    def test_hilbert_respects_spacing(self, disk, spacing):
        df = core.DistanceField.build(disk, 0.3)
        chains = core.hilbert_fill(df, spacing, 0.5)
        # after dropping straight-run midpoints every inner segment is a
        # whole number of grid steps — the grid step must be the spacing
        steps = [core._dist(a, b) / spacing for ch in chains
                 for a, b in zip(ch[1:-1], ch[2:-1])]
        assert steps
        for q in steps:
            assert q == pytest.approx(round(q), abs=1e-6) and round(q) >= 1

    def test_hilbert_cap_warns(self, disk):
        df = core.DistanceField.build(disk, 0.3)
        warnings = []
        core.hilbert_fill(df, 0.2, 0.0, max_points=4096, warnings=warnings)
        assert warnings and "further apart" in warnings[0]

    def test_hilbert_inside(self, disk):
        df = core.DistanceField.build(disk, 0.3)
        chains = core.hilbert_fill(df, 1.5, 0.5)
        assert chains
        for ch in chains:
            for x, y in ch[::11]:
                assert df.sample(x, y) >= 0.5 - 0.15


# ------------------------------------------------------------ optimizer

class TestOptimizer:
    def test_never_worse(self):
        import random
        rng = random.Random(7)
        chains = []
        for _ in range(60):
            x, y = rng.uniform(0, 100), rng.uniform(0, 100)
            chains.append([(x, y), (x + 3, y + 1)])
        opt, before, after = core.optimize_order(chains)
        lift = 8.0
        assert after[1] + lift * after[2] <= before[1] + lift * before[2]
        assert after[0] == pytest.approx(before[0])   # draw length kept

    def test_join_merges_touching(self):
        chains = [[(0, 0), (10, 0)], [(10, 0), (20, 0)], [(50, 5), (60, 5)]]
        opt, _, after = core.optimize_order(chains, join_tol=0.01)
        assert after[2] == 2   # first two merged into one stroke

    def test_empty(self):
        opt, before, after = core.optimize_order([])
        assert opt == [] and before == (0.0, 0.0, 0)


# ------------------------------------------------------------ bezier fit

def dist_to_polyline(p, pts):
    best = float("inf")
    for a, b in zip(pts, pts[1:]):
        ax, ay = a
        dx, dy = b[0] - ax, b[1] - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 < 1e-12 else max(
            0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg2))
        ex, ey = p[0] - (ax + t * dx), p[1] - (ay + t * dy)
        best = min(best, ex * ex + ey * ey)
    return math.sqrt(best)


class TestFit:
    def test_within_tolerance(self):
        pts = [(x * 0.5, math.sin(x * 0.25) * 8) for x in range(80)]
        tol = 0.05
        for bez in core.fit_beziers(pts, tol):
            for k in range(1, 8):
                sample = core._bezier_eval(bez, k / 8)
                assert dist_to_polyline(sample, pts) < tol * 3

    def test_no_control_point_blowup(self):
        loop = circle(0, 0, 10, n=24) + [circle(0, 0, 10, n=24)[0]]
        for P0, P1, P2, P3 in core.fit_beziers(loop, 0.1, closed=True):
            for px, py in (P1, P2):
                assert math.hypot(px, py) < 14.0


# ------------------------------------------------------------ top level

class TestGenerateFill:
    @pytest.mark.parametrize("mode", core.MODES)
    def test_all_modes_run(self, donut, mode):
        res = core.generate_fill(donut, mode, 0.8, 0.8)
        assert res.chains, "mode {} produced nothing".format(mode)
        for ch in res.chains:
            assert len(ch) >= 2

    def test_hatch_keeps_lateral_clearance(self, disk):
        # "keep inside" must hold even for lines parallel to the edge
        res = core.generate_fill(disk, "hatch", 1.0, 1.0)
        clearance = min(20.0 - math.hypot(x - 40, y - 25)
                        for ch in res.chains for x, y in ch)
        assert clearance >= 0.5 - 0.15   # pen/2 minus grid tolerance

    def test_keep_inside_hatch_still_joins_lines(self, disk, donut):
        # v2.0 lost most serpentine joins once "keep inside" was on
        # (disc: 14 strokes, donut: 32)
        for region in (disk, donut):
            loose = core.generate_fill(region, "hatch", 1.0, 1.2,
                                       connect=False)
            joined = core.generate_fill(region, "hatch", 1.0, 1.2)
            assert len(joined.chains) * 4 <= len(loose.chains)
        assert len(core.generate_fill(disk, "hatch", 1.0, 1.2).chains) <= 3

    def test_joining_bridges_keep_clearance(self, donut):
        pen = 1.0
        df = core.DistanceField.build(donut, 0.1)
        res = core.generate_fill(donut, "hatch", pen, 1.2, angle=30.0)
        for ch in res.chains:
            for a, b in zip(ch, ch[1:]):
                for t in (0.0, 0.5, 1.0):
                    x = a[0] + (b[0] - a[0]) * t
                    y = a[1] + (b[1] - a[1]) * t
                    assert df.sample(x, y) >= pen / 2 * 0.8

    @pytest.mark.parametrize("mode", ["hatch", "sine", "hilbert",
                                      "archimedean"])
    def test_outline_adds_closed_edge(self, disk, mode):
        plain = core.generate_fill(disk, mode, 1.0, 1.5)
        lined = core.generate_fill(disk, mode, 1.0, 1.5, outline=True)
        extra = [c for c in lined.chains if c not in plain.chains]
        assert len(extra) == 1
        loop = extra[0]
        assert core._dist(loop[0], loop[-1]) < 1e-9          # closed
        # traced half a pen inside the r=20 disc
        assert core.polyline_length(loop) == pytest.approx(
            2 * math.pi * 19.5, rel=0.02)

    def test_outline_not_doubled_on_rings(self, disk):
        for mode in ("concentric", "spiral"):
            plain = core.generate_fill(disk, mode, 1.0, 1.0)
            lined = core.generate_fill(disk, mode, 1.0, 1.0, outline=True)
            assert len(lined.chains) == len(plain.chains)

    def test_degenerate_region(self):
        flat = core.Region([[(0, 0), (10, 0), (20, 0)]])
        for mode in core.MODES:
            res = core.generate_fill(flat, mode, 1.0, 1.0)
            assert not res.chains and res.warnings

    def test_too_small_warns(self):
        tiny = core.Region([circle(0, 0, 0.4, n=32)])
        res = core.generate_fill(tiny, "concentric", 2.0, 2.0)
        assert not res.chains
        assert res.warnings

    def test_path_d_smooth_and_straight(self, disk):
        res = core.generate_fill(disk, "hatch", 1.0, 1.0)
        d = core.chains_to_path_d(res.chains)
        assert d.startswith("M") and "C" not in d
        res2 = core.generate_fill(disk, "concentric", 1.0, 1.0)
        d2 = core.chains_to_path_d(res2.chains, smooth=True, fit_tol=0.1)
        assert "C" in d2
