#!/usr/bin/env python3
"""
Regenerate the demo SVGs in this folder with the real fill engine
(penfill_core). Deterministic — same input, same pixels.

    python3 images/generate.py
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import penfill_core as core  # noqa: E402


# ---------------------------------------------------------------- shapes

def heart(scale=2.4, ox=0.0, oy=0.0, n=240):
    pts = []
    for i in range(n):
        t = math.pi * 2 * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t)
              - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((x * scale + ox, y * scale + oy))
    return pts


def circle(cx, cy, r, n=200):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def flower(petals=6, r_outer=42, r_inner=16, n_per=60):
    pts = []
    for i in range(petals * n_per):
        t = 2 * math.pi * i / (petals * n_per)
        r = r_inner + (r_outer - r_inner) * (math.sin(petals * t / 2) ** 2)
        pts.append((r * math.cos(t), r * math.sin(t)))
    return pts


def swash(n=360):
    """Ribbon-like wavy closed shape mimicking a calligraphy stroke."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        r = 32 + 9 * math.sin(3 * t) + 4 * math.cos(5 * t) + 2 * math.sin(7 * t)
        pts.append((r * math.cos(t), r * math.sin(t) * 0.85))
    return pts


def letter_o():
    outer = []
    inner = []
    for i in range(160):
        t = 2 * math.pi * i / 160
        outer.append((30 * math.cos(t), 38 * math.sin(t)))
        inner.append((16 * math.cos(t), 23 * math.sin(t)))
    return outer, inner


# ---------------------------------------------------------------- render

INK = "#16324c"       # deep ink blue
INK2 = "#c0392b"      # crimson accent
INK3 = "#1e7d46"      # forest accent
OUTLINE = "#222222"


def fill_to_d(region, mode, pen, spacing, **kw):
    res = core.generate_fill(region, mode, pen, spacing, **kw)
    chains, _, _ = core.optimize_order(res.chains, join_tol=pen * 0.1)
    return core.chains_to_path_d(chains, smooth=res.smooth,
                                 fit_tol=max(pen * 0.08, 0.02))


def lifts(region, mode, pen, spacing, **kw):
    res = core.generate_fill(region, mode, pen, spacing, **kw)
    return core.optimize_order(res.chains, join_tol=pen * 0.1)[2][2]


def outline_d(region):
    return core.chains_to_path_d([r + [r[0]] for r in region.rings])


def bbox_of(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def panel(x, y, w, h, label, content):
    return (
        '<g transform="translate({x},{y})">'
        '<rect class="panel" width="{w}" height="{h}" rx="7"/>'
        "{content}"
        '<text class="label" x="{cx}" y="{ly}">{label}</text>'
        "</g>"
    ).format(x=x, y=y, w=w, h=h, content=content,
             cx=w / 2, ly=h + 20, label=label)


def centred(d, css, bb, w, h, extra="", fit=0.82):
    """Centre the shape in the panel and scale it to `fit` of the panel."""
    cx = (bb[0] + bb[2]) / 2
    cy = (bb[1] + bb[3]) / 2
    sw = bb[2] - bb[0]
    sh = bb[3] - bb[1]
    s = min(w * fit / sw, h * fit / sh) if sw > 0 and sh > 0 else 1.0
    return ('<g transform="translate({tx},{ty}) scale({s:.4f}) '
            'translate({mx},{my})"><path class="{css}" d="{d}"{extra}/></g>'
            ).format(tx=w / 2, ty=h / 2, s=s, mx=-cx, my=-cy,
                     css=css, d=d, extra=extra)


STYLE = """<style>
  .bg { fill: #fafafa }
  .panel { fill: #ffffff; stroke: #e3e3e3; stroke-width: 1 }
  .label { font: 600 13px 'Helvetica Neue', Arial, sans-serif;
           fill: #555; text-anchor: middle }
  .sub { font: 500 11px 'Helvetica Neue', Arial, sans-serif;
         fill: #888; text-anchor: middle }
  .outline { fill: none; stroke: #222; stroke-width: 1.3;
             stroke-linejoin: round }
  .ink, .ink2, .ink3, .bad { fill: none; stroke-linecap: round;
                             stroke-linejoin: round }
  .ink  { stroke: %s }
  .ink2 { stroke: %s }
  .ink3 { stroke: %s }
  .bad  { stroke: #c0392b }
</style>""" % (INK, INK2, INK3)


def build_svg(width, height, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            '{style}<rect class="bg" width="100%" height="100%"/>{body}</svg>'
            ).format(w=width, h=height, style=STYLE, body=body)


def write(name, svg):
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote", path)


# ---------------------------------------------------------------- demos

def make_hero():
    """All seven fill styles + the outline, on one heart."""
    shape = heart()
    region = core.Region([shape])
    bb = bbox_of([shape])
    pen, sp = 1.05, 1.05

    cases = [
        ("Outline", "outline", outline_d(region), 1.3),
        ("Concentric rings", "ink",
         fill_to_d(region, "concentric", pen, sp), pen * 0.62),
        ("Spiral — one stroke", "ink2",
         fill_to_d(region, "spiral", pen, sp), pen * 0.62),
        ("Round spiral", "ink",
         fill_to_d(region, "archimedean", pen, sp), pen * 0.62),
        ("Hatch 45°", "ink3",
         fill_to_d(region, "hatch", pen, sp * 1.25, angle=45.0), pen * 0.62),
        ("Cross-hatch", "ink",
         fill_to_d(region, "crosshatch", pen, sp * 1.9, angle=45.0),
         pen * 0.55),
        ("Waves", "ink2",
         fill_to_d(region, "sine", pen, sp * 1.6, angle=0.0,
                   sine_amplitude=1.1, sine_wavelength=7.0), pen * 0.6),
        ("Hilbert maze", "ink3",
         fill_to_d(region, "hilbert", pen, sp * 1.5), pen * 0.55),
    ]

    pw, ph, pad = 205, 190, 16
    cols = 4
    rows = (len(cases) + cols - 1) // cols
    width = cols * pw + (cols + 1) * pad
    height = rows * (ph + 42) + pad

    body = []
    for i, (label, css, d, sw) in enumerate(cases):
        col, row = i % cols, i // cols
        x = pad + col * (pw + pad)
        y = pad + row * (ph + 42)
        content = centred(d, css, bb, pw, ph,
                          ' stroke-width="{:.2f}"'.format(sw))
        body.append(panel(x, y, pw, ph, label, content))
    return build_svg(width, height, "".join(body))


def make_holes():
    """The v2 headline fix: subpaths stay together, holes are respected."""
    outer, inner = letter_o()
    region_correct = core.Region([outer, inner])       # with hole
    region_naive = core.Region([outer])                # v1: outer only
    hole_only = core.Region([inner])                   # v1 also fills hole
    bb = bbox_of([outer])
    pen, sp = 1.2, 1.2

    naive = (fill_to_d(region_naive, "concentric", pen, sp))
    correct = fill_to_d(region_correct, "concentric", pen, sp)
    hole_fill = fill_to_d(hole_only, "concentric", pen, sp)

    pw, ph, pad = 250, 210, 18
    body = [
        panel(pad, pad, pw, ph, "v1 — every subpath filled solid",
              centred(naive, "bad", bb, pw, ph,
                      ' stroke-width="0.75" opacity="0.85"')
              + centred(hole_fill, "bad", bb, pw, ph,
                        ' stroke-width="0.75" opacity="0.85"')
              + centred(outline_d(region_correct), "outline", bb, pw, ph)),
        panel(pad * 2 + pw, pad, pw, ph, "v2 — holes respected",
              centred(correct, "ink", bb, pw, ph, ' stroke-width="0.75"')
              + centred(outline_d(region_correct), "outline", bb, pw, ph)),
    ]
    return build_svg(pw * 2 + pad * 3, ph + 60, "".join(body))


def make_serpentine():
    """Connected hatch = drastically fewer pen lifts."""
    shape = swash()
    region = core.Region([shape])
    bb = bbox_of([shape])
    pen, sp = 1.15, 1.5

    kw = dict(angle=45.0)
    d_loose = fill_to_d(region, "hatch", pen, sp, connect=False, **kw)
    d_tight = fill_to_d(region, "hatch", pen, sp, connect=True, **kw)

    pw, ph, pad = 280, 220, 18
    body = [
        panel(pad, pad, pw, ph,
              "separate lines — {} pen lifts".format(
                  lifts(region, "hatch", pen, sp, connect=False, **kw)),
              centred(d_loose, "ink", bb, pw, ph, ' stroke-width="0.7"')),
        panel(pad * 2 + pw, pad, pw, ph,
              "joined into long strokes — {} pen lifts".format(
                  lifts(region, "hatch", pen, sp, connect=True, **kw)),
              centred(d_tight, "ink2", bb, pw, ph, ' stroke-width="0.7"')),
    ]
    return build_svg(pw * 2 + pad * 3, ph + 60, "".join(body))


def make_density():
    """Line density driven by each shape's fill tone."""
    pen = 1.0
    tones = [0.15, 0.4, 0.65, 0.85]     # coverage = 1 - luminance
    pw, ph, pad = 160, 160, 16
    body = []
    for i, cov in enumerate(tones):
        region = core.Region([circle(0, 0, 58)])
        bb = bbox_of(region.rings)
        spacing = pen / cov
        d = fill_to_d(region, "hatch", pen, spacing, angle=45.0)
        grey = int(255 * (1 - cov))
        x = pad + i * (pw + pad)
        swatch = ('<circle cx="{cx}" cy="26" r="9" fill="rgb({g},{g},{g})" '
                  'stroke="#ccc"/>').format(cx=pw / 2, g=grey)
        body.append(panel(x, pad, pw, ph,
                          "{:.0f}% grey".format(cov * 100),
                          centred(d, "ink", bb, pw, ph,
                                  ' stroke-width="0.62"') + swatch))
    return build_svg(4 * pw + 5 * pad, ph + 58, "".join(body))


def make_pen_sizes():
    """Same flower, three pen widths."""
    shape = flower()
    region = core.Region([shape])
    bb = bbox_of([shape])
    cases = [(2.6, "ink", "Wide pen (≈ 1 mm)"),
             (1.4, "ink2", "Medium pen (≈ 0.5 mm)"),
             (0.75, "ink3", "Fine pen (≈ 0.25 mm)")]
    pw, ph, pad = 220, 230, 18
    body = []
    for i, (pen, css, label) in enumerate(cases):
        d = fill_to_d(region, "spiral", pen, pen)
        x = pad + i * (pw + pad)
        body.append(panel(x, pad, pw, ph, label,
                          centred(d, css, bb, pw, ph,
                                  ' stroke-width="{:.2f}"'.format(pen * 0.6))))
    return build_svg(3 * pw + 4 * pad, ph + 60, "".join(body))


def make_calligraphy():
    """Organic curvy shape — outline vs one-stroke spiral fill."""
    shape = swash()
    region = core.Region([shape])
    bb = bbox_of([shape])
    pen = 1.0
    d = fill_to_d(region, "spiral", pen, pen)

    pw, ph, pad = 300, 240, 18
    body = [
        panel(pad, pad, pw, ph, "Original calligraphy stroke",
              centred(outline_d(region), "outline", bb, pw, ph)),
        panel(pad * 2 + pw, pad, pw, ph, "Spiral fill — one stroke",
              centred(d, "ink", bb, pw, ph, ' stroke-width="0.62"')),
    ]
    return build_svg(pw * 2 + pad * 3, ph + 60, "".join(body))


def make_outline():
    """v2.1: the optional outline pass gives hatch fills a crisp edge."""
    shape = heart()
    region = core.Region([shape])
    bb = bbox_of([shape])
    pen, sp = 1.05, 1.9
    kw = dict(angle=45.0)
    pw, ph, pad = 250, 210, 18
    body = [
        panel(pad, pad, pw, ph, "Hatch",
              centred(fill_to_d(region, "hatch", pen, sp, **kw), "ink", bb,
                      pw, ph, ' stroke-width="0.62"')),
        panel(pad * 2 + pw, pad, pw, ph, "Hatch + outline",
              centred(fill_to_d(region, "hatch", pen, sp, outline=True,
                                **kw), "ink", bb, pw, ph,
                      ' stroke-width="0.62"')),
    ]
    return build_svg(pw * 2 + pad * 3, ph + 60, "".join(body))


if __name__ == "__main__":
    write("hero.svg", make_hero())
    write("holes.svg", make_holes())
    write("serpentine.svg", make_serpentine())
    write("density.svg", make_density())
    write("pen-sizes.svg", make_pen_sizes())
    write("calligraphy.svg", make_calligraphy())
    write("outline.svg", make_outline())
