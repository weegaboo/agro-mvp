# Agro MVP

Build: docker build -t agro-mvp:f2c-src . 

Run: docker run -d --name agro-mvp -p 8501:8501 -v "$(pwd)":/app agro-mvp:f2c-src


# Для обновления версии на сервере
git fetch origin
git checkout main
git pull origin main
docker build -t agro-mvp:latest .
docker run -d --name agro-mvp -p 8501:8501 -v $(pwd)/data:/app/data agro-mvp:latest