FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/apps/api:/app/packages/planner/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake g++ swig libgeos-dev libgdal-dev libeigen3-dev \
    libtinyxml2-dev nlohmann-json3-dev libtbb-dev git python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone https://github.com/Fields2Cover/Fields2Cover.git && \
    mkdir -p /opt/Fields2Cover/build

WORKDIR /opt/Fields2Cover/build
RUN cmake .. && make -j"$(nproc)" && make install
RUN cmake -DBUILD_PYTHON=ON .. && make -j"$(nproc)" && make install

WORKDIR /app
COPY apps/api/requirements.txt /app/apps/api/requirements.txt
COPY packages/planner/requirements.txt /app/packages/planner/requirements.txt
RUN pip install --no-cache-dir -r /app/apps/api/requirements.txt

COPY . /app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "apps/api", "--host", "0.0.0.0", "--port", "8000"]
