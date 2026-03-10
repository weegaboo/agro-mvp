# agro-mvp web platform

Monorepo:
- `apps/api` - FastAPI backend
- `apps/web` - Next.js frontend
- `packages/planner` - planner/domain logic

## Environment

All runtime settings are configured through `.env`.

1. Create `.env` from template:
```bash
cp .env.example .env
```
2. Edit values in `.env` for your target environment.

Main variables:
- `NEXT_PUBLIC_API_BASE_URL` - URL used by frontend to call API.
- `API_ALLOWED_ORIGINS` - comma-separated list of allowed frontend origins for CORS.
- `JWT_SECRET_KEY` - JWT secret (must be changed in production).
- `POSTGRES_PASSWORD` - database password (must be changed in production).
- `WEB_BIND_ADDRESS`, `API_BIND_ADDRESS`, `POSTGRES_BIND_ADDRESS` - bind addresses for published ports.

## Local run (laptop)

### 1) Set `.env` for local
Recommended local values:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
API_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
WEB_BIND_ADDRESS=0.0.0.0
API_BIND_ADDRESS=0.0.0.0
POSTGRES_BIND_ADDRESS=127.0.0.1
```

### 2) Start services
```bash
docker compose up --build -d
```

### 3) Run migrations
```bash
docker compose exec api alembic -c apps/api/alembic.ini upgrade head
```

### 4) Check
```bash
curl http://localhost:8000/health
```

Open:
- frontend: `http://localhost:3000`
- backend health: `http://localhost:8000/health`

## Server run (Ubuntu)

### 1) Install Docker and Compose plugin
```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

### 2) Deploy project
```bash
git clone <YOUR_REPO_URL> ~/agro-mvp
cd ~/agro-mvp
cp .env.example .env
```

### 3) Configure `.env` for server
Example (replace with your values):
```env
JWT_SECRET_KEY=<LONG_RANDOM_SECRET>
POSTGRES_PASSWORD=<STRONG_PASSWORD>
NEXT_PUBLIC_API_BASE_URL=http://<SERVER_IP>:8000
API_ALLOWED_ORIGINS=http://<SERVER_IP>:3000
POSTGRES_BIND_ADDRESS=127.0.0.1
```

If you use a domain:
```env
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
API_ALLOWED_ORIGINS=https://app.example.com
```

### 4) Start services
```bash
docker compose up --build -d
docker compose exec api alembic -c apps/api/alembic.ini upgrade head
```

### 5) Check status and logs
```bash
docker compose ps
docker compose logs api --tail=100
docker compose logs web --tail=100
```

### 6) Firewall
Open required ports (if you expose services directly):
```bash
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
sudo ufw status
```

## Update on server

```bash
cd ~/agro-mvp
git pull
docker compose up --build -d
docker compose exec api alembic -c apps/api/alembic.ini upgrade head
```

## Useful commands

Start/restart:
```bash
docker compose up -d
```

Rebuild:
```bash
docker compose up --build -d
```

Stop:
```bash
docker compose down
```

Run backend tests:
```bash
docker compose exec api pytest apps/api/tests
```

Run frontend lint:
```bash
docker compose exec web npm run lint
```

## API endpoints

- `GET /health`
- `POST /auth/register`
- `POST /auth/login`
- `POST /planner/build-from-project`
- `POST /planner/build-from-upload`
- `POST /missions`
- `POST /missions/from-geo`
- `GET /missions`
- `GET /missions/{id}`
- `GET /missions/{id}/waypoints.zip`

## Troubleshooting

### `Failed to fetch` on login/register
- Check browser origin and `API_ALLOWED_ORIGINS`.
- Check preflight:
```bash
curl -i -X OPTIONS http://<API_HOST>:8000/auth/register \
  -H "Origin: http://<WEB_HOST>:3000" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"
```

### `422 Unprocessable Entity` on register/login
- This is request validation, not network.
- Constraints:
  - `login` min length = 3
  - `password` min length = 6
