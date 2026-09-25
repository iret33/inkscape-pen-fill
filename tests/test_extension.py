"""End-to-end tests: run pen_fill.py the way Inkscape does (CLI on an SVG
document) and check the emitted document. Requires the `inkex` package
(pip install inkex — no Inkscape installation needed)."""

import os
import re
import subprocess
import sys

import pytest

pytest.importorskip("inkex")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="210mm" height="148mm" viewBox="0 0 210 148">
  <g inkscape:groupmode="layer" inkscape:label="L1" id="layer1">
    <path id="donut" style="fill:#ff0000;fill-rule:evenodd"
          d="M 60,40 A 25,25 0 1 1 10,40 A 25,25 0 1 1 60,40 Z
             M 47,40 A 12,12 0 1 0 23,40 A 12,12 0 1 0 47,40 Z"/>
    <g id="xform" transform="translate(80,10) scale(1.4) rotate(12)">
      <path id="blob" style="fill:#3050c8"
            d="M 20,30 C 40,5 70,12 68,35 C 66,58 40,64 28,52
               C 16,40 8,45 20,30 Z"/>
    </g>
    <rect id="box" x="20" y="85" width="48" height="38" rx="8"
          style="fill:#222222"/>
    <g id="extras">
      <rect id="hidden" x="100" y="85" width="30" height="30"
            style="display:none;fill:#000000"/>
      <image id="photo" x="140" y="85" width="30" height="30"
             href="data:image/png;base64,iVBORw0KGgo="/>
      <rect id="shown" x="100" y="120" width="30" height="20"
            style="fill:#000000"/>
    </g>
  </g>
</svg>
"""


def run_extension(tmp_path, args, ids=("donut",), source=FIXTURE):
    src = tmp_path / "in.svg"
    src.write_text(source)
    out = tmp_path / "out.svg"
    cmd = [sys.executable, os.path.join(ROOT, "pen_fill.py")]
    for i in ids:
        cmd.append("--id={}".format(i))
    cmd += list(args) + ["--output={}".format(out), str(src)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stderr
    return out.read_text(), proc.stderr


def path_points(d):
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    vals = [float(v) for v in nums]
    return list(zip(vals[::2], vals[1::2]))


MODES = ["hatch", "crosshatch", "sine", "concentric", "spiral",
         "archimedean", "hilbert"]


@pytest.mark.parametrize("mode", MODES)
def test_mode_produces_fill_in_bbox(tmp_path, mode):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode={}".format(mode), "--pen_size=1.0"])
    assert "penfill {} donut".format(mode) in svg
    m = re.search(r'label="penfill[^"]*"[^>]*>\s*<path[^>]*d="([^"]+)"', svg)
    assert m, "no fill path emitted"
    pts = path_points(m.group(1))
    assert len(pts) > 4
    for x, y in pts:
        # donut bbox is (10,15)-(60,65); allow curve-fit slack
        assert 8.0 <= x <= 62.0 and 13.0 <= y <= 67.0


def test_keep_original_false_removes_source(tmp_path):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode=hatch", "--keep_original=false"])
    assert 'id="donut"' not in svg


def test_fill_is_tagged_with_its_source(tmp_path):
    svg, _ = run_extension(tmp_path, ["--fill_mode=hatch"])
    assert re.search(r'label="penfill hatch donut"[^>]*'
                     r'data-penfill-source="donut"', svg)


def fill_labels(svg):
    return re.findall(r'label="(penfill [^"]*)"', svg)


def test_reapply_replaces_earlier_fill(tmp_path):
    first, _ = run_extension(tmp_path, ["--fill_mode=hatch"])
    second, _ = run_extension(tmp_path, ["--fill_mode=spiral"],
                              source=first)
    assert fill_labels(second) == ["penfill spiral donut"]


def test_reapply_can_stack_fills(tmp_path):
    first, _ = run_extension(tmp_path, ["--fill_mode=hatch"])
    second, _ = run_extension(
        tmp_path, ["--fill_mode=spiral", "--replace=false"], source=first)
    assert sorted(fill_labels(second)) == ["penfill hatch donut",
                                           "penfill spiral donut"]


def test_v20_fill_is_replaced_too(tmp_path):
    first, _ = run_extension(tmp_path, ["--fill_mode=hatch"])
    legacy = first.replace('data-penfill-source="donut"', "")
    second, _ = run_extension(tmp_path, ["--fill_mode=spiral"],
                              source=legacy)
    assert fill_labels(second) == ["penfill spiral donut"]


def test_select_all_never_fills_fills(tmp_path):
    first, _ = run_extension(tmp_path, ["--fill_mode=hatch"])
    second, stderr = run_extension(tmp_path, ["--fill_mode=hatch"],
                                   ids=("layer1",), source=first)
    assert "<?>" not in stderr and "too small" not in stderr
    assert sorted(fill_labels(second)) == [
        "penfill hatch blob", "penfill hatch box", "penfill hatch donut",
        "penfill hatch shown"]


def test_hidden_objects_and_images_are_skipped(tmp_path):
    svg, _ = run_extension(tmp_path, ["--fill_mode=hatch"],
                           ids=("extras",))
    assert fill_labels(svg) == ["penfill hatch shown"]


def test_object_selected_twice_is_filled_once(tmp_path):
    svg, _ = run_extension(tmp_path, ["--fill_mode=hatch"],
                           ids=("extras", "shown"))
    assert fill_labels(svg) == ["penfill hatch shown"]


def test_outline_option_adds_a_loop(tmp_path):
    def loops(extra):
        svg, _ = run_extension(
            tmp_path, ["--fill_mode=hatch", "--pen_size=1.0"] + extra)
        m = re.search(r'label="penfill[^"]*"[^>]*>\s*<path[^>]*d="([^"]+)"',
                      svg)
        return m.group(1).count("Z")
    # the donut's outline is two closed loops: outer edge + hole edge
    assert loops(["--outline=true"]) == loops([]) + 2


def test_inx_matches_arguments():
    import argparse
    import xml.etree.ElementTree as ET
    sys.path.insert(0, ROOT)
    import pen_fill
    ns = "{http://www.inkscape.org/namespace/inkscape/extension}"
    tree = ET.parse(os.path.join(ROOT, "pen_fill.inx"))
    params = {p.get("name") for p in tree.iter(ns + "param")}
    parser = argparse.ArgumentParser()
    pen_fill.PenFill.add_arguments(None, parser)
    args = {a.dest for a in parser._actions if a.dest != "help"}
    assert params == args


def test_color_from_shape(tmp_path):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode=hatch", "--color_mode=shape"])
    m = re.search(r'label="penfill[^"]*"[^>]*>\s*<path[^>]*'
                  r'style="([^"]+)"', svg)
    assert m and "stroke:#ff0000" in m.group(1)


def test_transform_cancels_parent(tmp_path):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode=hatch", "--pen_size=1.0"], ids=("blob",))
    m = re.search(r'label="penfill hatch blob"[^>]*transform='
                  r'"matrix\(([^)]+)\)"', svg)
    assert m, "wrapper should carry the inverse parent transform"
    a, b, c, d, e, f = [float(v) for v in re.split(r"[\s,]+",
                                                   m.group(1).strip())]
    # parent matrix(1.36941, 0.291076, -0.291076, 1.36941, 80, 10)
    pa, pb, pc, pd = 1.36941, 0.291076, -0.291076, 1.36941
    # linear part of parent @ wrapper must be identity
    assert pa * a + pc * b == pytest.approx(1.0, abs=1e-3)
    assert pb * a + pd * b == pytest.approx(0.0, abs=1e-3)
    assert pa * c + pc * d == pytest.approx(0.0, abs=1e-3)
    assert pb * c + pd * d == pytest.approx(1.0, abs=1e-3)


def test_rect_primitive_without_object_to_path(tmp_path):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode=concentric", "--pen_size=1.0"], ids=("box",))
    assert "penfill concentric box" in svg


def test_stats_output(tmp_path):
    _, stderr = run_extension(
        tmp_path, ["--fill_mode=spiral", "--show_stats=true"])
    assert "mm of ink" in stderr and "pen lifts" in stderr


def test_density_from_fill_darker_is_denser(tmp_path):
    light_fixture = FIXTURE.replace("fill:#ff0000", "fill:#cccccc")
    src = tmp_path / "light.svg"
    src.write_text(light_fixture)

    def total_len(fixture_text):
        f = tmp_path / "in2.svg"
        f.write_text(fixture_text)
        out = tmp_path / "out2.svg"
        cmd = [sys.executable, os.path.join(ROOT, "pen_fill.py"),
               "--id=donut", "--fill_mode=hatch",
               "--density_from_fill=true", "--pen_size=1.0",
               "--output={}".format(out), str(f)]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=300)
        assert proc.returncode == 0, proc.stderr
        svg = out.read_text()
        m = re.search(r'label="penfill[^"]*"[^>]*>\s*<path[^>]*d="([^"]+)"',
                      svg)
        pts = path_points(m.group(1))
        return sum(1 for _ in pts)

    dark = total_len(FIXTURE)                       # red, lum 0.30
    light = total_len(light_fixture)                # light grey, lum 0.80
    assert dark > light * 1.5
