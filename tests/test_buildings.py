"""Verrouille les nouvelles mécaniques de bâtiments (cf. CLAUDE.md) :

- Académie : l'entraînement d'une unité non-basique exige sa recherche préalable ;
  après le temps de recherche, l'unité devient entraînable.
- Forge : l'amélioration d'une unité augmente bien sa puissance au combat (le
  niveau d'amélioration est transmis au moteur de combat par movement.py).
- Trappeur : la construction de pièges respecte la capacité et débite les ressources.

Approximations documentées (kirilloid ne modélise pas ces coûts) : coût de
recherche = coût d'entraînement ; coût d'amélioration = coût × niveau ; coût/temps
des pièges = constantes (cf. village.TRAP_COST / TRAP_TIME).
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "buildings.db"

from app.engine import village as V
from app.engine import movement as M
from app.data import buildings as BLD
from app.data.buildings import B
from app.data.tribes import Tribe


def _gaul(resources=200000.0, **slots):
    v = V.new_village("T", Tribe.GAULS, server_speed=100, player_id=1)
    v.slots[19].level = 12
    idx = 20
    for bid, lvl in slots.items():
        v.slots[idx] = V.Slot(building_id=getattr(B, bid), level=lvl); idx += 1
    v.resources = [resources] * 4
    return v


def test_research_gating():
    now = time.time()
    v = _gaul(STABLES=5, ACADEMY=5)
    # Le cavalier Theutates (index 3) nécessite une recherche.
    assert V.needs_research(v, 3) and not v.research[3]
    try:
        V.enqueue_training(v, B.STABLES, 3, 1, now)
        assert False, "entraînement sans recherche aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (non recherché) :", e)

    order = V.enqueue_research(v, 3, now)
    V.tick(v, order.finish_at + 1)
    assert v.research[3] == 1, "recherche non appliquée"
    # Désormais entraînable.
    V.enqueue_training(v, B.STABLES, 3, 1, order.finish_at + 1)
    print("✅ cavalier Theutates entraînable après recherche")

    # L'unité de base de la caserne (index 0) ne demande aucune recherche.
    v2 = _gaul(BARRACKS=3)
    assert not V.needs_research(v2, 0)
    V.enqueue_training(v2, B.BARRACKS, 0, 5, now)
    print("✅ phalange (index 0) entraînable sans recherche")


def test_research_requires_building_levels():
    """Vrai Travian : la cavalerie avancée exige une écurie de plus en plus haute
    (Gaulois : éclaireur écurie 1, Theutates écurie 3, Druide écurie 5, Haeduan
    écurie 10 + académie 15). Avec une écurie niv 1 on ne doit PAS pouvoir tout
    rechercher (régression signalée). Recoupé wiki travian.fandom.com."""
    now = time.time()
    v = _gaul(STABLES=1, ACADEMY=5)
    # Éclaireur (index 2) : écurie 1 + académie 5 → OK.
    assert V.reqs_met(v, 2)
    V.enqueue_research(v, 2, now)
    # Cavalier Theutates (index 3, écurie 3) et Haeduan (index 5, écurie 10) verrouillés.
    for idx in (3, 5):
        assert not V.reqs_met(v, idx)
        try:
            V.enqueue_research(v, idx, now)
            assert False, f"recherche unité {idx} aurait dû échouer (écurie trop basse)"
        except V.BuildError as e:
            print(f"refus attendu (prérequis) unité {idx} :", e)
    # L'académie liste bien toutes les unités à rechercher, verrouillées comprises.
    listed = {i for i, _ in V.researchable_units(v)}
    assert {2, 3, 5} <= listed
    # Avec écurie 10 + académie 15, le Haeduan se débloque.
    v2 = _gaul(STABLES=10, ACADEMY=15)
    assert V.reqs_met(v2, 5)
    V.enqueue_research(v2, 5, now)
    print("✅ prérequis d'écurie/académie respectés par unité")


def _raid_losses(upgrade_level):
    """Razzia identique d'un village vers un autre ; renvoie (% pertes off, def)."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / f"smithy_{upgrade_level}.db"
    store.init_db()
    pid = store.create_player("A", Tribe.GAULS)
    pid2 = store.create_player("B", Tribe.GAULS)
    now = time.time()
    att = V.new_village("Att", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    att.troops[1] = 200                 # 200 épéistes (index 1, attaque 65)
    att.upgrades[1] = upgrade_level     # amélioration en forge
    att = store.insert_village(att)
    deff = V.new_village("Def", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=pid2)
    deff.troops[0] = 100                # 100 phalanges en défense
    deff = store.insert_village(deff)

    info = M.send(att.id, deff.id, pid, "raid", [0, 200] + [0] * 8, now)
    M.process_due(now + info["arrive_in"] + 1)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    return rep["body"]["pertes_pct"], rep["body"].get("survivantes")


def test_smithy_combat():
    base_loss, base_surv = _raid_losses(0)
    upg_loss, upg_surv = _raid_losses(10)
    print(f"pertes attaquant — sans forge : {base_loss}% | forge niv 10 : {upg_loss}%")
    print(f"survivants — sans forge : {sum(base_surv)} | forge niv 10 : {sum(upg_surv)}")
    assert upg_loss < base_loss, "l'amélioration en forge devrait réduire les pertes"
    assert sum(upg_surv) > sum(base_surv)
    print("✅ l'amélioration en forge renforce bien l'attaque au combat")


def test_trapper():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "trapper.db"
    now = time.time()
    v = _gaul(resources=10000.0, TRAPPER=2)
    assert V.trap_capacity(v) == V.F.trapper_traps(2)
    cap = V.trap_capacity(v)
    res0 = list(v.resources)
    order = V.enqueue_traps(v, 10, now)
    assert V.traps_total(v) == 10
    # Coût débité = 10 × TRAP_COST.
    for i in range(4):
        assert res0[i] - v.resources[i] == 10 * V.TRAP_COST[i]
    # Dépasser la capacité est refusé.
    try:
        V.enqueue_traps(v, cap, now)
        assert False, "dépassement de capacité aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (capacité) :", e)
    # Après le temps de construction, les pièges sont posés.
    V.tick(v, order.next_finish + 10 * order.per_unit + 1)
    assert v.traps == 10, v.traps
    print(f"✅ trappeur : {v.traps}/{cap} pièges construits, coût débité")


def test_traps_in_combat():
    """Les pièges capturent les assaillants AVANT la bataille : ils deviennent
    prisonniers (non tués), le surplus combat. Capture totale ⇒ pas de bataille."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / "traps_combat.db"
    store.init_db()
    pid = store.create_player("Att", Tribe.GAULS)
    did = store.create_player("Def", Tribe.GAULS)
    now = time.time()
    att = V.new_village("Att", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    att.troops[1] = 50                  # 50 épéistes
    att = store.insert_village(att)
    deff = V.new_village("Def", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=did)
    deff.troops[0] = 100                # 100 phalanges en défense
    deff.traps = 20                     # 20 pièges posés
    deff = store.insert_village(deff)

    info = M.send(att.id, deff.id, pid, "raid", [0, 50] + [0] * 8, now)
    M.process_due(now + info["arrive_in"] + 1)

    d2 = store.load_village(deff.id)
    assert V.prisoners_count(d2) == 20, V.prisoners_count(d2)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    assert sum(rep["body"]["captures"]) == 20, rep["body"]["captures"]
    # Seuls 30 épéistes (50−20 piégés) ont combattu : survivants ≤ 30.
    assert sum(rep["body"]["survivantes"]) <= 30
    print(f"✅ pièges : {sum(rep['body']['captures'])} capturés, le surplus combat")

    # Capture totale : assez de pièges pour tout l'assaillant ⇒ aucune bataille.
    deff2 = V.new_village("Def2", Tribe.GAULS, server_speed=100, x=2, y=0, player_id=did)
    deff2.troops[0] = 100
    deff2.traps = 100
    deff2 = store.insert_village(deff2)
    a2 = store.load_village(att.id); a2.troops[1] = 40; store.save_village(a2)
    info2 = M.send(att.id, deff2.id, pid, "attack", [0, 40] + [0] * 8, now)
    M.process_due(now + info2["arrive_in"] + 1)
    d3 = store.load_village(deff2.id)
    assert V.prisoners_count(d3) == 40, V.prisoners_count(d3)
    assert d3.troops[0] == 100, "défense intacte (pas de bataille livrée)"

    # Libération : le groupe quitte les pièges (réintégration côté API).
    grp = V.release_prisoner(d3, 0)
    assert sum(grp["units"]) == 40 and V.prisoners_count(d3) == 0
    print("✅ capture totale ⇒ pas de bataille ; libération vide les pièges")


def test_great_barracks_trains():
    """La grande caserne forme les mêmes unités que la caserne, à coût ×3, via sa
    propre file (entraînement en parallèle de la caserne normale)."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / "great.db"
    now = time.time()
    v = _gaul(resources=100000.0, BARRACKS=5, GREAT_BARRACKS=3)
    base = [i for i, _ in V.trainable_units(v, B.BARRACKS)]
    great = [i for i, _ in V.trainable_units(v, B.GREAT_BARRACKS)]
    assert base and base == great, (base, great)

    u0 = V.UNITS[v.tribe][0]              # phalange : pas de recherche requise
    res0 = list(v.resources)
    V.enqueue_training(v, B.GREAT_BARRACKS, 0, 2, now)
    for i in range(4):
        assert res0[i] - v.resources[i] == u0.cost[i] * 2 * V.GREAT_COST_MULT, i
    assert any(t.building_id == B.GREAT_BARRACKS for t in v.training)
    print("✅ grande caserne : mêmes unités, coût ×3, file dédiée")


def test_siege_wiring():
    """Siège câblé : une attaque avec béliers + catapultes démolit la muraille et le
    bâtiment ciblé (niveaux persistés chez le défenseur, récap dans le rapport) ;
    une razzia identique ne détruit rien (le siège n'agit qu'en attaque normale)."""
    store.DB_PATH = Path(tempfile.mkdtemp()) / "siege.db"
    store.init_db()
    pid = store.create_player("Att", Tribe.ROMANS)
    did = store.create_player("Def", Tribe.GAULS)
    now = time.time()

    def make_def(x):
        d = V.new_village(f"Def{x}", Tribe.GAULS, server_speed=100, x=x, y=0, player_id=did)
        d.troops[0] = 10                                   # défense faible (10 phalanges)
        d.slots[40] = V.Slot(building_id=B.PALISADE, level=10)    # muraille niv 10
        d.slots[20] = V.Slot(building_id=B.MARKETPLACE, level=10)  # cible niv 10
        return store.insert_village(d)

    def arm_attacker():
        a = store.load_village(att.id)
        a.troops = [500, 0, 0, 0, 0, 0, 20, 50, 0, 0]     # légionnaires + béliers + catas
        a.away = [0] * 10
        store.save_village(a)

    att = V.new_village("Att", Tribe.ROMANS, server_speed=100, x=0, y=0, player_id=pid)
    att = store.insert_village(att)
    arm_attacker()

    # Attaque normale ciblant la place de marché.
    d1 = make_def(1)
    units = [500, 0, 0, 0, 0, 0, 20, 50, 0, 0]
    info = M.send(att.id, d1.id, pid, "attack", units, now, targets=[B.MARKETPLACE])
    M.process_due(now + info["arrive_in"] + 1)
    d1b = store.load_village(d1.id)
    assert d1b.slots[40].level < 10, ("mur non démoli", d1b.slots[40].level)
    assert d1b.slots[20].level < 10, ("marché non démoli", d1b.slots[20].level)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["siege"]["mur"], rep["body"]["siege"]
    assert rep["body"]["siege"]["degats"], rep["body"]["siege"]
    print(f"✅ siège attaque : mur 10→{d1b.slots[40].level}, marché 10→{d1b.slots[20].level}")

    # Razzia identique : aucune destruction persistée.
    arm_attacker()
    d2 = make_def(2)
    info2 = M.send(att.id, d2.id, pid, "raid", units, now, targets=[B.MARKETPLACE])
    M.process_due(now + info2["arrive_in"] + 1)
    d2b = store.load_village(d2.id)
    assert d2b.slots[40].level == 10 and d2b.slots[20].level == 10, "razzia ne détruit rien"
    print("✅ razzia : aucune destruction (siège réservé à l'attaque normale)")


def test_mansion_trapper_build_time():
    """Temps de construction trappeur/manoir : écart kirilloid corrigé (`time(a,0)`
    plaçait le 0 dans *k* ⇒ temps négatif/nul dès le niveau 2). Doit croître avec le
    niveau et coller au vrai Travian (manoir niv 1 = 2300 s de base ; niv 1→20 à BP 10
    : 27:30 → 7:42:20)."""
    hm = BLD.get(B.HERO_MANSION)
    tr = BLD.get(B.TRAPPER)
    # Temps strictement positifs et croissants (plus de niveau nul/négatif).
    hm_times = [hm.time_at(l) for l in range(1, 21)]
    tr_times = [tr.time_at(l) for l in range(1, 21)]
    assert all(t > 0 for t in hm_times + tr_times), (hm_times, tr_times)
    assert hm_times == sorted(hm_times) and tr_times == sorted(tr_times)
    assert hm.time_at(1) == 2300 and tr.time_at(1) == 2000   # niveau 1 = paramètre a (b=0)
    # Recoupe le vrai Travian à BP 10 (facteur 0.964**9).
    f = 0.964 ** 9
    assert abs(hm.time_at(1) * f - 1650) < 15      # 27:30
    assert abs(hm.time_at(20) * f - 27740) < 60    # 7:42:20
    print("✅ temps de construction trappeur/manoir corrigés (positifs, croissants)")


def test_settler_chief_training_gating():
    """Colons/chefs : le vrai Travian exige le bâtiment au **niveau 10**, et les
    administrateurs se forment en **résidence OU palais** (correctif de fidélité T4.6 —
    la croyance « chefs = palais uniquement » venait de la Travian 3.6 ; recoupé
    support.travian.com : Résidence → « Trains administrators: Yes »)."""
    from app.data.buildings import B as Bb

    def mk(bid, lvl):
        v = V.new_village("T", Tribe.ROMANS, server_speed=100)
        v.slots[5] = V.Slot(building_id=bid, level=lvl)
        v.resources = [10 ** 9] * 4
        return v

    units = V.UNITS[Tribe.ROMANS]
    chief = next(i for i, u in enumerate(units) if u.is_chief)
    settler = next(i for i, u in enumerate(units) if u.is_settler)

    # Sous le niveau 10 : rien n'est formable, ni en résidence ni en palais.
    assert V.trainable_units(mk(Bb.RESIDENCE, 9), Bb.RESIDENCE) == []
    assert V.trainable_units(mk(Bb.PALACE, 9), Bb.PALACE) == []

    # Niveau 10 : résidence ET palais → colon + chef (les deux forment les administrateurs).
    res10 = [i for i, _ in V.trainable_units(mk(Bb.RESIDENCE, 10), Bb.RESIDENCE)]
    pal10 = [i for i, _ in V.trainable_units(mk(Bb.PALACE, 10), Bb.PALACE)]
    assert settler in res10 and chief in res10, res10
    assert settler in pal10 and chief in pal10, pal10

    # enqueue : refus du colon sous niv 10 (le chef doit être recherché — cf. test dédié —
    # donc pas d'enqueue direct ici) ; la résidence forme bien le chef (fidélité T4.6).
    try:
        V.enqueue_training(mk(Bb.RESIDENCE, 9), Bb.RESIDENCE, settler, 1)
        assert False, "colon sous niv 10 aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (niv < 10) :", e)
    v = mk(Bb.RESIDENCE, 10)   # 1 emplacement d'expansion, aucun autre village
    v.research[chief] = 1      # le chef doit être recherché en académie 20 (cf. test dédié)
    o = V.enqueue_training(v, Bb.RESIDENCE, chief, 1, now=v.updated_at)
    assert o.per_unit == units[chief].train_time / v.server_speed   # facteur 1.0, pas de crash
    print("✅ colons/chefs : niveau 10 requis, administrateurs formables résidence OU palais")


def test_chief_requires_academy_research():
    """Vrai Travian : le chef/sénateur/chef de clan se **recherche à l'académie (niveau 20)**
    avant d'être formable au palais. Recoupé travian.fandom.com « Senator/Chief/Chieftain »."""
    now = time.time()
    v = _gaul(resources=500000.0, PALACE=10, ACADEMY=20)
    chief = next(i for i, u in enumerate(V.UNITS[Tribe.GAULS]) if u.is_chief)

    # Le chef relève désormais de l'académie et figure dans la liste à rechercher.
    assert V.needs_research(v, chief) and not v.research[chief]
    assert chief in {i for i, _ in V.researchable_units(v)}

    # Sans recherche, l'entraînement au palais est refusé.
    try:
        V.enqueue_training(v, B.PALACE, chief, 1, now)
        assert False, "chef entraînable sans recherche aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (chef non recherché) :", e)

    # Académie trop basse (< 20) : recherche refusée.
    v_low = _gaul(PALACE=10, ACADEMY=15)
    assert not V.reqs_met(v_low, chief)
    try:
        V.enqueue_research(v_low, chief, now)
        assert False, "recherche du chef à l'académie 15 aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (académie < 20) :", e)

    # Académie 20 : recherche possible, puis entraînement débloqué.
    order = V.enqueue_research(v, chief, now)
    V.tick(v, order.finish_at + 1)
    assert v.research[chief] == 1
    v.resources = [500000.0] * 4  # recharge (le stockage aurait plafonné pendant le tick)
    V.enqueue_training(v, B.PALACE, chief, 1, order.finish_at + 1)
    print("✅ chef : recherche académie 20 requise avant entraînement")


def test_forge_requires_research():
    """Vrai Travian : la forge n'améliore qu'une unité **déjà recherchée** en académie
    (les unités de base — index 0 — n'ont pas besoin de recherche)."""
    now = time.time()
    v = _gaul(BARRACKS=3, STABLES=10, ACADEMY=20, SMITHY=5)
    # Le Haeduan (index 5) n'est pas recherché → pas améliorable.
    assert V.needs_research(v, 5) and not v.research[5]
    assert 5 not in {i for i, _ in V.upgradable_units(v)}
    try:
        V.enqueue_upgrade(v, 5, now)
        assert False, "amélioration d'une unité non recherchée aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (forge, non recherché) :", e)

    # L'épéiste de base (index 0, phalange) est améliorable sans recherche.
    assert not V.needs_research(v, 0)
    assert 0 in {i for i, _ in V.upgradable_units(v)}
    V.enqueue_upgrade(v, 0, now)

    # Une fois le Haeduan recherché, il devient améliorable.
    order = V.enqueue_research(v, 5, now)
    V.tick(v, order.finish_at + 1)
    assert 5 in {i for i, _ in V.upgradable_units(v)}
    V.enqueue_upgrade(v, 5, order.finish_at + 1)
    print("✅ forge : recherche préalable exigée avant amélioration")


def test_demolish():
    """Démolition (bâtiment principal niv 10+, vrai Travian) : un niveau à la fois
    jusqu'à destruction (niv 0 ⇒ emplacement libéré), sans remboursement, une seule
    à la fois, jamais sur les champs / bâtiment principal / rassemblement."""
    now = time.time()
    v = _gaul(WAREHOUSE=5)  # slot 20 = entrepôt niv 5 ; slot 19 = bâtiment principal niv 12
    ware = 20

    # Sans bâtiment principal niv 10, pas de démolition possible.
    v.slots[19].level = 9
    assert not V.can_demolish(v)
    try:
        V.enqueue_demolish(v, ware, now=now)
        assert False, "démolition sans bâtiment principal niv 10 aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (bâtiment principal <10) :", e)
    v.slots[19].level = 12
    assert V.can_demolish(v)

    # Champs de ressources / bâtiment principal / rassemblement : non démolissables.
    v.slots[1].level = 5                                          # champ de bois niv 5
    assert not V.is_demolishable_slot(v, 1)                       # champ de bois (res)
    assert not V.is_demolishable_slot(v, 19)                      # bâtiment principal
    assert not V.is_demolishable_slot(v, V.RALLY_SLOT)            # rassemblement
    assert V.is_demolishable_slot(v, ware)                        # entrepôt : OK

    # Aucun remboursement : les ressources ne bougent pas à la mise en démolition.
    before = list(v.resources)
    o = V.enqueue_demolish(v, ware, now=now)                       # défaut : un niveau (5→4)
    assert o.target_level == 4
    assert v.resources == before, "la démolition ne doit rien rembourser ni coûter"

    # Une seule démolition à la fois.
    try:
        V.enqueue_demolish(v, ware, now=now)
        assert False, "deux démolitions simultanées auraient dû échouer"
    except V.BuildError as e:
        print("refus attendu (déjà en cours) :", e)

    V.tick(v, o.finish_at + 1)
    assert v.slots[ware].level == 4, ("niveau non redescendu", v.slots[ware].level)
    assert v.demolition is None, "démolition non purgée après complétion"

    # Destruction complète (target 0) ⇒ emplacement libéré (slot supprimé).
    o2 = V.enqueue_demolish(v, ware, target_level=0, now=o.finish_at + 1)
    assert o2.target_level == 0
    V.tick(v, o2.finish_at + 1)
    assert ware not in v.slots, "l'emplacement doit être libéré après destruction"
    print("✅ démolition : niv 10 requis, un niveau à la fois, sans remboursement, "
          "destruction ⇒ emplacement libéré")


def test_upgrade_survives_demolished_prerequisite():
    """Vrai Travian : les prérequis ne sont contrôlés qu'à la **construction initiale**
    (niveau 0→1) et jamais recalculés ensuite. Démolir un bâtiment prérequis ne fige donc
    pas un bâtiment déjà construit — on peut continuer à l'**améliorer** ; seule la pose
    d'un **nouveau** bâtiment reste bloquée tant que ses prérequis manquent. Recoupé
    support.travian.com / wiki Fandom « Main building » / Travian Answers."""
    now = time.time()
    v = _gaul(ACADEMY=5, SMITHY=5)   # slot 20 = académie niv 5, slot 21 = forge niv 5
    acad, smithy = 20, 21
    assert v.slots[acad].building_id == B.ACADEMY
    assert v.slots[smithy].building_id == B.SMITHY
    # La forge (id 12) exige académie niv 1 ; l'écurie (id 19) exige forge 3 + académie 5.

    # On « démolit » entièrement l'académie (emplacement libéré) ⇒ prérequis disparu.
    del v.slots[acad]

    # Amélioration d'un bâtiment DÉJÀ construit (forge) : autorisée malgré la perte du
    # prérequis (académie) — c'est le correctif (avant, enqueue_build re-vérifiait les reqs).
    o = V.enqueue_build(v, smithy, now=now)
    assert o.target_level == 6, ("la forge doit rester améliorable", o)

    # Pose d'un NOUVEAU bâtiment qui exige ce prérequis (écurie → académie 5) : refusée.
    free = 30
    assert free not in v.slots
    try:
        V.enqueue_new_building(v, free, B.STABLES, now=now)
        assert False, "poser une écurie sans académie 5 aurait dû échouer"
    except V.BuildError as e:
        print("refus attendu (nouveau bâtiment, prérequis manquant) :", e)
    print("✅ prérequis démoli : amélioration d'un bâtiment existant OK, "
          "nouvelle construction bloquée")


def main():
    test_research_gating()
    test_smithy_combat()
    test_trapper()
    test_traps_in_combat()
    test_great_barracks_trains()
    test_siege_wiring()
    test_mansion_trapper_build_time()
    test_settler_chief_training_gating()
    test_demolish()
    test_upgrade_survives_demolished_prerequisite()
    print("\n✅ Mécaniques de bâtiments (académie / forge / trappeur / pièges / "
          "grande caserne / siège / temps manoir / colons-chefs / démolition) validées")


if __name__ == "__main__":
    main()
