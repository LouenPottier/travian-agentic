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

### Hiérarchie des sources (à respecter pour toute nouvelle mécanique)
Deux sources, deux rôles **distincts** — ne pas les confondre :
1. **Chiffres & formules → kirilloid (modèle `t4`)** = source de vérité unique. Coûts, prod,
   capacités, vitesses, temps. Tout nombre se recoupe ici.
2. **Mécanique, flux, cas limites → TravianZ (PHP, lecture seule)** = référence de *comportement*
   pour ce que kirilloid ne couvre pas (jeu complet : ordre de résolution, distribution du butin,
   empilement des renforts, points de culture → expansion, Natars / Merveille du Monde, artefacts,
   alliances). Dépôt : `github.com/Shadowss/TravianZ` (tronc **T3.6**, le plus lisible ; logique du
   marché/expansion ≈ identique à T4.6). Forks T4.x si besoin : `advocaite/TravianT4.6`,
   `Travium/Travium` (T4.4/4.6), `dsteindo/TravianT4_PHP7`.
3. **Conflit entre les deux → le vrai Travian tranche**, et on documente l'écart (cf. écarts déjà
   corrigés ci-dessus).
4. **Garde-fous** : (a) TravianZ est un *clone* (ère T3.6) avec ses propres bugs et sa dérive de
   version → jamais oracle de chiffres, jamais copier-coller (archi PHP+MySQL+cron ≠ Python+SQLite
   +sim paresseuse : on retraduit « le cron fait X toutes les N s » en « au passage de la date,
   appliquer X »). (b) Toute mécanique reprise de TravianZ → **verrouillée par un test** avant de
   passer à la suite.

## Architecture
```
app/data/     fidélité : formulas, buildings, units, tribes (tables T4.6)
app/engine/   village.py (production paresseuse, construction, entraînement),
              combat.py (formule exacte), movement.py (trajets, raids, rapports),
              world.py (carte : génération déterministe vallées/oasis, garnisons Nature)
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
Phase 3 en cours (livrée incrémentalement) :
- ✅ **Carte** : monde déterministe (`world.py`, seed fixe, rayon `WORLD_RADIUS`, ~30 % d'oasis),
  cases vallées (distribution de champs) ou oasis (8 types de bonus, emoji distinct, garnison
  d'animaux Nature), persistées en table `tiles`. API `/api/map` (viewport) et `/api/tile/{x}/{y}`.
  UI : onglet Carte (grille navigable, emoji par type d'oasis, clic → détail, « Voir le village »).
- ✅ **Attaque depuis la carte** : `movement.send` accepte une cible **village** (`target_id`)
  ou **oasis** (`target_x/y`, `target_id` NULL en base). Combat d'oasis = troupes vs animaux
  (`_resolve_oasis`, pas de butin, garnison réduite, `place.pop=off.pop` ⇒ pas de bonus de moral).
  UI : formulaire d'envoi directement dans la modale de case (razzia/attaque ; renfort villages seulement).
- ✅ **Place de marché** : transfert de ressources vers n'importe quel village (le tien ou un
  autre). Marchands = `movement.send_resources` ; nombre de marchands = **niveau du marché**,
  capacité/marchand par **tribu** (`tribes.MERCHANT_CAPACITY` : Rom 500 / Teu 1000 / Gau 750),
  +10 %/niveau de comptoir ; vitesse marchand par tribu (`MERCHANT_SPEED`). ⚠️ **Kirilloid ne
  modélise PAS le commerce** → chiffres pris du vrai Travian (cf. tribes.py), capacité **non**
  multipliée par la vitesse serveur (stocks à l'échelle de base ⇒ marchands aussi, équilibre réel).
  Marchands indisponibles jusqu'au retour à vide ; surplus perdu au-delà du stockage cible.
  API `/api/village/{id}/market` + `/api/village/{id}/trade` ; UI : panneau « Place de marché »
  sous l'Armée. Verrouillé par `tests/test_trade.py`. Routes commerciales (récurrentes) : à faire.
- ⬜ **Expansion 2ᵉ village** : points de culture (les bâtiments ont déjà `cp`/`culture_at`),
  slots de résidence/palais (`residence_benefit`/`palace_benefit`), entraînement de colons
  (déjà unités `is_settler`), fondation sur une vallée libre (réutiliser `layout_fields`).
- ⬜ **Occupation d'oasis** : une fois les animaux nettoyés (déjà jouable), occuper l'oasis
  (via manoir du héros) pour rattacher son bonus de prod à un village. Reste à faire.

Puis Phase 4 (API agents : schéma observation/action, bot scripté, puis agents LLM via le
Claude Agent SDK).
