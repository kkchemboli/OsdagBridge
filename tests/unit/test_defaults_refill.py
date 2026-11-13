"""solve_extend_basic_input_dict must not erase values the user already set."""
import pytest

from osdagbridge.core.bridge_types.plate_girder.defaults import (
    BASIC_INPUT_DICT,
    solve_extend_basic_input_dict,
    solve_layout_for_spacing,
)
from osdagbridge.core.utils.common import (
    KEY_CARRIAGEWAY_WIDTH,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_OVERALL_WIDTH,
    KEY_WC_THICKNESS,
)


def _fresh(span="30", cw="9"):
    d = dict(BASIC_INPUT_DICT)
    d[KEY_SPAN], d[KEY_CARRIAGEWAY_WIDTH] = span, cw
    solve_extend_basic_input_dict(d)
    return d


def test_first_solve_still_uses_defaults():
    d = _fresh()
    assert d[KEY_TS_DECK_THICKNESS] == 250.0
    n = d[KEY_TS_NO_OF_GIRDERS]
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(d[KEY_TS_OVERALL_WIDTH] / n, abs=1e-3)


def test_deck_thickness_survives_span_edit_and_resolve():
    d = _fresh()
    d[KEY_TS_DECK_THICKNESS] = 300.0
    d[KEY_SPAN] = "35"
    solve_extend_basic_input_dict(d)
    assert d[KEY_TS_DECK_THICKNESS] == 300.0


def test_empty_deck_thickness_falls_back_to_default():
    d = _fresh()
    d[KEY_TS_DECK_THICKNESS] = ""
    solve_extend_basic_input_dict(d)
    assert d[KEY_TS_DECK_THICKNESS] == 250.0


def test_girder_spacing_survives_span_edit():
    d = _fresh()
    solve_layout_for_spacing(d, 2.0)
    overhang = d[KEY_TS_DECK_OVERHANG]
    d[KEY_SPAN] = "38"                       # width unchanged
    solve_extend_basic_input_dict(d)
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(2.0)
    assert d[KEY_TS_DECK_OVERHANG] == pytest.approx(overhang)


def test_girder_spacing_recomputed_when_overall_width_changes():
    d = _fresh()
    solve_layout_for_spacing(d, 2.0)
    d[KEY_CARRIAGEWAY_WIDTH] = "11"          # width changes -> old spacing is meaningless
    solve_extend_basic_input_dict(d)
    n = d[KEY_TS_NO_OF_GIRDERS]
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(d[KEY_TS_OVERALL_WIDTH] / n, abs=1e-3)


def test_infeasible_kept_spacing_falls_back_to_default():
    d = _fresh()
    d[KEY_TS_GIRDER_SPACING] = 40.0          # no longer fits the (unchanged) width
    solve_extend_basic_input_dict(d)
    n = d[KEY_TS_NO_OF_GIRDERS]
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(d[KEY_TS_OVERALL_WIDTH] / n, abs=1e-3)


def test_other_additional_defaults_are_still_refilled():
    d = _fresh()
    d[KEY_WC_THICKNESS] = 80.0
    solve_extend_basic_input_dict(d)
    assert d[KEY_WC_THICKNESS] == 50.0       # unchanged behaviour outside the two kept keys
