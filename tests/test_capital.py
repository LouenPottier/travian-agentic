"""Verrouille la capitale, le palais et le plafond des champs (cf. CLAUDE.md).

Comportement recoupé sur support.travian.com (« Capital Village ») :
- seule la **capitale** monte les champs de ressources au-delà du niveau 10 ;
- **un seul palais** par compte ; palais ⇄ résidence exclusifs dans un village ;
- déclarer une capitale exige un **palais** ; l'ancienne capitale voit ses champs
  > 10 ramenés au niveau 10 au changement.
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "capital.db"

from app.engine import village as V
from app.engine import capital as CAP
from app.data.buildings import B
from app.data.tribes import Tribe


def fresh():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "capital.db"
    store.init_db()


# --- Bug 4 : champs > 10 réservés à la capitale ------------------------------
def test_field_cap_non_capital():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    cap = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0,
                        player_id=pid, is_capital=True)
    sec = V.new_village("Sec", Tribe.GAULS, server_speed=100, x=5, y=0,
                        player_id=pid, is_capital=False)
    # Un bûcheron (slot « res ») à niveau 10 dans chaque village + stockage pour
    # pouvoir financer la montée 10 → 11 (entrepôt/grenier niv 20).
    for v in (cap, sec):
        v.slots[1] = V.Slot(building_id=B.WOODCUTTER, level=10)
        v.slots[20] = V.Slot(building_id=B.WAREHOUSE, level=20)
        v.slots[21] = V.Slot(building_id=B.GRANARY, level=20)
        v.resources = [1e6] * 4
    assert V.effective_max_level(cap, V.BLD.get(B.WOODCUTTER)) == 20
    assert V.effective_max_level(sec, V.BLD.get(B.WOODCUTTER)) == 10
    # Capitale : 10 → 11 autorisé.
    V.enqueue_build(cap, 1)
    assert cap.queue[0].target_level == 11
    # Hors capitale : 10 → 11 refusé.
    try:
        V.enqueue_build(sec, 1)
        assert False, "le champ hors capitale ne doit pas dépasser 10"
    except V.BuildError as e:
        assert "niveau 10" in str(e)
    print("✅ champs > 10 réservés à la capitale")


# --- Bug 3 : palais ⇄ résidence + un seul palais par compte ------------------
def test_palace_residence_exclusion():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    # Prérequis du palais : bâtiment principal niv 5 + ambassade niv 1.
    v.slots[19] = V.Slot(building_id=B.MAIN_BUILDING, level=5)
    v.slots[20] = V.Slot(building_id=B.EMBASSY, level=1)
    # Sans résidence ni palais : le palais est proposé sur un emplacement vide.
    offered = {b.id for b in V.available_buildings(v, 21)}
    assert B.PALACE in offered and B.RESIDENCE in offered
    # Avec une résidence : plus de palais possible (et inversement).
    v.slots[22] = V.Slot(building_id=B.RESIDENCE, level=1)
    offered = {b.id for b in V.available_buildings(v, 21)}
    assert B.PALACE not in offered, "pas de palais là où il y a une résidence"
    # Un palais ailleurs sur le compte (account_has_palace) masque le palais partout.
    v2 = V.new_village("Sec", Tribe.GAULS, server_speed=100, x=5, y=0, player_id=pid)
    v2.slots[19] = V.Slot(building_id=B.MAIN_BUILDING, level=5)
    v2.slots[20] = V.Slot(building_id=B.EMBASSY, level=1)
    offered = {b.id for b in V.available_buildings(v2, 21, account_has_palace=True)}
    assert B.PALACE not in offered, "un seul palais par compte"
    print("✅ palais ⇄ résidence exclusifs ; un seul palais/compte")


# --- Bug 2 : changement de capitale ------------------------------------------
def test_make_capital():
    fresh()
    now = time.time()
    pid = store.create_player("Toi", Tribe.GAULS)
    cap = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0,
                        player_id=pid, is_capital=True)
    cap.slots[1] = V.Slot(building_id=B.WOODCUTTER, level=15)   # champ > 10
    cap.resources = [1e6] * 4
    cap = store.insert_village(cap)
    sec = V.new_village("Sec", Tribe.GAULS, server_speed=100, x=5, y=0,
                        player_id=pid, is_capital=False)
    sec.slots[19] = V.Slot(building_id=B.MAIN_BUILDING, level=5)
    sec.slots[20] = V.Slot(building_id=B.PALACE, level=1)       # palais requis
    sec.resources = [1e6] * 4
    sec = store.insert_village(sec)

    # Sans palais on ne peut pas déclarer la capitale.
    try:
        CAP.make_capital(pid, cap.id, now)   # cap n'a pas de palais
        assert False
    except CAP.CapitalError:
        pass

    info = CAP.make_capital(pid, sec.id, now)
    new_cap = store.load_village(sec.id)
    old_cap = store.load_village(cap.id)
    assert new_cap.is_capital and not old_cap.is_capital
    # L'ancienne capitale : champ 15 → 10.
    assert old_cap.slots[1].level == 10, old_cap.slots[1].level
    assert (1, 15) in info["reduced"]
    print("✅ capitale déplacée ; champs > 10 de l'ancienne ramenés à 10")


def test_brewery_capital_only():
    """La brasserie (Teuton) ne se construit qu'en **capitale** (support.travian.com /
    unofficialtravian « Brewery ») — flag capital_only sur le bâtiment."""
    fresh()
    pid = store.create_player("Teu", Tribe.TEUTONS)
    v = V.new_village("Cap", Tribe.TEUTONS, server_speed=100, x=0, y=0,
                      player_id=pid, is_capital=True)
    # Prérequis de la brasserie : grenier niv 20 + place de rassemblement niv 10.
    v.slots[18] = V.Slot(building_id=B.GRANARY, level=20)
    v.slots[19] = V.Slot(building_id=B.RALLY_POINT, level=10)
    assert B.BREWERY in {b.id for b in V.available_buildings(v, 20)}, \
        "brasserie offerte en capitale (Teuton, prérequis remplis)"
    v.is_capital = False
    assert B.BREWERY not in {b.id for b in V.available_buildings(v, 20)}, \
        "brasserie interdite hors capitale"
    print("✅ brasserie : capitale uniquement")


def test_make_capital_drops_incompatible_buildings():
    """Au changement de capitale : la **nouvelle** capitale perd ses bâtiments
    `non_capital` (grande caserne/écurie…) et l'**ancienne** ses bâtiments `capital_only`
    (tailleur de pierre…). Sans remboursement (support.travian.com « Capital Village »).
    ⚠️ Le grand entrepôt/grenier n'est **pas** concerné : il est légitime en capitale."""
    fresh()
    now = time.time()
    pid = store.create_player("Toi", Tribe.GAULS)

    # Capitale actuelle : un tailleur de pierre (capital_only) qui devra disparaître.
    cap = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0,
                        player_id=pid, is_capital=True)
    cap.slots[15] = V.Slot(building_id=B.STONEMASON, level=5)
    cap = store.insert_village(cap)

    # Futur de capitale : une grande caserne (non_capital) qui devra disparaître,
    # plus le palais requis pour déclarer la capitale. On y pose aussi un grand entrepôt
    # (PAS non_capital) qui, lui, doit **survivre** au changement.
    sec = V.new_village("Sec", Tribe.GAULS, server_speed=100, x=5, y=0,
                        player_id=pid, is_capital=False)
    sec.slots[20] = V.Slot(building_id=B.PALACE, level=1)
    sec.slots[15] = V.Slot(building_id=B.GREAT_BARRACKS, level=3)
    sec.slots[16] = V.Slot(building_id=B.GREAT_WAREHOUSE, level=3)
    sec = store.insert_village(sec)

    info = CAP.make_capital(pid, sec.id, now)
    new_cap = store.load_village(sec.id)
    old_cap = store.load_village(cap.id)

    assert B.GREAT_BARRACKS not in {s.building_id for s in new_cap.slots.values()}, \
        "la grande caserne doit disparaître de la nouvelle capitale"
    assert B.GREAT_BARRACKS in info["removed_new_capital"]
    assert B.GREAT_WAREHOUSE in {s.building_id for s in new_cap.slots.values()}, \
        "le grand entrepôt doit SURVIVRE dans la nouvelle capitale (pas non_capital)"
    assert B.GREAT_WAREHOUSE not in info["removed_new_capital"]
    assert B.STONEMASON not in {s.building_id for s in old_cap.slots.values()}, \
        "le tailleur de pierre doit disparaître de l'ancienne capitale"
    assert B.STONEMASON in info["removed_old_capital"]
    print("✅ changement de capitale : bâtiments incompatibles retirés des deux côtés")
