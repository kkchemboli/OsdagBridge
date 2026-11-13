"""Tests for double-click editing of dimension labels on the 2D CAD canvases."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osdagbridge.core.bridge_types.plate_girder.defaults import (
    BASIC_INPUT_DICT,
    solve_extend_basic_input_dict,
    solve_layout_for_spacing,
)
from osdagbridge.core.utils.common import (
    KEY_CARRIAGEWAY_WIDTH,
    KEY_MP_CB_SPACING,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_NO_OF_GIRDERS,
    KEY_TS_OVERALL_WIDTH,
)

EDITABLE = {KEY_SPAN, KEY_CARRIAGEWAY_WIDTH, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS}
LOCKED = {KEY_TS_DECK_OVERHANG, KEY_TS_OVERALL_WIDTH, KEY_MP_CB_SPACING}


# ---------------------------------------------------------------- registry
def test_registry_matches_task_scope():
    from osdagbridge.desktop.ui.utils.cad_dimension_edit import EDITABLE_KEYS, LOCKED_KEYS
    assert set(EDITABLE_KEYS) == EDITABLE
    assert set(LOCKED_KEYS) == LOCKED


# ---------------------------------------------------------------- layout solve
def _solved_dict():
    d = dict(BASIC_INPUT_DICT)
    d[KEY_SPAN] = "30"
    d[KEY_CARRIAGEWAY_WIDTH] = "9"
    solve_extend_basic_input_dict(d)
    return d


def test_spacing_edit_keeps_width_equation():
    d = _solved_dict()
    n, width = int(d[KEY_TS_NO_OF_GIRDERS]), d[KEY_TS_OVERALL_WIDTH]
    new_spacing = d[KEY_TS_GIRDER_SPACING] - 0.2
    solve_layout_for_spacing(d, new_spacing)
    assert d[KEY_TS_GIRDER_SPACING] == pytest.approx(new_spacing)
    assert (n - 1) * d[KEY_TS_GIRDER_SPACING] + 2 * d[KEY_TS_DECK_OVERHANG] == pytest.approx(width, abs=1e-3)
    assert d[KEY_TS_OVERALL_WIDTH] == pytest.approx(width)


def test_oversize_spacing_is_rejected_and_dict_untouched():
    d = _solved_dict()
    before = {k: d[k] for k in (KEY_TS_GIRDER_SPACING, KEY_TS_DECK_OVERHANG, KEY_TS_OVERALL_WIDTH)}
    with pytest.raises(ValueError):
        solve_layout_for_spacing(d, 50.0)
    assert {k: d[k] for k in before} == before


# ---------------------------------------------------------------- canvas (offscreen Qt)
@pytest.fixture
def dual(qapp_instance):
    from osdagbridge.desktop.ui.docks.cad_dual_view import BridgeDualCADWidget
    w = BridgeDualCADWidget()
    w.resize(1100, 900)
    w.show()
    qapp_instance.processEvents()
    w.cross_section_widget.update_params({"span_length": 30000, "carriageway_width": 9000})
    w.top_view_widget.update_params({"span_length": 30000, "carriageway_width": 9000})
    for canvas in (w.cross_section_widget, w.top_view_widget):
        canvas.grab()                      # paint -> registers the label zones
    qapp_instance.processEvents()
    yield w
    w.close()


@pytest.fixture(scope="module")
def qapp_instance():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _zone(canvas, key):
    for rect, k in canvas.dimension_label_zones:
        if k == key:
            return rect
    return None


def test_zones_registered_for_every_labelled_parameter(dual):
    cross, top = dual.cross_section_widget, dual.top_view_widget
    assert _zone(cross, KEY_CARRIAGEWAY_WIDTH) and _zone(cross, KEY_TS_GIRDER_SPACING)
    assert _zone(cross, KEY_TS_DECK_THICKNESS)
    assert _zone(cross, KEY_TS_DECK_OVERHANG) and _zone(cross, KEY_TS_OVERALL_WIDTH)
    assert _zone(top, KEY_SPAN) and _zone(top, KEY_MP_CB_SPACING)
    assert _zone(top, KEY_TS_GIRDER_SPACING)


def _double_click(canvas, rect):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(rect.center().x()), int(rect.center().y())))


def _type_and_enter(canvas, text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    editor = canvas._dim_editor
    editor.selectAll()
    QTest.keyClicks(editor, text)
    QTest.keyClick(editor, Qt.Key_Return)


@pytest.mark.parametrize("canvas_name,key,text", [
    ("cross_section_widget", KEY_TS_DECK_THICKNESS, "300"),
    ("cross_section_widget", KEY_CARRIAGEWAY_WIDTH, "10.5"),
    ("cross_section_widget", KEY_TS_GIRDER_SPACING, "2.4"),
    ("top_view_widget", KEY_SPAN, "32.5"),
])
def test_double_click_edit_emits_key_and_text(dual, canvas_name, key, text):
    canvas = getattr(dual, canvas_name)
    got = []
    dual.dimension_edit_requested.connect(lambda k, t: got.append((k, t)))
    _double_click(canvas, _zone(canvas, key))
    assert canvas._dim_editor is not None and canvas._dim_editing_key == key
    _type_and_enter(canvas, text)
    assert got == [(key, text)]
    assert canvas._dim_editor is None


@pytest.mark.parametrize("canvas_name,key", [
    ("cross_section_widget", KEY_TS_DECK_OVERHANG),
    ("cross_section_widget", KEY_TS_OVERALL_WIDTH),
    ("top_view_widget", KEY_MP_CB_SPACING),
])
def test_locked_labels_do_not_open_an_editor(dual, canvas_name, key):
    canvas = getattr(dual, canvas_name)
    got = []
    dual.dimension_edit_requested.connect(lambda k, t: got.append((k, t)))
    _double_click(canvas, _zone(canvas, key))
    assert canvas._dim_editor is None and got == []


def test_escape_cancels_and_unchanged_value_is_not_emitted(dual):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    cross = dual.cross_section_widget
    got = []
    dual.dimension_edit_requested.connect(lambda k, t: got.append((k, t)))

    _double_click(cross, _zone(cross, KEY_TS_DECK_THICKNESS))
    QTest.keyClicks(cross._dim_editor, "9")
    QTest.keyClick(cross._dim_editor, Qt.Key_Escape)
    assert cross._dim_editor is None and got == []

    _double_click(cross, _zone(cross, KEY_TS_DECK_THICKNESS))
    QTest.keyClick(cross._dim_editor, Qt.Key_Return)          # same value as shown
    assert got == []


def test_preview_canvas_stays_read_only(qapp_instance):
    from osdagbridge.desktop.ui.docks.cad_cross_section import CrossSectionCADWidget
    preview = CrossSectionCADWidget()
    assert preview.interactive_edit is False


def test_click_outside_the_editor_commits(dual):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    cross = dual.cross_section_widget
    got = []
    dual.dimension_edit_requested.connect(lambda k, t: got.append((k, t)))
    _double_click(cross, _zone(cross, KEY_TS_DECK_THICKNESS))
    editor = cross._dim_editor
    editor.selectAll()
    QTest.keyClicks(editor, "320")
    # click on an empty part of the canvas (no focusable widget there)
    QTest.mouseClick(cross, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    assert got == [(KEY_TS_DECK_THICKNESS, "320")]
    assert cross._dim_editor is None


def test_click_outside_on_another_canvas_commits(dual):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    cross, top = dual.cross_section_widget, dual.top_view_widget
    got = []
    dual.dimension_edit_requested.connect(lambda k, t: got.append((k, t)))
    _double_click(cross, _zone(cross, KEY_TS_DECK_THICKNESS))
    editor = cross._dim_editor
    editor.selectAll()
    QTest.keyClicks(editor, "330")
    QTest.mouseClick(top, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    assert got == [(KEY_TS_DECK_THICKNESS, "330")]
