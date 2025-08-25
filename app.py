import os, json, time, math
from typing import List, Dict, Any
import streamlit as st
from streamlit_folium import st_folium
import folium

st.set_page_config(page_title="AgroRoute MVP — Week 1", layout="wide")
st.title("AgroRoute — рисование + старт по началу ВПП (без маркера)")

# --------- SIDEBAR ----------
with st.sidebar:
    st.header("Параметры самолёта")
    spray_width_m = st.number_input("Ширина захвата (м)", 1.0, 100.0, 20.0, 1.0)
    turn_radius_m = st.number_input("Мин. радиус разворота (м)", 5.0, 500.0, 50.0, 5.0)

    st.divider()
    st.header("Проект")
    os.makedirs("data/projects", exist_ok=True)
    project_name = st.text_input("Имя проекта", "demo")
    project_file = f"data/projects/{project_name}.json"

    c1, c2 = st.columns(2)
    with c1:
        save_btn = st.button("💾 Сохранить", use_container_width=True)
    with c2:
        load_btn = st.button("📂 Загрузить", use_container_width=True)

st.caption("Правило: самолёт стоит в НАЧАЛЕ линии ВПП и направлен по её первому сегменту. Маркер не рисуем, просто сохраняем эти данные в JSON.")

# --------- helpers ---------
def split_drawings(drawings: List[Dict[str, Any]]):
    """Первый Polygon — поле, остальные Polygon — NFZ, первая LineString — ВПП (ось)."""
    field = None
    runway = None
    nfz = []
    for feat in drawings or []:
        g = feat.get("geometry", {})
        t = g.get("type")
        if t == "Polygon":
            if field is None:
                field = g
            else:
                nfz.append(g)
        elif t == "LineString" and runway is None:
            runway = g
    return field, runway, nfz

def calc_runway_pose(runway_line: Dict[str, Any]):
    """Старт — первая точка polyline; курс — по первому сегменту (в градусах [0..360))."""
    if not runway_line or not runway_line.get("coordinates"):
        return None
    coords = runway_line["coordinates"]
    if len(coords) == 0:
        return None
    start_lon, start_lat = coords[0]
    heading_deg = 0.0
    if len(coords) >= 2:
        (x0, y0), (x1, y1) = coords[0], coords[1]
        heading_rad = math.atan2(y1 - y0, x1 - x0)
        heading_deg = (math.degrees(heading_rad) + 360) % 360
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [start_lon, start_lat]},
        "properties": {"heading_deg": heading_deg}
    }

# --------- карта (одна) ---------
center = [55.75, 37.61]
m = folium.Map(location=center, zoom_start=12, control_scale=True, tiles=None)

# базовые слои
folium.TileLayer(
    tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr="© OpenStreetMap contributors",
    name="OSM"
).add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri", name="Спутник (Esri)"
).add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    attr="Esri Labels", name="Подписи", overlay=True, control=True, opacity=0.75
).add_to(m)

# плагин рисования: Polygon (поле/NFZ), Polyline (ВПП). Marker отключен.
draw = folium.plugins.Draw(
    draw_options={
        "polygon": {"shapeOptions": {"color": "green", "fillOpacity": 0.2}},
        "polyline": {"shapeOptions": {"color": "blue", "weight": 6}},
        "marker": False,
        "rectangle": False,
        "circle": False,
        "circlemarker": False,
    },
    edit_options={"edit": True, "remove": True},
)
draw.add_to(m)
folium.LayerControl(position="topleft", collapsed=False).add_to(m)

# рендер и чтение геометрий
out = st_folium(m, width="100%", height=600, returned_objects=["all_drawings"])
drawings = out.get("all_drawings", [])
field_gj, runway_gj, nfz_gj_list = split_drawings(drawings)

# вычисляем "виртуальный" старт на основе ВПП
runway_pose = calc_runway_pose(runway_gj)

# --------- статус ---------
st.subheader("Статус")
col1, col2, col3 = st.columns(3)
col1.metric("Поле (Polygon)", "OK" if field_gj else "—")
col2.metric("ВПП (Polyline)", "OK" if runway_gj else "—")
col3.metric("NFZ (шт.)", len(nfz_gj_list))
if runway_pose:
    lat = runway_pose["geometry"]["coordinates"][1]
    lon = runway_pose["geometry"]["coordinates"][0]
    hdg = runway_pose["properties"]["heading_deg"]
    st.info(f"Старт (виртуально): lat {lat:.6f}, lon {lon:.6f} • курс ≈ {hdg:.1f}°")

# --------- сохранение / загрузка ---------
payload = {
    "timestamp": int(time.time()),
    "aircraft": {
        "spray_width_m": float(spray_width_m),
        "turn_radius_m": float(turn_radius_m),
    },
    "geoms": {
        "field": field_gj,
        "nfz": nfz_gj_list,
        "runway_centerline": runway_gj,  # ось ВПП, как нарисована
        "runway_pose": runway_pose,      # старт + heading (по началу polyline)
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
        st.info(f"Загружено: {project_file}")
        st.json(data)
    else:
        st.error(f"Файл не найден: {project_file}")