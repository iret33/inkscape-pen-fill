# Changelog

## 2.1.0 — 2026-09-25

### Added
- **Outline pass** (*Also draw the outline*): traces the shape edge once,
  at the same half-pen inset as the fill, for a crisp border around
  hatch, wave, round-spiral and maze fills. Concentric and spiral fills
  already start with the outline, so nothing is drawn twice.
- **Apply again to replace**: every fill is tagged with its source shape
  (`data-penfill-source`); re-applying swaps the old fill for the new
  one instead of stacking. v2.0 fills are recognised by their label and
  replaced too. *Replace the shape's earlier fill* can be unticked to
  layer fills on purpose.
- Clones (`<use>`) take colour, tone and fill rule from their original.

### Fixed
- Serpentine joining with *Keep ink inside* (the default) rejected most
  bridges, because they run exactly along the slightly wiggly inset
  outline. Bridges may now graze it by ≤ 7.5 % of the pen width: disc
  hatch 14 → 2 strokes, donut 32 → 8, heart cross-hatch 85 → 13. When the
  nearest bridge is blocked the next-nearest is tried.
- Overlapping subpaths (nonzero union, e.g. combined circles) and
  subpaths sharing an edge: the distance field treated the hidden inner
  edges as outline, so rings circled each piece and hatch insets left
  gaps. Only edges with empty space on one side count now.
- Spiral on shapes with holes: rings are linked to the nearest ring one
  level out (not the first one that contains them), so a donut spirals
  in 2 strokes instead of 9. Rings without a parent were silently
  dropped — every ring is now drawn.
- Waves: rows alternated phase and crossed each other whenever the wave
  was taller than the line spacing (always, with the defaults),
  double-inking the paper. Rows now run parallel.
- Hilbert maze ignored the line spacing (up to ~2× too dense). The curve
  now uses exactly the requested spacing, centred on the shape, and
  warns if it has to be capped.
- Round spiral started with a duplicated point (degenerate tangent).
- Hidden objects (`display:none`) and images inside a selected group
  were filled; earlier fills were filled again on select-all; an object
  selected together with its group was filled twice.
- Inkscape 1.1: `specified_style()` only exists from inkex 1.2, so every
  shape failed; falls back to `composed_style()` now.
- *Line density from fill colour* now takes `fill-opacity` and
  `opacity` into account (a 50 % black shape plots as mid-grey).

### Changed
- Simpler dialog: three tabs (Fill / More / Help) instead of five.
  *Optimize plotting order* (never makes things worse), *Join tolerance*
  (fixed at 0.05 mm) and *Group the generated fill* (always grouped) are
  no longer options.
- Faster scanlines: edges are bucketed by height, so point-in-shape
  tests no longer scan every edge (waves ~5× faster on big shapes). The
  distance-field band is built from short edge pieces, which also avoids
  a slowdown on long diagonal edges. The trade-off: ring and spiral fills
  of one path with many subpaths now check which edges are real outline
  (~1.6× slower on a 30-letter combined path, still under a second).
- `penfill_core` API: `hatch_fill`/`cross_hatch_fill`/`sine_fill` lost
  the per-line `edge_gap` argument (use an inset region) and gained
  `bridge_ok`; `eroded_region()` became `DistanceField.inset()`;
  `FillResult.closed_flags` and `chains_to_path_d(closed_flags=)` are
  gone (closure is detected from the points); `generate_fill` gained
  `outline=`. Unused helpers removed.

## 2.0.0 — 2026-08-12

Ground-up rewrite of the fill engine. The extension is now three files:
`pen_fill.inx` (UI), `pen_fill.py` (Inkscape glue) and `penfill_core.py`
(pure-Python geometry, importable and unit-tested on its own).

### Added
- **Five new fill styles**: hatch, cross-hatch, sine waves, Archimedean
  spiral (centred on the pole of inaccessibility), Hilbert curve — next
  to the rewritten concentric and spiral styles.
- **Hole support**: all subpaths of a path form one region under the
  SVG fill rule (even-odd / nonzero). Donuts and letter counters fill
  correctly instead of being inked over.
- **Serpentine joining**: neighbouring hatch/wave rows connect into long
  continuous strokes when the bridge stays inside the shape (53 pen
  lifts → 4 on the demo swash).
- **Plot-order optimization**: greedy nearest-neighbour ordering with
  stroke reversal and merge-on-touch; never accepts a worse order than
  the natural one.
- **Statistics dialog**: ink length, pen-up travel, pen lifts, and a
  rough plot-time estimate.
- **Colour workflows**: stroke colour from each shape's fill (multi-pen
  separations), custom colour, and *line density from fill tone* for
  tonal art from grey-shaded drawings.
- **Primitives work directly**: rectangles, ellipses, stars and whole
  groups no longer need *Object to Path*; text is detected and reported.
- Tabbed dialog (Fill / Style options / Output / Plot / Help).
- Test suite (53 tests: geometry invariants + CLI end-to-end runs) and
  CI workflow.

### Changed
- Concentric fills are computed from a **signed distance field** with
  marching-squares contours instead of an angle-bisector polygon inset:
  shapes that pinch now split into clean islands, sharp corners no
  longer derail the offset, and ring spacing stays even everywhere.
  numpy (bundled with every Inkscape) accelerates it automatically;
  a pure-Python fallback is built in.
- Output is placed **next to the source shape in its own layer** with
  the parent transform cancelled exactly — no more fills dumped at the
  document root in document coordinates.
- Menu moved to **Extensions ▸ Pen Plotter ▸ Pen Plotter Fill**.
- "Keep ink inside the outline" (on by default) insets the fill by half
  a pen width, so wet lines stop at the edge instead of straddling it.

### Fixed
- Concentric fills no longer scatter confetti or leave missing arcs
  near a shape's medial axis (fragmented sub-cell contours are detected
  and replaced by one clean gap-bisecting ring).
- Bezier output no longer produces control-point spikes on sparse rings
  (Schneider alpha blow-up guard + wrap-around tangents on closed loops).
- Stroke width of the generated fill renders correctly inside
  transformed groups.

## 1.0.0 — 2026-02

Initial release: concentric inset fill via angle-bisector offset with
overshoot pruning, spiral linking of the loops, Schneider curve refit,
pen-size-aware spacing.
