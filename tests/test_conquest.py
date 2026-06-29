"""Verrouille la conquête de village (loyauté + administrateurs), cf. CLAUDE.md.

Mécaniques (sourcées dans engine/conquest.py — kirilloid muet) :
- un administrateur (sénateur/chef) survivant réduit la loyauté sur **attaque normale** ;
- conquête seulement si la cible n'a **plus de bâtiment d'administration**, n'est **pas
  une capitale**, **ni l'unique village** du défenseur, et l'attaquant a culture + slot ;
- à 0 % : changement de propriétaire, troupes du village perdues, survivants en garnison,
  recherche/forge réinitialisées, mur supprimé, tribu adoptée par le conquérant ;
- la loyauté régénère (+⅔ × niveau résidence/palais / h).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "conquest.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import conquest as CQ
from app.data.buildings import B
from app.data.tribes import Tribe


def _setup():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "conquest.db"
    store.init_db()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    def_pid = store.create_player("Defenseur", Tribe.GAULS)
    now = time.time()

    # Attaquant : palais niv 10 ⇒ 1 emplacement d'expansion ; culture suffisante.
    att = V.new_village("Capitale Att", Tribe.ROMANS, server_speed=100, x=0, y=0,
                        player_id=att_pid)
    att.slots[20] = V.Slot(building_id=B.PALACE, level=10)
    att.troops = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]   # 100 légionnaires + 5 sénateurs (idx 8)
    att = store.insert_village(att)
    store.set_culture(att_pid, 300.0, now)          # ≥ 200 requis pour le 2ᵉ village

    return att_pid, def_pid, att, now


def _make_target(def_pid, x, *, is_capital, residence=0, palisade=0):
    d = V.new_village(f"Cible{x}", Tribe.GAULS, server_speed=100, x=x, y=0,
                      player_id=def_pid, is_capital=is_capital)
    d.troops[0] = 10                                # défense faible (10 phalanges)
    if residence:
        d.slots[21] = V.Slot(building_id=B.RESIDENCE, level=residence)
    if palisade:
        d.slots[40] = V.Slot(building_id=B.PALISADE, level=palisade)
    return store.insert_village(d)


def test_conquest_changes_owner():
    att_pid, def_pid, att, now = _setup()
    # Le défenseur possède DEUX villages (sa capitale + la cible) : la cible n'est
    # donc pas son unique village. La cible n'est pas capitale et n'a pas de résidence.
    _make_target(def_pid, 5, is_capital=True)                  # sa capitale (protégée)
    target = _make_target(def_pid, 1, is_capital=False, palisade=8)

    assert target.loyalty == 100.0
    units = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, target.id, att_pid, "attack", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(target.id)
    assert t.player_id == att_pid, ("le village n'a pas changé de propriétaire", t.player_id)
    assert t.tribe == Tribe.ROMANS, "le village conquis adopte la tribu du conquérant"
    assert not t.is_capital, "un village conquis n'est jamais capitale"
    assert t.troops[0] > 0, "les survivants (légionnaires) doivent garnisonner"
    assert t.troops[8] == 0, "les sénateurs disparaissent après conquête"
    assert sum(t.troops[i] for i in range(10) if i != 0) == 0, \
        "aucune troupe de l'ancien propriétaire ne subsiste"
    assert B.PALISADE not in {s.building_id for s in t.slots.values()}, \
        "le mur doit disparaître à la conquête"
    assert t.loyalty == CQ.RESET_LOYALTY

    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["conquete"] is True, rep["body"]
    print(f"✅ conquête : village {target.id} → joueur {att_pid}, "
          f"garnison {t.troops[0]} légionnaires, loyauté {t.loyalty}")


def test_residence_blocks_conquest():
    """Avec une résidence debout, l'administrateur ne réduit pas la loyauté."""
    att_pid, def_pid, att, now = _setup()
    _make_target(def_pid, 5, is_capital=True)
    target = _make_target(def_pid, 1, is_capital=False, residence=5)

    units = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, target.id, att_pid, "attack", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(target.id)
    assert t.player_id == def_pid, "résidence debout ⇒ pas de conquête"
    assert t.loyalty == 100.0, ("la loyauté ne doit pas baisser", t.loyalty)
    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["conquete"] is False
    assert rep["body"]["loyaute"]["bloque"], rep["body"]["loyaute"]
    print("✅ résidence debout : conquête bloquée, loyauté intacte")


def test_capital_immune():
    """Une capitale ne peut pas être conquise, même sans résidence."""
    att_pid, def_pid, att, now = _setup()
    _make_target(def_pid, 5, is_capital=False)           # autre village (non capitale)
    target = _make_target(def_pid, 1, is_capital=True)   # capitale ciblée

    units = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, target.id, att_pid, "attack", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(target.id)
    assert t.player_id == def_pid, "une capitale est imprenable"
    assert t.loyalty == 100.0
    print("✅ capitale imprenable")


def test_raid_does_not_reduce_loyalty():
    """Une razzia (même avec administrateurs) ne touche pas la loyauté."""
    att_pid, def_pid, att, now = _setup()
    _make_target(def_pid, 5, is_capital=True)
    target = _make_target(def_pid, 1, is_capital=False)

    units = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, target.id, att_pid, "raid", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(target.id)
    assert t.player_id == def_pid and t.loyalty == 100.0, "la razzia ne conquiert pas"
    print("✅ razzia : loyauté intacte")


def test_conquest_kills_homed_hero_and_purges_state():
    """À la conquête : le héros du défenseur rattaché à la cible **meurt** et se
    re-rattache à un village survivant (fidélité, cf. conquest) ; routes commerciales,
    liste de fermes et célébration du village conquis sont purgées (pas d'état zombie)."""
    from app.engine import hero as H
    att_pid, def_pid, att, now = _setup()
    cap = _make_target(def_pid, 5, is_capital=True)            # capitale survivante
    target = _make_target(def_pid, 1, is_capital=False, palisade=8)

    # Héros du défenseur rattaché au village qui va tomber.
    h = H.get_or_create(def_pid, target.id, now)
    h.home_village_id = target.id
    h.status = "home"
    H.save(h)

    # État rattaché au village conquis : route commerciale, ferme, célébration en cours.
    store.insert_trade_route(target.id, cap.id, def_pid, [10, 10, 10, 10], 1, now + 1e9)
    store.insert_farm_target(target.id, def_pid, cap.id, cap.x, cap.y, [1] * 10, "x")
    t0 = store.load_village(target.id)
    t0.celebration = {"type": 0, "ends_at": now + 1e9, "cp": 500}
    store.save_village(t0)

    units = [100, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, target.id, att_pid, "attack", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(target.id)
    assert t.player_id == att_pid, "le village doit être conquis pour ce test"
    assert t.celebration is None, "la fête de l'ancien proprio ne se poursuit pas"
    assert store.trade_routes_for(target.id) == [], "routes commerciales purgées"
    assert store.farm_targets_for(target.id) == [], "liste de fermes purgée"

    hh = H.load(def_pid)
    assert hh.status == "dead", ("le héros meurt avec son village d'attache", hh.status)
    assert hh.health == 0.0
    assert hh.home_village_id == cap.id, \
        ("le héros se re-rattache à un village survivant", hh.home_village_id)

    rep = next(r for r in store.reports_for(def_pid)
               if r["body"].get("type") == "defensive")
    assert rep["body"]["hero_def_perdu"] is True, rep["body"]
    print("✅ conquête : héros tué + re-rattaché à la capitale, routes/fermes/fête purgées")


def test_loyalty_regen():
    """La loyauté remonte avec le temps : +⅔ × niveau résidence/palais par heure."""
    v = V.new_village("Regen", Tribe.ROMANS, server_speed=1)
    v.slots[20] = V.Slot(building_id=B.RESIDENCE, level=6)   # +4/h
    v.loyalty = 50.0
    now = time.time()
    v.updated_at = now
    V.tick(v, now + 3600)                                    # 1 h
    assert abs(v.loyalty - 54.0) < 0.01, ("régén attendue +4/h", v.loyalty)
    # Plafonnée à 100.
    V.tick(v, now + 3600 * 100)
    assert v.loyalty == 100.0
    print("✅ régén loyauté : +4/h (résidence niv 6), plafonnée à 100")
