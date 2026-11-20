"""Additional Inputs dialog: is_modified tracking and the unsaved-changes warning on close."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osdagbridge.core.bridge_types.plate_girder.ui_fields_additional_input import ADDITIONAL_INPUT_SELECTOR_KEYS
from osdagbridge.core.utils.common import *  # noqa: F401,F403  (central KEY_* constants)

from test_additional_inputs_validation import _box, _combo, _open_dialog, _quiet, popups, qapp  # noqa: F401


@pytest.fixture
def dialog(qapp, popups):
    dlg, live = _open_dialog(qapp)
    yield dlg, live, popups
    dlg.is_modified = False
    dlg.hide()


def _user_types(box, text="1"):
    from PySide6.QtTest import QTest
    _quiet(lambda: QTest.keyClicks(box, text))


def _user_picks_next(combo):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    key = Qt.Key_Down if combo.currentIndex() < combo.count() - 1 else Qt.Key_Up
    _quiet(lambda: QTest.keyClick(combo, key))


# ---------------------------------------------------------------- tracking
def test_navigation_and_programmatic_changes_are_not_modifications(dialog, qapp):
    from PySide6.QtWidgets import QTabWidget
    dlg, _live, _popups = dialog
    for tabs in dlg.findChildren(QTabWidget):
        for i in range(tabs.count()):
            if tabs.isTabEnabled(i):
                _quiet(lambda t=tabs, n=i: t.setCurrentIndex(n))
    for key in ADDITIONAL_INPUT_SELECTOR_KEYS:              # browsing girders / members as a user
        combo = _combo(dlg, key)
        if combo is not None and combo.count() > 1:
            _user_picks_next(combo)
    _quiet(lambda: _combo(dlg, KEY_WC_MATERIAL).setCurrentText(VALUE_CUSTOM))   # programmatic
    _box(dlg, KEY_WC_THICKNESS).setText("75")                                   # programmatic
    qapp.processEvents()
    assert dlg.is_modified is False


def test_picking_the_same_option_again_is_not_a_modification(dialog):
    dlg, _live, _popups = dialog
    combo = _combo(dlg, KEY_WC_MATERIAL)
    _quiet(lambda: combo.activated.emit(combo.currentIndex()))
    assert dlg.is_modified is False


def test_defaults_button_marks_modified_and_save_clears_it(dialog):
    dlg, _live, popups = dialog
    _quiet(dlg.defaults_button.click)
    assert dlg.is_modified is True
    _quiet(dlg._save_inputs)
    assert popups.shown[-1].title == "Saved"
    assert dlg.is_modified is False


def test_a_blocked_save_keeps_the_dialog_modified(dialog):
    dlg, _live, popups = dialog
    _user_picks_next(_combo(dlg, KEY_DO_CAMBER_MODE))        # Default -> Custom, box left empty
    assert _combo(dlg, KEY_DO_CAMBER_MODE).currentText() == VALUE_CUSTOM
    _quiet(dlg._save_inputs)
    assert popups.shown[-1].type == "Critical"
    assert dlg.is_modified is True


@pytest.mark.parametrize("design_mode", VALUES_GIRDER_DESIGN_MODE)
def test_every_editable_input_marks_the_dialog_modified(qapp, popups, monkeypatch, design_mode):
    """Coverage: a user edit on any enabled box, dropdown or checkbox must set the flag."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLineEdit
    dlg, _live = _open_dialog(qapp, design_mode=design_mode)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)     # pop-ups opened by a dropdown: cancel at once
    missed, exercised = [], {"box": 0, "dropdown": 0, "checkbox": 0}

    for box in dlg.findChildren(QLineEdit):
        if not box.isEnabled() or box.isReadOnly():
            continue
        dlg.is_modified = False
        before = box.text()
        _quiet(lambda b=box: QTest.keyClick(b, Qt.Key_Backspace) if b.text() else QTest.keyClicks(b, "1"))
        if box.text() != before:
            exercised["box"] += 1
            if not dlg.is_modified:
                missed.append(("box", box.objectName()))

    for combo in dlg.findChildren(QComboBox):
        if not combo.isEnabled() or combo.count() < 2 or combo.objectName() in ADDITIONAL_INPUT_SELECTOR_KEYS:
            continue
        dlg.is_modified = False
        before = combo.currentIndex()
        _user_picks_next(combo)
        if combo.currentIndex() != before:
            exercised["dropdown"] += 1
            if not dlg.is_modified:
                missed.append(("dropdown", combo.objectName()))

    for check in dlg.findChildren(QCheckBox):
        if not check.isEnabled():
            continue
        dlg.is_modified = False
        _quiet(check.click)
        exercised["checkbox"] += 1
        if not dlg.is_modified:
            missed.append(("checkbox", check.objectName()))

    assert missed == []
    assert all(count >= 20 for count in exercised.values()), exercised   # the sweep really ran
    dlg.is_modified = False
    dlg.hide()


# ---------------------------------------------------------------- closing
def _press_escape(dlg):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _quiet(lambda: QTest.keyClick(dlg, Qt.Key_Escape))


@pytest.mark.parametrize("close", [lambda d: _quiet(d.close), _press_escape], ids=["title-bar X", "Esc"])
def test_close_without_changes_does_not_warn(dialog, close):
    dlg, _live, popups = dialog
    close(dlg)
    assert popups.shown == [] and not dlg.isVisible()


@pytest.mark.parametrize("close", [lambda d: _quiet(d.close), _press_escape], ids=["title-bar X", "Esc"])
def test_cancel_keeps_the_dialog_open_with_its_changes(dialog, close):
    dlg, _live, popups = dialog
    _user_types(_box(dlg, KEY_WC_THICKNESS))
    typed = _box(dlg, KEY_WC_THICKNESS).text()
    popups.answer = BTN_CANCEL

    close(dlg)

    assert len(popups.shown) == 1
    warning = popups.shown[0]
    assert warning.type == "Warning"
    assert warning.text == "You have unsaved changes. Closing this window will discard them. Do you want to proceed?"
    assert warning.buttons == ["Discard", "Cancel"]
    assert dlg.isVisible() and dlg.is_modified is True
    assert _box(dlg, KEY_WC_THICKNESS).text() == typed


@pytest.mark.parametrize("close", [lambda d: _quiet(d.close), _press_escape], ids=["title-bar X", "Esc"])
def test_discard_closes_and_drops_the_changes(dialog, close, qapp):
    dlg, live, popups = dialog
    before = dict(live)
    _user_types(_box(dlg, KEY_WC_THICKNESS))
    popups.answer = BTN_DISCARD

    close(dlg)

    assert len(popups.shown) == 1                            # asked exactly once
    assert not dlg.isVisible() and dlg.is_modified is False
    assert live == before                                    # nothing was committed
    # reopening shows the saved values again
    _quiet(lambda: (dlg.set_input_dictionary(live), dlg.show()))
    qapp.processEvents()
    assert float(_box(dlg, KEY_WC_THICKNESS).text()) == float(before[KEY_WC_THICKNESS])
    assert dlg.is_modified is False


def test_closing_the_warning_box_itself_counts_as_cancel(dialog):
    dlg, _live, popups = dialog
    _user_types(_box(dlg, KEY_WC_THICKNESS))
    popups.answer = None                                     # box closed with its own X
    _quiet(dlg.close)
    assert dlg.isVisible() and dlg.is_modified is True


def test_no_warning_after_save_or_when_locked(dialog):
    dlg, _live, popups = dialog
    _user_types(_box(dlg, KEY_WC_THICKNESS))
    _quiet(dlg._save_inputs)
    popups.shown.clear()
    _quiet(dlg.close)
    assert popups.shown == [] and not dlg.isVisible()

    _quiet(dlg.show)
    dlg.is_modified = True
    dlg.lock(True)
    _quiet(dlg.close)
    assert popups.shown == [] and not dlg.isVisible()
    dlg.lock(False)


# ---------------------------------------------------------------- closing while a box is still being edited
@pytest.fixture
def real_boxes(monkeypatch):
    """The real message box (it takes focus like on screen); answers automatically, records nesting."""
    from PySide6.QtCore import QTimer
    import osdagbridge.desktop.ui.dialogs.additional_input.additional_inputs as module
    from osdagbridge.desktop.ui.dialogs.custom_messagebox import CustomMessageBox

    log = {"opened": [], "depth": 0, "max_depth": 0, "answer": BTN_DISCARD}

    class AutoAnswered(CustomMessageBox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._title, self._buttons = kwargs.get("title"), kwargs.get("buttons") or ["OK"]

        def exec(self):
            log["opened"].append(self._title)
            log["depth"] += 1
            log["max_depth"] = max(log["max_depth"], log["depth"])
            choice = log["answer"] if log["answer"] in self._buttons else self._buttons[0]
            QTimer.singleShot(150, lambda: self.buttonClicked(choice))
            try:
                return super().exec()
            finally:
                log["depth"] -= 1

    monkeypatch.setattr(module, "CustomMessageBox", AutoAnswered)
    return log


def _type_into_and_keep_focus(dlg, qapp, key, text):
    box = _box(dlg, key)
    dlg.activateWindow()
    box.setFocus()
    qapp.processEvents()
    box.selectAll()
    _user_types(box, text)
    return box


def _click_title_bar_close(dlg, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _quiet(lambda: QTest.mouseClick(dlg.title_bar.btn_close, Qt.LeftButton))
    for _ in range(10):
        QTest.qWait(50)


@pytest.mark.parametrize("key,text", [
    (KEY_TS_NO_OF_GIRDERS, "11"),         # layout warning on leaving the box
    (KEY_TS_DECK_THICKNESS, "900"),       # range warning
    (KEY_TS_GIRDER_SPACING, "40"),        # range warning + layout warning
])
def test_closing_while_typing_an_invalid_value_asks_once_and_never_stacks_boxes(qapp, popups, real_boxes, key, text):
    dlg, live = _open_dialog(qapp)
    before = dict(live)
    _type_into_and_keep_focus(dlg, qapp, key, text)

    _click_title_bar_close(dlg, qapp)

    assert real_boxes["opened"] == ["Unsaved Changes"]
    assert real_boxes["max_depth"] == 1
    assert not dlg.isVisible() and live == before


def test_escape_while_typing_asks_once(qapp, popups, real_boxes):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    dlg, _live = _open_dialog(qapp)
    box = _type_into_and_keep_focus(dlg, qapp, KEY_TS_NO_OF_GIRDERS, "11")

    _quiet(lambda: QTest.keyClick(box, Qt.Key_Escape))
    for _ in range(10):
        QTest.qWait(50)

    assert real_boxes["opened"] == ["Unsaved Changes"] and real_boxes["max_depth"] == 1
    assert not dlg.isVisible()


def test_cancel_keeps_the_typed_value_and_it_is_still_validated_when_leaving_the_box(qapp, popups, real_boxes):
    from PySide6.QtTest import QTest
    dlg, _live = _open_dialog(qapp)
    box = _type_into_and_keep_focus(dlg, qapp, KEY_TS_NO_OF_GIRDERS, "11")
    real_boxes["answer"] = BTN_CANCEL

    _click_title_bar_close(dlg, qapp)

    assert dlg.isVisible() and box.text() == "11"
    dlg.activateWindow()
    qapp.processEvents()
    assert qapp.focusWidget() is box                         # back in the box the user was typing in

    _quiet(lambda: _box(dlg, KEY_WC_THICKNESS).setFocus())    # user leaves the box
    for _ in range(10):
        QTest.qWait(50)

    assert real_boxes["opened"] == ["Unsaved Changes", "Layout"]   # validated, one box at a time
    assert real_boxes["max_depth"] == 1
    assert box.text() == "4"                                   # back to the value before the edit
    dlg.is_modified = False
    dlg.hide()
