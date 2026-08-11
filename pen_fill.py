#!/usr/bin/env python3
"""
Pen Plotter Fill — Inkscape extension (v2).

Turns closed shapes into plotter-ready fills: hatch, cross-hatch, sine
waves, robust concentric rings, continuous spirals, Archimedean spirals
and Hilbert curves — with hole support, serpentine connection, plot-order
optimization and pen-lift statistics.

All geometry lives in penfill_core.py (pure Python, no inkex), this file
is only the Inkscape glue: selection walking, transforms, units, styles
and document output.
"""

import math

import inkex
from inkex import Group, PathElement
from inkex.bezier import cspsubdiv

import penfill_core as core

__version__ = core.__version__

# How finely input Beziers are flattened before filling, as a fraction of
# the pen width. Finer pens automatically get finer geometry.
FLATNESS_RATIO = 0.05

# Rough hardware speeds for the time estimate in the stats dialog.
EST_DRAW_MMS = 40.0     # pen-down drawing speed
EST_TRAVEL_MMS = 100.0  # pen-up travel speed
EST_LIFT_S = 0.35       # cost of one pen lift/lower cycle


class PenFill(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--tab", type=str, default="fill")
        # Fill
        pars.add_argument("--fill_mode", type=str, default="concentric")
        pars.add_argument("--pen_size", type=float, default=0.5)
        pars.add_argument("--spacing_factor", type=float, default=1.0)
        pars.add_argument("--angle", type=float, default=45.0)
        pars.add_argument("--connect", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_inside", type=inkex.Boolean, default=True)
        pars.add_argument("--density_from_fill", type=inkex.Boolean,
                          default=False)
        # Mode options
        pars.add_argument("--cross_angle", type=float, default=90.0)
        pars.add_argument("--sine_amplitude", type=float, default=1.5)
        pars.add_argument("--sine_wavelength", type=float, default=6.0)
        # Output
        pars.add_argument("--color_mode", type=str, default="black")
        pars.add_argument("--custom_color", type=inkex.Color,
                          default=inkex.Color("#000000"))
        pars.add_argument("--group", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_original", type=inkex.Boolean, default=True)
        # Plot
        pars.add_argument("--optimize", type=inkex.Boolean, default=True)
        pars.add_argument("--join_tolerance", type=float, default=0.05)
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

        pen_uu = self.svg.unittouu("{}mm".format(opt.pen_size))
        shapes = []
        for elem in self.svg.selection:
            self._collect_shapes(elem, shapes)
        if not shapes:
            inkex.errormsg(
                "Nothing fillable in the selection. Shapes, paths and "
                "groups of those work; text must be converted with "
                "Path > Object to Path first.")
            return

        totals = {"draw": 0.0, "travel": 0.0, "lifts": 0, "shapes": 0}
        messages = []
        for elem in shapes:
            try:
                self._process(elem, pen_uu, totals, messages)
            except Exception as exc:   # keep going on multi-selection
                messages.append("<{}>: failed — {}".format(
                    elem.get("id", "?"), exc))

        for msg in messages:
            inkex.errormsg(msg)
        if opt.show_stats and totals["shapes"]:
            inkex.errormsg(self._stats_text(totals))

    # ------------------------------------------------------------------

    def _collect_shapes(self, elem, out):
        """Recurse groups/layers; keep anything with path geometry."""
        if isinstance(elem, inkex.Group):
            for child in elem:
                self._collect_shapes(child, out)
        elif isinstance(elem, inkex.TextElement):
            inkex.errormsg(
                "Skipping text <{}> — run Path > Object to Path on it "
                "first.".format(elem.get("id", "?")))
        elif isinstance(elem, inkex.ShapeElement):
            try:
                if len(elem.path) > 0:
                    out.append(elem)
            except (AttributeError, TypeError):
                pass

    # ------------------------------------------------------------------

    def _region_of(self, elem, pen_uu):
        """Flatten the element (all subpaths together!) into a Region."""
        transform = elem.composed_transform()
        path = elem.path.transform(transform).to_absolute()
        flatness = max(pen_uu * FLATNESS_RATIO, 0.002)
        csp = path.to_superpath()
        cspsubdiv(csp, flatness)
        rings = []
        for sub in csp:
            ring = [(pt[1][0], pt[1][1]) for pt in sub]
            if len(ring) >= 2 and core._dist(ring[0], ring[-1]) < 1e-9:
                ring = ring[:-1]
            if len(ring) >= 3:
                rings.append(ring)

        style = elem.specified_style()
        fill_rule = style.get("fill-rule", "nonzero")
        return core.Region(rings, fill_rule)

    def _spacing_of(self, elem, pen_uu):
        """Line spacing in user units; optionally scaled by the shape's
        fill luminance (darker fill → denser lines)."""
        spacing = pen_uu * self.options.spacing_factor
        if not self.options.density_from_fill:
            return spacing, None
        style = elem.specified_style()
        fill = style.get("fill", "black")
        if fill in (None, "", "none"):
            return spacing, None
        try:
            rgb = inkex.Color(fill).to_rgb()
        except Exception:
            return spacing, None
        lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
        coverage = 1.0 - lum
        if coverage < 0.06:
            return None, ("<{}> fill is nearly white — skipped (density "
                          "from colour is on)".format(elem.get("id", "?")))
        return spacing / coverage, None

    def _stroke_color(self, elem):
        mode = self.options.color_mode
        if mode == "custom":
            return str(self.options.custom_color)
        if mode == "shape":
            style = elem.specified_style()
            for key in ("fill", "stroke"):
                val = style.get(key)
                if val and val != "none":
                    try:
                        return str(inkex.Color(val))
                    except Exception:
                        continue
        return "#000000"

    # ------------------------------------------------------------------

    def _process(self, elem, pen_uu, totals, messages):
        opt = self.options
        region = self._region_of(elem, pen_uu)
        if region.is_empty():
            messages.append("<{}> has no closed area — skipped."
                            .format(elem.get("id", "?")))
            return

        spacing, note = self._spacing_of(elem, pen_uu)
        if spacing is None:
            messages.append(note)
            return

        mm = self.svg.unittouu("1mm")
        result = core.generate_fill(
            region,
            opt.fill_mode,
            pen_uu,
            spacing,
            angle=opt.angle,
            cross_angle=opt.cross_angle,
            sine_amplitude=opt.sine_amplitude * mm,
            sine_wavelength=opt.sine_wavelength * mm,
            edge_gap=(pen_uu * 0.5 if opt.keep_inside else 0.0),
            connect=opt.connect,
        )
        for warning in result.warnings:
            messages.append("<{}>: {}".format(elem.get("id", "?"), warning))
        if not result.chains:
            return

        if opt.optimize:
            join_uu = max(opt.join_tolerance, 0.0) * mm
            chains, _, stats = core.optimize_order(result.chains, join_uu)
        else:
            chains = result.chains
            stats = core.path_stats(chains)

        fit_tol = max(pen_uu * 0.08, 0.005)
        d = core.chains_to_path_d(chains, smooth=result.smooth,
                                  fit_tol=fit_tol)
        if not d:
            return

        new_elem = PathElement()
        new_elem.set("d", d)
        new_elem.style = inkex.Style({
            "fill": "none",
            "stroke": self._stroke_color(elem),
            "stroke-width": "{:.4f}".format(pen_uu),
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
        })

        # Geometry is in document coords; place the output next to the
        # source element and cancel the parent's transform so it lands
        # exactly on top of the source shape, inside the same layer.
        parent = elem.getparent()
        if parent is None:
            parent = self.svg
        wrapper = Group() if opt.group else new_elem
        if opt.group:
            wrapper.append(new_elem)
            wrapper.set("inkscape:label", "penfill {} {}".format(
                opt.fill_mode, elem.get("id", "shape")))
        ptrans = parent.composed_transform()
        if ptrans is not None:
            inv = -inkex.Transform(ptrans)
            if inv != inkex.Transform():
                wrapper.transform = inv
        parent.insert(parent.index(elem) + 1, wrapper)

        if not opt.keep_original:
            parent.remove(elem)

        totals["draw"] += stats[0]
        totals["travel"] += stats[1]
        totals["lifts"] += stats[2]
        totals["shapes"] += 1

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
