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
  formule de capacité de cachette buguée, **temps de construction trappeur/manoir du héros**
  (kirilloid écrit `time(2000,0)`/`time(2300,0)` : le `0` est mis dans l'argument *k* au lieu de
  *b* ⇒ temps négatif/nul dès le niveau 2 ; corrigé en `make_time(a, 1.16, 0)`, recoupé sur le
  vrai Travian — manoir niv 1→20 à BP 10 : 27:30 … 7:42:20). Si tu repères un autre écart, corrige
  vers le **vrai Travian** et documente-le.
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
   corrigés ci-dessus). **Pour consulter « le vrai Travian » — et dès qu'on a un doute sur une
   fonctionnalité ou qu'on ne trouve l'info ni chez kirilloid ni dans TravianZ — vérifier la doc
   officielle et le wiki communautaire (par ordre) :**
   - **`support.travian.com`** (centre d'aide officiel Travian Legends) = autorité de comportement.
   - **`travian.fandom.com/wiki`** (wiki communautaire Fandom) en complément.
   Ne **jamais** trancher une mécanique « de mémoire » : recouper sur ces sources et **citer l'URL**
   dans le commentaire/la doc de l'écart (cf. la garnison d'oasis, recoupée ainsi).
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
  sous l'Armée. Verrouillé par `tests/test_trade.py`. ✅ **Routes commerciales récurrentes**
  (table `trade_routes`, `movement.create_trade_route`/`_process_trade_routes_locked`, verrouillé
  par `tests/test_trade.test_trade_route`) : envoi périodique automatique d'une cargaison fixe
  vers un village, déclenché au passage de `next_run` (cadence en heures ÷ vitesse serveur),
  réutilisant la machinerie marchands de `send_resources` ; cycle **sauté** (réessai au suivant)
  si ressources/marchands indisponibles. API `/api/village/{id}/trade_route[s]` (POST/GET/DELETE) ;
  UI : section « Routes commerciales récurrentes » dans la modale du marché.
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
  **Kirilloid ne modélise RIEN de tout cela** → la plupart des valeurs (santé, force/point,
  bonus/point, butin, objets) sont des **approximations documentées** en tête de `hero.py`/`items.py`.
  Exceptions **fidèles au vrai T4** (recoupées) : la **production de ressources** = +10/h d'une
  ressource par point, ou +3/h de **chaque** ressource par point si « réparti » (`PROD_PER_POINT_*`) ;
  et la **production cesse quand le héros n'est pas présent** (`tick` ne crédite que `status=="home"`,
  pas en aventure/au combat/mort). API `/api/hero[...]` ; UI : onglet 🦸 Héros (état, attributs,
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
  ⚠️ **SIMPLIFICATION ACTUELLE INFIDÈLE — garnison d'oasis non modélisée** : `movement.send`
  interdit *tout* renfort d'oasis (« On ne peut pas renforcer une oasis. ») et
  `_resolve_oasis`/`oasis.conquer` ne font qu'un combat **troupes-vs-animaux** ⇒ une oasis occupée
  par un joueur se reprend en battant des *animaux*, alors qu'elle devrait être défendue par la
  **garnison** de son propriétaire. Le vrai T4.6 (cf. support.travian.com / wiki) : oasis **libre**
  = animaux seulement, pas de garnison ; oasis **occupée par toi** = tu **peux** y stationner des
  renforts (nourris par le village d'attache, rapatriables au rassemblement, **sans** bonus
  mur/résidence) ; **reprendre** une oasis ennemie = **attaque normale** (pas razzia) qui **détruit
  la garnison** stationnée. → à corriger (cf. trous Phase 3).
- ✅ **Siège câblé** (`movement.py`, verrouillé par `tests/test_buildings.test_siege_wiring`) :
  le calcul existait déjà dans `combat.py` (`result.wall` béliers, `result.buildings` catapultes) ;
  il est maintenant **câblé**. `send(..., targets=[ids de bâtiments])` conserve les cibles de
  catapulte (colonne `movements.targets`) ; à la résolution d'une **attaque de village**,
  `_resolve_battle` résout chaque id en emplacement concret (plus haut niveau, hors muraille,
  via `_cata_target_slots`), remplit `off.targets` avec les niveaux courants, puis **réapplique**
  `res.wall` (béliers) et `res.buildings` (catapultes) au défenseur — niveaux **persistés** et
  récapitulés dans les rapports (`siege` : mur + bâtiments, avant→après). Nombre de cibles
  distinctes = `catapult_target_limit` (atelier ≥ niv 20 ⇒ 2, sinon 1). Le siège n'agit **qu'en
  attaque normale**, jamais en razzia (fidélité Travian / TravianZ : les engins ne détruisent rien
  lors d'un pillage). API : `SendArmy.targets` ; `serialize` expose `siege` (limite + liste de
  bâtiments visables, `CATA_TARGET_BUILDINGS`) si le village abrite des catapultes. UI : sélecteur(s)
  « 🎯 Catapultes — cible(s) » dans les deux formulaires d'envoi (rassemblement & carte, village
  uniquement), récap « 🧱/💥 avant→après » dans les rapports.
- ✅ **Conquête de village (loyauté + chefs)** (`app/engine/conquest.py`, verrouillé par
  `tests/test_conquest.py`) : chaque village a une **loyauté** 0..100 (`Village.loyalty`,
  persistée) qui **régénère** paresseusement dans `village._accumulate` (+⅔ × niveau du
  **bâtiment d'administration** = max résidence/palais, par heure ; ×vitesse serveur ;
  sans résidence/palais ⇒ pas de régén). Un **administrateur survivant** (sénateur/chef/chef
  de clan, `is_chief`) **réduit la loyauté** sur **attaque normale** (jamais razzia), via
  `_resolve_battle` : drop par chef = plage de tribu (Rom 20–30, Teu/Gau 20–25). La baisse
  n'a lieu que si la cible est **éligible** (`conquer_eligible`, évalué **après le siège**) :
  **plus de bâtiment d'administration actif** (résidence/palais détruit dans la même attaque
  suffit), **pas une capitale**, **pas l'unique village** du défenseur, et l'attaquant a
  **culture + emplacement d'expansion** (réutilise `expansion.expansion_status.can_settle`).
  À **0 %** → `conquer_village` : changement de propriétaire, le village **adopte la tribu**
  du conquérant et **n'est plus capitale** ; les **survivants garnisonnent** (administrateurs
  retirés, ils disparaissent) ; **troupes du village conquis perdues** (`troops`+`away`+files,
  mouvements partants supprimés) ; **recherche/forge réinitialisées** ; **mur supprimé** ;
  **bâtiments d'une autre tribu supprimés** ; **oasis annexées libérées** ; **loyauté → 25**.
  ⚠️ **Kirilloid muet** → tous les chiffres viennent de la **doc officielle/wiki** (citée en
  tête de `conquest.py` : support.travian.com, unofficialtravian, travianlibrary). `RESET_LOYALTY`
  (=25) et la non-destruction des renforts stationnés ailleurs = **approximations documentées**.
  Pas de ±5 % de célébration (dépend de l'hôtel de ville, item #2). UI : badge 🏳️ loyauté dans
  l'en-tête + récap loyauté/conquête dans les rapports off/déf (les chefs s'envoient comme des
  troupes via le rassemblement). Les administrateurs s'entraînent **au palais niv 10+** (déjà géré).
- ⬜ **Combat héros — affinages** : le héros n'est embarqué que depuis son village d'attache
  (pas de relais entre villages) ; pas encore de monture→cavalerie en combat, ni de prise en
  compte des objets de vitesse sur la durée de trajet de l'armée. À raffiner.

### Mécaniques Phase 3 restant à implémenter (recoupé code / vrai T4.6 / TravianZ `GameEngine/`)
Par ordre de rentabilité recommandé :
1. ✅ **Conquête de village (loyauté + chefs)** — **fait** (cf. puce ✅ « Conquête de village »
   ci-dessus, `app/engine/conquest.py`, `tests/test_conquest.py`). Reste à brancher le ±5 %
   de célébration une fois l'hôtel de ville fait (item suivant).
2. ⬜ **Hôtel de ville / célébrations** : `TOWNHALL` (id 23) existe (`buildings.py`, `effects.py`)
   mais les **petite/grande célébrations** (points de culture ; la grande débloque le bonus de
   conquête ±5 %/chef) ne sont pas implémentées. À brancher sur la culture déjà gérée
   (`expansion.py`).
3. ⬜ **Alliances** : ambassade = « à venir » (`web/index.html`). Création/adhésion, diplomatie
   (confédération/guerre/NAP) et surtout les **bonus d'alliance T4.6** (philosophie, métallurgie,
   recrutement, commerce…). Réf. TravianZ `Alliance.php`. Dépend d'avoir plusieurs joueurs.
4. ⬜ **Endgame Natars** : **artefacts** (mi-partie, butin sur villages Natars, bonus
   uniques/petits/grands — `effects.py` réserve déjà les emplacements de trésor) et **Merveille du
   Monde** (fin de partie). Réf. TravianZ `Artifacts.php`. Lourd.
5. ⬜ **Annexes** (TravianZ `GameEngine/`) : **farm list** (razzias groupées T4), **messagerie
   joueur-à-joueur** (`Message.php` ; on a les rapports, pas les MP), **classements/statistiques**
   (`Ranking.php`), NPC trader / bourse du marché (à décider si dans le périmètre), médailles,
   protection débutant.
6. ⬜ **Bâtiments spéciaux constructibles mais INERTES** (effet décoratif seulement) : tous ont
   leur table `buildings.py` + un descriptif `effects.py`, mais **aucun effet câblé dans le moteur**
   (`grep B.X` ⇒ 0 usage hors `data`/`effects`). Plusieurs **cassent la fidélité de systèmes déjà
   implémentés** ⇒ corrections de fidélité bon marché, pas du contenu « endgame » :
   - **Arène** (place de tournoi, id 13) : bonus de vitesse des troupes **au-delà de 20 cases**.
     `movement.army_speed` = simple min des vitesses, aucun bonus ⇒ **fausse les durées de trajet déjà codées**.
   - **Tailleur de pierre** (id 33) : durabilité des bâtiments ↑ vs catapultes ⇒ **impacte le siège déjà câblé**.
   - **Grand entrepôt / grand grenier** (id 37/38) : `village.warehouse_capacity`/`granary_capacity`
     ne comptent **que** `WAREHOUSE`/`GRANARY` ⇒ ces deux bâtiments **n'ajoutent rien au stockage**.
   - **Brasserie** (Teuton, id 34) : bonus d'attaque + débloque la **grande célébration** (à traiter
     avec l'item 2). « brew » n'est qu'un *commentaire* dans `combat.py:12`, rien ne l'alimente.
   - **Abreuvoir** (Romain, id 40) : coût/entretien cavalerie ↓ + vitesse cavalerie ↑. Inerte.
   ⚠️ Chiffres à recouper kirilloid (modèle `t4`) ; mécanique/cas limites TravianZ `GameEngine/`.
7. ⬜ **Garnison d'oasis (renfort + défense + reprise)** — corrige une simplification infidèle de
   l'occupation d'oasis déjà livrée (cf. puce « Occupation d'oasis »). Vrai T4.6 (recoupé
   support.travian.com / wiki) : on **peut renforcer une oasis qu'on occupe** (troupes nourries par
   le village d'attache, rapatriables au rassemblement, **sans** bonus mur/résidence) ; une oasis
   **libre** n'a que des animaux ; **reprendre** une oasis ennemie = **attaque normale** (pas razzia)
   qui doit **détruire la garnison** du propriétaire (pas juste les animaux). À faire : lever
   l'interdit `movement.send` pour `kind="reinforce"` vers une oasis **occupée par l'envoyeur** ;
   stocker la garnison (sur le `tile` / `Village.oases`) ; faire défendre cette garnison dans
   `_resolve_oasis` quand l'oasis est occupée (animaux seulement si libre) ; conditionner
   `oasis.conquer` à la destruction de la garnison. Verrouiller par un test.

> Note : la **famine** est déjà faite (`village.py._starve` : grenier vide + prod de blé négative →
> mort de troupes), ne pas la relister comme manquante.

Puis Phase 4 (API agents : schéma observation/action, bot scripté, puis agents LLM via le
Claude Agent SDK).
