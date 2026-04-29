#!/usr/bin/env python3
"""
Regenerate the demo SVGs in this folder using the real offset and
curve-fitting code from pen_fill.py. Run as:  python3 images/generate.py
"""

import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Stub inkex so pen_fill.py imports outside of Inkscape.
inkex = types.ModuleType("inkex")
inkex.EffectExtension = object
inkex.PathElement = object
inkex.Group = object
inkex.Boolean = bool
inkex.Style = dict
sys.modules["inkex"] = inkex
bezier = types.ModuleType("inkex.bezier")
bezier.cspsubdiv = lambda *a, **kw: None
sys.modules["inkex.bezier"] = bezier

import pen_fill as pf  # noqa: E402


# ---------- shape generators ----------

def heart(scale=4.0, n=300):
    pts = []
    for i in range(n):
        t = math.pi * 2 * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x * scale, y * scale))
    return pts


def calligraphic_swash(n=400):
    """A ribbon-like wavy closed shape that mimics a calligraphy stroke."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        r = 32 + 9 * math.sin(3 * t) + 4 * math.cos(5 * t) + 2 * math.sin(7 * t)
        pts.append((r * math.cos(t), r * math.sin(t) * 0.85))
    return pts


def flower(petals=6, r_outer=50, r_inner=18, n_per=60):
    pts = []
    for i in range(petals * n_per):
        t = 2 * math.pi * i / (petals * n_per)
        r = r_inner + (r_outer - r_inner) * (math.sin(petals * t / 2) ** 2)
        pts.append((r * math.cos(t), r * math.sin(t)))
    return pts


# ---------- offset chain helpers ----------

def chain(poly, step):
    out = [poly]
    cur = poly
    for _ in range(2000):
        r = pf.offset_polygon(cur, step)
        if not r:
            break
        cur = r[0]
        out.append(cur)
    return out


def poly_to_d_concentric(loops):
    parts = []
    for poly in loops:
        if len(poly) < 2:
            continue
        x0, y0 = poly[0]
        parts.append(f"M {x0:.3f} {y0:.3f}")
        for x, y in poly[1:]:
            parts.append(f"L {x:.3f} {y:.3f}")
        parts.append("Z")
    return " ".join(parts)


def poly_to_d_spiral(loops):
    if not loops:
        return ""
    points = list(loops[0]) + [loops[0][0]]
    for nxt in loops[1:]:
        end = points[-1]
        best = min(
            range(len(nxt)),
            key=lambda i: (nxt[i][0] - end[0]) ** 2 + (nxt[i][1] - end[1]) ** 2,
        )
        rotated = nxt[best:] + nxt[:best]
        points.extend(rotated)
        points.append(rotated[0])
    parts = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.3f} {y:.3f}")
    return " ".join(parts)


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


# ---------- SVG building ----------

STYLE = """<style>
  .panel { fill: #ffffff; stroke: #e3e3e3; stroke-width: 1; rx: 6 }
  .label { font: 600 14px sans-serif; fill: #555; text-anchor: middle }
  .outline { fill: none; stroke: #222; stroke-width: 1.4 }
  .fill { fill: none; stroke: #1a73e8; stroke-width: 0.55; stroke-linejoin: round; stroke-linecap: round }
  .spiral { fill: none; stroke: #d93025; stroke-width: 0.55; stroke-linejoin: round; stroke-linecap: round }
  .accent { fill: none; stroke: #137333; stroke-width: 0.55; stroke-linejoin: round; stroke-linecap: round }
</style>
<rect width="100%" height="100%" fill="#fafafa"/>"""


def panel_group(d, css, panel_x, panel_y, panel_w, panel_h, shape_bbox):
    cx_p = panel_x + panel_w / 2
    cy_p = panel_y + panel_h / 2
    cx_s = (shape_bbox[0] + shape_bbox[2]) / 2
    cy_s = (shape_bbox[1] + shape_bbox[3]) / 2
    return (
        f'<g transform="translate({cx_p - cx_s:.2f}, {cy_p - cy_s:.2f})">'
        f'<path class="{css}" d="{d}"/></g>'
    )


def panel_rect(x, y, w, h):
    return f'<rect class="panel" x="{x}" y="{y}" width="{w}" height="{h}" rx="6"/>'


def build_svg(width, height, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f"{STYLE}{body}</svg>"
    )


def write(name, svg):
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {path}")


# ---------- demos ----------

def make_hero():
    """Three panels: outline / concentric / spiral of a heart."""
    shape = heart()
    bb = bbox(shape)
    loops = chain(shape, 2.2)

    pw, ph = 280, 320
    pad = 18
    width = 3 * pw + 4 * pad
    height = ph + 60

    body = []
    titles = ["Original outline", "Concentric fill", "Spiral fill"]
    contents = [
        (poly_to_d_concentric([shape]), "outline"),
        (poly_to_d_concentric(loops), "fill"),
        (poly_to_d_spiral(loops), "spiral"),
    ]
    for i, (d, css) in enumerate(contents):
        x = pad + i * (pw + pad)
        body.append(panel_rect(x, pad, pw, ph))
        body.append(panel_group(d, css, x, pad, pw, ph, bb))
        body.append(
            f'<text class="label" x="{x + pw/2}" y="{ph + pad + 32}">{titles[i]}</text>'
        )

    return build_svg(width, height, "".join(body))


def make_calligraphy():
    """Side-by-side: original swash vs concentric fill — meant to show the
    extension working on a complex curvy shape, the calligraphy use case."""
    shape = calligraphic_swash()
    bb = bbox(shape)
    loops = chain(shape, 1.0)

    pw, ph = 360, 300
    pad = 18
    width = 2 * pw + 3 * pad
    height = ph + 60

    body = [
        panel_rect(pad, pad, pw, ph),
        panel_group(poly_to_d_concentric([shape]), "outline", pad, pad, pw, ph, bb),
        f'<text class="label" x="{pad + pw/2}" y="{ph + pad + 32}">Original calligraphy stroke</text>',
        panel_rect(pad * 2 + pw, pad, pw, ph),
        panel_group(poly_to_d_concentric(loops), "fill", pad * 2 + pw, pad, pw, ph, bb),
        f'<text class="label" x="{pad*2 + pw + pw/2}" y="{ph + pad + 32}">Filled with pen strokes</text>',
    ]

    return build_svg(width, height, "".join(body))


def make_pen_sizes():
    """Same flower at three pen widths to illustrate the pen-size control."""
    shape = flower()
    bb = bbox(shape)

    pw, ph = 240, 280
    pad = 18
    width = 3 * pw + 4 * pad
    height = ph + 60

    cases = [
        (3.5, "fill", "Wide pen (≈ 1 mm)"),
        (1.8, "spiral", "Medium pen (≈ 0.5 mm)"),
        (0.9, "accent", "Fine pen (≈ 0.25 mm)"),
    ]

    body = []
    for i, (step, css, title) in enumerate(cases):
        x = pad + i * (pw + pad)
        loops = chain(shape, step)
        d = poly_to_d_concentric(loops)
        body.append(panel_rect(x, pad, pw, ph))
        body.append(panel_group(d, css, x, pad, pw, ph, bb))
        body.append(
            f'<text class="label" x="{x + pw/2}" y="{ph + pad + 32}">{title}</text>'
        )

    return build_svg(width, height, "".join(body))


if __name__ == "__main__":
    write("hero.svg", make_hero())
    write("calligraphy.svg", make_calligraphy())
    write("pen-sizes.svg", make_pen_sizes())
