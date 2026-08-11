# Changelog

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
