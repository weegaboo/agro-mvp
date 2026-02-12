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
  - `GET http://localhost:8000/missions`
  - `GET http://localhost:8000/missions/{id}`
- postgres: localhost:5432

Planner smoke flow:
- open http://localhost:3000/app
- upload project JSON and click `Build from upload`
- or enter absolute path to project JSON inside API container (`/app/...`) and click `Build from path`
- mission storage smoke flow:
  - `curl -F "file=@/absolute/path/project.json" http://localhost:8000/missions`
  - `curl http://localhost:8000/missions`
  - `curl http://localhost:8000/missions/1`

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
