# CLAUDE.md — travian-local

Réimplémentation **fidèle** de Travian **T4.6 (Legends classique)** : moteur custom,
UI web jouable + API JSON pour des agents. Voir `README.md` pour le détail et le lancement.

## Règles de travail
- **Français** partout (code, commentaires, doc).
- **Venv** dédié à la racine (`venv/`, gitignoré). Toujours `./venv/bin/python`, `./venv/bin/pip`
  (jamais le Python système). Gros wheels : `TMPDIR=venv/.piptmp ./venv/bin/pip install …`.
- **zsh** : quoter les extras pip (`'uvicorn[standard]'`), sinon le glob avale la commande.
- Lancer : `./venv/bin/uvicorn app.main:app --reload` → http://127.0.0.1:8000/

## Fidélité = priorité absolue
- Les données et formules viennent de **kirilloid/travian** (modèle `t4`, cascade
  base→t3→t3.1→t3.5→t4). Toute valeur de jeu doit être recoupée avec travian.kirilloid.ru.
- Écarts Kirilloid corrigés (cf. commentaires) : prérequis scierie/briqueterie intervertis,
  formule de capacité de cachette buguée. Si tu repères un autre écart, corrige vers le **vrai
  Travian** et documente-le.
- Le **combat** est validé contre les vecteurs de `t4/combat.spec.ts` (`tests/test_combat.py`) :
  ne pas régresser. Idem `tests/test_raid.py` pour le cycle d'attaque.

## Architecture
```
app/data/     fidélité : formulas, buildings, units, tribes (tables T4.6)
app/engine/   village.py (production paresseuse, construction, entraînement),
              combat.py (formule exacte), movement.py (trajets, raids, rapports)
app/store.py  persistance SQLite (villages en JSON, joueurs, mouvements, rapports)
app/main.py   FastAPI : API REST + sert l'UI ; SERVER_SPEED = vitesse ×N
web/          UI vanilla (vue village isométrique en SVG)
scripts/, tests/
```

## Principes
- **Simulation paresseuse** (pas de tick/seconde) : ressources recalculées à la lecture,
  événements (construction, sortie d'unité, arrivée d'armée) appliqués au passage de leur date.
- **Vitesse serveur** : production ×N, durées ÷N (= temps accéléré). ×100 par défaut pour tester.
- **Visuels** : tuiles isométriques en SVG, images dans `web/img/buildings/b{id}.png`
  (`id` de bâtiment = index de cellule du sprite). Deux sources kirilloid/travian :
  - **Champs de ressources** (id 0-3) : sprite **`t5/buildings.png`** (plus détaillé/joli).
  - **Bâtiments du village** (id ≥ 4) : sprite **`t4/buildings.png`**. ⚠️ Ne *pas* basculer les
    bâtiments en t5 : le sprite t5 (jeu Kingdoms) range les bâtiments dans un **ordre différent**
    et en omet certains (tournoi, résidence, palais), donc `id`≠cellule → numérotation fausse
    (ex. palais affiché à la place de la palissade). Le t4 a `id`=cellule, correct.
  - Repli emoji si l'image manque (`BICON`/`FIELD_ICON` dans `web/index.html`). UI = emoji libres.

## Suite prévue
Phase 3 (carte, marché, expansion 2ᵉ village) puis Phase 4 (API agents : schéma
observation/action, bot scripté, puis agents LLM via le Claude Agent SDK).
