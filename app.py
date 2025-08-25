import os, json, time
from typing import List, Dict, Any
import streamlit as st
from streamlit_folium import st_folium
import folium

st.set_page_config(page_title="AgroRoute MVP — Week 1", layout="wide")
st.title("AgroRoute")

# --------- SIDEBAR: параметры самолёта + управление проектом ----------
with st.sidebar:
    st.header("Параметры самолёта")
    spray_width_m = st.number_input("Ширина захвата (м)", 1.0, 100.0, 20.0, 1.0)
    turn_radius_m = st.number_input("Минимальный радиус разворота (м)", 5.0, 500.0, 50.0, 5.0)

    st.divider()
    st.header("Проект")
    os.makedirs("data/projects", exist_ok=True)
    project_name = st.text_input("Имя проекта", "demo")
    project_file = f"data/projects/{project_name}.json"

    col_a, col_b = st.columns(2)
    with col_a:
        save_btn = st.button("💾 Сохранить проект", use_container_width=True)
    with col_b:
        load_btn = st.button("📂 Загрузить проект", use_container_width=True)

# --------- Инициализация карты ----------
center = [55.75, 37.61]  # можно поменять под свой регион
m = folium.Map(location=center, zoom_start=12, control_scale=True)
draw = folium.plugins.Draw(
    draw_options={
        "polyline": True,   # ВПП как линия
        "polygon": True,    # Поле и NFZ
        "marker": True,     # Старт
        "rectangle": False,
        "circle": False,
        "circlemarker": False,
    },
    edit_options={"edit": True, "remove": True},
)
draw.add_to(m)

# --------- Рендер и сбор геометрий из рисовалки ----------
out = st_folium(
    m, width="100%", height=600,
    returned_objects=["all_drawings", "last_active_drawing"]
)

# Функции разбора
def split_drawings(drawings: List[Dict[str, Any]]):
    field = None
    runway_line = None
    start_pt = None
    nfz_list = []
    for feat in drawings or []:
        g = feat.get("geometry", {})
        gtype = g.get("type")
        if gtype == "Polygon":
            # первый полигон считаем полем, остальные — NFZ
            if field is None:
                field = g
            else:
                nfz_list.append(g)
        elif gtype == "LineString":
            runway_line = g
        elif gtype == "Point":
            start_pt = g
    return field, runway_line, start_pt, nfz_list

drawings = out.get("all_drawings", [])
field_gj, runway_gj, start_gj, nfz_gj_list = split_drawings(drawings)

# --------- Статус ввода ----------
st.subheader("Статус")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Поле (Polygon)", "OK" if field_gj else "—")
col2.metric("ВПП (Polyline)", "OK" if runway_gj else "—")
col3.metric("Старт (Marker)", "OK" if start_gj else "—")
col4.metric("NFZ (шт.)", len(nfz_gj_list))

# --------- Сохранение / Загрузка ----------
payload = {
    "timestamp": int(time.time()),
    "aircraft": {
        "spray_width_m": float(spray_width_m),
        "turn_radius_m": float(turn_radius_m),
    },
    "geoms": {
        "field": field_gj,
        "runway": runway_gj,
        "start": start_gj,
        "nfz": nfz_gj_list,
    },
}
if save_btn:
    with open(project_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    st.success(f"Сохранено: {project_file}")

if load_btn:
    if os.path.exists(project_file):
        with open(project_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Показываем загруженный JSON. Для реального редактирования — можно сделать прелоад на карту,
        # но для Недели 1 достаточно проверки корректности (критерий).
        st.info(f"Загружено: {project_file}")
        st.json(data)
    else:
        st.error(f"Файл не найден: {project_file}")

# st.caption("Критерии Недели 1: 1) на экране рисуются все слои; 2) JSON сохраняется и загружается без ошибок.")

