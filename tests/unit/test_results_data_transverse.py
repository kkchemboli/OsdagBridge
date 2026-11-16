"""
Transverse-member result extraction (results_data.py).

Small ospgrillage models are analysed for real, so these tests check the
element mapping and the member-axis forces against what the solver reports.
"""

import numpy as np
import pytest

og = pytest.importorskip("ospgrillage")
ops = pytest.importorskip("openseespy.opensees")

from osdagbridge.core.bridge_types.plate_girder import results_data as rd
from osdagbridge.core.utils.common import (
    MEMBER_KIND_CROSS_BRACING,
    MEMBER_KIND_END_DIAPHRAGM,
)

from grillage_models import (
    FORCE_KEYS, LAYOUTS, N_GIRDERS, N_TRANS,
    add_point_load as _add_point_load,
    build_model as _build_model,
)


@pytest.fixture(params=LAYOUTS, ids=lambda p: f"skew{p[0]:g}_edge{p[1]:g}")
def bridge(request):
    """Analysed model with two load cases."""
    skew, edge = request.param
    model = _build_model(skew, edge)
    _add_point_load(model, "P1", x=8.0, z=edge + 3.0)
    _add_point_load(model, "P2", x=13.0, z=edge + 6.0, p=-60e3)
    model.analyze()
    ds = model.get_results()
    data = rd.restructure_data(model, edge_dist=edge, dataset=ds, lazy=True)
    yield {
        "model": model, "ds": ds, "data": data, "skew": skew, "edge": edge,
        "nodes": {int(k): v for k, v in data["nodes"].items()},
        "members": {int(k): v for k, v in data["members"].items()},
        "map": data["member_map"],
    }
    ops.wipe()


# ── element mapping ──────────────────────────────────────────────────────────

def test_every_element_is_classified_exactly_once(bridge):
    orientation = bridge["map"]["element_orientation"]
    assert set(orientation) == set(bridge["members"])
    assert set(orientation.values()) == {"L", "T"}


def test_transverse_tags_match_the_ospgrillage_groups(bridge):
    model = bridge["model"]
    expected = set()
    for group in ("start_edge", "end_edge", "transverse_slab"):
        expected |= {int(e) for e in model.get_element(member=group, options="elements")}
    transverse = {t for t, o in bridge["map"]["element_orientation"].items() if o == "T"}
    assert transverse == expected


def test_longitudinal_tags_run_along_the_span(bridge):
    nodes, members = bridge["nodes"], bridge["members"]
    for tag, o in bridge["map"]["element_orientation"].items():
        n1, n2 = members[tag]
        same_z = abs(nodes[n1][2] - nodes[n2][2]) < 1e-6
        assert same_z == (o == "L")


def test_transverse_lines_are_one_per_grid_line_and_ordered(bridge):
    lines = bridge["map"]["transverse"]
    nodes = bridge["nodes"]
    assert len(lines) == N_TRANS
    assert [ln["x"] for ln in lines] == sorted(ln["x"] for ln in lines)
    for ln in lines:
        assert len(ln["nodes"]) == len(ln["elements"]) + 1
        zs = [nodes[n][2] for n in ln["nodes"]]
        assert zs == sorted(zs)
        for k, tag in enumerate(ln["elements"]):
            assert set(bridge["members"][tag]) == {ln["nodes"][k], ln["nodes"][k + 1]}


def test_end_diaphragms_are_the_two_end_lines(bridge):
    lines = bridge["map"]["transverse"]
    assert [ln["id"] for ln in lines] == ["ED1", "CB1", "CB2", "CB3", "ED2"]
    kinds = [ln["kind"] for ln in lines]
    assert kinds[0] == kinds[-1] == MEMBER_KIND_END_DIAPHRAGM
    assert set(kinds[1:-1]) == {MEMBER_KIND_CROSS_BRACING}

    model = bridge["model"]
    start = {int(e) for e in model.get_element(member="start_edge", options="elements")}
    end = {int(e) for e in model.get_element(member="end_edge", options="elements")}
    assert set(lines[0]["elements"]) <= start
    assert set(lines[-1]["elements"]) <= end


def test_overhang_stubs_are_left_out(bridge):
    """With an overhang the outermost lines are edge beams, not girders, and
    the stubs reaching them are not part of a transverse member."""
    # four girders in every layout, so three bays per line once stubs are gone
    for ln in bridge["map"]["transverse"]:
        assert len(ln["elements"]) == N_GIRDERS - 1
    if bridge["edge"] > 0:
        nodes = bridge["nodes"]
        z_all = [c[2] for c in nodes.values()]
        for ln in bridge["map"]["transverse"]:
            zs = [nodes[n][2] for n in ln["nodes"]]
            assert min(zs) > min(z_all) + 1e-6 and max(zs) < max(z_all) - 1e-6


def test_missing_member_groups_raise(bridge):
    """The ospgrillage groups are the only source; without them there is no map."""
    import types
    args = (bridge["nodes"], bridge["members"])

    with pytest.raises(RuntimeError, match="no grillage model"):
        rd.build_member_map(None, *args)

    def unreadable(member, options):
        raise KeyError(member)
    with pytest.raises(RuntimeError, match="could not read .*'start_edge'"):
        rd.build_member_map(types.SimpleNamespace(get_element=unreadable), *args)

    real = bridge["model"]
    def no_end_lines(member, options):
        return [] if member == "end_edge" else real.get_element(member=member, options=options)
    with pytest.raises(RuntimeError, match="'end_edge' has no elements"):
        rd.build_member_map(types.SimpleNamespace(get_element=no_end_lines), *args)


def test_select_transverse_lines(bridge):
    mm = bridge["map"]
    ids = lambda sel: [ln["id"] for ln in rd.select_transverse_lines(mm, sel)]
    assert ids("ED1") == ["ED1"] and ids("ED2") == ["ED2"]
    assert ids("CB2") == ["CB2"]
    assert ids("G1") == [] and ids("All") == []
    assert rd.select_transverse_lines(None, "CB2") == []


# ── forces in member axes ────────────────────────────────────────────────────

@pytest.mark.parametrize("skew,edge", LAYOUTS)
def test_member_axis_forces_match_the_solver_local_forces(skew, edge):
    """One load case, so the live OpenSees state is that case's solution."""
    model = _build_model(skew, edge)
    _add_point_load(model, "P", x=8.0, z=edge + 3.0)
    model.analyze()
    ds = model.get_results()
    nodes, members = rd._build_nodes_members()
    mm = rd.build_member_map(model, nodes, members, edge_dist=edge)

    # OpenSees local end forces: [N, Vy, Vz, T, My, Mz] at i, then at j
    local_index = {"Fx": 0, "Fy": 1, "Fz": 2, "Mx": 3, "My": 4, "Mz": 5}
    checked = 0
    for line in mm["transverse"]:
        local = np.array([ops.eleResponse(t, "localForces") for t in line["elements"]]) / 1000.0
        for key in FORCE_KEYS:
            out = rd.member_line_forces(ds, "P", line, nodes, members, key)
            np.testing.assert_allclose(np.abs(out["val_a"]), np.abs(local[:, local_index[key]]),
                                       atol=1e-6)
            np.testing.assert_allclose(np.abs(out["val_b"]), np.abs(local[:, 6 + local_index[key]]),
                                       atol=1e-6)
            checked += 1
    assert checked == N_TRANS * len(FORCE_KEYS)
    ops.wipe()


def test_shear_is_constant_and_moment_is_continuous_within_an_element(bridge):
    """Loads act at the nodes only, so along one element shear does not change
    and bending changes by shear x length."""
    nodes, members = bridge["nodes"], bridge["members"]
    for line in bridge["map"]["transverse"]:
        args = (bridge["ds"], "P1", line, nodes, members)
        shear = rd.member_line_forces(*args, "Fy")
        np.testing.assert_allclose(shear["val_a"], shear["val_b"], atol=1e-6)
        moment = rd.member_line_forces(*args, "Mz")
        length = np.hypot(moment["xb"] - moment["xa"], moment["zb"] - moment["za"])
        np.testing.assert_allclose(np.abs(moment["val_b"] - moment["val_a"]),
                                   np.abs(shear["val_a"]) * length, atol=1e-6)


def _stored_member_forces(ds, loadcase, line, nodes, members, key):
    """The selected load case's stored end forces, turned into member axes here,
    independently of results_data: (start values, end values) in kN / kNm."""
    vec, which = {"Fx": ("V", "axial"), "Fy": ("V", "y"), "Fz": ("V", "z"),
                  "Mx": ("M", "axial"), "My": ("M", "y"), "Mz": ("M", "z")}[key]
    f = ds["forces"].sel(Loadcase=loadcase)
    start, end = [], []
    for k, tag in enumerate(line["elements"]):
        a, b = line["nodes"][k], line["nodes"][k + 1]
        dx, dz = nodes[b][0] - nodes[a][0], nodes[b][2] - nodes[a][2]
        c, s = np.array([dx, dz]) / np.hypot(dx, dz)
        ends = ("i", "j") if members[tag][0] == a else ("j", "i")
        for out, end_name, sign in ((start, ends[0], 1), (end, ends[1], -1)):
            X, Y, Z = (float(f.sel(Element=tag, Component=f"{vec}{ax}_{end_name}")) for ax in "xyz")
            out.append(sign * {"axial": c * X + s * Z, "y": Y, "z": -s * X + c * Z}[which] / 1000)
    return np.array(start), np.array(end)


def test_values_come_from_the_selected_load_case(bridge):
    """For each load case, every value equals that load case's stored results,
    so reading another load case by mistake fails."""
    ds, nodes, members = bridge["ds"], bridge["nodes"], bridge["members"]
    for lc in ("P1", "P2"):
        for line in bridge["map"]["transverse"]:
            for key in FORCE_KEYS:
                out = rd.member_line_forces(ds, lc, line, nodes, members, key)
                start, end = _stored_member_forces(ds, lc, line, nodes, members, key)
                np.testing.assert_allclose(out["val_a"], start, atol=1e-9)
                np.testing.assert_allclose(out["val_b"], end, atol=1e-9)
            for key, comp, sign in (("Dx", "x", 1), ("Dy", "y", -1), ("Dz", "z", -1)):
                out = rd.member_line_displacements(ds, lc, line, nodes, members, key)
                stored = ds["displacements"].sel(Loadcase=lc, Component=comp)
                expected = [sign * float(stored.sel(Node=n)) * 1000 for n in line["nodes"]]
                np.testing.assert_allclose(out["val_a"], expected[:-1], atol=1e-9)
                np.testing.assert_allclose(out["val_b"], expected[1:], atol=1e-9)
    # the two load cases really differ, so a mix-up could not pass unnoticed
    line = bridge["map"]["transverse"][2]
    p1 = _stored_member_forces(ds, "P1", line, nodes, members, "Mz")[0]
    p2 = _stored_member_forces(ds, "P2", line, nodes, members, "Mz")[0]
    assert not np.allclose(p1, p2)
    with pytest.raises(KeyError):
        rd.member_line_forces(ds, "no such case", line, nodes, members, "Mz")
