"""
Small analysed ospgrillage models shared by the transverse-member tests.
"""

import pytest

og = pytest.importorskip("ospgrillage")
ops = pytest.importorskip("openseespy.opensees")

SPAN = 20.0
SPACING = 2.5
N_GIRDERS = 4
N_TRANS = 5

# (skew in degrees, deck overhang in m)
LAYOUTS = [(0.0, 0.0), (0.0, 1.0), (15.0, 1.0)]

FORCE_KEYS = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")


def build_model(skew, edge):
    section = og.create_section(A=0.05, J=1e-4, Iz=0.02, Iy=0.002, Az=0.02, Ay=0.02)
    cross = og.create_section(A=0.5, J=5e-3, Iz=1.3e-3, Iy=0.3, Az=0.4, Ay=0.4)
    steel = og.create_material(material="steel", E=200e9, v=0.3, rho=7850 * 9.81,
                               Fy=250e6, E0=200e9, b=0.01)
    girder = og.create_member(section=section, material=steel)
    transverse = og.create_member(section=cross, material=steel)

    model = og.create_grillage(
        bridge_name="test", long_dim=SPAN,
        width=(N_GIRDERS - 1) * SPACING + 2 * edge, skew=skew,
        num_long_grid=N_GIRDERS + (2 if edge > 0 else 0), num_trans_grid=N_TRANS,
        edge_beam_dist=edge, ext_to_int_dist=SPACING, mesh_type="Oblique",
    )
    for name in ("edge_beam", "exterior_main_beam_1", "interior_main_beam",
                 "exterior_main_beam_2"):
        model.set_member(girder, member=name)
    for name in ("transverse_slab", "start_edge", "end_edge"):
        model.set_member(transverse, member=name)
    model.create_osp_model(pyfile=False)
    return model


def add_point_load(model, name, x, z, p=-100e3):
    lc = og.create_load_case(name=name)
    lc.add_load(og.create_load(loadtype="point", name=name,
                               point1=og.create_load_vertex(x=x, y=0, z=z, p=p)))
    model.add_load_case(lc)
