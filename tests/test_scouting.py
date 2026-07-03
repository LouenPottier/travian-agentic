"""Verrouille l'espionnage (reconnaissance) — cf. CLAUDE.md / engine/scouting.py.

Comportement recoupé sur support.travian.com « Troop Actions: Scouting », wiki Fandom
« Scouts » et TravianZ GameEngine/Battle.php :
- On espionne en n'envoyant **que des éclaireurs**, vers un **village** ; deux modes
  (ressources / défenses).
- Défenseur **sans** éclaireur ⇒ **non détecté** : aucune perte, info complète, et il
  n'est **pas** prévenu (aucun rapport défensif).
- Puissance de reconnaissance défensive **≥** offensive ⇒ éclaireurs attaquants
  **anéantis**, aucune info renvoyée, défenseur **notifié**.
- Cachette : capacité de protection **doublée** pour les Gaulois.
"""
import tempfile
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "scout.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import scouting as SC
from app.data.buildings import B
from app.data.tribes import Tribe

T0 = 1_000_000.0
TEUTON_SCOUT = 3   # index d'éclaireur teuton
ROMAN_SCOUT = 3    # index d'éclaireur romain (Equites Legati)


def setup():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "scout.db"
    store.init_db()
    store.insert_tiles([
        {"x": 0, "y": 0, "kind": "valley", "layout": "4-4-4-6", "animals": None},
        {"x": 3, "y": 0, "kind": "valley", "layout": "4-4-4-6", "animals": None},
        {"x": 5, "y": 0, "kind": "oasis", "layout": "wood25", "animals": [0] * 10},
    ])
    att_pid = store.create_player("Espion", Tribe.TEUTONS)
    def_pid = store.create_player("Cible", Tribe.ROMANS)
    att = V.new_village("Base", Tribe.TEUTONS, server_speed=100, x=0, y=0, player_id=att_pid)
    att.troops[TEUTON_SCOUT] = 20
    att = store.insert_village(att)
    tgt = V.new_village("Proie", Tribe.ROMANS, server_speed=100, x=3, y=0, player_id=def_pid)
    tgt.resources = [4000.0, 3000.0, 2000.0, 1000.0]
    tgt.troops[0] = 50  # légionnaires (non-éclaireurs, invisibles en défense d'espionnage)
    tgt = store.insert_village(tgt)
    return att_pid, att.id, def_pid, tgt.id


def _reports(pid):
    return store.reports_for(pid)


def test_scout_undefended_returns_full_intel_and_no_defender_report():
    att_pid, att_id, def_pid, tgt_id = setup()
    M.send(att_id, tgt_id, att_pid, "scout",
           [0, 0, 0, 5, 0, 0, 0, 0, 0, 0], now=T0, scout_mode="res")
    M.process_due(T0 + 100000)

    off = _reports(att_pid)
    assert off and off[0]["body"]["type"] == "scout_off"
    body = off[0]["body"]
    assert body["detecte"] is False, body
    assert body["survivants"] == 5, body           # aucune perte : non détecté
    assert body["info"]["resources"] == [4000, 3000, 2000, 1000], body["info"]
    # Les troupes présentes sont révélées (50 légionnaires).
    names = {t["name"]: t["count"] for t in body["info"]["troops"]}
    assert names.get("Légionnaire") == 50, names
    # Défenseur non prévenu (aucun éclaireur ⇒ non détecté).
    assert _reports(def_pid) == [], _reports(def_pid)
    # Les éclaireurs rentrent (mouvement retour créé, garnison restaurée à terme).
    M.process_due(T0 + 500000)
    att = store.load_village(att_id)
    assert att.troops[TEUTON_SCOUT] == 20, att.troops
    print("✅ espionnage non défendu : info complète, aucune perte, défenseur non prévenu")


def test_scout_repelled_by_stronger_defense():
    att_pid, att_id, def_pid, tgt_id = setup()
    tgt = store.load_village(tgt_id)
    tgt.troops[ROMAN_SCOUT] = 100  # forte garnison d'éclaireurs défenseurs
    store.save_village(tgt)

    M.send(att_id, tgt_id, att_pid, "scout",
           [0, 0, 0, 3, 0, 0, 0, 0, 0, 0], now=T0, scout_mode="res")
    M.process_due(T0 + 100000)

    off = _reports(att_pid)[0]["body"]
    assert off["detecte"] is True, off
    assert off["survivants"] == 0, off             # anéantis
    assert off["info"] is None, off                # aucune info
    deff = _reports(def_pid)
    assert deff and deff[0]["body"]["type"] == "scout_def", deff
    assert deff[0]["body"]["vu"] is False, deff[0]["body"]  # repoussé : rien vu
    print("✅ espionnage repoussé : éclaireurs anéantis, aucune info, défenseur notifié")


def test_scout_defense_mode_reveals_buildings():
    att_pid, att_id, def_pid, tgt_id = setup()
    tgt = store.load_village(tgt_id)
    tgt.slots[40] = V.Slot(building_id=B.CITY_WALL, level=8)
    tgt.slots[24] = V.Slot(building_id=B.RESIDENCE, level=10)
    store.save_village(tgt)

    M.send(att_id, tgt_id, att_pid, "scout",
           [0, 0, 0, 4, 0, 0, 0, 0, 0, 0], now=T0, scout_mode="def")
    M.process_due(T0 + 100000)
    info = _reports(att_pid)[0]["body"]["info"]
    assert info["mode"] == "def", info
    assert info["defenses"]["wall"]["level"] == 8, info["defenses"]
    assert info["defenses"]["residence"] == 10, info["defenses"]
    assert "resources" not in info, info           # mode défenses : pas de ressources
    print("✅ mode défenses : muraille + résidence révélées, ressources masquées")


def test_send_rejects_non_scout_and_oasis():
    att_pid, att_id, def_pid, tgt_id = setup()
    # Une unité non-éclaireur embarquée ⇒ refus.
    try:
        M.send(att_id, tgt_id, att_pid, "scout",
               [10, 0, 0, 5, 0, 0, 0, 0, 0, 0], now=T0, scout_mode="res")
        assert False, "aurait dû refuser une unité non-éclaireur"
    except M.MoveError:
        pass
    # Cible oasis ⇒ refus (l'espionnage vise un village).
    try:
        M.send(att_id, None, att_pid, "scout",
               [0, 0, 0, 5, 0, 0, 0, 0, 0, 0], now=T0,
               target_x=5, target_y=0, scout_mode="res")
        assert False, "aurait dû refuser une cible oasis"
    except M.MoveError:
        pass
    print("✅ envoi : refuse les unités non-éclaireur et les cibles oasis")


def test_cranny_protection_gaul_double():
    v = V.new_village("G", Tribe.GAULS, x=0, y=0)
    v.slots[35] = V.Slot(building_id=B.CRANNY, level=5)
    r = V.new_village("R", Tribe.ROMANS, x=0, y=0)
    r.slots[35] = V.Slot(building_id=B.CRANNY, level=5)
    from app.data import formulas as F
    base = F.cranny(5)
    assert V.cranny_protection(r) == base, V.cranny_protection(r)
    assert V.cranny_protection(v) == 2 * base, V.cranny_protection(v)
    print("✅ cachette : capacité doublée pour les Gaulois")


if __name__ == "__main__":
    test_scout_undefended_returns_full_intel_and_no_defender_report()
    test_scout_repelled_by_stronger_defense()
    test_scout_defense_mode_reveals_buildings()
    test_send_rejects_non_scout_and_oasis()
    test_cranny_protection_gaul_double()
    print("\n✅ tous les tests d'espionnage passent")
