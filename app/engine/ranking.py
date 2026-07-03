"""Classement / statistiques des joueurs (façon Travian).

Deux natures de quantités :
- **cumulées** au fil des combats et persistées sur le joueur (points d'attaque, de
  défense, ressources pillées) — cf. `store.add_player_stats`, alimenté par
  `movement._resolve_battle`/`_resolve_oasis` ;
- **calculées à la lecture** (population, nombre de villages) — sommées sur les
  villages du joueur.

⚠️ **Fidélité** : kirilloid ne modélise pas le classement. La **mécanique** vient de
TravianZ (`GameEngine/Ranking.php`) ; les **chiffres** sont recoupés sur
support.travian.com (« Statistics ») / wiki Fandom (« Ranking ») :

- **Points d'attaque / de défense** = somme de la **consommation de céréales (upkeep)**
  des troupes ennemies **détruites**. Tuer une unité rapporte autant de points que son
  entretien (un Légionnaire d'upkeep 1 = 1 point ; un Bélier d'upkeep 3 = 3 points…).
  L'attaquant cumule des **points d'attaque** pour les défenseurs tués ; le défenseur
  des **points de défense** pour les assaillants tués.
- **Ressources pillées** = total (bois+argile+fer+céréales) rapporté par les
  razzias/attaques victorieuses.
- **Population** = somme des populations de tous les villages du joueur.

Les combats contre la **Nature** (animaux d'oasis) créditent l'attaquant (points
d'attaque) mais personne côté défense (la Nature n'est pas un joueur classé).
"""
from __future__ import annotations

from app import store
from app.data.tribes import Tribe, TRIBE_NAMES_FR
from app.data.units import UNITS


def kill_points(tribe: Tribe, killed: list[int]) -> int:
    """Points gagnés en détruisant `killed` (vecteur [10]) unités de `tribe` : somme
    de leur consommation de céréales (upkeep)."""
    units = UNITS[tribe]
    return sum(killed[i] * units[i].upkeep for i in range(len(killed)))


def credit_battle(att_pid: int | None, att_tribe: Tribe, att_losses: list[int],
                  def_pid: int | None, def_tribe: Tribe, def_losses: list[int],
                  raided: float = 0.0) -> None:
    """Crédite les compteurs de classement à l'issue d'un combat.

    - `att_losses` : assaillants perdus (tués par le défenseur) ⇒ **points de défense**
      au défenseur, valorisés à l'upkeep de la tribu de l'attaquant.
    - `def_losses` : défenseurs perdus (tués par l'attaquant) ⇒ **points d'attaque** à
      l'attaquant, valorisés à l'upkeep de la tribu du défenseur.
    - `raided` : ressources pillées ⇒ créditées à l'attaquant.
    """
    off_pts = kill_points(def_tribe, def_losses)   # l'attaquant a tué ces défenseurs
    def_pts = kill_points(att_tribe, att_losses)   # le défenseur a tué ces assaillants
    store.add_player_stats(att_pid, off=off_pts, raided=raided)
    store.add_player_stats(def_pid, deff=def_pts)


# --- Construction du classement ---------------------------------------------
# (clé, libellé, icône) — l'ordre définit l'affichage des onglets côté UI.
CATEGORIES = [
    ("population", "Population", "👥"),
    ("off", "Points d'attaque", "⚔️"),
    ("def", "Points de défense", "🛡️"),
    ("raided", "Ressources pillées", "💰"),
    ("villages", "Villages", "🏘️"),
]


def _player_rows() -> list[dict]:
    """Une ligne par joueur possédant au moins un village ou un compteur non nul."""
    from app.engine import village as V
    rows = []
    for p in store.all_players():
        pop = 0
        nv = 0
        for vid in store.player_villages(p["id"]):
            v = store.load_village(vid)
            if v:
                pop += V.population(v)
                nv += 1
        off = round(p["off_points"])
        deff = round(p["def_points"])
        raided = round(p["raided"])
        if nv == 0 and off == 0 and deff == 0 and raided == 0:
            continue  # joueur « fantôme » sans village ni activité
        rows.append({
            "player_id": p["id"], "name": p["name"], "is_npc": bool(p["is_npc"]),
            "tribe": p["tribe"], "tribe_name": TRIBE_NAMES_FR.get(Tribe(p["tribe"]),
                                                                  str(p["tribe"])),
            "population": pop, "villages": nv,
            "off": off, "def": deff, "raided": raided,
        })
    return rows


def rankings(human_player_id: int | None = None) -> dict:
    """Classements par catégorie, chacun trié par valeur décroissante et rangé."""
    rows = _player_rows()
    out = []
    for key, label, icon in CATEGORIES:
        ranked = sorted(rows, key=lambda r: r[key], reverse=True)
        table = []
        for rank, r in enumerate(ranked, start=1):
            table.append({
                "rank": rank, "player_id": r["player_id"], "name": r["name"],
                "tribe_name": r["tribe_name"], "is_npc": r["is_npc"],
                "value": r[key], "is_own": r["player_id"] == human_player_id,
            })
        out.append({"key": key, "label": label, "icon": icon, "rows": table})
    return {"categories": out, "human_player_id": human_player_id}
