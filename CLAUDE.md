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
  troupes via le rassemblement). Les administrateurs s'entraînent **au palais niv 10+** (déjà géré).
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
  rétrogradée et ses **champs > 10 ramenés à 10** (sans remboursement, fidèle). API
  `POST /api/village/{id}/make-capital` (+ `is_capital`/`can_make_capital` exposés par `serialize`) ;
  UI : panneau « Capitale » dans la modale du palais.
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
     vaincue** (défenseurs à 0), **et** une **trésorerie vide** assez haute au **village
     d'origine** (`can_store` : niv **10** = petit / **20** = grand·unique, cf.
     `formulas.slots2`) ⇒ l'artefact passe en `holder='player'`, stocké dans cette
     trésorerie. **Effets branchés** (petit → son village ; grand/unique → tout le
     compte) : **durabilité des bâtiments** (architecte ×3/4/5, multiplie
     `place.dur_bonus`/`wall_durability` dans `_build_place`), **vitesse des troupes**
     (bottes ×1,5/2, divise le trajet **aller** dans `movement.send`), **consommation de
     céréales** (diète ×0,5, dans `village.troop_upkeep`). Les **5 autres effets**
     (entraînement, stockage, cachette, espionnage, confusion) sont **catalogués mais pas
     encore actifs** (`wired=False`, comme les bonus d'alliance à venir). Un **village
     conquis détache** ses artefacts (inactifs, `release_artifacts_of_village`). API
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
   - ⬜ **Classements/statistiques** (`Ranking.php`), NPC trader / bourse du marché (périmètre à
     décider), médailles, protection débutant.
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
   - **Abreuvoir** (Romain, id 40) : `village.unit_upkeep` retire **−1 céréale/h** par cavalier romain
     aux paliers (Equites Legati niv 10, Imperatoris niv 15, Caesaris niv 20) ; `horse_pool_train_factor`
     accélère l'entraînement de la cavalerie de **−1 %/niveau** (appliqué dans `enqueue_training`).
   - **Brasserie** (Teuton, id 34, capitale, niv max 10) : `app/engine/brewery.py` — **fête de la
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

Puis Phase 4 (API agents : schéma observation/action, bot scripté, puis agents LLM via le
Claude Agent SDK).
