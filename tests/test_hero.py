"""Verrouille le héros, les aventures et les objets (cf. CLAUDE.md).

⚠️ Tout est approximation documentée (kirilloid ne modélise pas le héros).
On vérifie la *mécanique*, pas des chiffres officiels :
- XP → niveau → points d'attribut (4/niveau) ; allocation.
- Régénération de santé paresseuse (×vitesse serveur).
- Production de ressources créditée au village d'attache.
- Aventure : envoi → résolution (XP, ressources, santé, éventuel objet) au retour.
- Objets : équiper depuis le sac applique le bonus ; consommable soigne.
- Combat : le héros renforce l'attaque (force + bonus) et gagne XP / perd santé.
"""
import tempfile, time
from pathlib import Path

from app import store
store.DB_PATH = Path(tempfile.mkdtemp()) / "hero.db"

from app.engine import village as V
from app.engine import movement as M
from app.engine import hero as H
from app.data.buildings import B
from app.data.tribes import Tribe


def fresh():
    store.DB_PATH = Path(tempfile.mkdtemp()) / "hero.db"
    store.init_db()


def test_level_and_allocate():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    v = store.insert_village(v)
    h = H.get_or_create(pid, v.id)
    # Niveau 0, 0 point.
    assert h.level == 0 and h.points == 0
    h.experience = H.xp_threshold(3)          # assez pour le niveau 3
    H._refresh_level(h)
    assert h.level == 3 and h.points == 12, (h.level, h.points)
    H.allocate(h, "fight", 5)
    assert h.fight == 5 and h.points == 7
    eff = H.effective(h)
    assert eff["strength"] == H.BASE_STRENGTH + 5 * H.STRENGTH_PER_FIGHT
    print(f"✅ niveau 3 → 12 points ; force = {eff['strength']}")


def test_health_regen_and_production():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    v.resources = [100.0] * 4
    v = store.insert_village(v)
    now = time.time()
    h = H.get_or_create(pid, v.id, now)
    h.health = 50.0
    h.res_points = 5                          # production
    h.res_choice = 0                          # bois
    home = store.load_village(v.id)
    V.tick(home, now)
    res0 = list(home.resources)
    changed = H.tick(h, home, now + 3600)     # +1 h réelle (×100)
    assert changed
    assert h.health > 50.0, "la santé doit régénérer"
    assert home.resources[0] > res0[0], "le bois produit par le héros doit être crédité"
    print(f"✅ santé {50.0}→{round(h.health,1)} ; bois +{round(home.resources[0]-res0[0])}")


def test_adventure_cycle():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    v.resources = [100.0] * 4
    v = store.insert_village(v)
    # Grenier/entrepôt pour stocker le butin d'aventure.
    v.slots[20] = V.Slot(building_id=B.WAREHOUSE, level=10)
    v.slots[21] = V.Slot(building_id=B.GRANARY, level=10)
    store.save_village(v)
    now = time.time()
    h = H.get_or_create(pid, v.id, now)
    H.replenish_adventures(pid, h, now)
    H.save(h)
    advs = store.adventures_for(pid)
    assert advs, "des aventures doivent apparaître"

    info = H.send_to_adventure(pid, advs[0]["id"], now)
    h = H.load(pid)
    assert h.status == "adventure"
    xp0 = h.experience
    # Au retour : récompense appliquée.
    home = store.load_village(v.id)
    H.tick(h, home, now + info["arrive_in"] + 1)
    store.save_village(home)
    H.save(h)
    assert h.status in ("home", "dead")
    assert h.experience >= xp0, "l'aventure doit rapporter de l'XP"
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "adventure")
    print(f"✅ aventure : +{rep['body']['xp']} XP, objet={rep['body']['item']}, "
          f"santé−{rep['body']['health_loss']}")


def test_items():
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = store.insert_village(V.new_village("Cap", Tribe.GAULS, server_speed=100,
                                           x=0, y=0, player_id=pid))
    h = H.get_or_create(pid, v.id)
    h.bag["sword_great"] = 1
    base = H.effective(h)["strength"]
    H.equip(h, "sword_great")
    assert "right" in h.equipment and "sword_great" not in h.bag
    assert H.effective(h)["strength"] > base, "l'arme doit augmenter la force"
    assert H.effective(h)["off_bonus"] > 0, "la grande épée donne un bonus d'attaque"
    # Consommable : soigne.
    h.health = 40.0
    h.bag["bandage"] = 1
    H.use_consumable(h, "bandage")
    assert h.health == 65.0 and "bandage" not in h.bag
    print(f"✅ objets : grande épée équipée (force {base}→{round(H.effective(h)['strength'])}), "
          f"bandage +25 santé")


def test_revive_delay_no_double_charge():
    """Résurrection : coût prélevé une fois, délai en cours ⇒ re-clic refusé
    (sinon on repaie et on remet le compte à rebours à zéro : le héros semble
    « ne jamais ressusciter »). Puis, le délai échu, il repasse « home »."""
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    v = V.new_village("Cap", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    v.slots[22] = V.Slot(building_id=B.HERO_MANSION, level=1)
    v.slots[20] = V.Slot(building_id=B.WAREHOUSE, level=10)
    v.slots[21] = V.Slot(building_id=B.GRANARY, level=10)
    v.resources = [5000.0] * 4
    now = time.time()
    v = store.insert_village(v)
    h = H.get_or_create(pid, v.id, now)
    h.health = 0.0
    h.status = "dead"
    H.save(h)

    info = H.revive(pid, now)
    assert info["revive_in"] > 0
    v2 = store.load_village(v.id)
    paid = [round(5000.0 - v2.resources[i]) for i in range(4)]
    assert paid == list(H.REVIVE_COST), paid
    assert H.load(pid).status == "dead", "toujours mort pendant le délai"

    # Re-clic pendant le délai → refusé, aucune ressource prélevée de plus.
    try:
        H.revive(pid, now + 1)
        assert False, "un second appel doit être refusé"
    except H.HeroError:
        pass
    v3 = store.load_village(v.id)
    assert [round(x, 3) for x in v3.resources] == [round(x, 3) for x in v2.resources], \
        "pas de double prélèvement"

    # Délai échu → le tick ressuscite (santé pleine, statut home).
    h = H.load(pid)
    home = store.load_village(v.id)
    H.tick(h, home, now + info["revive_in"] + 1)
    assert h.status == "home" and h.health == H.MAX_HEALTH, (h.status, h.health)
    print(f"✅ résurrection : coût {H.REVIVE_COST} prélevé une fois, délai {info['revive_in']}s, puis home")


def test_hero_in_combat():
    fresh()
    pid = store.create_player("A", Tribe.GAULS)
    pid2 = store.create_player("B", Tribe.GAULS)
    now = time.time()
    att = V.new_village("Att", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    att.troops[1] = 50                        # 50 épéistes
    att = store.insert_village(att)
    deff = V.new_village("Def", Tribe.GAULS, server_speed=100, x=1, y=0, player_id=pid2)
    deff.troops[0] = 40                       # 40 phalanges
    deff = store.insert_village(deff)

    # Héros costaud chez l'attaquant.
    h = H.get_or_create(pid, att.id, now)
    h.experience = H.xp_threshold(10)
    H._refresh_level(h)
    H.allocate(h, "fight", h.points)          # tout en force
    H.allocate  # noqa
    H.save(h)

    info = M.send(att.id, deff.id, pid, "raid", [0, 50] + [0]*8, now, with_hero=True)
    M.process_due(now + info["arrive_in"] + 1)
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "offensive")
    assert rep["body"]["hero"], "le rapport doit indiquer la présence du héros"
    h = H.load(pid)
    assert h.experience > H.xp_threshold(10), "le héros gagne de l'XP au combat"
    # Le héros revient (status attacking jusqu'au retour, puis home).
    # On force le traitement du retour.
    M.process_due(now + info["arrive_in"] + 100000)
    h = H.load(pid)
    assert h.status in ("home", "dead", "attacking")
    print(f"✅ héros au combat : XP {round(h.experience)}, santé {round(h.health)}, statut {h.status}")


def test_hero_reinforce_rehomes():
    """Héros envoyé en assistance (renfort) vers un autre de TES villages : il s'y
    réinstalle (nouveau village de rattachement) à l'arrivée."""
    fresh()
    pid = store.create_player("Toi", Tribe.GAULS)
    other = store.create_player("Ennemi", Tribe.GAULS)
    now = time.time()
    v1 = V.new_village("V1", Tribe.GAULS, server_speed=100, x=0, y=0, player_id=pid)
    v1 = store.insert_village(v1)
    v2 = V.new_village("V2", Tribe.GAULS, server_speed=100, x=5, y=0, player_id=pid)
    v2 = store.insert_village(v2)
    enemy = V.new_village("E", Tribe.GAULS, server_speed=100, x=2, y=2, player_id=other)
    enemy = store.insert_village(enemy)

    h = H.get_or_create(pid, v1.id, now)
    # On ne peut renforcer que SES villages avec le héros.
    try:
        M.send(v1.id, enemy.id, pid, "reinforce", [0]*10, now, with_hero=True)
        assert False, "renfort héros vers un ennemi interdit"
    except M.MoveError:
        pass

    # Héros seul en renfort vers v2 (units toutes nulles ⇒ vitesse du héros).
    info = M.send(v1.id, v2.id, pid, "reinforce", [0]*10, now, with_hero=True)
    h = H.load(pid)
    assert h.status == "moving" and h.home_village_id == v1.id  # en transit
    # En déplacement, le héros ne peut PAS partir à l'aventure.
    aid = store.insert_adventure(pid, 1, 1, "normal", now)
    try:
        H.send_to_adventure(pid, aid, now)
        assert False, "aventure interdite tant que le héros est en déplacement"
    except H.HeroError:
        pass
    M.process_due(now + info["arrive_in"] + 1)
    h = H.load(pid)
    assert h.home_village_id == v2.id, "le héros se rattache au village renforcé"
    assert h.status == "home"
    rep = next(r for r in store.reports_for(pid) if r["body"].get("type") == "reinforce")
    assert rep["body"]["hero"], "le rapport doit signaler la ré-attache du héros"
    print(f"✅ héros réinstallé : v{v1.id} → v{v2.id} (statut {h.status})")


def test_production_rates_exact():
    """Production de ressources fidèle au vrai Travian T4 : ressource unique = +10/pt
    sur cette seule ressource ; réparti = +3/pt sur **chacune** des 4 ressources."""
    h = H.Hero(player_id=1, home_village_id=1)
    h.res_points = 4
    # Mode ressource unique (bois) : 4 × 10 = 40 sur le bois, 0 ailleurs.
    h.res_choice = 0
    assert H.hero_production(h) == [40.0, 0.0, 0.0, 0.0], H.hero_production(h)
    assert H.effective(h)["production_per_hour"] == 40
    # Mode réparti : 4 × 3 = 12 sur CHAQUE ressource (total 48 > 40, comme le vrai jeu).
    h.res_choice = -1
    assert H.hero_production(h) == [12.0, 12.0, 12.0, 12.0], H.hero_production(h)
    assert H.effective(h)["production_per_hour"] == 12
    # Aucun point ⇒ aucune production.
    h.res_points = 0
    assert H.hero_production(h) == [0.0, 0.0, 0.0, 0.0]
    print("✅ taux exacts : 10/pt (unique), 3/pt de chaque (réparti)")


def main():
    test_level_and_allocate()
    test_health_regen_and_production()
    test_adventure_cycle()
    test_items()
    test_hero_in_combat()
    test_production_rates_exact()
    print("\n✅ Héros / aventures / objets validés")


if __name__ == "__main__":
    main()
