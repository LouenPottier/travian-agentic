"""Verrouille le peuplement des comptes rivaux avancés (cf. engine.rivals, CLAUDE.md).

Ce sont des **PNJ de peuplement** (au même statut que les Natars / le joueur IA :
choix de dev, pas des chiffres de jeu). On vérifie l'essentiel :
- seeding **idempotent** (marqueur `SEED_MARKER`) ;
- les **légendes** ont une dizaine de villages, capitale **15-cropper niveau 20** ;
- l'armée de la capitale est **gigantesque mais tenable** : upkeep ≤ blé brut −
  population ⇒ **production nette de céréales ≥ 0** (aucune famine au passage du temps) ;
- les secondaires **ravitaillent** la capitale (routes commerciales) ;
- rien ne meurt de faim après un gros `tick`.
"""
import tempfile
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "rivals.db"

from app.engine import world as W
from app.engine import village as V
from app.engine import rivals as RIV
from app.data.tribes import Tribe


def _seed(radius=45):
    store.DB_PATH = Path(tempfile.mkdtemp()) / "rivals.db"
    store.init_db()
    store.insert_tiles(W.generate_world(radius))
    # Distances d'apparition ramenées dans le petit monde de test (déterministe). Sans
    # humain semé, les rivaux `near` retombent sur `HUMAN_START` (60, 60) via `_human_ref`
    # (hors petit monde) → on les rapproche aussi de l'origine pour le test.
    for r in RIV.ALL_RIVALS:
        r.dist = min(r.dist, radius - 10)
        r.zone = "far"


def test_seed_is_idempotent():
    _seed()
    a = RIV.spawn_rivals(server_speed=100)
    b = RIV.spawn_rivals(server_speed=100)   # 2ᵉ appel : marqueur présent ⇒ no-op
    assert len(a) == len(RIV.ALL_RIVALS)
    assert b == [], "le seeding rival doit être idempotent"
    assert store.find_player_by_name(RIV.SEED_MARKER) is not None
    print(f"✅ {len(a)} rivaux semés, re-seed = no-op")


def test_legend_capital_is_maxed_15_cropper():
    _seed()
    RIV.spawn_rivals(server_speed=100)
    pid = store.find_player_by_name("Auguste")
    vids = store.player_villages(pid)
    assert len(vids) >= 8, "une légende a une dizaine de villages"
    cap = next(store.load_village(v) for v in vids
               if store.load_village(v).is_capital)
    # 15-cropper : 15 champs de céréales, tous niveau 20 (capitale > 10).
    crop_fields = [s for i, s in cap.slots.items()
                   if 1 <= i <= 18 and s.building_id == 3]
    assert len(crop_fields) == 15, f"capitale = 15-cropper (got {len(crop_fields)})"
    assert all(s.level == 20 for s in crop_fields), "champs de céréales niveau 20"
    assert cap.tribe == Tribe.ROMANS
    print(f"✅ Auguste : capitale 15-cropper niv 20, {sum(cap.troops)} troupes")


def test_capital_army_is_huge_but_sustainable():
    _seed()
    RIV.spawn_rivals(server_speed=100)
    pid = store.find_player_by_name("Vercingétorix")
    cap = next(v for v in (store.load_village(i) for i in store.player_villages(pid))
               if v.is_capital)
    assert sum(cap.troops) > 10000, "armée gigantesque attendue sur une capitale légende"
    # Tenable : la production nette de blé reste ≥ 0 (sinon famine au 1ᵉʳ tick).
    assert V.net_production(cap)[W.CROP] >= 0, "armée non tenable (blé net < 0)"
    print(f"✅ {sum(cap.troops)} troupes, blé net {V.net_production(cap)[W.CROP]:.0f}/h ≥ 0")


def test_no_starvation_after_big_tick():
    """Après un gros passage du temps (×vitesse serveur), aucune troupe ne meurt de faim."""
    _seed()
    RIV.spawn_rivals(server_speed=100)
    pid = store.find_player_by_name("Auguste")
    for vid in store.player_villages(pid):
        v = store.load_village(vid)
        before = sum(v.troops)
        V.tick(v, v.updated_at + 7 * 24 * 3600)   # +7 jours de base (×100 en interne)
        assert sum(v.troops) >= before, "aucune troupe ne doit mourir de faim"
    print("✅ aucune famine après +7 j simulés")


def test_secondaries_supply_capital_via_trade_routes():
    _seed()
    RIV.spawn_rivals(server_speed=100)
    pid = store.find_player_by_name("Auguste")
    cap = next(v for v in (store.load_village(i) for i in store.player_villages(pid))
               if v.is_capital)
    routes = [r for vid in store.player_villages(pid)
              for r in store.trade_routes_for(vid)]
    assert routes, "les secondaires doivent ravitailler la capitale"
    assert all(r["target_id"] == cap.id for r in routes), "routes → capitale"
    assert all(__import__("json").loads(r["amounts"])[W.CROP] > 0 for r in routes), \
        "cargaison = céréales"
    print(f"✅ {len(routes)} routes commerciales secondaires → capitale (blé)")
