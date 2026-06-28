# Travian local — réimplémentation fidèle T4.6

Moteur de jeu type Travian (version **T4.6 Legends classique**), reconstruit de zéro
avec des mécaniques **fidèles** (données et formules portées depuis
[kirilloid/travian](https://github.com/kirilloid/travian), la référence communautaire),
exposant une **UI web jouable** et une **API JSON** destinée à des agents.

> ⚠️ Les *images* officielles de Travian sont sous copyright (Travian Games GmbH) et ne
> sont pas redistribuées. Les visuels sont des placeholders (emoji) en attendant des
> textures libres au style proche.

## Lancer

```bash
python3 -m venv venv
TMPDIR=venv/.piptmp ./venv/bin/pip install fastapi 'uvicorn[standard]' sqlmodel jinja2 httpx
./venv/bin/uvicorn app.main:app --reload
```

Puis ouvrir http://127.0.0.1:8000/

## État d'avancement

- [x] **Phase 0** — données T4.6 (bâtiments, unités, tribus) + formules, validées vs kirilloid.ru
- [x] **Phase 1** — village jouable : production paresseuse, stockage, construction, **vue isométrique**
- [x] **Phase 2** — militaire & combat : **formule de combat exacte** (vecteurs Kirilloid validés),
      entraînement (caserne/écurie/atelier), mouvements d'armées (trajet, combat, butin, retour, rapports)
- [~] **Phase 3** — monde & multijoueur : persistance SQLite, joueurs, plusieurs villages, coordonnées,
      **carte du monde** (vallées + oasis avec animaux, ~30 % d'oasis, 8 types distingués par emoji),
      **attaque depuis la carte** (villages et oasis : combat vs animaux sauvages), **place de marché**
      (marchands : transfert de ressources, capacité/nombre selon tribu + niveau du marché). Reste :
      expansion (2ᵉ village : points de culture, colons, fondation), **occupation** d'oasis.
- [ ] **Phase 4** — API agents & bots

### Tests
```bash
./venv/bin/python -m scripts.validate_data   # valeurs vs kirilloid.ru
./venv/bin/python -m pytest tests/test_combat.py   # vecteurs de combat Kirilloid (pytest)
./venv/bin/python -m tests.test_raid         # raid de bout en bout (script)
./venv/bin/python -m tests.test_trade        # commerce de bout en bout (script)
```

> Vitesse serveur ×100 par défaut (cf. `SERVER_SPEED` dans `app/main.py`) pour tester
> rapidement. La base `game.db` se (re)crée automatiquement ; la supprimer réinitialise le monde.
>
> Village de test développé : `./venv/bin/python -m scripts.restore_village` restaure un
> village déjà avancé (instantané `scripts/saves/mon_village.json`) ; `--save` met à jour
> l'instantané depuis l'état courant. **Arrêter le serveur avant** (sinon il réécrit la base).

## Architecture

```
app/data/      tables et formules de jeu (fidélité) — formulas, buildings, units, tribes
app/engine/    moteur : village (village.py), combat (combat.py), mouvements (movement.py), monde/carte (world.py)
app/api/        (à venir) endpoints REST structurés
app/main.py    application FastAPI + UI minimale
web/           interface web (HTML/JS vanilla)
scripts/       outils (validate_data.py : recoupe les valeurs avec kirilloid.ru)
agents/        (à venir) bots et agents LLM jouant via l'API
```

### Principe « paresseux » (fidèle à Travian)

Pas de tick par seconde : les ressources sont recalculées à la lecture
(production nette × temps écoulé, plafonnée par entrepôt/grenier), et la file de
construction est appliquée au passage des heures de fin. Voir `app/engine/village.py`.

## Fidélité — écarts connus vs Kirilloid (corrigés pour coller au vrai Travian)

- Prérequis Scierie/Briqueterie intervertis dans Kirilloid → rétablis.
- Formule de capacité de la cachette buguée dans Kirilloid → formule réelle Travian.
- Prérequis de bâtiments à recouper finement avec travian.kirilloid.ru (tâche en cours).
