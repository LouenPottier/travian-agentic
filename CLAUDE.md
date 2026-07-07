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
  **pièges** du trappeur = `village.TRAP_COST`/`TRAP_TIME`. **Prérequis de recherche par unité**
  (`units.REQUIREMENTS`, appliqués par `village.unmet_requirements`/`enqueue_research`, verrouillés
  par `tests/test_buildings.test_research_requires_building_levels`) : chaque unité non-basique exige
  un **niveau d'académie ET de bâtiment producteur** minimal (cavalerie avancée → écurie de plus en
  plus haute : Legati/éclaireur écurie 1, tier 2 écurie 3, etc. jusqu'à écurie 10 + académie 15 pour
  Caesaris/Haeduan ; siège : atelier 1 + académie 10, catapulte atelier 10 + académie 15 ;
  **chef/sénateur/chef de clan** (`is_chief`, index 8) → **académie 20**, cf. ci-dessous). ⚠️
  **Kirilloid muet** (champ `r` jamais rempli) → valeurs = **vrai Travian Legends T4** ; extrémités
  cavalerie (écurie 1 / écurie 10) recoupées en direct sur `travian.fandom.com` (Equites Legati,
  Equites Caesaris), paliers intermédiaires = progression canonique Legends (à reconfirmer cellule
  par cellule si l'infobox wiki redevient fetchable).
- **Chef (administrateur) = recherche académie 20** (`village.needs_research` renvoie `True` pour
  `is_chief` + `units.REQUIREMENTS[..][8] = {ACADEMY: 20}`, verrouillé par
  `tests/test_buildings.test_chief_requires_academy_research`) : le sénateur/chef/chef de clan se
  **recherche à l'académie niveau 20** avant d'être **formable en résidence OU au palais** (niv 10+) — il
  n'était auparavant gardé que par le palais 10, disponible d'emblée. Le contrôle « bâtiment producteur
  présent » de `enqueue_research` est **sauté** pour le chef (son `producer` = résidence). Recoupé
  `travian.fandom.com` « Senator/Chief/Chieftain » (Academy 20). Le **colon** (`is_settler`), lui, ne se
  recherche pas.
- ⚠️ **Correctif de fidélité T4.6 — les administrateurs se forment en RÉSIDENCE aussi, pas seulement
  au palais** (`village.CHIEF_TRAINERS = (RESIDENCE, PALACE)`, verrouillé par
  `tests/test_buildings.test_settler_chief_training_gating`). L'ancien code les réservait au **palais
  uniquement** avec la justification « la résidence sert à fonder, pas à conquérir » : **faux en T4.6**
  (croyance héritée de la Travian 3.6). **support.travian.com** « The Palace, Residence and Command
  Center » liste la **Résidence → « Trains administrators: Yes »**. En outre, **un emplacement
  d'expansion vaut 3 colons OU 1 administrateur** (support.travian.com « Expansion Slots » : *« Each of
  these can train 3 settlers or 1 administrator. You also need at least one free expansion slot »*) :
  former un chef **consomme un emplacement** (vivier partagé avec les colons), gardé par
  `expansion.chief_training_allowance`/`chiefs_in_progress` et `settler_training_allowance` (qui
  soustrait désormais les chefs), verrouillé par `tests/test_expansion.test_chief_training_needs_slot`.
  La culture reste vérifiée **à la conquête** (comme le colon à l'envoi), pas à l'entraînement.
- **Forge : recherche préalable exigée** (`village.enqueue_upgrade`/`upgradable_units` filtrent par
  `is_researched`, verrouillé par `tests/test_buildings.test_forge_requires_research`) : on n'améliore
  **qu'une unité déjà débloquée en académie** (les unités de base — index 0 — n'exigent pas de
  recherche). La forge continue de plafonner l'amélioration par **son propre niveau** ; s'y ajoute
  désormais ce gate de recherche (chefs et colons restent **non améliorables** en forge). Vrai Travian
  (la forge n'affiche que les unités recherchées).
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
  cumulés), **entraînement de colons** (résidence, unités `is_settler` déjà existantes ;
  **gardé par les emplacements d'expansion** : `expansion.settler_training_allowance` —
  un colon **occupe un slot dès l'entraînement** (3 colons / slot, vrai Travian, cf.
  support.travian.com / wiki « Expansion slots »), donc `enqueue_training` refuse de
  former un colon sans slot libre, en comptant villages fondés + colons en vol +
  colons déjà debout/en file ; verrouillé par `test_settler_training_needs_slot`),
  **envoi de 3 colons** sur une vallée libre (mouvement `kind="settle"`, `target_id` NULL) →
  fondation à l'arrivée (`found_on_arrival`, colons consommés, nouveau village non-capitale via
  `settled_village` qui réutilise `world.layout_fields`). ⚠️ **Kirilloid ne modélise PAS le
  seuil de points** : `CULTURE_NEEDED` = approximation documentée (tables communautaires T4.6),
  extrapolation cubique au-delà. API `/api/expansion` + `/api/village/{id}/settle` ; UI : modale
  de vallée libre (carte) → « Fonder un village ici » si culture + slot dispo.
  ✅ **Réservation des fondations en vol** (`store.pending_settlements`, exposé par
  `expansion_status`, verrouillé par `test_expansion.test_pending_settlement_reserves_slot`/
  `test_failed_settlement_frees_slot`) : chaque train de colons `settle` en phase aller
  **réserve** un emplacement d'expansion **et** le palier de culture du village qu'il fondera,
  tant qu'il n'est pas arrivé. Sans ça (slot/culture consommés seulement à l'arrivée), on
  dépassait son quota en lançant plusieurs colons en parallèle — infidèle. Une fondation
  **échouée** (case prise/non-vallée) repasse en phase « back » ⇒ libère aussitôt le slot.
  Cas **2 joueurs sur la même vallée** : déjà fidèle (résolution sérialisée sous `_PROCESS_LOCK`,
  tri `due_movements` par `arrive_at` ⇒ premier arrivé fonde, le second rentre « Fondation impossible »).
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
  ✅ **Garnison d'oasis** (cf. item #7 ci-dessous, verrouillé) : on peut **renforcer une oasis qu'on
  occupe** (garnison stockée sur `Village.oases`, défend dans `_resolve_oasis` sans bonus
  mur/résidence) ; une oasis **libre** n'a que des animaux ; **reprendre** une oasis ennemie =
  **attaque normale** (jamais razzia) qui doit **détruire la garnison**. `abandon` rapatrie la
  garnison. ⚠️ Simplification documentée : la garnison d'oasis ne consomme pas de céréales.
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
- ✅ **Espionnage / reconnaissance** (`app/engine/scouting.py`, verrouillé par
  `tests/test_scouting.py`) : mission `kind="scout"` (`movement.send`/`_resolve_scout`)
  n'embarquant **que des éclaireurs** (`is_scout`), vers un **village** uniquement, sans
  héros/butin/siège. Deux modes (`scout_mode`) : **"res"** (ressources présentes + part
  protégée par la cachette + troupes) ou **"def"** (muraille, résidence/palais, place de
  rassemblement + troupes). Combat de reconnaissance : puissance = **20/éclaireur**
  (améliorée par la forge comme le combat), moral côté attaquant, **muraille** côté
  défenseur ; l'attaquant ne **perd** d'éclaireurs **que si** la cible en abrite
  (« détecté ») — un défenseur **sans** éclaireur n'est **pas prévenu** ; défense **≥**
  attaque ⇒ éclaireurs **anéantis** (aucune info, défenseur notifié `vu=False`). Survivants
  rentrent à vide. **Cachette** : `village.cranny_protection` (×2 Gaulois) alimente le
  rapport (pas encore le butin — à raffiner, cf. combat). ⚠️ **Kirilloid muet** → valeur
  20/éclaireur, exposant d'immensité et absence de fossé (water ditch) = **approximations
  documentées** recoupées **support.travian.com « Troop Actions: Scouting »** / wiki Fandom
  « Scouts » / **TravianZ** `GameEngine/Battle.php` (cf. en-tête `scouting.py`). API :
  `SendArmy.kind="scout"` + `scout_mode` ; UI : option « 🔍 Espionnage » + sélecteur de mode
  dans les deux formulaires d'envoi (rassemblement & carte, village), rapports `scout_off`/
  `scout_def`.
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
  **bâtiments d'une autre tribu supprimés** ; **oasis annexées libérées** ; **célébration en
  cours annulée** ; **routes commerciales & liste de fermes partant d'ici purgées** (sinon
  zombies retentées pour l'ancien proprio / héritées par le nouveau) ; **loyauté → 25**.
  ✅ **Héros du défenseur rattaché à la cible** : il **meurt** à la conquête (fidélité,
  support.travian.com / unofficialtravian « Conquering Villages ») et se **re-rattache à un
  village survivant** (préférence capitale, `conquest.pick_surviving_home`) pour pouvoir y
  ressusciter — géré dans `movement._resolve_battle` (après `H.save(def_hero)`, pour ne pas
  écraser la sauvegarde du combat) ; rapport déf. « 💀 héros perdu ». Verrouillé par
  `test_conquest.test_conquest_kills_homed_hero_and_purges_state`.
  ⚠️ **Kirilloid muet** → tous les chiffres viennent de la **doc officielle/wiki** (citée en
  tête de `conquest.py` : support.travian.com, unofficialtravian, travianlibrary). `RESET_LOYALTY`
  (=25) et la non-destruction des renforts stationnés ailleurs = **approximations documentées**.
  Bonus **+5 %/chef de grande fête** branché (cf. hôtel de ville ci-dessous). UI : badge 🏳️ loyauté dans
  l'en-tête + récap loyauté/conquête dans les rapports off/déf (les chefs s'envoient comme des
  troupes via le rassemblement). Les administrateurs s'entraînent **en résidence ou au palais niv 10+**
  (correctif de fidélité T4.6, cf. puce dédiée ci-dessus ; 1 emplacement d'expansion = 3 colons OU 1 chef).
- ✅ **Hôtel de ville / célébrations** (`app/engine/celebration.py`, verrouillé par
  `tests/test_celebration.py`) : **petite fête** (hôtel de ville niv 1+) et **grande fête**
  (niv 10+) générant des **points de culture**. Coût **fixe** + **durée par niveau** d'hôtel
  de ville (÷ vitesse serveur), portés de **TravianZ** `GameEngine/Data/cel.php` (mécanique
  que kirilloid ne couvre pas). Points accordés = **production de culture/jour plafonnée**
  (village ≤ 500 pour la petite, compte ≤ 2000 pour la grande — **vrai Travian**, cf.
  support.travian.com ; écart documenté avec le forfait 500/2000 de TravianZ), **figés au
  lancement** (approx. documentée) et **crédités à la fin** par récolte paresseuse
  (`harvest_completed` appelée depuis `expansion.accumulate_culture`, crédit au total de
  culture du *joueur*). État persisté sur `Village.celebration` (`{type, ends_at, cp}`) ;
  une seule fête à la fois par village. La **grande fête active** dans le village d'**origine**
  des chefs ajoute **+5** points de loyauté retirés par administrateur
  (`conquest.GREAT_CELEBRATION_BONUS`, branché dans `movement._resolve_battle`). API
  `/api/village/{id}/celebration` (GET / POST `/{type}`) ; UI : panneau « Célébrations » dans
  la modale de l'hôtel de ville ; `serialize` expose la fête en cours.
- ✅ **Héros en assistance + changement de rattachement** (`movement.send`/`_process_due_locked`,
  verrouillé par `tests/test_hero.test_hero_reinforce_rehomes`) : on peut désormais envoyer le
  héros en **renfort** (`with_hero=True`, `kind="reinforce"`) vers **un de ses propres villages** ;
  à l'arrivée il s'y **réinstalle** (nouveau `home_village_id`) ⇒ il y défend, y produit et y
  ressuscite. En transit `status="moving"` (ni production, ni dispo) ; envoi héros **seul**
  autorisé (units à 0 ⇒ trajet à la vitesse du héros). UI : case « Envoyer le héros » du
  rassemblement/carte ouverte au renfort (le formulaire s'affiche même sans troupes si le héros
  est dispo) ; rapport de renfort « 🦸 héros rattaché ici ».
- ✅ **Capitale / palais / champs > 10** (`app/engine/capital.py`, `village.effective_max_level`,
  verrouillé par `tests/test_capital.py`) — fidélité recoupée sur **support.travian.com**
  (« Capital Village ») : (a) seuls les **champs de ressources de la capitale** dépassent le
  niveau 10 (hors capitale, `enqueue_build`/serialize plafonnent à 10) ; (b) **un seul palais par
  compte** et **palais ⇄ résidence exclusifs** dans un même village (`available_buildings` +
  `account_has_palace`, calculé par `main._account_has_palace`) ; (c) **changer de capitale** se
  fait depuis le **palais** (niv ≥ 1) via `capital.make_capital` — l'ancienne capitale est
  rétrogradée et ses **champs > 10 ramenés à 10** (sans remboursement, fidèle) ; (d) **bâtiments
  incompatibles retirés au changement** (`capital._drop_incompatible`, support.travian.com :
  « If your new capital has Great Barracks or Great Stable, those will be removed ») — la
  **nouvelle** capitale perd ses bâtiments `non_capital` (grande caserne/écurie ; ⚠️ **PAS**
  le grand entrepôt/grenier, cf. correctif ci-dessous), l'**ancienne** ses bâtiments
  `capital_only` (tailleur de pierre, brasserie ⇒
  fête de la bière interrompue), files purgées, sans remboursement ; généralisé via les flags
  pour préserver l'invariant de `available_buildings`. API
  `POST /api/village/{id}/make-capital` (+ `is_capital`/`can_make_capital` exposés par `serialize`) ;
  UI : panneau « Capitale » dans la modale du palais.
- ✅ **Démolition de bâtiments** (`village.enqueue_demolish`/`Village.demolition`, verrouillé par
  `tests/test_buildings.test_demolish`) — fidélité recoupée **support.travian.com « Demolishing
  Buildings »** / unofficialtravian / wiki Fandom « Demolition » (kirilloid muet : il ne modélise
  que la construction) : le **bâtiment principal niveau 10** débloque la démolition ; on rase un
  bâtiment **un niveau à la fois** jusqu'au niveau 0 (= destruction ⇒ **emplacement libéré**,
  `del v.slots[...]`), une seule démolition à la fois par village, **indépendante** de la file de
  construction. **Aucun remboursement** (seuls l'entretien/population sont libérés). Non
  démolissables : **champs de ressources**, **bâtiment principal** (qui fournit la fonction) et
  **place de rassemblement**. Événement `"demolish"` appliqué paresseusement dans `tick`.
  ⚠️ **Durée = approximation documentée** (valeur exacte non publiée ; les sources disent « comme
  la construction, selon le niveau du bâtiment principal ») → temps de construction du niveau retiré
  (`demolish_time` = Σ `build_time`, réduction BP + vitesse serveur incluses). API
  `POST /api/village/{id}/demolish/{slot}[?target_level=N]` (N omis ⇒ un niveau ; 0 ⇒ destruction) ;
  `serialize` expose par emplacement `can_demolish` + `demolish_finish_in`/`demolish_target`. UI :
  bouton « 🏚️ Démolir » (avec sélecteur de niveau cible) dans la modale du bâtiment + indicateur
  🏚️ sur la tuile et dans le bandeau des chantiers en cours.
- ✅ **File de construction planifiée (arbitrairement longue)** (`Village.build_plan`/`PlannedBuild`,
  `village.enqueue_build`/`enqueue_new_building`/`cancel_plan` + `_start_ready_builds`/`tick`,
  verrouillé par `tests/test_build_queue.py`) — **choix de dev assumé, écart documenté avec le vrai
  Travian** (qui limite la file — 1/2 créneaux, gold — et **paie à la mise en file**) : on enfile un
  nombre **illimité** d'ordres ; ils démarrent **dans l'ordre** dès qu'un créneau de construction
  (`max_queue`) se libère **et** que les ressources sont là ; les ressources sont **débitées au
  démarrage** (promotion `PlannedBuild → BuildOrder`), pas à l'enfilage ; un ordre est **annulable
  tant qu'il n'a pas démarré** (`cancel_plan`, aucun remboursement puisqu'il n'a rien coûté).
  `enqueue_build` vise le **niveau projeté** (courant + file) +1 ⇒ on peut enfiler plusieurs niveaux
  du même emplacement ; **prérequis évalués contre l'état projeté** (on peut enfiler un prérequis puis
  le bâtiment qui en dépend). **Promotion paresseuse et indépendante du moment de lecture** : `tick`
  est une **boucle d'événements intégrée** (les builds sont dynamiques — une promotion crée une fin de
  construction qui libère un créneau ; le moment d'une promotion = **instant analytique** où les
  ressources franchissent le coût à production courante, `_affordable_delay`), donc un gros `tick` ==
  plein de petits `tick`. Vaut pour **le joueur ET les comptes IA** (même surface HTTP `X-Acting-Player`
  enforced ; l'exécuteur `playbook.py` évalue « ordre accompli » sur le **niveau projeté** pour ne pas
  sur-enfiler ; outil agent `cancel_build`). API : `build`/`construct` renvoient `{started, finish_in|queued}`,
  `POST …/build/cancel/{pos}` annule ; `serialize` expose `build_plan` + par emplacement `planned`/
  `projected_level`/`can_queue`. UI : bouton « ➕ Enfiler → niv N », liste « 🕑 En file » (avec ✕) sous
  les chantiers, marqueur 🕑 sur les tuiles. ⚠️ File **en mémoire** persistée avec le village (survit au
  reload, contrairement aux registres d'agents). ⚠️ **Bug corrigé (boucle infinie de `tick`)** : `cursor`
  est un timestamp Unix (~1,8e9) ; une promotion « finançable dans δ » avec δ **sous la résolution du
  float** (`cursor+δ == cursor`, manque de ressources ~1e-6) figeait la boucle d'événements (jamais
  d'avancée ni de promotion) — exposé par un gros rattrapage (grand trou ×vitesse). Corrigé dans
  `village.tick` : si `cursor+delay <= cursor`, on comble le manque négligeable et on promeut ce tour
  (progrès garanti). Verrou : `test_build_queue.test_no_infinite_loop_on_subresolution_promo_delay`.
- ✅ **Perf du chemin chaud de `tick`** (grosses armées / rejeu de villages) : (a) `village._starve`
  retirait les unités **une par une** en rappelant `troop_upkeep` (→ requête SQLite `crop_multiplier`)
  à chaque unité ⇒ gel sur une armée de dizaines de milliers ; réécrit en **arithmétique pure**
  (coûts par unité + multiplicateur d'artefact calculés une fois). (b) `store.artifacts_owned_by` est
  **caché** (invalidé sur capture/conquête d'artefact) : c'était une connexion SQLite **par appel** de
  `crop_multiplier`, sur le chemin `net_production`→`troop_upkeep` appelé à chaque itération de `tick`.
  (c) `downtime._freeze` ne rejoue que les villages **exposés à la famine** (garnison non vide + blé net
  < 0) ⇒ redémarrage borné (écarte les villages à longue file, coûteux et sans risque de famine).
- ✅ **Peuplement rival avancé** (`app/engine/rivals.py`, verrouillé par `tests/test_rivals.py`) :
  carte **agrandie à `WORLD_RADIUS=150`** (301×301 ≈ 90 k cases, agrandissement **non
  destructif** : `INSERT OR IGNORE` + terrain déterministe) pour loger des **empires rivaux
  frontaliers**. `spawn_rivals` sème (**additif-idempotent par nom** : crée les rivaux dont le nom
  n'existe pas encore, saute les présents — ne supprime/modifie **jamais** un joueur/village existant)
  **~16 rivaux en 3 tiers** (`ALL_RIVALS = CLOSE + MIDS + STRONG + LEGENDS`) : un groupe **`CLOSE`** de
  voisins **collés au joueur** (Crixus/Marbod/Catilina/Divico, `zone="near"` `dist` ~12-22) + **3 légendes**
  (Auguste/Romains, Vercingétorix/Gaulois, Arminius/Teutons — **~10 villages**, capitale sur un
  **15-cropper (1-1-1-15) champs niveau 20**, **armée ~50 k**), **4 « très bons joueurs » (strong)**
  (Scipion, Brennus, Boudica, Ambiorix — **6-7 villages**, capitale **15-cropper champs 14 ⇒ ~12-14 k**)
  et **5 joueurs solides (mid)** (Spartacus, Marius, Ariovist, Sylla, Alaric — **4-5 villages**, capitale
  **9-cropper (3-3-3-9) ⇒ ~2 k**). **De vraies armées sur CHAQUE village** : les **secondaires** sont
  posés sur des **croppers 9/15** (motif `sec_layouts` imposé, `want_layout` forcé si aucune vallée du
  type n'est libre à côté) avec une **forte fraction de budget céréalier** ⇒ **≥ ~1 000 troupes** partout
  (jusqu'à ~3,5 k sur un 15-cropper). Chacun avec un **héros aguerri** + des **routes commerciales
  secondaires → capitale** (blé). **Placement proche ET loin** (`Rival.zone`) : les légendes restent
  **frontalières** (`"far"`, ancrées autour de l'origine), strong/mid sont répartis **dans le voisinage
  du joueur** (`"near"`, ancrés autour de la capitale humaine via `_human_ref`) **et** au loin — le
  joueur a donc des rivaux puissants à sa porte comme à l'horizon. Placés en `is_npc=True` (PNJ de
  peuplement, non pilotés) mais **de tribu jouable** ⇒ **attaquables, pillables et conquérables** (tri du
  classement par population/armée mené par les légendes). ⚠️ **Fidélité** : ce sont des **choix de
  peuplement** (qui/combien/où/tier/composition d'armée), au même statut que le seeding Natars / joueur
  IA ; les *valeurs de jeu* restent celles des tables kirilloid (on ne fait que **poser un état** que le
  moteur produit). **Dimensionnement « armée max tenable »** : la famine (`village._starve`) est ancrée
  sur la production **du village hôte**, donc l'armée stationnée est calée à **`army_frac × (blé brut −
  population)`** (frac 0,60–0,90 selon tier/rôle, toujours < 1 ⇒ **blé net ≥ 0, aucune famine**) ⇒ énorme
  sur un 15-cropper niv 20 (mill+boulangerie +50 %) ; le blé reçu par commerce **remplit le grenier** mais
  ne relève pas ce plafond (limitation documentée du modèle paresseux) ⇒ les routes = **ravitaillement
  tampon/RP**. Câblé dans `main.seed_world` via `_ensure_rivals` (migration douce).
- ⬜ **Combat héros — affinages** : pas encore de monture→cavalerie en combat, ni de prise en
  compte des objets de vitesse sur la durée de trajet de l'armée. À raffiner.

### Mécaniques Phase 3 restant à implémenter (recoupé code / vrai T4.6 / TravianZ `GameEngine/`)
Par ordre de rentabilité recommandé :
1. ✅ **Conquête de village (loyauté + chefs)** — **fait** (cf. puce ✅ « Conquête de village »
   ci-dessus, `app/engine/conquest.py`, `tests/test_conquest.py`). Le bonus **+5 %/chef de la
   grande fête** est désormais branché (cf. item #2, `conquest.GREAT_CELEBRATION_BONUS`).
2. ✅ **Hôtel de ville / célébrations** — **fait** (`app/engine/celebration.py`, verrouillé par
   `tests/test_celebration.py`). Petite fête (hôtel de ville niv 1+) / grande fête (niv 10+) :
   coût fixe + durée par niveau ÷ vitesse serveur (TravianZ `GameEngine/Data/cel.php`). Points
   de culture = production/jour **plafonnée** (village ≤ 500 / compte ≤ 2000, **vrai Travian** ;
   écart documenté avec le forfait TravianZ), **figés au lancement** (approx. documentée) et
   **crédités à la fin** (récolte paresseuse `harvest_completed`, appelée depuis
   `expansion.accumulate_culture` ⇒ crédit au total de culture du *joueur*). État sur
   `Village.celebration` (`{type, ends_at, cp}`, persisté). Une seule fête à la fois par village.
   **Bonus de conquête** : tant qu'une **grande fête** est active dans le village d'**origine**
   des chefs, chaque administrateur retire **+5** points de loyauté (`conquest.GREAT_CELEBRATION_BONUS`,
   branché dans `movement._resolve_battle` via `celebration.great_celebration_active`). API
   `/api/village/{id}/celebration` (GET état, POST `/{type}` lancer) ; UI : panneau
   « Célébrations » dans la modale de l'hôtel de ville + champ `celebration` exposé par `serialize`.
3. ⬜ **Alliances** : ambassade = « à venir » (`web/index.html`). Création/adhésion, diplomatie
   (confédération/guerre/NAP) et surtout les **bonus d'alliance T4.6** (philosophie, métallurgie,
   recrutement, commerce…). Réf. TravianZ `Alliance.php`. Dépend d'avoir plusieurs joueurs.
4. 🟡 **Endgame Natars** — **socle + artefacts faits**, Merveille à venir :
   - ✅ **Villages Natars + carte agrandie** (`app/engine/natars.py`, tribu `Tribe.NATARS`
     + 10 unités dans `app/data/units.py`, verrouillé par `tests/test_natars.py`) :
     6ᵉ tribu **PNJ** (`NPC_TRIBES`, non jouable, non entraînable — `producer=-1` comme
     la Nature). Carte portée à **`WORLD_RADIUS=100`** (201×201, Natars vers le centre),
     agrandie **sans wiper `game.db`** (`seed_world` ré-insère idempotemment via
     `INSERT OR IGNORE` + terrain déterministe ; migration douce `_ensure_natars` pour
     les mondes existants). `spawn_natar_villages` place 16 villages PNJ sur vallées
     libres de l'anneau central (`NATAR_ZONE_INNER..OUTER`), garnison `troops[10]`
     **d'autant plus forte qu'on est proche du centre** (`garrison_for`). Attaquables et
     **pillables** (combat/butin normaux, le défenseur lit déjà `UNITS[target.tribe]`)
     mais **NON conquérables** (garde-fou `conquest.conquer_eligible` sur `NPC_TRIBES`).
     UI : marqueur 🏯 (classe `.vnatar`) sur la carte, `is_natar` exposé par `/api/map`
     et `/api/tile`. **Placement du joueur** : la capitale humaine démarre désormais
     **loin du centre** (`HUMAN_START`, ~rayon 60, hors zone Natar) avec un **2ᵉ village
     proche** ; `_relocate_human_start` (dans `seed_world`) migre les mondes existants
     (capitale encore au centre → déplacée loin, données conservées ; voisin rapproché). ⚠️ **Kirilloid muet** sur villages/garnisons → tailles de garnison =
     **approximation documentée** (calibrée « plus fort au centre », support.travian.com
     « Strongest Natars defenses ») ; **stats de combat des unités Natars** recoupées sur
     le **wiki Fandom** (API MediaWiki, infoboxes — cf. en-tête `units.py`), valeurs
     absentes (Éléphant de guerre, vitesses, Baliste/Empereur/Colon) = approximations
     documentées au même titre que les animaux de la Nature.
   - ✅ **Artefacts** (`app/data/artifacts.py` catalogue, `app/engine/artifacts.py`,
     verrouillé par `tests/test_artifacts.py`) : **8 types × 3 tailles** (petit = effet
     **village**, grand/unique = effet **compte**). Détenus par des **villages Natars
     dédiés** (`spawn_artifact_villages`, table `artifacts`, `holder='natar'`, posés vers
     le centre donc fortement gardés ; seeding idempotent `_ensure_artifacts`). **Capture
     par le héros** (`try_capture`, branché dans `movement._resolve_battle`) : **attaque
     normale** (jamais razzia) menée par le **héros présent & survivant**, **garnison
     vaincue** (défenseurs à 0), **trésorerie du village-artefact détruite** (catapultes,
     `treasury_level(target)==0`) — les villages-artefact Natars ont une trésorerie **niv 20
     dans tous les cas** (même petit artefact ⇒ ~55 catapultes ; vrai T4.6, support.travian.com /
     unofficialtravian « Artefacts »), **et** une **trésorerie vide** assez haute au **village
     d'origine** (`can_store` : niv **10** = petit / **20** = grand·unique, cf.
     `formulas.slots2`) ⇒ l'artefact passe en `holder='player'`, stocké dans cette
     trésorerie. **Effets branchés** (petit → son village ; grand/unique → tout le
     compte) — **magnitudes officielles recoupées** (support.travian.com « Artefact
     Effects » / wiki Fandom ; le **petit** est le plus fort car concentré sur un village) :
     **durabilité des bâtiments** (architecte **petit ×4 / grand ×3 / unique ×5**, multiplie
     `place.dur_bonus`/`wall_durability` dans `_build_place`), **vitesse des troupes**
     (bottes **petit ×2 / grand ×1,5 / unique ×2**, divise le trajet **aller** dans
     `movement.send`), **consommation de céréales** (diète **petit ×0,5 / grand ×0,75 /
     unique ×0,5**, dans `village.troop_upkeep`) et **grand entrepôt/grenier**
     (bâtisseur : `artifacts.great_storage_allowed` **débloque** la construction de
     `GREAT_WAREHOUSE`/`GREAT_GRANARY` dans `village.available_buildings` — petit = son
     village, grand/unique = tout le compte ; vrai T4.6, support.travian.com « Artefact
     Effects »), **temps d'entraînement** (entraîneur `training_multiplier` dans
     `village.enqueue_training` ; magnitude non publiée ⇒ approximation ×0,5/0,66/0,5,
     `numeric=False`), **capacité des cachettes** (cartographe `cranny_multiplier` ×200/100/500
     dans `village.cranny_protection`), **espionnage** (œil de l'aigle `spy_multiplier` ×5/3/10
     sur la puissance de reconnaissance att. **et** déf. dans `movement._resolve_scout`) et
     **artefact du fou** (`data.artifacts.fool_current` : prend un des 6 effets chiffrables
     **au sort** par fenêtre de 24 h de jeu, déterministe par id×fenêtre, résolu dans
     `artifacts._resolve` ⇒ un seul effet actif à la fois ; ⚠️ simplifications documentées :
     **positif seulement** et **portée figée** à sa taille). Le fou exclut « grand entrepôt »
     et lui-même (règle officielle). **Tous les 8 effets sont désormais `wired=True`.** Un
     **village conquis détache** ses artefacts (inactifs, `release_artifacts_of_village`). API
     `/api/artifacts` (owned + carte) + `treasury` exposé par `serialize` ; UI : panneau
     « Trésorerie — artefacts » (modale trésorerie) + récap de capture dans le rapport
     offensif. ⚠️ **Kirilloid muet** → catalogue, magnitudes et seuils = **approximations
     documentées** (support.travian.com « Artefacts » / unofficialtravian / wiki Fandom,
     cf. en-tête `data/artifacts.py`). **Simplifications documentées** : trésorerie **du
     village d'origine** exigée (pas n'importe lequel) ; vitesse appliquée à l'aller seul ;
     limite = nombre d'emplacements de trésor (pas de plafond artificiel « 3 actifs »).
   - ⬜ **Merveille du Monde** (endgame, **lourd**) : plans de construction lâchés par des
     villages Natars spéciaux (capture héros), bâtiment **Merveille** (`WORLD_WONDER`, id 39
     déjà défini) à monter **niv 1→100** = condition de victoire ; vagues d'attaques Natars
     contre les villages-Merveille (mouvement PNJ périodique). Réf. TravianZ `Artifacts.php`
     / endgame. Verrou `tests/test_wonder.py`.
5. 🟡 **Annexes** (TravianZ `GameEngine/`) — **farm list faite**, le reste à venir :
   - ✅ **Farm list (razzias groupées)** (`app/engine/farmlist.py`, table `farm_targets`, verrouillé
     par `tests/test_farmlist.py`) : chaque village (sa place de rassemblement) tient une liste de
     cibles de razzia (village ou oasis) avec un **modèle de troupes** ; « razzia groupée »
     (`raid_all`) envoie un `raid` par cible et **saute** celles aux troupes insuffisantes (réutilise
     `movement.send`, butin/combat inchangés ⇒ aucun nouveau chiffre de jeu). API
     `/api/village/{id}/farmlist` (GET/POST/DELETE `{id}`) + `/farmlist/raid` (POST) ; UI : section
     « 🚜 Liste de fermes » dans la modale du rassemblement (ajouter la cible+troupes saisies,
     retirer, razzia groupée). ⚠️ Simplification : **une liste par village** (pas de listes nommées
     multiples) ; cibles oasis ajoutables via l'API (l'UI rally ajoute les cibles village).
   - ⬜ **Messagerie joueur-à-joueur** (`Message.php` ; on a les rapports, pas les MP).
   - ✅ **Classements/statistiques** (`app/engine/ranking.py`, verrouillé par `tests/test_ranking.py`) :
     onglet 🏆 Classement listant les joueurs par **population** (somme des pop. de villages,
     calculée), **points d'attaque**, **points de défense**, **ressources pillées** (compteurs
     cumulés sur `players.off_points/def_points/raided`, migration douce) et **nombre de villages**.
     Suivi branché dans `movement._resolve_battle`/`_resolve_oasis` via `ranking.credit_battle` :
     **points = upkeep (consommation de céréales) des troupes ennemies tuées** (attaquant ↔
     défenseurs tués, défenseur ↔ assaillants tués), pillage crédité à l'attaquant. La **Nature**
     (animaux d'oasis) crédite l'attaquant mais n'est pas classée (pas de joueur). ⚠️ **Kirilloid
     muet** → mécanique = **TravianZ `Ranking.php`**, chiffres recoupés support.travian.com
     « Statistics » / wiki Fandom « Ranking » (règle upkeep=points, documentée en tête de
     `ranking.py`). API `/api/ranking` ; UI : onglet avec sélecteur de catégorie (médailles 🥇🥈🥉,
     joueur courant surligné). NPC trader / bourse du marché, médailles, protection débutant : à venir.
6. ✅ **Bâtiments spéciaux jadis INERTES — câblés** (verrouillés par `tests/test_special_buildings.py`).
   Chiffres recoupés **support.travian.com / unofficialtravian** (kirilloid muet) ; cf. commentaires
   dans `buildings.py` / `movement.py` / `village.py` / `brewery.py` :
   - **Arène** (place de tournoi, id 13) : `movement._leg_seconds` parcourt les **20 premières
     cases** à vitesse normale puis applique **+20 %/niveau** au-delà (benefit `ARENA`=`percent(20)`,
     niv 20 ⇒ ×5). Appliqué à l'aller **et** au retour (place de tournoi du village d'origine de
     l'armée), pas aux marchands. `travel_seconds`/le trajet du héros prennent un arg `arena`.
   - **Tailleur de pierre** (id 33, capitale) : `_build_place` fixe `place.dur_bonus`/`wall_durability`
     = `1+0,10×niveau` ⇒ catapultes (bâtiments) et béliers (mur) d'autant moins efficaces (le moteur
     `combat.demolish_points/_wall` divise par la durabilité — déjà prévu, jamais alimenté avant).
   - **Grand entrepôt / grand grenier** (id 37/38) : `village._storage` somme désormais
     `WAREHOUSE+GREAT_WAREHOUSE` (resp. `GRANARY+GREAT_GRANARY`), chacun = 3× la capacité ordinaire.
     ⚠️ **Correctif de fidélité T4.6 — ils NE sont PAS `non_capital`** (`app/data/buildings.py`,
     verrouillé par `tests/test_artifacts.test_storage_artifact_gates_great_warehouse` **sur une
     capitale** + `tests/test_capital.test_make_capital_drops_incompatible_buildings`) : ils étaient
     à tort flaggés `non_capital=True` (regroupés avec la grande caserne/écurie) ⇒ **invisibles
     dans la capitale** même avec l'artefact du bâtisseur. **Faux** : le grand entrepôt/grenier
     est **légitime en capitale**, c'est même son **usage premier** — seule la capitale monte ses
     champs > 10, et il faut un grand grenier pour stocker assez de blé afin de monter les **champs
     de blé au-delà du niv 19** (cropper). Recoupé **support.travian.com « Artefact Effects »** +
     wiki Fandom (« impossible to upgrade wheat fields to >level 19 without great warehouses/
     granaries »). Le flag retiré ⇒ ils survivent aussi à un changement de capitale
     (`capital._drop_incompatible` ne les touche plus). Le gate artefact (`great_storage_allowed`)
     reste correct. Seuls la **grande caserne/écurie** demeurent `non_capital`.
   - **Abreuvoir** (Romain, id 40) : `village.unit_upkeep` retire **−1 céréale/h** par cavalier romain
     aux paliers (Equites Legati niv 10, Imperatoris niv 15, Caesaris niv 20) ; `horse_pool_train_factor`
     accélère l'entraînement de la cavalerie de **−1 %/niveau** (appliqué dans `enqueue_training`).
   - **Brasserie** (Teuton, id 34, **capitale uniquement** — flag `capital_only=True` sur le
     bâtiment, recoupé support.travian.com/unofficialtravian « Brewery » ; sans ce flag un Teuton
     pouvait la bâtir hors capitale, corrigé + verrou `test_capital.test_brewery_capital_only`), niv max 10) : `app/engine/brewery.py` — **fête de la
     bière** (coût fixe 3870/1680/215/10900, durée 72 h ÷ vitesse serveur) ⇒ tant qu'active,
     **+1 %/niveau d'attaque pour tout le compte** (`attack_bonus`, branché dans `movement._resolve_battle`
     via `off.bonus`, s'ajoute au bonus du héros). État `Village.brewery_festival` (persisté). API
     `/api/village/{id}/brewery` (GET) + `/brewery/festival` (POST) ; UI : panneau « Fête de la bière »
     dans la modale de la brasserie ; `serialize` expose `brewery`. ⚠️ **Effets secondaires NON
     modélisés** (approx. documentée) : catapultes teutonnes au hasard + persuasion des chefs ÷2.
7. ✅ **Garnison d'oasis (renfort + défense + reprise)** — **fait** (verrouillé par
   `tests/test_oasis.test_oasis_garrison_defends_and_blocks_reconquest`). `movement.send` autorise
   désormais `kind="reinforce"` vers une **oasis occupée par l'envoyeur** (sinon refus « Tu ne peux
   renforcer qu'une oasis que tu occupes »). La garnison vit sur l'entrée `Village.oases`
   (`{"x","y","code","garrison":[10]}`, unités de la tribu du propriétaire ; helpers
   `oasis.oasis_garrison`/`set_oasis_garrison`). `_resolve_oasis` défend avec **les animaux** si
   l'oasis est libre, **la garnison du propriétaire** (sans bonus mur/résidence) si elle est occupée ;
   **reprendre** une oasis ennemie exige de **détruire la garnison** par une **attaque normale**
   (jamais razzia) — alors `oasis.conquer` la rattache à un village éligible. Renfort en vol vers une
   oasis perdue entre-temps ⇒ demi-tour. `abandon` **rapatrie** la garnison au village d'attache.
   API : `/api/tile` expose la garnison (au propriétaire seul) ; UI : la modale de case ouvre le
   formulaire « Renfort (garnison) » sur ton oasis et « Attaque » sur une oasis ennemie nettoyée,
   récap garnison + rapport `reinforce_oasis`. ⚠️ **Simplification documentée** (cf. `oasis.py`) :
   la garnison d'oasis **ne consomme pas** de céréales (le vrai jeu la nourrit au village d'attache ;
   le modèle ne suit pas les contingents par origine, comme les renforts de village).

> Note : la **famine** est déjà faite (`village.py._starve` : grenier vide + prod de blé négative →
> mort de troupes), ne pas la relister comme manquante.

- ✅ **Pause famine + routes pendant les arrêts serveur** (`app/engine/downtime.py`, param
  `village.tick(starve=…)`, verrouillé par `tests/test_downtime.py`) — **choix de dev assumé**
  (« dans le vrai Travian le serveur ne s'éteint jamais ») : la sim est paresseuse, donc un
  redémarrage après un long arrêt rattrapait tout d'un coup ⇒ **famines fantômes** (l'armée d'un
  village au blé net négatif mourait sur tout le trou alors que les **routes commerciales**
  l'auraient nourrie) et routes qui envoyaient une **cargaison de rattrapage**. On met **uniquement**
  ces deux mécaniques en pause sur le laps d'arrêt : **production, file de construction et
  entraînement continuent** (les bâtiments se montent). Détection = **battement de cœur en temps
  mural réel** (`meta.last_alive`, table `meta`, rafraîchi par une tâche de fond
  `heartbeat_loop` **tant que le process tourne** — ce qui distingue « serveur actif mais inactif »
  de « éteint/en veille » ; ⚠️ **pas** le `now` de *jeu* qui peut sauter sans arrêt réel). Un trou
  mural > `GRACE` (120 s) ⇒ `absorb` rejoue **tous** les villages `tick(…, starve=False)` (les troupes
  **ne mangent pas** et ne meurent pas ⇒ grenier sain pour la reprise) — **y compris les rivaux**
  (`engine.rivals`, `is_npc=True` mais **tribu jouable** donc affamables ; Nature/Natars déjà immunisés
  par `_starve`) et fait **glisser**
  `next_run` des routes échues au prochain créneau futur (reprise, sans rafale). Branché en tête de
  `movement.process_due` (import paresseux, sous `_PROCESS_LOCK`) + au démarrage de `main` + tâche de
  fond `@app.on_event("startup")`.

## Phase 4 — agents LLM (en cours)
- ✅ **Macros pilotées par Claude Code** (`app/agents/{tools,macro}.py`, endpoints
  `/api/village/{id}/macro[/stop]`, onglet 🤖 Macro, verrouillé par `tests/test_macro.py`) :
  depuis le site, on lance un **agent LLM** qui gère un village vers un objectif en langage
  naturel (« améliore tous les champs niveau 10 », objectifs militaires, etc.).
  **Cerveau = Claude Code local via le Claude Agent SDK** (`claude-agent-sdk`, pilote le CLI
  `claude` — abonnement, **PAS l'API Anthropic, aucune clé, aucune facturation par token**).
  **« Sans tricher » garanti structurellement** : (a) l'agent ne dispose QUE d'un serveur MCP
  in-process (`tools.py`) dont chaque outil **forwarde vers l'endpoint HTTP joueur existant**
  (self-HTTP `127.0.0.1:8000` → surface identique à l'UI ; coûts/temps/files/loyauté/ownership
  déjà enforced, 403/400 remontés à l'agent) ; (b) les **outils intégrés de Claude Code**
  (Bash/Read/Write/Edit/…) sont **interdits** (`disallowed_tools` + gate `can_use_tool` qui
  ne laisse passer que `mcp__travian__*`) → jamais d'accès direct à `game.db`. Périmètre v1 =
  **tout** (construction, entraînement, recherche, forge, célébrations, marché/routes, envoi
  d'armées, farm list, expansion, oasis) + `wait`/`finish` pour piloter la boucle (sim
  paresseuse ×100 : `wait` dort puis renvoie l'état frais). Garde-fous : `max_turns`, deadline
  wall-clock, `wait` borné, une macro/village, Stop = `interrupt()`. Registre **en mémoire**
  (les macros ne survivent pas au reload uvicorn). Modèles : sonnet (défaut)/opus/haiku.
- ✅ **Agent défenseur PAR VILLAGE, piloté à la main** (`app/agents/defender.py` +
  `app/engine/situation.py` + `app/agents/playbook.py`, endpoints
  `/api/village/{id}/defender/{wake,unplug,stop}` + `GET .../defender`, `/api/agent/{situation,players}`,
  section « 🛡️ Défenseur IA » de l'onglet 🤖, verrouillé par `tests/test_defender.py` +
  `tests/test_playbook.py`). Vise les villages d'un **vrai compte IA** (semé près du joueur
  humain, **`players.agent=1`**, Gaulois pour la défense/pièges ; migration douce
  `_ensure_agent_player`, distinct de `is_npc`) — les villages humains, eux, se pilotent par
  les macros. **Cerveau = Claude Code local** (Claude Agent SDK, abonnement, pas d'API payante).
  - **Modèle « un tour puis sommeil » (choix utilisateur, pas de réveil auto)** : l'agent ne se
    réveille **jamais** seul. Trois commandes indépendantes par village (registre en mémoire
    clé=`village_id`) : **Réveiller** = un seul tour de LLM (`_turn` : observe → (re)pose la
    pile d'ordres via `set_plan` → `finish(note)`, note persistante) puis sommeil ;
    **Débrancher le LLM** = interrompt un tour en cours **sans toucher la pile** (`unplug`, garde
    `playbook`) ⇒ l'exécuteur continue ; **Arrêter** = débranche **et** vide la pile
    (`stop` → `playbook.clear_village`). Verrou : `test_unplug_keeps_stack_stop_clears`.
  - **Contexte LLM minuscule = 2 couches** (retour utilisateur : minimiser les tokens) :
    (1) **Exécuteur d'ordres permanents `playbook.py` — 0 LLM** : la pile d'ordres déclaratifs
    (`build`/`construct` slot→niveau, `train` unité jusqu'à N, `traps`, `research`) est réalisée
    **automatiquement** par une tâche asyncio de fond dès que la garde passe (ressources/file),
    **via la surface HTTP enforced** (au plus une action réussie/cycle ; refus ⇒ ordre laissé
    pour plus tard). C'est ce qui fait tourner le compte **sans LLM** entre deux réveils.
    **Vaut aussi pour les macros** (outil `set_plan` ajouté à `ALL_TOOLS`). (2) **Un tour de
    LLM** ne voit qu'un **digest compact** (`situation.build_digest`, ~centaines de tokens/compte,
    **jamais `serialize()`**) + sa note ⇒ contexte borné et constant.
  - **Identité par requête** (Partie clé) : la surface joueur était épinglée au global
    `HUMAN_PLAYER_ID` ; on résout désormais un **joueur agissant** par requête via un
    `ContextVar` posé par une dependency FastAPI depuis l'en-tête **`X-Acting-Player`** (repli
    sur `HUMAN_PLAYER_ID` ⇒ navigateur humain inchangé). `acting_player()` remplace le global
    dans **tous les handlers** ; l'ownership `v.player_id != acting_player()` reste imposé (403)
    ⇒ l'agent (et l'exécuteur, `tools.set_acting_player`) n'agit que sur les villages de SON
    compte, **même surface enforced** (« sans tricher » étendu à un autre compte).
  - **Parité d'observation** : les menaces du digest n'exposent QUE ce que l'UI montre déjà
    (kind/ETA/effectif total, **jamais la composition ni l'identité** de l'attaquant). Nouveaux
    outils défensifs `get_situation`/`get_reports`/`get_trapper` (`get_reports` comblait le plus
    gros manque : l'agent ne pouvait pas lire ses rapports). Sous-ensemble d'outils défensif
    (`DEFENSIVE_TOOL_NAMES`) : lecture + fortification + renfort + `set_plan`, pas d'attaque.
    `situation.should_wake` existe (menace/rapport/plan vide) mais **n'est pas auto-déclenché**
    en v1 (réveil manuel) — dispo pour un futur mode auto optionnel.
  - ⚠️ **Kirilloid muet** sur tout ceci → mécanique/valeurs = approximations documentées (le
    seeding du joueur IA et les cadences sont des choix de dev, pas des chiffres de jeu ; toute
    ACTION reste imposée par les endpoints existants recoupés kirilloid). Registre **en mémoire**
    (défenseurs/plans ne survivent pas au reload uvicorn).
- ⬜ Reste : **réveil auto optionnel** (brancher `should_wake` sur un événement) ; posture
  **offensive/complète** (v1 = défense seule) ; plusieurs comptes IA ; bot scripté déterministe ;
  messagerie ; schéma observation/action formalisé pour usage externe.
