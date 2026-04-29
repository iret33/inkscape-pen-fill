# Pen Plotter Fill

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Inkscape 1.x](https://img.shields.io/badge/Inkscape-1.x-orange.svg)](https://inkscape.org)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#installation)
[![Pen plotters](https://img.shields.io/badge/for-pen%20plotters-purple.svg)](#)

An Inkscape extension that turns closed shapes into solid fills for **pen plotters** by generating concentric inset copies of the outline. The pen draws each loop in turn, ending up with the shape filled with parallel pen strokes — the way plotters can "fill" a region without lifting the pen mid-area.

Pure-Python, **no `pip install` step**, no compiled libraries — drop the two files into Inkscape's extensions folder and you're done.

![Concentric and spiral fill demo](images/hero.svg)

---

## Features

- 🖊️ **Pen-size aware** — set your physical pen width in millimeters; the extension spaces the inset copies so adjacent pen strokes just touch (or overlap, if you want denser coverage)
- 🌀 **Two fill modes** — *concentric loops* (separate closed paths, simple) or *spiral* (one continuous polyline that bridges between loops, fewer pen lifts and faster plots)
- 🎯 **Precise count control** — leave it at 0 to fill until the shape collapses, or click the spinbox `+` / `−` to set an exact number of copies
- ✨ **Smooth output** — bezier curves are sampled finely *and* re-fit as cubic Bezier `C` segments using Schneider's algorithm, so the inset paths keep the same visual quality as the original (no chunky polylines)
- 🔄 **Robust on complex shapes** — handles concave corners and sharp acute angles by iteratively pruning overshooting vertices, instead of giving up at the first self-intersection
- 📦 **Zero dependencies** — entirely pure Python, no Clipper / pyclipper / Shapely needed

---

## Installation

1. Locate Inkscape's user extensions folder:
   - **Linux / macOS:** `~/.config/inkscape/extensions/`
   - **Windows:** `%APPDATA%\inkscape\extensions\` (typically `C:\Users\<you>\AppData\Roaming\inkscape\extensions\`)
   - You can also find the path in *Inkscape → Edit → Preferences → System → User extensions*.
2. Copy `pen_fill.py` and `pen_fill.inx` from this repo into that folder.
3. Restart Inkscape.

That's it — no pip install, no compilation.

---

## Usage

1. Draw or import a shape. If it's a primitive (rectangle, ellipse, star), select it and run **Path → Object to Path** first.
2. Select the shape(s) you want to fill.
3. Open **Extensions → Generate from Path → Pen Plotter Fill**.
4. Set:
   - **Pen size (mm)** — the physical width of your plotter pen tip (e.g. `0.3` for a typical fineliner).
   - **Overlap factor** — `1.0` means adjacent strokes just touch; `<1.0` overlaps for denser fill; `>1.0` leaves gaps.
   - **Number of copies** — `0` = fill until the shape collapses; otherwise use the `+` / `−` to dial in an exact count.
   - **Fill mode** — *Concentric* or *Spiral*.
   - **Group** / **Keep original** — output organization options.
5. Apply.

The result is added to your document as a new path (or a group of paths) with the stroke width set to your pen size for a faithful preview of the plotted output.

### Pen size controls fill density

Smaller pens produce more inset copies and therefore denser coverage:

![Same shape filled at three pen sizes](images/pen-sizes.svg)

### Works on complex curves

Calligraphy strokes, organic shapes, and concave regions are handled by iteratively pruning vertices that overshoot during the offset, so the chain keeps going through sharp corners instead of giving up:

![Calligraphy stroke before and after fill](images/calligraphy.svg)

---

## How it works

For each selected closed path:

1. **Flatten** the path's Bezier segments into a polyline at a tolerance proportional to the pen size (so finer pens automatically get finer detail).
2. **Inset** the polygon by `pen_size × overlap` repeatedly. The offset uses an angle-bisector method with two robustness layers:
   - any vertex whose turn direction has flipped relative to the source polygon is pruned (handles sharp concave corners and overshoot);
   - every iteration must strictly shrink the polygon's area, which prevents degenerate oscillation on shapes like sharp stars.
3. **Refit** the resulting polyline back into smooth cubic Bezier segments with Schneider's algorithm (Graphics Gems, 1990) so the output uses real `C` commands and stays visually smooth.
4. In **spiral mode**, each successive loop is rotated to start at the point closest to the end of the previous loop and the loops are concatenated into a single polyline — minimizing pen-up moves on the plotter.

---

## Limitations

- Shapes with very sharp inward (concave) corners may stop earlier than a full Clipper-style polygon offset would. If a calligraphy character has multiple disconnected strokes, **Path → Break Apart** before running gives finer-grained control.
- The chain stops when the offset can't shrink the polygon further, which for unusually thin or branchy shapes might leave a small unfilled region. Use a smaller pen size / overlap factor to fill closer to the centerline.

---

## Tested with

- Inkscape 1.3 / 1.4 on Windows 10/11 and Linux
- Pen plotters: AxiDraw, NextDraw, generic GRBL pen plotters
- Pens: 0.1 mm – 1.0 mm fineliners, gel, and brush pens

---

## Contributing

Bug reports, feature requests, and pull requests are all welcome. If you have a shape that breaks the offset algorithm, attaching the SVG to your issue is the most useful thing you can do.

---

## License

[MIT](LICENSE) — do whatever you want with it, attribution appreciated.
