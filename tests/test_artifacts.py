"""Verrouille les artefacts (endgame Natars), cf. CLAUDE.md item #4.

Mécaniques (sourcées dans data/artifacts.py + engine/artifacts.py — kirilloid muet) :
- catalogue de 8 types × 3 tailles ; seuls durabilité/vitesse/céréales sont branchés ;
- détention par des villages Natars dédiés, **capture** par le héros sur une **attaque**
  victorieuse avec une **trésorerie vide** suffisante (niv 10 petit / 20 grand·unique) ;
- échec si razzia / pas de héros / trésorerie absente (l'artefact reste au Natar) ;
- **effets** : petit artefact → son village seul ; grand/unique → tout le compte
  (durabilité ×3/4/5, vitesse ×1,5/2, céréales ×0,5) ;
- un village conquis **détache** ses artefacts (inactifs).
"""
import tempfile
import time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "artifacts.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import artifacts as ART
from app.engine import natars as NAT
from app.engine import hero as HERO
from app.data import artifacts as AT
from app.data.buildings import B
from app.data.tribes import Tribe
from app.data.units import UNITS


def _fresh():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "artifacts.db"
    store.init_db()


# --- Catalogue ---------------------------------------------------------------
def test_catalogue():
    assert len(AT.TYPES) == 8, "8 types d'artefacts"
    assert set(AT.SIZES) == {"small", "large", "unique"}
    # Effets branchés attendus.
    wired = {t.effect for t in AT.TYPES.values() if t.wired}
    assert wired == {"durability", "speed", "crop"}, wired
    assert AT.magnitude(1, "unique") == 5.0 and AT.magnitude(1, "small") == 3.0
    assert AT.scope("small") == "village" and AT.scope("large") == "account"
    print(f"✅ catalogue : {len(AT.TYPES)} types, branchés = {sorted(wired)}")


# --- Effets : portée village vs compte --------------------------------------
def _owned_village(pid, x=50, y=50, name="V", capital=True):
    v = V.new_village(name, Tribe.GAULS, server_speed=100, x=x, y=y,
                      player_id=pid, is_capital=capital)
    return store.insert_village(v)


def test_small_artifact_scope_is_its_village():
    _fresh()
    pid = store.create_player("Joueur", Tribe.GAULS)
    a = _owned_village(pid, 50, 50, "A")
    b = _owned_village(pid, 52, 50, "B", capital=False)
    # Petit artefact de durabilité stocké dans A.
    aid = store.insert_artifact(1, "small", natar_village_id=-1)
    store.capture_artifact(aid, pid, a.id)
    assert ART.durability_multiplier(store.load_village(a.id)) == 3.0
    assert ART.durability_multiplier(store.load_village(b.id)) == 1.0, "petit = village seul"
    print("✅ petit artefact : actif dans son village, pas ailleurs")


def test_large_artifact_scope_is_account():
    _fresh()
    pid = store.create_player("Joueur", Tribe.GAULS)
    a = _owned_village(pid, 50, 50, "A")
    b = _owned_village(pid, 52, 50, "B", capital=False)
    aid = store.insert_artifact(2, "large", natar_village_id=-1)
    store.capture_artifact(aid, pid, a.id)  # stocké dans A mais effet de compte
    assert ART.speed_multiplier(store.load_village(a.id)) == 2.0
    assert ART.speed_multiplier(store.load_village(b.id)) == 2.0, "grand = tout le compte"
    print("✅ grand artefact : actif sur tout le compte")


def test_crop_artifact_halves_upkeep():
    _fresh()
    pid = store.create_player("Joueur", Tribe.GAULS)
    a = _owned_village(pid, 50, 50, "A")
    a.troops = [100, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # 100 phalanges, upkeep 1 → 100/h
    store.save_village(a)
    base = V.troop_upkeep(store.load_village(a.id))
    assert base == 100, base
    aid = store.insert_artifact(3, "small", natar_village_id=-1)
    store.capture_artifact(aid, pid, a.id)
    assert V.troop_upkeep(store.load_village(a.id)) == 50, "diète ×0,5"
    print(f"✅ diète : consommation {base} → 50")


# --- Spawn -------------------------------------------------------------------
def test_spawn_creates_artifact_villages():
    _fresh()
    from app.engine import world as W
    store.insert_tiles(W.generate_world(35))
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    created = ART.spawn_artifact_villages(nat_pid, server_speed=100)
    assert len(created) > 0
    for v in created:
        assert v.tribe == Tribe.NATARS and sum(v.troops) > 0
        assert V.building_levels(v).get(B.TREASURY, 0) >= ART.TREASURY_SMALL
        assert store.artifact_held_by_natar(v.id) is not None
    assert store.artifacts_exist()
    print(f"✅ spawn : {len(created)} villages-trésor Natars avec artefacts")


# --- Capture -----------------------------------------------------------------
def _attacker_with_hero(pid, treasury_level=None):
    att = V.new_village("Capitale Att", Tribe.ROMANS, server_speed=100,
                        x=50, y=50, player_id=pid)
    if treasury_level is not None:
        att.slots[22] = V.Slot(building_id=B.TREASURY, level=treasury_level)
    att.troops = [300, 0, 0, 0, 0, 0, 0, 0, 0, 0]   # 300 légionnaires
    att = store.insert_village(att)
    HERO.get_or_create(pid, att.id)
    return att


def _artifact_natar(nat_pid, kind=1, size="small", x=52, y=50):
    nat = NAT._natar_village("Trésor natar", x, y, nat_pid, 100, "4-4-4-6")
    nat.troops = [3, 0, 0, 0, 0, 0, 0, 0, 0, 0]      # garnison dérisoire ⇒ vaincue
    nat = store.insert_village(nat)
    store.insert_artifact(kind, size, nat.id)
    return nat


def test_hero_captures_artifact_with_treasury():
    _fresh()
    now = time.time()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    att = _attacker_with_hero(att_pid, treasury_level=ART.TREASURY_SMALL)
    nat = _artifact_natar(nat_pid, kind=1, size="small")

    info = M.send(att.id, nat.id, att_pid, "attack",
                  [300, 0, 0, 0, 0, 0, 0, 0, 0, 0], now, with_hero=True)
    M.process_due(now + info["arrive_in"] + 1)

    assert store.artifact_held_by_natar(nat.id) is None, "l'artefact a quitté le Natar"
    stored = store.artifact_in_village(att.id)
    assert stored is not None and stored["owner_id"] == att_pid, stored
    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["artefact"]["captured"] is True, rep["body"]["artefact"]
    print(f"✅ capture : {rep['body']['artefact']['artefact']} stocké à {att.name}")


def test_capture_fails_without_treasury():
    _fresh()
    now = time.time()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    att = _attacker_with_hero(att_pid, treasury_level=None)   # pas de trésorerie
    nat = _artifact_natar(nat_pid)

    info = M.send(att.id, nat.id, att_pid, "attack",
                  [300, 0, 0, 0, 0, 0, 0, 0, 0, 0], now, with_hero=True)
    M.process_due(now + info["arrive_in"] + 1)

    assert store.artifact_held_by_natar(nat.id) is not None, "l'artefact reste au Natar"
    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    art = rep["body"]["artefact"]
    assert art["captured"] is False and "trésorerie" in art["raison"], art
    print(f"✅ sans trésorerie : pas de capture ({art['raison']})")


def test_capture_fails_on_raid():
    _fresh()
    now = time.time()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    att = _attacker_with_hero(att_pid, treasury_level=ART.TREASURY_SMALL)
    nat = _artifact_natar(nat_pid)

    info = M.send(att.id, nat.id, att_pid, "raid",
                  [300, 0, 0, 0, 0, 0, 0, 0, 0, 0], now, with_hero=True)
    M.process_due(now + info["arrive_in"] + 1)

    assert store.artifact_held_by_natar(nat.id) is not None, "razzia ne capture pas"
    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    art = rep["body"]["artefact"]
    assert art["captured"] is False and "razzia" in art["raison"], art
    print("✅ razzia : pas de capture (attaque requise)")


def test_capture_fails_without_hero():
    _fresh()
    now = time.time()
    att_pid = store.create_player("Attaquant", Tribe.ROMANS)
    nat_pid = store.create_player("Natars", Tribe.NATARS, is_npc=True)
    att = _attacker_with_hero(att_pid, treasury_level=ART.TREASURY_SMALL)
    nat = _artifact_natar(nat_pid)

    info = M.send(att.id, nat.id, att_pid, "attack",
                  [300, 0, 0, 0, 0, 0, 0, 0, 0, 0], now, with_hero=False)
    M.process_due(now + info["arrive_in"] + 1)

    assert store.artifact_held_by_natar(nat.id) is not None, "sans héros : pas de capture"
    rep = next(r for r in store.reports_for(att_pid) if r["body"].get("type") == "offensive")
    art = rep["body"]["artefact"]
    assert art["captured"] is False and "héros" in art["raison"], art
    print("✅ sans héros : pas de capture (héros requis)")


def test_conquered_village_detaches_artifact():
    _fresh()
    from app.engine import conquest as CQ
    pid = store.create_player("Joueur", Tribe.GAULS)
    a = _owned_village(pid, 50, 50, "A")
    aid = store.insert_artifact(2, "large", natar_village_id=-1)
    store.capture_artifact(aid, pid, a.id)
    assert ART.speed_multiplier(store.load_village(a.id)) == 2.0
    # Conquête du village stockant l'artefact ⇒ détaché (inactif).
    CQ.conquer_village(store.load_village(a.id), attacker_player_id=999,
                       attacker_tribe=Tribe.TEUTONS, survivors=[0] * 10, now=time.time())
    detached = store.artifacts_owned_by(pid)[0]
    assert detached["village_id"] is None, detached
    print("✅ village conquis : artefact détaché (inactif)")


if __name__ == "__main__":
    for fn in [test_catalogue, test_small_artifact_scope_is_its_village,
               test_large_artifact_scope_is_account, test_crop_artifact_halves_upkeep,
               test_spawn_creates_artifact_villages, test_hero_captures_artifact_with_treasury,
               test_capture_fails_without_treasury, test_capture_fails_on_raid,
               test_capture_fails_without_hero, test_conquered_village_detaches_artifact]:
        fn()
    print("Tous les tests Artefacts OK.")
