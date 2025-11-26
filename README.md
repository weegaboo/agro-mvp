# Agro MVP

Build: docker build -t agro-mvp:f2c-src . 

Run: docker run -d --name agro-mvp -p 8501:8501 -v "$(pwd)":/app agro-mvp:f2c-src


# Для обновления версии на сервере
ssh maksimseleznev@89.169.191.52
cd agro-mvp/
git fetch origin
git checkout main
git pull origin main
docker ps
docker kill <container_id>
docker rm <container_id>
docker build -t agro-mvp:latest .
docker run -d --name agro-mvp -p 8501:8501 -v $(pwd)/data:/app/data agro-mvp:latest