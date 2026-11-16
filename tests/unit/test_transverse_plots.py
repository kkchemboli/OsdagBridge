"""
Transverse-member plots: the figure builder (plot_generator) and the
selection path through the Member dropdown and the plot widget.
"""

import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from grillage_models import add_point_load, build_model, ops

from osdagbridge.core.bridge_types.plate_girder import plot_generator as pg
from osdagbridge.core.bridge_types.plate_girder import results_data as rd
from osdagbridge.core.utils.common import (
    KEY_ANALYSIS_LOAD_COMBINATION,
    KEY_ANALYSIS_MEMBER,
    KEY_OUTPUT_DOCK_MEMBER_ID,
    KEY_TS_NO_OF_GIRDERS,
    VALUE_ANALYSIS_MEMBER_ALL,
)

EDGE = 1.0
PLOT_KEYS = ("Fx", "Fy", "Fz", "Mx", "My", "Mz", "Dx", "Dy", "Dz")
SELECTIONS = {member: [member] for member in ("ED1", "CB1", "CB2", "CB3", "ED2")}


@pytest.fixture(scope="module", params=[0.0, 15.0], ids=["square", "skew15"])
def results(request):
    model = build_model(request.param, EDGE)
    add_point_load(model, "P1", x=8.0, z=EDGE + 3.0)
    add_point_load(model, "P2", x=13.0, z=EDGE + 6.0, p=-60e3)
    model.analyze()
    ds = model.get_results()
    nodes, members = rd._build_nodes_members()
    member_map = rd.build_member_map(model, nodes, members, edge_dist=EDGE)
    yield types.SimpleNamespace(ds=ds, nodes=nodes, members=members, map=member_map)
    ops.wipe()


def _artists(fig, gid):
    ax = fig.axes[0]
    return [a for a in list(ax.lines) + list(ax.texts) if a.get_gid() == gid]


# ── figure builder ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", PLOT_KEYS)
@pytest.mark.parametrize("selection", list(SELECTIONS))
def test_every_component_plots_for_every_selection(results, key, selection):
    fig, summary = pg.build_figure_transverse(
        results.ds, "P1", key, results.nodes, results.members, results.map,
        selection, edge_dist=EDGE)
    assert list(summary) == SELECTIONS[selection]
    labels = [t.get_text() for t in _artists(fig, "girder_labels")]
    assert labels == SELECTIONS[selection]
    # the toggles of the output dock find their artists
    assert _artists(fig, "max_line") and _artists(fig, "min_line")
    fig.canvas.draw()


@pytest.mark.parametrize("key", PLOT_KEYS)
def test_plot_shows_the_selected_load_case(results, key):
    """For each load case the plotted extremes are that load case's values."""
    line = rd.select_transverse_lines(results.map, "CB2")[0]
    read = rd.member_line_displacements if key.startswith("D") else rd.member_line_forces
    for lc in ("P1", "P2"):
        summary = pg.build_figure_transverse(results.ds, lc, key, results.nodes,
                                             results.members, results.map, "CB2")[1]["CB2"]
        data = read(results.ds, lc, line, results.nodes, results.members, key)
        values = np.concatenate([data["val_a"], data["val_b"]])
        assert summary["max"] == pytest.approx(values.max(), abs=1e-9)
        assert summary["min"] == pytest.approx(values.min(), abs=1e-9)


@pytest.mark.parametrize("key", PLOT_KEYS)
def test_add_transverse_members_to_a_girder_figure(results, key):
    """The "All" picture: girder figure + every transverse member, one scale."""
    ds = results.ds.sel(Loadcase="P1")
    builder = (pg.build_figure_deflection if key.startswith("D")
               else pg.build_figure_sfd if key.startswith("F") else pg.build_figure_bmd)
    fig, girders = builder(ds, key, results.nodes, results.members, edge_dist=EDGE)
    added = pg.add_transverse_members(fig, results.ds, "P1", key, results.nodes,
                                      results.members, results.map)
    assert list(added) == ["ED1", "CB1", "CB2", "CB3", "ED2"]
    labels = [t.get_text() for t in _artists(fig, "girder_labels")]
    assert set(labels) >= {"G1", "G4", "ED1", "CB2", "ED2"}
    for member, extremes in added.items():
        alone = pg.build_figure_transverse(results.ds, "P1", key, results.nodes,
                                           results.members, results.map, member)[1]
        assert extremes == alone[member]
    # the value axis covers the transverse diagrams too
    sign = -1.0 if key.startswith("M") else 1.0
    lo, hi = fig.axes[0].get_zlim()
    for extremes in added.values():
        for v in extremes.values():
            assert lo <= sign * v <= hi
    fig.canvas.draw()


def test_grillage_view_shows_only_the_selected_member_like_a_girder(results):
    """Selecting CB2 shows CB2 alone, the way selecting G2 shows G2 alone."""
    def shown(selection):
        fig = pg.build_figure_grillage(results.nodes, results.members, edge_dist=EDGE,
                                       selected_girder=selection, member_map=results.map)
        labels = [t.get_text() for t in _artists(fig, "girder_labels")]
        numbers = {int(t.get_text().strip()) for t in fig.axes[0].texts
                   if t.get_gid() == "node_number"}
        return labels, numbers

    girder_labels, girder_nodes = shown("G2")
    assert girder_labels == ["G2"]

    labels, node_ids = shown("CB2")
    cb2 = rd.select_transverse_lines(results.map, "CB2")[0]
    assert labels == ["CB2"]
    assert node_ids == set(cb2["nodes"])                 # only CB2's nodes
    assert not node_ids & girder_nodes - set(cb2["nodes"])

    all_labels, all_nodes = shown(VALUE_ANALYSIS_MEMBER_ALL)
    assert "CB2" not in all_labels and len(all_nodes) == len(results.nodes)


# ── Member dropdown and plot widget (offscreen Qt) ───────────────────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _output_dock(member_map):
    """The real output dock, built from the real field list, on a minimal backend."""
    from osdagbridge.core.bridge_types.plate_girder.ui_fields import FrontendData
    from osdagbridge.desktop.ui.docks.output_dock import OutputDock
    backend = types.SimpleNamespace(
        input_dict={KEY_TS_NO_OF_GIRDERS: 4},
        output_values=FrontendData().output_values,
        get_member_map=lambda: member_map,
    )
    return OutputDock(backend=backend)


def _combo(dock, key):
    from PySide6.QtWidgets import QComboBox
    return dock.output_widget.findChild(QComboBox, key)


def _radio(dock, text):
    from osdagbridge.desktop.ui.utils.custom_widgets import CustomRadioButton
    return next(rb for rb in dock.output_widget.findChildren(CustomRadioButton)
                if rb.text() == text)


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count()) if combo.itemText(i)]


def test_member_dropdown_lists_girders_then_transverse_members(qapp, results):
    dock = _output_dock(results.map)
    dock.refresh_member_dropdown()
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)
    assert _items(combo) == [
        VALUE_ANALYSIS_MEMBER_ALL, "G1", "G2", "G3", "G4",
        "ED1", "CB1", "CB2", "CB3", "ED2",
    ]
    assert combo.currentText() == VALUE_ANALYSIS_MEMBER_ALL
    # transverse entries carry their id; girder entries stay plain text
    assert combo.itemData(combo.findText("CB2")) == "CB2"
    assert combo.itemData(combo.findText("G1")) is None
    # the design dropdown is girders only
    assert _items(_combo(dock, KEY_OUTPUT_DOCK_MEMBER_ID)) == ["G1", "G2", "G3", "G4"]


def test_member_dropdown_without_results_and_after_reset(qapp, results):
    empty = _output_dock({})
    empty.refresh_member_dropdown()
    assert _items(_combo(empty, KEY_ANALYSIS_MEMBER)) == [
        VALUE_ANALYSIS_MEMBER_ALL, "G1", "G2", "G3", "G4"]

    dock = _output_dock(results.map)
    dock.refresh_member_dropdown()
    dock.reset()
    assert _items(_combo(dock, KEY_ANALYSIS_MEMBER)) == [VALUE_ANALYSIS_MEMBER_ALL]


@pytest.fixture
def plot_widget(qapp, results):
    from osdagbridge.desktop.ui.mpl_plot_widget import MplPlotWidget
    dock = _output_dock(results.map)
    dock.refresh_member_dropdown()
    widget = MplPlotWidget()
    widget.resize(900, 500)
    widget.setup(results.ds, ["P1", "P2"], results.nodes, results.members,
                 edge_dist=EDGE, member_map=results.map)
    widget.link_output_dock(dock)
    yield widget, dock
    widget.clear()
    widget.deleteLater()


def _view(widget):
    ax = widget._fig.axes[0]
    return ax.elev, ax.azim


def test_selecting_a_transverse_member_plots_it(plot_widget):
    widget, dock = plot_widget
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)

    combo.setCurrentIndex(combo.findText("CB2"))
    assert list(widget._summary_data) == ["CB2"]
    assert _view(widget) == (pg.TRANSVERSE_ELEV, pg.TRANSVERSE_AZIM)
    # subtitle: the load case only; summary heading names the kind of member
    assert widget._title_overlay.subtitle_label.text() == "Load Case/Combination : P1"
    assert "Transverse&nbsp;Member" in widget._summary_overlay.text_label.text()
    assert "Girder" not in widget._summary_overlay.text_label.text()

    combo.setCurrentIndex(combo.findText("ED2"))
    assert list(widget._summary_data) == ["ED2"]


def test_all_plots_every_girder_and_every_transverse_member(plot_widget, results):
    widget, dock = plot_widget
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)
    assert combo.currentText() == VALUE_ANALYSIS_MEMBER_ALL
    assert list(widget._summary_data) == [
        "G1", "G2", "G3", "G4", "ED1", "CB1", "CB2", "CB3", "ED2"]
    assert _view(widget) == (pg.TRANSVERSE_ELEV, pg.TRANSVERSE_AZIM)
    assert widget._title_overlay.subtitle_label.text() == "Load Case/Combination : P1"
    hud = widget._summary_overlay.text_label.text()
    assert "<b>Member" in hud and "Transverse" not in hud and "Girder" not in hud

    # the transverse values in "All" are the ones the single-member plots show
    combo.setCurrentIndex(combo.findText("CB2"))
    single = dict(widget._summary_data["CB2"])
    combo.setCurrentIndex(combo.findText(VALUE_ANALYSIS_MEMBER_ALL))
    assert widget._summary_data["CB2"] == single

    # a single girder stays girders only
    combo.setCurrentIndex(combo.findText("G2"))
    assert list(widget._summary_data) == ["G2"]


def test_force_radio_and_load_case_drive_the_transverse_plot(plot_widget):
    widget, dock = plot_widget
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)
    combo.setCurrentIndex(combo.findText("CB2"))
    shear = dict(widget._summary_data["CB2"])

    _radio(dock, "M<sub>z</sub>").setChecked(True)
    moment_p1 = dict(widget._summary_data["CB2"])
    assert moment_p1 != shear
    assert "Bending Moment" in widget._title_overlay.title_label.text()

    lc = _combo(dock, KEY_ANALYSIS_LOAD_COMBINATION)
    lc.setCurrentIndex(lc.findText("P2"))
    assert widget._summary_data["CB2"] != moment_p1

    _radio(dock, "D<sub>y</sub>").setChecked(True)
    assert "Deflection" in widget._title_overlay.title_label.text()
    assert widget._title_overlay.subtitle_label.text() == "Load Case/Combination : P2"


def test_camera_is_kept_within_a_kind_and_reset_when_the_kind_changes(plot_widget):
    widget, dock = plot_widget
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)
    combo.setCurrentIndex(combo.findText("CB1"))
    widget._fig.axes[0].view_init(elev=40, azim=10)          # the user rotates
    combo.setCurrentIndex(combo.findText("CB3"))
    assert _view(widget) == (40, 10)                          # kept

    combo.setCurrentIndex(combo.findText("G2"))
    assert _view(widget) == (pg.DEFAULT_ELEV, pg.DEFAULT_AZIM)
    assert list(widget._summary_data) == ["G2"]
    assert "Girder" in widget._summary_overlay.text_label.text()


def test_grillage_mode_with_a_transverse_member_selected(plot_widget):
    widget, dock = plot_widget
    combo = _combo(dock, KEY_ANALYSIS_MEMBER)
    combo.setCurrentIndex(combo.findText("CB2"))
    widget._on_grillage_toggled(True)
    labels = [t.get_text() for t in widget._fig.axes[0].texts if t.get_gid() == "girder_labels"]
    assert labels == ["CB2"]                            # CB2 on its own, like a girder
    widget._on_grillage_toggled(False)
    assert list(widget._summary_data) == ["CB2"]
