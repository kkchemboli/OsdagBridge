"""
Interactive dimension editing for the 2D CAD canvases.

The canvases are painted with QPainter, so dimension labels are not widgets.
Each canvas registers the rectangle of every dimension label it draws (keyed by
the central ``KEY_*`` constant of the parameter it shows).  A double-click on
an editable rectangle opens a temporary line edit on top of it; the canvas only
reports ``(key, typed_text)`` through a signal and never mutates the model.
Validation, storage and the redraw are done by the owner (TemplatePage).

Editable (write-enabled): span, carriageway width, girder spacing, deck thickness.
Locked (derived, read-only): overhang, overall bridge width, bracing spacing.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QLocale, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QPen
from PySide6.QtWidgets import QApplication, QLineEdit, QToolTip, QWidget

from osdagbridge.core.utils.common import (
    KEY_CARRIAGEWAY_WIDTH,
    KEY_MP_CB_SPACING,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_OVERALL_WIDTH,
)

# Extra pixels around a label that still count as a hit (easier to click).
HIT_MARGIN_PX = 3
LOCKED_TOOLTIP = "Derived value - not editable"
_EDIT_COLOR = QColor(144, 175, 19)      # OsdagBridge green
_LOCKED_COLOR = QColor(130, 130, 130)


@dataclass(frozen=True)
class DimensionSpec:
    key: str                    # central KEY_* constant of the parameter
    unit: str                   # unit shown next to the editor
    params_key: str | None      # entry in the CAD widget's ``params`` (mm), if any
    mm_per_unit: float          # CAD params are in mm; 1000 for metre parameters
    editable: bool
    decimals: int = 3


DIMENSION_SPECS: dict[str, DimensionSpec] = {
    spec.key: spec
    for spec in (
        # ---- write-enabled ----
        DimensionSpec(KEY_SPAN,               "m",  "span_length",           1000.0, True),
        DimensionSpec(KEY_CARRIAGEWAY_WIDTH,  "m",  "carriageway_width",     1000.0, True),
        DimensionSpec(KEY_TS_GIRDER_SPACING,  "m",  "girder_spacing",        1000.0, True),
        DimensionSpec(KEY_TS_DECK_THICKNESS,  "mm", "deck_thickness",        1.0,    True, 1),
        # ---- locked (derived) ----
        DimensionSpec(KEY_TS_DECK_OVERHANG,   "m",  "deck_overhang",         1000.0, False),
        DimensionSpec(KEY_TS_OVERALL_WIDTH,   "m",  None,                    1000.0, False),
        DimensionSpec(KEY_MP_CB_SPACING,      "m",  "cross_bracing_spacing", 1000.0, False),
    )
}

EDITABLE_KEYS = tuple(k for k, s in DIMENSION_SPECS.items() if s.editable)
LOCKED_KEYS = tuple(k for k, s in DIMENSION_SPECS.items() if not s.editable)


def format_number(value: float, decimals: int) -> str:
    """Fixed decimals with trailing zeros removed (35.0 -> '35', 2.75 -> '2.75')."""
    text = f"{value:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class _DimensionLineEdit(QLineEdit):
    """Line edit that reports Enter / Esc / focus loss instead of handling them."""

    committed = Signal()
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.committed.emit()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Clicking outside commits; a popup (e.g. IME / menu) does not.
        if event.reason() != Qt.PopupFocusReason:
            self.committed.emit()


class _OutsideClickFilter(QObject):
    """
    Application-wide filter that reports a mouse press anywhere outside the editor.

    Focus loss alone is not enough: clicking an empty part of the canvas (a widget that
    does not accept focus) leaves the keyboard focus in the editor.
    """

    clicked_outside = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and isinstance(obj, QWidget):
            if obj is not self._editor and not self._editor.isAncestorOf(obj):
                self.clicked_outside.emit()     # the click itself is still delivered
        return False


class DimensionEditMixin:
    """
    Mixin for a QWidget canvas.  The widget class must declare
    ``dimension_edit_requested = Signal(str, str)`` and call
    ``_init_dimension_edit()`` from ``__init__``.
    """

    def _init_dimension_edit(self):
        self.interactive_edit = False         # enabled only by the main-window canvas
        self.dimension_label_zones = []       # [(QRectF, key)] rebuilt on every paint
        self._dim_editor = None
        self._dim_editing_key = None
        self._dim_editing_start = None        # value (display unit) when editing began
        self._hovered_dim_index = -1

    # ---- registration (called while painting) -------------------------------
    def _reset_dimension_zones(self):
        self.dimension_label_zones = []

    def _register_dimension_zone(self, rect, key):
        if key in DIMENSION_SPECS and rect is not None:
            self.dimension_label_zones.append(
                (rect.adjusted(-HIT_MARGIN_PX, -HIT_MARGIN_PX, HIT_MARGIN_PX, HIT_MARGIN_PX), key)
            )

    def _dimension_hit(self, pos):
        """Index of the zone under ``pos`` (QPointF), or -1."""
        for i, (rect, _key) in enumerate(self.dimension_label_zones):
            if rect.contains(pos):
                return i
        return -1

    # ---- hover ---------------------------------------------------------------
    def _update_dimension_hover(self, pos):
        if not self.interactive_edit or self._dim_editor is not None:
            return
        idx = self._dimension_hit(pos)
        if idx == self._hovered_dim_index:
            return
        self._hovered_dim_index = idx
        if idx < 0:
            self.unsetCursor()
            QToolTip.hideText()
        else:
            spec = DIMENSION_SPECS[self.dimension_label_zones[idx][1]]
            if spec.editable:
                self.setCursor(Qt.IBeamCursor)
                QToolTip.hideText()
            else:
                self.setCursor(Qt.ForbiddenCursor)
                QToolTip.showText(self.mapToGlobal(pos.toPoint()), LOCKED_TOOLTIP, self)
        self.update()

    def leaveEvent(self, event):
        if self._hovered_dim_index != -1:
            self._hovered_dim_index = -1
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def _draw_dimension_hover(self, painter):
        """Highlight the hovered dimension label (green = editable, grey = locked)."""
        idx = self._hovered_dim_index
        if not self.interactive_edit or not (0 <= idx < len(self.dimension_label_zones)):
            return
        rect, key = self.dimension_label_zones[idx]
        editable = DIMENSION_SPECS[key].editable
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_EDIT_COLOR if editable else _LOCKED_COLOR, 1.4,
                            Qt.SolidLine if editable else Qt.DashLine))
        painter.drawRoundedRect(rect, 2, 2)
        painter.restore()

    # ---- editing -------------------------------------------------------------
    def current_dimension_value(self, key):
        """Stored value of ``key`` in its display unit, or None if unknown."""
        spec = DIMENSION_SPECS.get(key)
        if spec is None or spec.params_key is None:
            return None
        raw = self.params.get(spec.params_key)
        return None if raw is None else float(raw) / spec.mm_per_unit

    def mouseDoubleClickEvent(self, event):
        if not self.interactive_edit:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position()
        idx = self._dimension_hit(pos)
        if idx < 0:
            super().mouseDoubleClickEvent(event)
            return
        rect, key = self.dimension_label_zones[idx]
        if not DIMENSION_SPECS[key].editable:
            QToolTip.showText(event.globalPosition().toPoint(), LOCKED_TOOLTIP, self)
            return
        self._start_dimension_edit(key, rect)

    def _start_dimension_edit(self, key, rect):
        self._finish_dimension_edit(commit=False)       # at most one editor at a time
        spec = DIMENSION_SPECS[key]
        value = self.current_dimension_value(key)

        editor = _DimensionLineEdit(self)
        validator = QDoubleValidator(0.0, 1.0e6, 4, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setLocale(QLocale(QLocale.C))
        editor.setValidator(validator)
        editor.setAlignment(Qt.AlignCenter)
        editor.setToolTip(f"Enter new value ({spec.unit}) - Enter to apply, Esc to cancel")
        editor.setStyleSheet(
            "QLineEdit { background: white; color: black; border: 1.5px solid #90AF13;"
            " border-radius: 2px; padding: 0px 2px; font-size: 9pt; }"
        )
        if value is not None:
            editor.setText(format_number(value, spec.decimals + 1))
        editor.committed.connect(lambda: self._finish_dimension_edit(commit=True))
        editor.cancelled.connect(lambda: self._finish_dimension_edit(commit=False))
        click_filter = _OutsideClickFilter(editor)
        click_filter.clicked_outside.connect(lambda: self._finish_dimension_edit(commit=True))
        QApplication.instance().installEventFilter(click_filter)
        editor._click_filter = click_filter

        self._dim_editor = editor
        self._dim_editing_key = key
        self._dim_editing_start = value
        self._place_dimension_editor(rect)
        editor.show()
        editor.setFocus()
        editor.selectAll()
        self.unsetCursor()
        self.update()

    def _place_dimension_editor(self, rect):
        editor = self._dim_editor
        width = max(int(rect.width()) + 16, 72)
        height = max(int(rect.height()) + 4, 22)
        center = rect.center()
        x = min(max(int(center.x() - width / 2), 0), max(self.width() - width, 0))
        y = min(max(int(center.y() - height / 2), 0), max(self.height() - height, 0))
        editor.setGeometry(x, y, width, height)

    def _schedule_dimension_editor_reposition(self):
        """Keep the editor on its label after zoom/scroll/redraw (called after paint)."""
        if self._dim_editor is not None:
            QTimer.singleShot(0, self._reposition_dimension_editor)

    def _reposition_dimension_editor(self):
        editor = self._dim_editor
        if editor is None:
            return
        zones = [r for r, k in self.dimension_label_zones if k == self._dim_editing_key]
        if not zones:                                   # label no longer drawn
            self._finish_dimension_edit(commit=False)
            return
        here = QPointF(editor.geometry().center())
        nearest = min(zones, key=lambda r: (r.center() - here).manhattanLength())
        self._place_dimension_editor(nearest)

    def _finish_dimension_edit(self, commit):
        editor = self._dim_editor
        if editor is None:                              # already finished (Enter + focus-out)
            return
        key, start = self._dim_editing_key, self._dim_editing_start
        text = editor.text().strip()
        self._dim_editor = None
        self._dim_editing_key = None
        self._dim_editing_start = None
        editor.blockSignals(True)
        QApplication.instance().removeEventFilter(editor._click_filter)
        editor._click_filter.blockSignals(True)
        editor.hide()
        editor.deleteLater()
        self._hovered_dim_index = -1
        self.update()

        if not commit or not text or not editor.hasAcceptableInput():
            return
        try:
            new_value = float(text)
        except ValueError:
            return
        if start is not None and abs(new_value - start) < 1e-9:
            return                                      # unchanged
        self.dimension_edit_requested.emit(key, text)
