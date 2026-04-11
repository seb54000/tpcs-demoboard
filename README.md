# Demoboard – microservices de démonstration

Une mini‑application de demo représentant un tableau de tâches avec une API FastAPI, un worker Python, un frontend Vue 3 et deux briques d'infrastructure (PostgreSQL + Redis). Chaque service a son Dockerfile pour être déployé sur une VM, via Docker Compose ou empaqueté pour Kubernetes.
La base par défaut est **SQLite embarquée** (fichier `/data/tasks.db`) pour simplifier la prise en main, mais elle peut être externalisée vers PostgreSQL en posant `DB_BACKEND=postgres` + variables `DB_*`.

## Architecture

```
docker/demoboard
├── api-service          # FastAPI + PostgreSQL
├── worker-service       # Worker Python + Redis + PostgreSQL
├── frontend-service     # Vue 3 + Vite + Nginx
├── docker-compose.yml   # Mode complet (PostgreSQL + Redis + worker)
├── docker-compose.light.yml # Mode léger (API SQLite + frontend)
└── README.md            # Ce document
```

- **frontend-service** : interface Vue 3/Vite, buildée puis servie par Nginx. L'appel `/api` est automatiquement proxifié vers `api-service`.
- **api-service** : FastAPI expose CRUD `/tasks` + endpoint `/tasks/{id}/start-job`. Base SQLite intégrée par défaut, basculable vers PostgreSQL via les variables `DB_*`.
- **worker-service** : worker Python en écoute sur Redis, simule un traitement long puis met à jour PostgreSQL.
- **db-service** : PostgreSQL 15 pour stocker les tâches.
- **queue-service** : Redis 7 pour les jobs (liste `jobs`). La mise en file se fait via `publish_job`.

## Démarrage rapide (Docker Compose)

```bash
cd docker/demoboard
docker compose up --build
```

Services exposés :

| Service            | Port hôte | Description                         |
| ------------------ | --------- | ----------------------------------- |
| frontend-service   | 8080      | UI Vue (http://localhost:8080)      |
| api-service        | 8000      | API FastAPI (http://localhost:8000) |
| db (PostgreSQL)    | 5432      | Base de données                     |
| redis              | 6379      | File de jobs                        |

Arrêt : `docker compose down` (ajoutez `-v` pour supprimer aussi les volumes PostgreSQL).

### Variante "mode léger"

Pour démarrer sans Redis ni PostgreSQL :

```bash
cd docker/demoboard
docker compose -f docker-compose.light.yml up --build
```

| Service          | Port hôte | Description                                |
| ---------------- | --------- | ------------------------------------------ |
| frontend-service | 8080      | UI Vue (http://localhost:8080)             |
| api-service      | 8000      | API FastAPI avec SQLite locale             |

Dans ce mode `ENABLE_WORKER=false` et `VITE_ENABLE_WORKER=false` : l'UI n'affiche plus le bouton "Traitement long" et l'API renvoie `503` si l'endpoint `/tasks/{id}/start-job` est appelé. Le fichier SQLite est stocké dans un volume Docker (`api-data`) qu'on peut persister/capturer pour un TP.

## API utile pendant le TP

- `GET /tasks` : liste les tâches.
- `POST /tasks` : crée une tâche `{ "title": "..." }`.
- `GET /tasks/{id}` : détail.
- `PUT /tasks/{id}` : met à jour `title` et/ou `status`.
- `DELETE /tasks/{id}` : supprime.
- `POST /tasks/{id}/start-job` : passe la tâche en `processing`, envoie un message Redis, le worker termine et met `completed`.
- `GET /healthz` : ping rapide.

Variables de configuration :

| Variable            | Effet                                               |
| ------------------- | --------------------------------------------------- |
| `DB_BACKEND`        | `sqlite` (défaut) ou `postgres`                     |
| `SQLITE_PATH`       | Chemin du fichier SQLite dans le container          |
| `DB_HOST/PORT/...`  | Paramètres PostgreSQL                               |
| `ENABLE_WORKER`     | Active/désactive l'endpoint `/start-job` côté API   |
| `VITE_ENABLE_WORKER`| Affiche/masque le bouton "Traitement long" côté UI  |
| `WORKER_PROCESSING_TIME` | Force un temps fixe côté worker (en secondes) |
| `WORKER_PROCESSING_TIME_MIN_SECONDS` | Borne basse aléatoire du worker |
| `WORKER_PROCESSING_TIME_MAX_SECONDS` | Borne haute aléatoire du worker |
| `DEGRADED_WORKER_PROCESSING_TIME_MIN_SECONDS` | Borne basse worker en mode node dégradé |
| `DEGRADED_WORKER_PROCESSING_TIME_MAX_SECONDS` | Borne haute worker en mode node dégradé |
| `NODE_NAME` | Nom du node/pod host pour simuler un incident ciblé |
| `DEGRADED_NODE_MATCH` | Sous-chaîne du node activant le mode dégradé |
| `OTEL_ENABLED`      | Active l'export OTLP des traces et métriques        |
| `OTEL_METRICS_EXEMPLAR_FILTER` | Stratégie d'exemplars OTEL, `trace_based` par défaut |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Endpoint OTLP HTTP du collector           |
| `APP_LOG_FILE`      | Fichier de logs JSON corrélés à OpenTelemetry       |

## Observabilité

Le dépôt est prêt pour un stack OTEL externe :

- **API** : traces OTEL sur chaque requête, spans métier (`tasks.create`, `tasks.start_job`, etc.), métriques d'API, logs JSON avec `trace_id`/`span_id`.
- **Worker** : traces OTEL sur la consommation Redis, propagation de contexte depuis l'API vers le worker, métriques de traitement, logs JSON corrélés.
- **Frontend** : logs JSON Nginx collectables dans la même chaîne de logs. Pour des traces navigateur OTEL, il faudra ajouter une instrumentation web dédiée côté Vue.
- **PostgreSQL / Redis** : on **ne modifie pas** les images officielles ; on collecte leurs métriques via des exporters dédiés (`postgres-exporter`, `redis-exporter`) dans le dépôt monitoring.
- **Logs applicatifs** : écrits sur stdout et dans `./observability-logs/` pour être collectés par Fluent Bit ou un autre agent.

En Docker Compose, la configuration par défaut cible un collector OTLP sur `http://host.docker.internal:4318`. Si le stack monitoring n'est pas lancé, mettez `OTEL_ENABLED=false`.
Le worker simule par défaut un traitement aléatoire entre `1.5` et `2.7` secondes, ce qui peut être ajusté via `WORKER_PROCESSING_TIME_MIN_SECONDS` et `WORKER_PROCESSING_TIME_MAX_SECONDS`, ou forcé via `WORKER_PROCESSING_TIME`.
Si `NODE_NAME` contient la chaîne `DEGRADED_NODE_MATCH` (par défaut `eu-west-3c`), l'application active un scénario pédagogique de dégradation :
- côté API, des logs de perte de connexion base sont écrits avec 2 à 3 retries espacés de 200 à 300 ms
- côté worker, le traitement passe entre `3.5` et `6.0` secondes avec des logs de retry environ toutes les secondes

Quand `POD_NAME`, `POD_NAMESPACE`, `POD_UID`, `NODE_NAME`, `DEPLOYMENT_NAME` ou `CONTAINER_NAME` sont présents (cas Kubernetes), ils sont ajoutés automatiquement :
- aux logs JSON applicatifs
- aux ressources OpenTelemetry des traces et métriques

Hors Kubernetes, ces variables sont absentes et l'application continue simplement sans enrichissement K8s.

## Développement local sans Compose

1. Lancer Postgres + Redis (via `docker compose up db redis` ou vos services locaux). Pour le mode léger, sautez cette étape et laissez `DB_BACKEND=sqlite` / `ENABLE_WORKER=false`.
2. **API**
   ```bash
   cd api-service
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   # SQLite (défaut)
   uvicorn app:app --reload --host 0.0.0.0 --port 8000

   # PostgreSQL externe
   export DB_BACKEND=postgres DB_HOST=localhost DB_NAME=tasks DB_USER=postgres DB_PASSWORD=postgres
   uvicorn app:app --reload --host 0.0.0.0 --port 8000
   ```
3. **Worker**
   ```bash
   cd worker-service
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python worker.py
   ```
4. **Frontend**
   ```bash
   cd frontend-service
   npm install
   npm run dev -- --host
   ```
   Le proxy Vite redirige `/api` vers `http://localhost:8000`.

## Déploiement Kubernetes (pistes)

- Construire/pusher les images `api-service`, `worker-service`, `frontend-service`.
- Créer des manifests (Deployment + Service) pour chaque composant, ajouter un `StatefulSet`/`Deployment` pour PostgreSQL & Redis ou utiliser des offres managées.
- Injecter la configuration (variables `DB_*`, `REDIS_*`, `VITE_API_URL`) via ConfigMap/Secret.

Le dossier `kubernetes/` du dépôt pourra accueillir ces manifests pour aller plus loin en TP.

## Changelog

- 2026-03-11 : Frontend mis à jour pour un build propre (npm 11.11.0 dans l'image, passage à `vite` 7.3.1 et `@vitejs/plugin-vue` 6.0.4, ajout de `package-lock.json`, installation via `npm ci`) avec suppression de l'alerte de version npm et audit npm sans vulnérabilité.
- 2026-03-11 : Builds Python (`api-service` et `worker-service`) nettoyés en supprimant les warnings pip liés à l'exécution en root via configuration `PIP_ROOT_USER_ACTION=ignore` et `PIP_DISABLE_PIP_VERSION_CHECK=1`.
- 2026-03-12 : Dockerfile/frontend clarifié pour le TP avec sortie de build explicitement fixée vers `/app/dist` (`vite build --outDir dist` + `build.outDir = "dist"`), afin d'expliquer le `COPY --from=build /app/dist ...`.
