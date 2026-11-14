"""Additional Inputs dialog: pre-save validation of empty "Custom" inputs."""
import contextlib
import io
import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osdagbridge.core.bridge_types.plate_girder.defaults import (
    BASIC_INPUT_DICT,
    solve_extend_basic_input_dict,
)
from osdagbridge.core.bridge_types.plate_girder.ui_fields_additional_input import (
    ADDITIONAL_INPUTS_SCHEMA,
    CUSTOM_GIRDER_SELECT_FIELDS,
    CUSTOM_GIRDER_TEXT_FIELDS,
    CUSTOM_MODE_FIELDS,
    CUSTOM_TYPE_FIELDS,
    additional_input_label,
)
import osdagbridge.core.utils.common as common
from osdagbridge.core.utils.common import *  # noqa: F401,F403  (central KEY_* constants)


# ---------------------------------------------------------------- rule tables (no Qt)
def _schema_fields(node, ftype, out):
    if isinstance(node, dict):
        if node.get("type") == ftype:
            out.append(node)
        for value in node.values():
            _schema_fields(value, ftype, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _schema_fields(value, ftype, out)
    return out


def test_every_mode_field_in_the_schema_is_in_the_rule_table():
    schema_ids = {f["id"] for f in _schema_fields(ADDITIONAL_INPUTS_SCHEMA, TYPE_MODE_LINE, [])
                  if VALUE_CUSTOM in f.get("mode_choices", [])}
    assert schema_ids == {key for key, _mode, _value in CUSTOM_MODE_FIELDS}


def test_rule_tables_use_central_keys_that_match_the_widgets():
    central = {v for n, v in vars(common).items() if n.startswith("KEY_") and isinstance(v, str)}
    for key, mode_key, value_key in CUSTOM_MODE_FIELDS:
        assert {key, mode_key, value_key} <= central
        # the builder names the two widgets "<id>.mode" / "<id>.value"
        assert mode_key.startswith(key) and value_key.startswith(key) and mode_key != value_key
    for combo_key, tab_key, value_keys in CUSTOM_TYPE_FIELDS:
        assert {combo_key, tab_key, *value_keys} <= central
    assert set(CUSTOM_GIRDER_TEXT_FIELDS + CUSTOM_GIRDER_SELECT_FIELDS) <= central


def test_labels_come_from_the_schema_without_markup():
    assert additional_input_label(KEY_WL_GUST_FACTOR) == "Gust Factor, G"
    assert additional_input_label(KEY_RL_WIDTH, KEY_RL_TAB) == "Railing Width (m)"
    assert "<" not in additional_input_label(KEY_MP_GIRDER_TOP_FLANGE_WIDTH)


# ---------------------------------------------------------------- dialog (offscreen Qt)
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def popups(monkeypatch):
    """Replace the modal message box; records (title, text, type) and returns `popups.answer`."""
    import osdagbridge.desktop.ui.dialogs.additional_input.additional_inputs as module

    log = types.SimpleNamespace(shown=[], answer=None)

    class FakeBox:
        def __init__(self, title="", text="", buttons=None, dialogType=None, **_):
            log.shown.append(types.SimpleNamespace(title=title, text=text, buttons=buttons, type=dialogType))

        def exec(self):
            return log.answer

    monkeypatch.setattr(module, "CustomMessageBox", FakeBox)
    return log


def _open_dialog(qapp, design_mode="Optimized", **basic):
    import osdagbridge.desktop.ui.dialogs.additional_input.additional_inputs as module

    live = dict(BASIC_INPUT_DICT)
    live.update({KEY_SPAN: "30", KEY_CARRIAGEWAY_WIDTH: "9", KEY_DESIGN_MODE: design_mode,
                 KEY_PROJECT_LOCATION: {"set": True}})
    live.update(basic)
    with contextlib.redirect_stdout(io.StringIO()):
        solve_extend_basic_input_dict(live)
        dialog = module.AdditionalInputs()
        dialog.set_input_dictionary(live)
        dialog._update_additional_input_cad()
        dialog.design_mode_trigger(design_mode)
        dialog.show()
        qapp.processEvents()
    return dialog, live


@pytest.fixture
def dialog(qapp, popups):
    dlg, live = _open_dialog(qapp)
    yield dlg, live, popups
    dlg.hide()


def _quiet(fn):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()


def _combo(dlg, key):
    from PySide6.QtWidgets import QComboBox
    return dlg.findChild(QComboBox, key)


def _box(dlg, key):
    from PySide6.QtWidgets import QLineEdit
    return dlg.findChild(QLineEdit, key)


def _save(dlg):
    _quiet(dlg._save_inputs)


def test_untouched_dialog_saves(dialog):
    dlg, _live, popups = dialog
    assert dlg._validate_custom_fields() == []
    _save(dlg)
    assert [p.title for p in popups.shown] == ["Saved"]


def test_custom_mode_with_empty_box_blocks_save_and_names_the_field(dialog):
    dlg, live, popups = dialog
    before = dict(live)
    _quiet(lambda: _combo(dlg, KEY_WL_GUST_FACTOR_MODE).setCurrentText(VALUE_CUSTOM))

    _save(dlg)

    assert len(popups.shown) == 1
    assert popups.shown[0].type == "Critical"
    assert popups.shown[0].text == "Error: The custom value for 'Gust Factor, G' cannot be empty."
    assert live == before                                   # nothing committed
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "Loading"   # jumped to the field's tab


def test_custom_mode_with_a_value_saves(dialog):
    dlg, live, popups = dialog
    _quiet(lambda: _combo(dlg, KEY_WL_GUST_FACTOR_MODE).setCurrentText(VALUE_CUSTOM))
    box = _box(dlg, KEY_WL_GUST_FACTOR_VALUE)
    box.setText("2.0")
    _quiet(box.editingFinished.emit)

    _save(dlg)

    assert [p.title for p in popups.shown] == ["Saved"]
    assert live[KEY_WL_GUST_FACTOR_MODE] == VALUE_CUSTOM


def test_non_custom_mode_with_empty_box_saves(dialog):
    dlg, _live, popups = dialog
    assert _box(dlg, KEY_WL_GUST_FACTOR_VALUE).text() == ""
    assert _combo(dlg, KEY_WL_GUST_FACTOR_MODE).currentText() != VALUE_CUSTOM
    _save(dlg)
    assert [p.title for p in popups.shown] == ["Saved"]


def test_all_missing_fields_are_reported_at_once_in_tab_order(dialog):
    dlg, _live, popups = dialog
    for mode_key in (KEY_DO_CAMBER_MODE, KEY_WL_LIFT_COEFF_MODE, KEY_LL_FOOTPATH_PRESSURE_MODE):
        _quiet(lambda k=mode_key: _combo(dlg, k).setCurrentText(VALUE_CUSTOM))
    _quiet(lambda: _combo(dlg, KEY_WC_MATERIAL).setCurrentText(VALUE_CUSTOM))
    _box(dlg, KEY_WC_DENSITY).setText("")

    _save(dlg)

    assert popups.shown[0].text.split("\n") == [
        "Error: The custom value for 'Wearing Course Density (kN/m³)' cannot be empty.",
        "Error: The custom value for 'Footpath Pressure (kN/mm²)' cannot be empty.",
        "Error: The custom value for 'Lift Coefficient, CL' cannot be empty.",
        "Error: The custom value for 'Camber (m)' cannot be empty.",
    ]


def test_type_dropdown_custom_checks_only_the_boxes_it_unlocks(dialog):
    dlg, _live, _popups = dialog
    _quiet(lambda: _combo(dlg, KEY_CB_TYPE).setCurrentText(VALUE_CUSTOM))
    assert dlg._validate_custom_fields() == []              # boxes arrive pre-filled
    _box(dlg, KEY_CB_HEIGHT).setText("")
    assert [m["name"] for m in dlg._validate_custom_fields()] == ["Crash Barrier Height (m)"]
    # density/area are hidden for a Custom barrier, so an empty one is not reported
    _box(dlg, KEY_CB_DENSITY).setText("")
    assert [m["name"] for m in dlg._validate_custom_fields()] == ["Crash Barrier Height (m)"]


def test_custom_field_in_a_disabled_tab_is_ignored(dialog):
    dlg, _live, _popups = dialog                             # no median, no footpath
    _quiet(lambda: _combo(dlg, KEY_MD_TYPE).setCurrentText(VALUE_CUSTOM))
    for key in (KEY_MD_WIDTH, KEY_MD_HEIGHT, KEY_MD_LOAD):
        _box(dlg, key).setText("")
    assert dlg._validate_custom_fields() == []


def test_median_and_railing_custom_are_checked_when_their_tabs_are_active(qapp, popups):
    dlg, _live = _open_dialog(qapp, **{KEY_INCLUDE_MEDIAN: "Yes", KEY_FOOTPATH: VALUES_FOOTPATH[2]})
    try:
        _quiet(lambda: _combo(dlg, KEY_MD_TYPE).setCurrentText(VALUE_CUSTOM))
        _quiet(lambda: _combo(dlg, KEY_RL_TYPE).setCurrentText(VALUE_CUSTOM))
        _quiet(lambda: _combo(dlg, KEY_RL_LOAD_MODE).setCurrentText(VALUE_CUSTOM))
        for key in (KEY_MD_WIDTH, KEY_RL_HEIGHT, KEY_RL_LOAD_VALUE):
            _box(dlg, key).setText("")
        assert [m["name"] for m in dlg._validate_custom_fields()] == [
            "Median Width (m)", "Railing Height (m)", "Railing Load (kN/m)",
        ]
    finally:
        dlg.hide()


def test_empty_girder_box_on_a_member_that_is_not_on_screen_is_caught(qapp, popups):
    dlg, live = _open_dialog(qapp, design_mode=VALUES_GIRDER_DESIGN_MODE[1])
    try:
        assert dlg._validate_custom_fields() == []
        dlg.working_input_dict[KEY_MP_GIRDER_DEPTH + ".G3.M1"] = ""      # girder 1 is on screen
        before = dict(live)

        _save(dlg)

        assert popups.shown[0].text == "Error: The custom value for 'Total Depth, d (mm) (G3 M1)' cannot be empty."
        assert live == before
        assert _combo(dlg, KEY_MP_GD_SELECT_GIRDER).currentIndex() == 2  # brought on screen
    finally:
        dlg.hide()


def test_empty_girder_box_on_the_visible_member_is_read_from_the_box(qapp, popups):
    dlg, _live = _open_dialog(qapp, design_mode=VALUES_GIRDER_DESIGN_MODE[1])
    try:
        from PySide6.QtWidgets import QWidget
        box = dlg.findChild(QWidget, KEY_MP_GIRDER_TOP_FLANGE_WIDTH).currentWidget()
        box.blockSignals(True)
        box.setText("")
        box.blockSignals(False)
        assert [m["name"] for m in dlg._validate_custom_fields()] == ["Width of Top Flange, tfw (mm) (G1 M1)"]
    finally:
        dlg.hide()


def test_girder_boxes_are_not_checked_in_optimized_mode_or_for_rolled_or_symmetric_members(qapp, popups):
    dlg, _live = _open_dialog(qapp)                          # Optimized
    try:
        dlg.working_input_dict[KEY_MP_GIRDER_DEPTH + ".G2.M1"] = ""
        assert dlg._validate_custom_fields() == []
    finally:
        dlg.hide()

    dlg, _live = _open_dialog(qapp, design_mode=VALUES_GIRDER_DESIGN_MODE[1])
    try:
        d = dlg.working_input_dict
        d[KEY_MP_GIRDER_DEPTH + ".G2.M1"] = ""
        d[KEY_MP_GIRDER_TYPE + ".G2.M1"] = VALUES_GIRDER_TYPE[1]            # rolled
        d[KEY_MP_GIRDER_BOTTOM_FLANGE_WIDTH + ".G3.M1"] = ""
        d[KEY_MP_GIRDER_SYMMETRY + ".G3.M1"] = VALUES_GIRDER_SYMMETRY[0]    # symmetric: mirrors the top flange
        assert dlg._validate_custom_fields() == []
    finally:
        dlg.hide()


def test_custom_thickness_with_nothing_picked_is_caught(dialog):
    dlg, _live, _popups = dialog                             # Optimized
    d = dlg.working_input_dict
    d[KEY_MP_GIRDER_WEB_THICKNESS + ".G2.M1"] = VALUE_CUSTOM
    d[KEY_MP_GIRDER_WEB_THICKNESS + KEY_SUFFIX_SELECTED + ".G2.M1"] = []
    assert [m["name"] for m in dlg._validate_custom_fields()] == ["Web Thickness, wt (mm) (G2 M1)"]
    d[KEY_MP_GIRDER_WEB_THICKNESS + KEY_SUFFIX_SELECTED + ".G2.M1"] = ["10", "12"]
    assert dlg._validate_custom_fields() == []


# ---------------------------------------------------------------- empty Custom values: one message, from Save
def _user_empties(dlg, qapp, box):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    dlg.activateWindow()
    box.setFocus()
    qapp.processEvents()
    box.selectAll()
    QTest.keyClicks(box, "7")
    QTest.keyClick(box, Qt.Key_Backspace)
    _quiet(lambda: dlg.save_button.setFocus())               # user leaves the box
    qapp.processEvents()


@pytest.mark.parametrize("mode_key,value_key,label", [
    (KEY_WL_GUST_FACTOR_MODE, KEY_WL_GUST_FACTOR_VALUE, "Gust Factor, G"),
    (KEY_DO_CAMBER_MODE, KEY_DO_CAMBER_VALUE, "Camber (m)"),
])
def test_emptied_custom_value_is_left_empty_and_reported_once_by_save(dialog, qapp, mode_key, value_key, label):
    dlg, live, popups = dialog
    _quiet(lambda: _combo(dlg, mode_key).setCurrentText(VALUE_CUSTOM))
    box = _box(dlg, value_key)

    _user_empties(dlg, qapp, box)

    assert popups.shown == []                                # no "must be a numeric value" popup
    assert box.text() == ""                                  # no made-up replacement value
    _save(dlg)
    assert [p.text for p in popups.shown] == [f"Error: The custom value for '{label}' cannot be empty."]


def test_after_filling_the_reported_value_save_goes_through(dialog, qapp):
    dlg, live, popups = dialog
    _quiet(lambda: _combo(dlg, KEY_WL_GUST_FACTOR_MODE).setCurrentText(VALUE_CUSTOM))
    _save(dlg)
    assert popups.shown[-1].type == "Critical"
    box = _box(dlg, KEY_WL_GUST_FACTOR_VALUE)
    box.setText("2.5")
    _quiet(box.editingFinished.emit)
    _save(dlg)
    assert popups.shown[-1].title == "Saved"
    assert float(live[KEY_WL_GUST_FACTOR_VALUE]) == 2.5


def test_a_non_empty_out_of_range_custom_value_is_still_corrected(dialog):
    dlg, _live, popups = dialog
    _quiet(lambda: _combo(dlg, KEY_WL_GUST_FACTOR_MODE).setCurrentText(VALUE_CUSTOM))
    box = _box(dlg, KEY_WL_GUST_FACTOR_VALUE)
    box.setText("1.5")
    _quiet(box.editingFinished.emit)
    assert [p.title for p in popups.shown] == ["Input Error"]
    assert box.text() != "1.5"


# ---------------------------------------------------------------- thickness pick-list (real pop-up)
@pytest.mark.parametrize("move_all_out,expected", [
    (False, None),                                           # untouched: every thickness selected
    (True, "Error: The custom value for 'Web Thickness, wt (mm) (G1 M1)' cannot be empty."),
])
def test_custom_thickness_pick_list(dialog, monkeypatch, move_all_out, expected):
    from PySide6.QtWidgets import QWidget
    from osdagbridge.desktop.ui.dialogs.additional_input.dialogs.multi_select_dialog import CustomMultiSelectDialog
    dlg, _live, popups = dialog

    def submit(self):
        if move_all_out:
            self._move_all_left()
        return 1

    monkeypatch.setattr(CustomMultiSelectDialog, "exec", submit)
    _quiet(lambda: dlg.findChild(QWidget, KEY_MP_GIRDER_WEB_THICKNESS).currentWidget().setCurrentText(VALUE_CUSTOM))
    _save(dlg)
    assert popups.shown[-1].text == (expected or "Inputs saved successfully.")

