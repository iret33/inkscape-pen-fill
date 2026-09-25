#!/usr/bin/env python3
"""
Pen Plotter Fill — Inkscape extension (v2.1).

Turns closed shapes into plotter-ready fills: hatch, cross-hatch, sine
waves, concentric rings, continuous spirals, Archimedean spirals and
Hilbert curves — with hole support, serpentine joining, an optional
outline pass, plot-order optimization and pen-lift statistics.

All geometry lives in penfill_core.py (pure Python, no inkex); this file
is only the Inkscape glue: selection walking, transforms, units, styles
and document output.
"""

import inkex
from inkex import Group, PathElement
from inkex.bezier import cspsubdiv

import penfill_core as core

__version__ = core.__version__

# How finely input Beziers are flattened before filling, as a fraction of
# the pen width. Finer pens automatically get finer geometry.
FLATNESS_RATIO = 0.05

# Stroke ends closer than this are drawn as one stroke (no pen lift).
JOIN_TOLERANCE_MM = 0.05

# Rough hardware speeds for the time estimate in the stats dialog.
EST_DRAW_MMS = 40.0     # pen-down drawing speed
EST_TRAVEL_MMS = 100.0  # pen-up travel speed
EST_LIFT_S = 0.35       # cost of one pen lift/lower cycle

# Every generated fill carries the id of its source shape, so applying
# again replaces it — and a fill is never itself filled.
SOURCE_ATTR = "data-penfill-source"
LABEL_PREFIX = "penfill "


# ----------------------------------------------------------------------
# Style helpers
# ----------------------------------------------------------------------

def _style(elem):
    """Computed style. specified_style() needs inkex 1.2+; Inkscape 1.1
    only has composed_style()."""
    getter = getattr(elem, "specified_style", None) or elem.composed_style
    return getter()


def _prop(elem, key):
    """A style property as drawn — a clone takes it from its original."""
    if isinstance(elem, inkex.Use) and elem.href is not None:
        value = _style(elem.href).get(key)
        if value is not None:
            return value
    return _style(elem).get(key)


def _opacity(value):
    """Parse an opacity ("0.5", "50%"); anything unreadable counts as 1."""
    try:
        text = str(value).strip()
        num = float(text[:-1]) / 100.0 if text.endswith("%") else float(text)
    except (TypeError, ValueError):
        return 1.0
    return min(max(num, 0.0), 1.0)


def _is_hidden(elem):
    return "none" in (elem.get("display"), _style(elem).get("display"))


def _fill_source(elem):
    """Id of the shape a generated fill belongs to, or None. Also knows
    the v2.0 fills, which only carry a "penfill <style> <id>" label."""
    source = elem.get(SOURCE_ATTR)
    if source:
        return source
    parts = (elem.get("inkscape:label") or "").split(" ")
    if len(parts) == 3 and parts[0] + " " == LABEL_PREFIX \
            and parts[1] in core.MODES:
        return parts[2]
    return None


# ----------------------------------------------------------------------
# The extension
# ----------------------------------------------------------------------

class PenFill(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--tab", type=str, default="fill")
        # Fill tab
        pars.add_argument("--fill_mode", type=str, default="concentric")
        pars.add_argument("--pen_size", type=float, default=0.5)
        pars.add_argument("--spacing_factor", type=float, default=1.0)
        pars.add_argument("--angle", type=float, default=45.0)
        pars.add_argument("--outline", type=inkex.Boolean, default=False)
        pars.add_argument("--keep_inside", type=inkex.Boolean, default=True)
        pars.add_argument("--connect", type=inkex.Boolean, default=True)
        # More tab
        pars.add_argument("--color_mode", type=str, default="black")
        pars.add_argument("--custom_color", type=inkex.Color,
                          default=inkex.Color("#000000"))
        pars.add_argument("--density_from_fill", type=inkex.Boolean,
                          default=False)
        pars.add_argument("--cross_angle", type=float, default=90.0)
        pars.add_argument("--sine_amplitude", type=float, default=1.5)
        pars.add_argument("--sine_wavelength", type=float, default=6.0)
        pars.add_argument("--replace", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_original", type=inkex.Boolean, default=True)
        pars.add_argument("--show_stats", type=inkex.Boolean, default=False)

    # ------------------------------------------------------------------

    def effect(self):
        opt = self.options
        if not self.svg.selection:
            inkex.errormsg(
                "Pen Plotter Fill: select at least one closed shape first.")
            return
        if opt.pen_size <= 0 or opt.spacing_factor <= 0:
            inkex.errormsg("Pen size and line spacing must be > 0.")
            return

        shapes = []
        seen = set()
        for elem in self.svg.selection:
            self._collect(elem, shapes, seen)
        if not shapes:
            inkex.errormsg(
                "Nothing fillable in the selection. Shapes, paths and "
                "groups of those work; text must be converted with "
                "Path > Object to Path first.")
            return

        earlier = self._earlier_fills() if opt.replace else {}
        pen = self.svg.unittouu("{}mm".format(opt.pen_size))
        totals = {"draw": 0.0, "travel": 0.0, "lifts": 0, "shapes": 0}
        for elem in shapes:
            name = elem.get_id()
            try:
                made = self._fill(elem, name, pen, totals)
            except Exception as exc:   # keep going on multi-selection
                inkex.errormsg("<{}>: failed — {}".format(name, exc))
                continue
            if made is not None:
                for old in earlier.get(name, ()):
                    if old.getparent() is not None:
                        old.getparent().remove(old)

        if opt.show_stats and totals["shapes"]:
            inkex.errormsg(self._stats_text(totals))

    # ------------------------------------------------------------------

    def _collect(self, elem, out, seen):
        """Walk the selection into fillable shapes. Groups are opened;
        hidden objects, images and earlier fills are left alone, and an
        object selected twice (itself + its group) is filled once."""
        if not isinstance(elem, inkex.ShapeElement) or elem in seen:
            return
        seen.add(elem)
        if _is_hidden(elem) or _fill_source(elem):
            return
        if isinstance(elem, inkex.Group):
            for child in elem:
                self._collect(child, out, seen)
        elif isinstance(elem, (inkex.TextElement, inkex.FlowRoot)):
            inkex.errormsg(
                "Skipping text <{}> — run Path > Object to Path on it "
                "first.".format(elem.get_id()))
        elif not isinstance(elem, inkex.Image):
            try:
                if len(elem.path) > 0:
                    out.append(elem)
            except (AttributeError, TypeError, ValueError):
                pass

    def _earlier_fills(self):
        """Fills made by earlier runs, keyed by their source shape's id."""
        found = {}
        for elem in self.svg.xpath(
                '//*[@{} or starts-with(@inkscape:label, "{}")]'
                .format(SOURCE_ATTR, LABEL_PREFIX)):
            source = _fill_source(elem)
            if source:
                found.setdefault(source, []).append(elem)
        return found

    # ------------------------------------------------------------------

    def _region(self, elem, pen):
        """Flatten the element (all subpaths together!) into a Region."""
        path = elem.path.transform(elem.composed_transform()).to_absolute()
        csp = path.to_superpath()
        cspsubdiv(csp, max(pen * FLATNESS_RATIO, 0.002))
        rings = []
        for sub in csp:
            ring = [(pt[1][0], pt[1][1]) for pt in sub]
            if len(ring) >= 2 and core._dist(ring[0], ring[-1]) < 1e-9:
                ring = ring[:-1]
            if len(ring) >= 3:
                rings.append(ring)
        return core.Region(rings, _prop(elem, "fill-rule") or "nonzero")

    def _ink_tone(self, elem):
        """How dark the shape's fill renders: 0 = white .. 1 = black,
        including fill-opacity and opacity. None for no fill / gradients."""
        fill = _prop(elem, "fill") or "black"      # SVG default is black
        if fill == "none":
            return None
        try:
            r, g, b = inkex.Color(fill).to_rgb()[:3]
        except Exception:
            return None
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        return ((1.0 - lum) * _opacity(_prop(elem, "fill-opacity"))
                * _opacity(_prop(elem, "opacity")))

    def _stroke_color(self, elem):
        mode = self.options.color_mode
        if mode == "custom":
            return str(self.options.custom_color)
        if mode == "shape":
            for key in ("fill", "stroke"):
                val = _prop(elem, key)
                if val and val != "none":
                    try:
                        return str(inkex.Color(val))
                    except Exception:
                        continue
        return "#000000"

    # ------------------------------------------------------------------

    def _fill(self, elem, name, pen, totals):
        """Fill one shape; returns the new fill group, or None."""
        opt = self.options
        region = self._region(elem, pen)
        if region.is_empty():
            inkex.errormsg("<{}> has no closed area — skipped.".format(name))
            return None

        # Line spacing; with "density from fill colour", darker = denser.
        spacing = pen * opt.spacing_factor
        tone = self._ink_tone(elem) if opt.density_from_fill else None
        if tone is not None:
            if tone < 0.06:
                inkex.errormsg("<{}> fill is nearly white — skipped (density "
                               "from colour is on)".format(name))
                return None
            spacing /= tone

        mm = self.svg.unittouu("1mm")
        result = core.generate_fill(
            region,
            opt.fill_mode,
            pen,
            spacing,
            angle=opt.angle,
            cross_angle=opt.cross_angle,
            sine_amplitude=opt.sine_amplitude * mm,
            sine_wavelength=opt.sine_wavelength * mm,
            edge_gap=(pen * 0.5 if opt.keep_inside else 0.0),
            connect=opt.connect,
            outline=opt.outline,
        )
        for warning in result.warnings:
            inkex.errormsg("<{}>: {}".format(name, warning))

        chains, _, stats = core.optimize_order(result.chains,
                                               JOIN_TOLERANCE_MM * mm)
        d = core.chains_to_path_d(chains, smooth=result.smooth,
                                  fit_tol=max(pen * 0.08, 0.005))
        if not d:
            return None

        path = PathElement()
        path.set("d", d)
        path.style = inkex.Style({
            "fill": "none",
            "stroke": self._stroke_color(elem),
            "stroke-width": "{:.4f}".format(pen),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        })
        group = Group()
        group.append(path)
        group.set("inkscape:label", "{}{} {}".format(
            LABEL_PREFIX, opt.fill_mode, name))
        group.set(SOURCE_ATTR, name)

        # Geometry is in document coords; place the fill next to the
        # source and cancel the parent's transform so it lands exactly on
        # top of the source shape, inside the same layer.
        parent = elem.getparent()
        inverse = -inkex.Transform(parent.composed_transform())
        if inverse != inkex.Transform():
            group.transform = inverse
        parent.insert(parent.index(elem) + 1, group)
        if not opt.keep_original:
            parent.remove(elem)

        totals["draw"] += stats[0]
        totals["travel"] += stats[1]
        totals["lifts"] += stats[2]
        totals["shapes"] += 1
        return group

    # ------------------------------------------------------------------

    def _stats_text(self, totals):
        mm = self.svg.unittouu("1mm")
        draw_mm = totals["draw"] / mm
        travel_mm = totals["travel"] / mm
        secs = (draw_mm / EST_DRAW_MMS + travel_mm / EST_TRAVEL_MMS
                + totals["lifts"] * EST_LIFT_S)
        return (
            "Pen Plotter Fill — {} shape(s)\n"
            "  drawing:  {:.0f} mm of ink\n"
            "  travel:   {:.0f} mm pen-up\n"
            "  pen lifts: {}\n"
            "  ~plot time at {:.0f} mm/s: {}:{:02d} min"
        ).format(totals["shapes"], draw_mm, travel_mm, totals["lifts"],
                 EST_DRAW_MMS, int(secs // 60), int(secs % 60))


if __name__ == "__main__":
    PenFill().run()
