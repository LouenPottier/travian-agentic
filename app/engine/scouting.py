"""Espionnage (reconnaissance) — mécanique T4.6.

Kirilloid ne modélise **PAS** l'espionnage (cf. combat.py, qui le liste comme « non
géré »). Chiffres et comportement recoupés sur :
  - **support.travian.com** « Troop Actions: Scouting » (autorité de comportement) :
    on scoute en n'envoyant **que des éclaireurs** ; deux modes (ressources / défenses) ;
    si aucun éclaireur de l'attaquant ne survit, **aucune information** n'est renvoyée ;
    un défenseur **sans éclaireur** n'est **pas prévenu** de l'intrusion.
  - **wiki Fandom** « Scouts » (travian.fandom.com/wiki/Scouts).
  - **TravianZ** `GameEngine/Battle.php` (référence de comportement PHP) : valeur de
    reconnaissance de base = **20 / éclaireur**, améliorée par la forge comme le combat
    (`1,007^niveau`) ; l'attaquant ne perd d'éclaireurs **que si** le défenseur en a
    (« détecté ») ; pertes attaquant = `min(1, (déf/att)^immensité)` ⇒ défense **≥**
    attaque ⇒ éclaireurs **anéantis** (aucune info, défenseur notifié).

Approximations documentées : valeur **20/éclaireur** identique en attaque et en défense
(TravianZ ; le vrai jeu distingue forge/armurerie, non modélisées séparément ici — on
réutilise l'unique forge du village) ; **fossé (water ditch)** non modélisé ; la
**cachette** protège l'affichage des ressources espionnées mais n'est **pas encore
branchée sur le butin** (cf. combat, même statut « à raffiner »).
"""
from __future__ import annotations

from app.data import buildings as BLD
from app.data.buildings import B
from app.data.units import UNITS
from app.engine import combat as C

# Valeur de reconnaissance de base d'un éclaireur (TravianZ getDataDefScout : base 20,
# améliorée par la forge). Identique en attaque et en défense (approximation documentée).
SCOUT_VALUE = 20


def scout_indices(tribe) -> list[int]:
    """Index (dans troops[10]) des unités d'éclairage de la tribu."""
    return [i for i, u in enumerate(UNITS[tribe]) if u.is_scout]


def scout_count(tribe, numbers: list[int]) -> int:
    """Nombre total d'éclaireurs présents dans `numbers`."""
    return sum(numbers[i] for i in scout_indices(tribe))


def scout_power(tribe, numbers: list[int], upgrades: list[int]) -> float:
    """Puissance de reconnaissance = Σ éclaireurs × valeur de base améliorée en forge."""
    units = UNITS[tribe]
    total = 0.0
    for i in scout_indices(tribe):
        n = numbers[i] if i < len(numbers) else 0
        if n > 0:
            up = upgrades[i] if i < len(upgrades) else 0
            total += n * C.upgrade(SCOUT_VALUE, up, units[i].upkeep)
    return total


def wall_def_bonus(target) -> float:
    """Bonus défensif de reconnaissance apporté par la muraille (fraction, ex. 0,20).
    Réutilise le bonus défensif de la muraille (support.travian.com : la muraille
    renforce la défense anti-éclaireur ; le fossé n'est pas modélisé)."""
    for s in target.slots.values():
        b = BLD.get(s.building_id)
        if b.slot == "wall" and s.level > 0:
            return b.benefit(s.level).get("def_bonus", 0.0)
    return 0.0


def resolve_losses(off_power: float, def_power: float,
                   n_off: int, n_def: int) -> tuple[float, float, bool]:
    """Renvoie (pertes attaquant, pertes défenseur, détecté).

    - Défenseur **sans** éclaireur (`def_power<=0`) ⇒ **non détecté** : aucune perte,
      information complète renvoyée (comportement support.travian.com / TravianZ).
    - Sinon détecté : pertes selon le rapport de puissance, exposant d'immensité (comme
      le combat). Défense **≥** attaque ⇒ éclaireurs attaquants **anéantis** (aucune
      info) ; symétriquement les éclaireurs défenseurs fondent si l'attaque domine.
    """
    if off_power <= 0:
        return 1.0, 0.0, def_power > 0
    if def_power <= 0:
        return 0.0, 0.0, False
    imm = C.immensity(max(1, n_off + n_def))
    off_loss = min(1.0, (def_power / off_power) ** imm)
    def_loss = min(1.0, (off_power / def_power) ** imm)
    return off_loss, def_loss, True


def gather_intel(target, mode: str) -> dict:
    """Renseignement renvoyé par une mission d'espionnage réussie.

    Toujours : les **troupes présentes** (renforts inclus — notre modèle les fusionne
    dans `troops`). Selon le mode (support.travian.com) :
      - "res" : ressources présentes + part **protégée par la cachette** (par type).
      - "def" : bâtiments **défensifs** (muraille, résidence/palais, place de
        rassemblement).
    """
    from app.engine import village as V
    units = UNITS[target.tribe]
    troops = [{"index": i, "name": units[i].name, "count": int(c)}
              for i, c in enumerate(target.troops) if c > 0]
    intel = {"mode": mode, "troops": troops}
    if mode == "def":
        levels = V.building_levels(target)
        wall = None
        for s in target.slots.values():
            b = BLD.get(s.building_id)
            if b.slot == "wall" and s.level > 0:
                wall = {"name": b.name, "level": s.level}
        intel["defenses"] = {"wall": wall,
                             "residence": levels.get(B.RESIDENCE, 0),
                             "palace": levels.get(B.PALACE, 0),
                             "rally": levels.get(B.RALLY_POINT, 0)}
    else:
        intel["resources"] = [round(r) for r in target.resources]
        intel["protected"] = V.cranny_protection(target)
    return intel
