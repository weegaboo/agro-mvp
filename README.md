# agro-mvp web platform

Monorepo with:
- `apps/api` - FastAPI backend
- `apps/web` - Next.js frontend (TypeScript)
- `packages/planner` - route planning business logic adapter

## Run with Docker Compose

```bash
docker compose up --build
```

Services:
- frontend: http://localhost:3000
- backend health: http://localhost:8000/health
- backend planner endpoint: `POST http://localhost:8000/planner/build-from-project`
- backend planner upload endpoint: `POST http://localhost:8000/planner/build-from-upload`
- backend missions endpoints:
  - `POST http://localhost:8000/missions` (multipart file upload)
  - `POST http://localhost:8000/missions/from-geo` (build from map geometry + aircraft params)
  - `GET http://localhost:8000/missions`
  - `GET http://localhost:8000/missions/{id}`
- auth endpoints:
  - `POST http://localhost:8000/auth/register`
  - `POST http://localhost:8000/auth/login`
- postgres: localhost:5432

Planner smoke flow:
- open http://localhost:3000/app
- register or login first
- draw `Field`, `Runway`, and optional `NFZ` directly on map
- set aircraft/route params in left panel
- click `Build Mission`
- missions are scoped to current user token
- select mission in the middle panel to inspect metrics, logs, and route map layers (field/NFZ/swaths/transit)
- mission storage smoke flow:
  - `TOKEN=$(curl -s -X POST http://localhost:8000/auth/register -H 'Content-Type: application/json' -d '{"login":"demo","password":"secret12"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")`
  - `curl -H "Authorization: Bearer $TOKEN" -F "file=@/absolute/path/project.json" http://localhost:8000/missions`
  - `curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/missions`
  - `curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/missions/1`

## Backend tests

```bash
docker compose exec api pytest apps/api/tests
```

## Run frontend separately

```bash
cd apps/web
npm install
npm run dev
```

## Run backend separately

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt
uvicorn app.main:app --app-dir apps/api --reload --host 0.0.0.0 --port 8000
```

## Alembic migration

```bash
docker compose exec api alembic -c apps/api/alembic.ini upgrade head
```




DEBUG

docker compose down
docker compose up --build -d
