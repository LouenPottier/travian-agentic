"""Mise en pause de la famine et des routes commerciales pendant les arrêts serveur.

Verrouille le comportement voulu (cf. CLAUDE.md / engine.downtime) : quand le serveur
est resté éteint/en veille (grand trou de **temps mural** depuis le dernier battement
`meta.last_alive`), au redémarrage :
  - la **famine** ne s'applique pas sur le laps d'arrêt (les troupes d'un village au
    blé net négatif survivent, alors qu'un rattrapage normal les tuerait) ;
  - les **routes commerciales** échues **glissent** au prochain créneau futur, **sans**
    envoyer de cargaison de rattrapage ;
  - **production / construction continuent** (les bâtiments en file se terminent).
Et à l'inverse, un petit trou (serveur actif) ne fige rien : famine et routes normales.

⚠️ La détection est en **temps mural réel** (pas le `now` de jeu) : on simule donc un
arrêt en posant `last_alive` (et `updated_at`) dans le passé réel, puis en appelant
`absorb()` « maintenant ».
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "downtime.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import downtime as DT
from app.data.buildings import B
from app.data.tribes import Tribe

DOWN = 3600.0   # le serveur était vivant il y a 1 h (temps mural), puis éteint


def _starving_village(pid, x, y, alive_at):
    """Village au blé net très négatif (grosse armée, grenier vide), figé à `alive_at`."""
    v = V.new_village("Armée", Tribe.GAULS, server_speed=100, x=x, y=y, player_id=pid)
    v.troops = [1000, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # ~1000 blé/h d'entretien
    v.resources = [100.0, 100.0, 100.0, 0.0]        # grenier de blé vide
    v.updated_at = alive_at
    return v


def test_famine_paused_when_server_was_down():
    store.init_db()
    pid = store.create_player("Joueur", Tribe.GAULS)
    alive = time.time() - DOWN

    # Village témoin : rattrapage NORMAL (serveur actif) ⇒ famine, l'armée fond.
    ctrl = store.insert_village(_starving_village(pid, 0, 0, alive))
    ctrl = store.load_village(ctrl.id)
    V.tick(ctrl, time.time())
    store.save_village(ctrl)
    assert sum(ctrl.troops) < 1000, "témoin : la famine aurait dû tuer des troupes"

    # Village en arrêt : dernier battement il y a 1 h, puis serveur éteint jusqu'à maintenant.
    downv = store.insert_village(_starving_village(pid, 5, 5, alive))
    store.set_meta("last_alive", alive)
    gap = DT.absorb()
    assert gap > DT.GRACE, gap
    downv = store.load_village(downv.id)
    assert sum(downv.troops) == 1000, ("l'armée doit survivre (famine en pause)", downv.troops)
    assert downv.resources[V.CROP] >= 0.0
    assert downv.updated_at >= time.time() - 5   # bien rattrapé jusqu'à maintenant
    print("✅ famine en pause : témoin", sum(ctrl.troops), "vs arrêt", sum(downv.troops))


def test_npc_rival_army_also_survives():
    """Les **rivaux** (compte `is_npc=True` mais tribu jouable) sont affamables :
    leur armée doit aussi être épargnée sur l'arrêt (cf. engine.rivals / downtime)."""
    store.init_db()
    pid = store.create_player("Auguste", Tribe.ROMANS, is_npc=True)   # rival PNJ jouable
    alive = time.time() - DOWN
    v = V.new_village("Empire", Tribe.ROMANS, server_speed=100, x=30, y=30, player_id=pid)
    v.troops = [2000, 0, 0, 0, 0, 0, 0, 0, 0, 0]     # grosse armée en déficit de blé
    v.resources = [100.0, 100.0, 100.0, 0.0]
    v.updated_at = alive
    v = store.insert_village(v)
    store.set_meta("last_alive", alive)

    DT.absorb()

    v = store.load_village(v.id)
    assert sum(v.troops) == 2000, ("l'armée rivale PNJ doit survivre", v.troops)
    print("✅ armée rivale PNJ épargnée pendant l'arrêt")


def test_small_gap_does_not_freeze():
    """Serveur actif (petit trou mural) : rien n'est figé, la famine s'applique."""
    store.init_db()
    pid = store.create_player("J2", Tribe.GAULS)
    v = store.insert_village(_starving_village(pid, 1, 1, time.time()))
    store.set_meta("last_alive", time.time() - 30)   # 30 s < GRACE ⇒ pas d'arrêt
    assert DT.absorb() == 0.0
    # Le village n'a pas été « secouru » : un tick normal le fait bien mourir de faim.
    v = store.load_village(v.id)
    V.tick(v, time.time() + 3600)
    store.save_village(v)
    assert sum(v.troops) < 1000
    print("✅ petit trou : pas de gel, famine normale")


def test_trade_route_rolled_not_fired():
    store.init_db()
    pid = store.create_player("J3", Tribe.GAULS)
    now = time.time()
    o = V.new_village("Origine", Tribe.GAULS, server_speed=100, x=10, y=10, player_id=pid)
    o.slots[20] = V.Slot(building_id=B.MARKETPLACE, level=5)
    o.slots[21] = V.Slot(building_id=B.WAREHOUSE, level=10)
    o.slots[22] = V.Slot(building_id=B.GRANARY, level=10)
    o.resources = [5000.0] * 4
    o.updated_at = now - DOWN
    o = store.insert_village(o)
    store.insert_village(V.new_village("Cible", Tribe.GAULS, server_speed=100,
                                       x=13, y=13, player_id=pid))
    # Route échue (next_run dans le passé) : elle doit GLISSER au futur, pas tirer.
    store.insert_trade_route(o.id, o.id, pid, [500, 0, 0, 0],
                             interval_hours=1.0, next_run=now - 100)
    store.set_meta("last_alive", now - DOWN)

    DT.absorb()                                # grand trou mural ⇒ route glissée

    r = store.trade_routes_for(o.id)[0]
    assert r["next_run"] > time.time(), ("next_run doit être dans le futur", r["next_run"])
    assert not any(m["kind"] == "trade" for m in store.movements_for(o.id)), \
        "aucune cargaison de rattrapage ne doit partir"
    print("✅ route glissée au prochain créneau, sans rafale de rattrapage")


def test_buildings_finish_during_downtime():
    """La construction n'est PAS en pause : pour un village **affamé** (donc rejoué par
    `_freeze`), un ordre en file se termine sur le trou pendant que l'armée survit."""
    store.init_db()
    pid = store.create_player("J4", Tribe.GAULS)
    alive = time.time() - DOWN
    v = V.new_village("Chantier", Tribe.GAULS, server_speed=100, x=20, y=20, player_id=pid)
    v.troops = [800, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # armée ⇒ blé net < 0 ⇒ dans le set famine
    v.resources = [5000.0, 5000.0, 5000.0, 0.0]
    v.updated_at = alive
    # Un chantier en cours qui se termine pendant l'arrêt (finish_at dans le trou).
    v.queue = [V.BuildOrder(slot_index=1, target_level=1, finish_at=alive + 600)]
    v = store.insert_village(v)
    store.set_meta("last_alive", alive)

    DT.absorb()

    v = store.load_village(v.id)
    assert v.slots[1].level == 1, "le champ doit être monté au niveau 1 malgré l'arrêt"
    assert not v.queue
    assert sum(v.troops) == 800, "l'armée doit survivre (famine en pause)"
    print("✅ construction avance + armée survit pendant l'arrêt")


def test_freeze_skips_safe_villages():
    """Le filtre de coût : un village sans risque de famine (blé net ≥ 0) n'est PAS
    rejoué par `_freeze` (son updated_at reste au passé, tick paresseux plus tard)."""
    store.init_db()
    pid = store.create_player("J5", Tribe.GAULS)
    alive = time.time() - DOWN
    v = V.new_village("Paisible", Tribe.GAULS, server_speed=100, x=25, y=25, player_id=pid)
    v.resources = [500.0] * 4                      # pas de troupe ⇒ blé net > 0
    v.updated_at = alive
    v = store.insert_village(v)
    store.set_meta("last_alive", alive)

    DT.absorb()

    v = store.load_village(v.id)
    assert v.updated_at == alive, "village sûr : non rejoué par _freeze (updated_at inchangé)"
    print("✅ _freeze saute les villages sans risque de famine (coût borné)")


def main():
    test_famine_paused_when_server_was_down()
    test_npc_rival_army_also_survives()
    test_small_gap_does_not_freeze()
    test_trade_route_rolled_not_fired()
    test_buildings_finish_during_downtime()
    test_freeze_skips_safe_villages()
    print("✅ test_downtime OK")


if __name__ == "__main__":
    main()
