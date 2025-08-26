# Agro MVP

Build: docker build -t agro-mvp:f2c-src . 

Run: docker run -d --name agro-mvp -p 8501:8501 -v "$(pwd)":/app agro-mvp:f2c-src   