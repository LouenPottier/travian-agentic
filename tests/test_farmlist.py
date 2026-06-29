"""Verrouille la liste de fermes (farm list T4) — razzias groupées.

Fonctionnalité de confort : elle **réutilise** la machinerie de razzia (`movement.send`,
butin/combat inchangés) et n'introduit aucun nouveau chiffre de jeu. Comportement
fidèle : « envoyer la liste » lance un raid par cible avec son modèle de troupes ; une
cible aux troupes insuffisantes est **sautée** (pas d'échec global).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "farmlist.db"

from app.engine import village as V
from app.engine import farmlist as FARM
from app.data.tribes import Tribe


def _setup():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "farmlist.db"
    store.init_db()
    store.insert_tiles([{"x": 3, "y": 0, "kind": "oasis",
                         "layout": "wood25", "animals": [2] + [0] * 9}])
    me = store.create_player("Moi", Tribe.GAULS)
    foe = store.create_player("Ennemi", Tribe.GAULS)
    origin = V.new_village("Base", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=me)
    origin.troops[0] = 50            # 50 phalanges disponibles
    origin = store.insert_village(origin)
    f1 = V.new_village("Ferme1", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=foe)
    f1 = store.insert_village(f1)
    f2 = V.new_village("Ferme2", Tribe.GAULS, server_speed=100, x=2, y=0, player_id=foe)
    f2 = store.insert_village(f2)
    return me, origin, f1, f2


def test_add_validations_and_raid_all():
    me, origin, f1, f2 = _setup()
    now = time.time()

    # On ne razzie pas ses propres villages.
    try:
        FARM.add_target(origin.id, me, [10] + [0] * 9, target_id=origin.id)
        assert False, "razzier son propre village aurait dû échouer"
    except FARM.FarmError as e:
        print("refus attendu (propre village) :", e)

    # Trois cibles : 2 villages + 1 oasis.
    FARM.add_target(origin.id, me, [10] + [0] * 9, target_id=f1.id)
    FARM.add_target(origin.id, me, [10] + [0] * 9, target_id=f2.id)
    FARM.add_target(origin.id, me, [5] + [0] * 9, target_x=3, target_y=0)
    targets = FARM.list_targets(origin.id, me)
    assert len(targets) == 3 and all(t["can_raid"] for t in targets)

    # Razzia groupée : 3 raids partent (25 phalanges au total ≤ 50 dispo).
    res = FARM.raid_all(origin.id, me, now)
    assert len(res["sent"]) == 3 and not res["skipped"], res
    o = store.load_village(origin.id)
    assert o.troops[0] == 25 and sum(o.away) == 25, (o.troops[0], o.away)
    print(f"✅ razzia groupée : 3 cibles envoyées, {o.troops[0]} phalanges restantes")


def test_insufficient_troops_skipped():
    me, origin, f1, f2 = _setup()
    now = time.time()
    # Deux cibles à 40 phalanges chacune : la 1ʳᵉ part (40≤50), la 2ᵉ est sautée (40>10).
    FARM.add_target(origin.id, me, [40] + [0] * 9, target_id=f1.id)
    FARM.add_target(origin.id, me, [40] + [0] * 9, target_id=f2.id)
    res = FARM.raid_all(origin.id, me, now)
    assert len(res["sent"]) == 1 and len(res["skipped"]) == 1, res
    assert "troupe" in res["skipped"][0]["reason"].lower()
    print(f"✅ troupes insuffisantes : 1 envoyée, 1 sautée ({res['skipped'][0]['reason']})")


def test_remove_target():
    me, origin, f1, f2 = _setup()
    info = FARM.add_target(origin.id, me, [10] + [0] * 9, target_id=f1.id)
    assert len(FARM.list_targets(origin.id, me)) == 1
    FARM.remove_target(info["id"], origin.id, me)
    assert FARM.list_targets(origin.id, me) == []
    print("✅ retrait d'une cible de la liste de fermes")


def main():
    test_add_validations_and_raid_all()
    test_insufficient_troops_skipped()
    test_remove_target()
    print("\n✅ Liste de fermes validée")


if __name__ == "__main__":
    main()
