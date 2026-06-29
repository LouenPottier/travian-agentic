"""Verrouille le socle Natars (villages Natars + tribu PNJ), cf. CLAUDE.md item #4.

Mécaniques (sourcées dans engine/natars.py + data/units.py — kirilloid muet sur les
villages/garnisons ; stats d'unités recoupées sur le wiki Fandom) :
- la tribu `NATARS` a 10 unités **non entraînables** (producer == -1), comme la Nature ;
- les villages Natars apparaissent sur des **vallées libres** de la zone centrale, avec
  une **garnison d'autant plus forte qu'on est proche du centre** ;
- ils sont **attaquables/pillables** (combat + butin normaux) mais **NON conquérables**
  (garde-fou `conquest.conquer_eligible` sur les tribus PNJ).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "natars.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import conquest as CQ
from app.engine import natars as NAT
from app.engine import world as W
from app.data.buildings import B
from app.data.tribes import Tribe, PLAYABLE_TRIBES, NPC_TRIBES
from app.data.units import UNITS


def test_natar_units_table():
    """10 unités Natars, non entraînables, avec les rôles spéciaux attendus."""
    natars = UNITS[Tribe.NATARS]
    assert len(natars) == 10, "troops[10] : il faut exactement 10 unités Natars"
    assert all(u.producer == -1 for u in natars), "unités Natars non entraînables"
    assert Tribe.NATARS not in PLAYABLE_TRIBES and Tribe.NATARS in NPC_TRIBES
    assert natars[3].is_scout, "Rapace = éclaireur"
    assert natars[7].is_catapult, "Baliste = engin de siège (bâtiments)"
    assert natars[8].is_chief, "Empereur natarien = administrateur"
    assert natars[9].is_settler, "Colon natarien = colon"
    print(f"✅ tribu Natars : {len(natars)} unités, non entraînables")


def test_garrison_scales_toward_center():
    """La garnison près du centre est plus forte qu'en périphérie de la zone."""
    near = sum(NAT.garrison_for(NAT.NATAR_ZONE_INNER, 0))
    far = sum(NAT.garrison_for(NAT.NATAR_ZONE_OUTER, 0))
    assert near > far > 0, (near, far)
    print(f"✅ garnison : centre {near} > périphérie {far}")


def _seed_world(radius=35):
    store.DB_PATH = Path(tempfile.mkdtemp()) / "natars.db"
    store.init_db()
    store.insert_tiles(W.generate_world(radius))


def test_spawn_creates_natar_villages_on_valleys():
    _seed_world()
    pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    created = NAT.spawn_natar_villages(pid, server_speed=100, n=8)
    assert len(created) > 0, "au moins un village Natar doit être créé"
    for v in created:
        assert v.tribe == Tribe.NATARS
        assert sum(v.troops) > 0, "un village Natar a une garnison"
        tile = store.get_tile(v.x, v.y)
        assert tile is not None and tile["kind"] == "valley", "fondé sur une vallée"
    # Tous distincts et persistés sous le bon joueur.
    rows = [r for r in store.list_villages() if r["player_id"] == pid]
    assert len(rows) == len(created)
    print(f"✅ spawn : {len(created)} villages Natars sur vallées libres")


def test_natar_garrison_does_not_starve():
    """La garnison Natar (PNJ statique) ne fond pas avec le temps, même grosse et
    même avec la vitesse serveur (sinon elle s'éroderait dès qu'on touche le village)."""
    _seed_world()
    pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    v = NAT._natar_village("Natar fort", NAT.NATAR_ZONE_INNER, 0, pid, 100, "4-4-4-6")
    before = list(v.troops)
    assert sum(before) > 100, "garnison centrale conséquente"
    V.tick(v, v.updated_at + 365 * 24 * 3600)  # un an de jeu (×100 vitesse)
    assert v.troops == before, ("la garnison Natar ne doit pas être affamée", v.troops)
    print(f"✅ garnison Natar stable après 1 an : {sum(v.troops)} unités")


def test_natar_village_attackable_but_not_conquerable():
    """Une attaque victorieuse pille un village Natar mais ne le conquiert jamais,
    même avec des administrateurs survivants et l'attaquant éligible par ailleurs."""
    _seed_world()
    now = time.time()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)

    # Attaquant : palais niv 10 (slot d'expansion) + culture suffisante + sénateurs.
    att = V.new_village("Capitale Att", Tribe.ROMANS, server_speed=100, x=0, y=0,
                        player_id=att_pid)
    att.slots[20] = V.Slot(building_id=B.PALACE, level=10)
    att.troops = [200, 0, 0, 0, 0, 0, 0, 0, 5, 0]      # 200 légionnaires + 5 sénateurs
    att = store.insert_village(att)
    store.set_culture(att_pid, 500.0, now)

    # Village Natar faiblement gardé pour que l'attaquant gagne (et puisse piller).
    nat = NAT._natar_village("Village natar test", 4, 0, nat_pid, 100, "4-4-4-6")
    nat.troops = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0]        # 5 piquiers seulement
    nat = store.insert_village(nat)

    # L'attaquant est éligible « par ailleurs » : c'est bien le garde-fou PNJ qui bloque.
    ok, reason = CQ.conquer_eligible(nat, att_pid, now)
    assert ok is False and "PNJ" in reason, (ok, reason)

    units = [200, 0, 0, 0, 0, 0, 0, 0, 5, 0]
    info = M.send(att.id, nat.id, att_pid, "attack", units, now)
    M.process_due(now + info["arrive_in"] + 1)

    t = store.load_village(nat.id)
    assert t.player_id == nat_pid, "un village Natar ne change jamais de propriétaire"
    assert t.tribe == Tribe.NATARS
    assert t.loyalty == 100.0, ("loyauté intacte (non conquérable)", t.loyalty)

    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["conquete"] is False, rep["body"]
    assert sum(rep["body"].get("butin", [0, 0, 0, 0])) > 0, "le pillage doit rapporter du butin"
    print(f"✅ village Natar : pillé (butin {rep['body']['butin']}), jamais conquis")


if __name__ == "__main__":
    test_natar_units_table()
    test_garrison_scales_toward_center()
    test_spawn_creates_natar_villages_on_valleys()
    test_natar_garrison_does_not_starve()
    test_natar_village_attackable_but_not_conquerable()
    print("Tous les tests Natars OK.")
