"""Round-trip tests: generate conforming DXFs, validate, analyse; and the
validator's rejection of a non-conforming (all-on-layer-0) drawing.
"""

import ezdxf
import pytest

from tyre_analysis.pipeline import analyse_files, COLUMNS
from tyre_analysis.validate import validate_dxf
from scripts.make_sample_dxf import write_profile, _baseline, _iteration1


@pytest.fixture
def sample_files(tmp_path):
    b = tmp_path / "baseline.dxf"
    i = tmp_path / "iter1.dxf"
    write_profile(_baseline(), b)
    write_profile(_iteration1(), i)
    return b, i


def test_generated_files_conform(sample_files):
    for f in sample_files:
        rep = validate_dxf(f)
        assert rep.conforms, rep.problems
        assert set(rep.component_layers) >= {"TREAD", "SIDEWALL", "BEAD"}


def test_pipeline_columns_and_totals(sample_files):
    b, i = sample_files
    df, geoms, warns = analyse_files({"baseline": b, "iter1": i}, axis_x=0.0)
    assert list(df.columns) == COLUMNS
    # pct sums to ~100 within each file
    for _, g in df.groupby("file"):
        assert g["pct_of_total_weight"].sum() == pytest.approx(100.0, abs=1e-6)
    # The tread-thinning iteration reduces total weight.
    tot = df.groupby("file")["weight_g"].sum()
    assert tot["iter1"] < tot["baseline"]
    # No component crosses the axis (all geometry on +x side of x=0).
    assert not df["axis_crosses"].any()


def test_validator_rejects_layer_zero_soup(tmp_path):
    """Open curves all on layer 0 must be reported non-conforming."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))          # open
    msp.add_arc((10, 0), 5, 0, 90)         # open
    path = tmp_path / "soup.dxf"
    doc.saveas(path)
    rep = validate_dxf(path)
    assert not rep.conforms
    assert rep.all_on_layer_zero
    assert any("layer '0'" in p for p in rep.problems)


def test_cli_analyse(sample_files, capsys):
    from tyre_analysis.cli import main
    b, i = sample_files
    rc = main(["analyse", "--axis-x", "0", str(b), str(i)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TREAD" in out and "Tire totals" in out
