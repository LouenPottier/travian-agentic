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
- **Coûts non modélisés par kirilloid → approximations documentées** (même statut que les
  marchands, cf. `tribes.py`) : recherche en **académie** = coût d'entraînement de l'unité
  (temps = `research_time` kirilloid) ; amélioration en **forge** = coût × niveau visé ;
  **pièges** du trappeur = `village.TRAP_COST`/`TRAP_TIME`. Le niveau d'académie requis par
  unité n'est pas modélisé (académie niv 1 suffit). À raffiner si une source fiable apparaît.
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
- ✅ **Interfaces de bâtiments** : chaque bâtiment s'utilise via sa **modale** (clic), qui
  affiche son **effet au niveau courant → niveau suivant** (`app/engine/effects.py`) puis son
  panneau fonctionnel : caserne/écurie/atelier/résidence → entraînement, marché → commerce,
  place de rassemblement → envoi d'armée + mouvements, **académie → recherche** (débloque
  l'entraînement des unités non basiques), **forge → amélioration** des unités (transmise au
  combat via `movement.py`, plafonnée par le niveau de la forge), **trappeur → pièges**. État
  persisté (`research`/`upgrades`/`traps` + files). Verrouillé par `tests/test_buildings.py`.
  ✅ **Pièges en combat** (`movement._resolve_battle`, verrouillé par `tests/test_buildings.py`) :
  capture **pré-combat** des assaillants (modèle vrai Travian / TravianZ, kirilloid muet) — jusqu'à
  `village.free_traps` unités retenues (réparties au prorata, `distribute_traps`), le surplus combat ;
  capture totale ⇒ pas de bataille. Les capturés deviennent **prisonniers** du défenseur (`Village.prisoners`,
  un piège occupé par unité), libérables (`/prisoners/{i}/release` ⇒ retour immédiat au propriétaire,
  approximation). UI : prisonniers + bouton « Libérer » dans la modale du trappeur.
  ✅ **Grande caserne / grande écurie** (`village.GREAT_TRAINERS`/`base_producer`, verrouillé par
  `tests/test_buildings.test_great_barracks_trains`) : forment les **mêmes unités** que la caserne/écurie
  de base, à **coût ×3** (vrai Travian ; kirilloid muet), via leur **propre file** et leur propre niveau
  (réduction de temps indépendante ⇒ entraînement en parallèle). Leur modale ouvre désormais le panneau
  d'entraînement (en plus de l'amélioration).
- ✅ **Expansion 2ᵉ village** (`app/engine/expansion.py`, verrouillé par `tests/test_expansion.py`) :
  **points de culture** cumulés par *joueur* (table `players.culture`/`culture_at`, accumulation
  paresseuse = somme des `culture_at` des bâtiments de tous ses villages, ×vitesse serveur),
  **emplacements d'expansion** (résidence `slots2` niv 10/20, palais `slots3` niv 10/15/20,
  cumulés), **entraînement de colons** (résidence, unités `is_settler` déjà existantes),
  **envoi de 3 colons** sur une vallée libre (mouvement `kind="settle"`, `target_id` NULL) →
  fondation à l'arrivée (`found_on_arrival`, colons consommés, nouveau village non-capitale via
  `settled_village` qui réutilise `world.layout_fields`). ⚠️ **Kirilloid ne modélise PAS le
  seuil de points** : `CULTURE_NEEDED` = approximation documentée (tables communautaires T4.6),
  extrapolation cubique au-delà. API `/api/expansion` + `/api/village/{id}/settle` ; UI : modale
  de vallée libre (carte) → « Fonder un village ici » si culture + slot dispo.
- ✅ **Héros / aventures / objets** (`app/engine/hero.py`, `app/data/items.py`, verrouillé par
  `tests/test_hero.py`) : **un héros par joueur** (table `heroes`, blob JSON). Santé 0..100
  (régén paresseuse base+objets), **XP→niveau** (`xp_threshold`, 4 points/niveau), **attributs**
  (force de combat, bonus att./déf. d'armée, production de ressources créditée au village
  d'attache). **Combat** : le héros défend à la maison (force + bonus déf.) et peut accompagner
  une attaque/razzia (`send(..., with_hero=True)`, drapeau `movements.hero`) ; il gagne de l'XP
  (unités tuées), perd de la santé (pertes de son camp), meurt à 0 (résurrection au manoir du
  héros : coût + délai). **Aventures** : apparaissent au fil du temps (table `adventures`,
  `replenish_adventures`), le héros y part (durée = trajet), récompense **pré-tirée à l'envoi**
  (XP, ressources, perte de santé, drop d'objet) appliquée au retour. **Objets** : 6 emplacements
  d'équipement + consommables (soin), bonus cumulés dans `effective()`. Intégration combat via
  champs neutres ajoutés à `combat.Off`/`combat.Place` (pas de régression `test_combat`). ⚠️
  **Kirilloid ne modélise RIEN de tout cela** → toutes les valeurs (santé, XP, force/point,
  bonus/point, production/point, butin, objets) sont des **approximations documentées** en tête
  de `hero.py`/`items.py`. API `/api/hero[...]` ; UI : onglet 🦸 Héros (état, attributs,
  équipement/sac, aventures, résurrection) + cases « envoyer le héros » (rassemblement & carte).
- ✅ **Occupation d'oasis** (`app/engine/oasis.py`, verrouillé par `tests/test_oasis.py`) :
  une fois les animaux nettoyés, **annexer** une oasis à un village pour créditer son bonus
  de prod (additif, % de la base, dans `village.gross_production`). Emplacements d'oasis =
  **manoir du héros** niv 10/15/20 → 1/2/3 (`formulas.slots3`) ; oasis annexable seulement si
  **nettoyée** (0 animal), **en portée** (Tchebychev ≤ `OASIS_RANGE`=3), **libre** (non déjà
  occupée). Stockée sur `Village.oases` (+ colonne `tiles.owner_id`). API
  `/api/village/{id}/oasis/occupy|abandon` ; UI : modale de case (carte) → « Annexer depuis
  {village} » / « Abandonner », marqueur sur la grille, récap dans la modale du manoir. ⚠️
  **Kirilloid ne modélise PAS l'occupation** → seuils de manoir et portée = approximations
  documentées (`oasis.py`). ✅ **Re-conquête** d'une oasis ennemie (`oasis.conquer`, appelé depuis
  `movement._resolve_oasis`, verrouillé par `tests/test_oasis.test_reconquer_enemy_oasis`) : une
  **attaque/razzia victorieuse** (case nettoyée + troupes survivantes) détache l'oasis de son
  détenteur (notifié) et la rattache à un village éligible de l'attaquant (`best_eligible_village`,
  préférence à l'origine ; sinon oasis seulement libérée). UI : récap dans le rapport + indice dans
  la modale de case. L'occupation *pacifique* (`occupy`) reste réservée aux oasis libres.
- ⬜ **Combat héros — affinages** : le héros n'est embarqué que depuis son village d'attache
  (pas de relais entre villages) ; pas encore de monture→cavalerie en combat, ni de prise en
  compte des objets de vitesse sur la durée de trajet de l'armée. À raffiner.

Puis Phase 4 (API agents : schéma observation/action, bot scripté, puis agents LLM via le
Claude Agent SDK).
