FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    PYTHONUNBUFFERED=1

# 1) Пакеты как в документации F2C
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    build-essential ca-certificates cmake doxygen g++ git \
    libeigen3-dev libgdal-dev libpython3-dev python3 python3-pip \
    python3-matplotlib python3-tk lcov libgtest-dev libtbb-dev swig libgeos-dev \
    gnuplot libtinyxml2-dev nlohmann-json3-dev \
 && rm -rf /var/lib/apt/lists/*

# pytest (из доков по python-интерфейсу F2C)
RUN pip3 install --no-cache-dir pytest

# (необязательно) OR-Tools для Python — не мешает
RUN pip3 install --no-cache-dir ortools

# 2) Клонируем и собираем Fields2Cover (как в мануале)
WORKDIR /opt
RUN git clone https://github.com/Fields2Cover/Fields2Cover.git && \
    mkdir -p /opt/Fields2Cover/build

WORKDIR /opt/Fields2Cover/build
# базовая сборка C++ библиотеки
RUN cmake .. && make -j"$(nproc)" && make install

# сборка Python-интерфейса
RUN apt-get update && apt-get install -y --no-install-recommends swig python3-pytest && rm -rf /var/lib/apt/lists/* && \
    cmake -DBUILD_PYTHON=ON .. && make -j"$(nproc)" && make install

# 3) Твоё приложение
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . /app

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]