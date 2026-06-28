"""Verrouille l'occupation d'oasis (cf. CLAUDE.md) :

- Emplacements d'oasis = manoir du héros niv 10/15/20 → 1/2/3 (formulas.slots3).
- Annexion possible seulement si : oasis nettoyée (0 animal), en portée (Tchebychev
  ≤ OASIS_RANGE), manoir niv 10+, emplacement libre, oasis non déjà occupée.
- Le bonus de production de l'oasis est crédité au village (additif, % de la base).
- Abandon : libère l'emplacement et la case.

Approximations documentées (kirilloid ne modélise pas l'occupation) : seuils de
manoir, portée d'annexion (cf. engine/oasis.py).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "oasis.db"

from app.engine import village as V
from app.engine import oasis as OAS
from app.data.buildings import B
from app.data.tribes import Tribe

CLEARED = [0] * 10          # oasis nettoyée
GUARDED = [2] + [0] * 9     # oasis encore gardée


def setup(mansion_level: int = 10):
    store.DB_PATH = Path(tempfile.mkdtemp()) / "oasis.db"
    store.init_db()
    store.insert_tiles([
        {"x": 0, "y": 0, "kind": "valley", "layout": "4-4-4-6", "animals": None},
        {"x": 1, "y": 0, "kind": "oasis", "layout": "wood25", "animals": CLEARED},
        {"x": 0, "y": 1, "kind": "oasis", "layout": "clay25", "animals": CLEARED},
        {"x": 2, "y": 2, "kind": "oasis", "layout": "crop50", "animals": GUARDED},
        {"x": 10, "y": 0, "kind": "oasis", "layout": "iron25", "animals": CLEARED},
    ])
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Capitale", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    if mansion_level:
        v.slots[21] = V.Slot(building_id=B.HERO_MANSION, level=mansion_level)
    v.resources = [50000.0] * 4
    v = store.insert_village(v)
    return pid, v.id


def test_slots_scale_with_mansion():
    # 0 sous niv 10, puis 1 / 2 / 3 aux paliers 10 / 15 / 20.
    for lvl, expect in [(0, 0), (9, 0), (10, 1), (14, 1), (15, 2), (19, 2), (20, 3)]:
        v = V.new_village("X", Tribe.GAULS, x=0, y=0)
        if lvl:
            v.slots[21] = V.Slot(B.HERO_MANSION, lvl)
        assert OAS.max_oases(v) == expect, (lvl, OAS.max_oases(v))
    print("✅ emplacements d'oasis : 0/0/1/1/2/2/3 aux niveaux 0/9/10/14/15/19/20")


def test_occupy_credits_production():
    pid, vid = setup(mansion_level=10)
    now = time.time()
    before = V.gross_production(store.load_village(vid))

    info = OAS.occupy(vid, 1, 0, pid, now)          # wood25 en (1|0)
    assert info["free_slots"] == 0
    v = store.load_village(vid)
    assert {"x": 1, "y": 0, "code": "wood25"} in v.oases
    assert store.get_tile(1, 0)["owner_id"] == vid

    after = V.gross_production(v)
    assert abs(after[V.WOOD] - before[V.WOOD] * 1.25) < 1e-6, (before, after)
    assert abs(after[V.CLAY] - before[V.CLAY]) < 1e-6, "argile inchangée"
    print(f"✅ occupation (1|0) wood25 : bois {before[V.WOOD]:.0f} → {after[V.WOOD]:.0f} (+25 %)")


def test_occupy_refusals():
    pid, vid = setup(mansion_level=10)
    now = time.time()

    def refused(x, y, why):
        try:
            OAS.occupy(vid, x, y, pid, now)
            assert False, f"occupation ({x}|{y}) aurait dû échouer ({why})"
        except OAS.OasisError as e:
            print(f"refus attendu ({why}) :", e)

    refused(2, 2, "gardée par des animaux")
    refused(10, 0, "hors de portée")
    refused(0, 0, "pas une oasis (vallée)")

    # Manoir trop bas : aucune oasis annexable.
    pid2, vid2 = setup(mansion_level=9)
    try:
        OAS.occupy(vid2, 1, 0, pid2, now)
        assert False, "manoir niv 9 aurait dû refuser"
    except OAS.OasisError as e:
        print("refus attendu (manoir < 10) :", e)
    print("✅ refus : gardée / hors portée / vallée / manoir insuffisant")


def test_free_slot_and_already_occupied():
    pid, vid = setup(mansion_level=10)   # 1 seul emplacement
    now = time.time()
    OAS.occupy(vid, 1, 0, pid, now)

    # Emplacement plein : la 2ᵉ oasis (en portée, nettoyée) est refusée.
    try:
        OAS.occupy(vid, 0, 1, pid, now)
        assert False, "2ᵉ oasis sans emplacement libre aurait dû échouer"
    except OAS.OasisError as e:
        print("refus attendu (emplacement plein) :", e)

    # Oasis déjà occupée : re-annexion refusée.
    try:
        OAS.occupy(vid, 1, 0, pid, now)
        assert False, "ré-occuper une oasis possédée aurait dû échouer"
    except OAS.OasisError as e:
        print("refus attendu (déjà occupée) :", e)
    assert OAS.eligible_villages(pid, store.get_tile(1, 0)) == []
    print("✅ emplacement plein & oasis déjà occupée → refus")


def test_abandon_releases():
    pid, vid = setup(mansion_level=10)
    now = time.time()
    OAS.occupy(vid, 1, 0, pid, now)
    OAS.abandon(vid, 1, 0, pid, now)
    v = store.load_village(vid)
    assert v.oases == []
    assert store.get_tile(1, 0)["owner_id"] is None
    # Une fois libérée, elle redevient éligible.
    assert any(e["id"] == vid for e in OAS.eligible_villages(pid, store.get_tile(1, 0)))
    print("✅ abandon : oasis libérée, emplacement récupéré")


def main():
    test_slots_scale_with_mansion()
    test_occupy_credits_production()
    test_occupy_refusals()
    test_free_slot_and_already_occupied()
    test_abandon_releases()
    print("\n✅ Occupation d'oasis validée")


if __name__ == "__main__":
    main()
