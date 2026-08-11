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
  </g>
</svg>
"""


def run_extension(tmp_path, args, ids=("donut",)):
    src = tmp_path / "in.svg"
    src.write_text(FIXTURE)
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


def test_group_false_emits_bare_path(tmp_path):
    svg, _ = run_extension(
        tmp_path, ["--fill_mode=hatch", "--group=false"])
    assert "penfill" not in svg          # no labelled wrapper group
    assert svg.count("<path") == 3       # two source paths + bare fill


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
