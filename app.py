import os, json, time, math, traceback
import sys

_ROOT = os.path.abspath(os.path.dirname(__file__))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from typing import List, Dict, Any, Optional

import streamlit as st
from streamlit_folium import st_folium
import folium
from pyproj import Geod

from shapely.geometry import Point, LineString
from math import radians, atan2, cos, sin, tan

# наши модули
from agro.services.mission_builder import build_route_from_file
from agro.services.exporter import export_route_geojson_csv
from agro.services.mission_planner import export_mission_planner


_geod = Geod(ellps="WGS84")
st.set_page_config(page_title="AgroRoute — F2C cover", layout="wide")
st.title("AgroRoute — рисование → Сохранить → Построить (F2C внутри поля)")

# =============== SESSION STATE ===============
if "route" not in st.session_state:
    st.session_state["route"] = None
if "build_log" not in st.session_state:
    st.session_state["build_log"] = []

def log(msg: str):
    st.session_state["build_log"].append(msg)

def clear_log():
    st.session_state["build_log"] = []

# =============== SIDEBAR ===============
with st.sidebar:
    st.header("Параметры самолёта / покрытия")
    spray_width_m = st.number_input("Ширина захвата (м)", 1.0, 200.0, 20.0, 1.0)
    turn_radius_m = st.number_input("Мин. радиус разворота (м)", 1.0, 500.0, 40.0, 1.0)
    total_capacity_l = st.number_input("Общая ёмкость бака, л", 1.0, 10000.0, 200.0, 1.0)
    fuel_reserve_l = st.number_input("Резерв топлива, л", 0.0, 500.0, 5.0, 0.5)
    mix_rate_l_per_ha = st.number_input("Расход смеси, л/га", 0.0, 200.0, 10.0, 0.5)
    fuel_burn_l_per_km = st.number_input("Расход топлива, л/км", 0.0, 10.0, 0.35, 0.01)
    headland_factor = st.slider("Кромка (x ширины корпуса)", 0.0, 8.0, 3.0, 0.5)
    route_order = st.selectbox("Порядок обхода сватов", ["snake", "boustro", "spiral", "straight_loops"], index=0)
    objective = st.selectbox(
        "Цель генератора сватов",
        ["n_swath", "swath_length", "field_coverage", "overlap"],
        index=0
    )
    use_cc = st.checkbox("Непрерывная кривизна (DubinsCC)", True)

    st.divider()
    st.header("Проект")
    os.makedirs("data/projects", exist_ok=True)
    project_name = st.text_input("Имя проекта", "demo")
    project_file = f"data/projects/{project_name}.json"

    st.divider()
    st.header("Экспорт маршрута (WGS84)")
    export_step_m = st.number_input("Шаг дискретизации, м", 1.0, 100.0, 5.0, 1.0)
    export_name = st.text_input("Имя файла (без расширения)", f"{project_name}_route")
    export_btn = st.button("💾 Экспортировать (GeoJSON + CSV)", use_container_width=True)

    st.divider()
    st.header("Экспорт: Mission Planner (QGC WPL 110)")
    mp_alt_agl = st.number_input("Высота (AGL), м", 5.0, 150.0, 30.0, 1.0)
    mp_speed_ms = st.number_input("Скорость, м/с", 3.0, 40.0, 15.0, 0.5)
    mp_step_m = st.number_input("Шаг по маршруту, м", 1.0, 50.0, 5.0, 1.0)
    mp_filename = st.text_input("Имя файла (.waypoints)", f"{project_name}_mission")
    mp_export_btn = st.button("💾 Экспорт в Mission Planner", use_container_width=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        save_btn = st.button("💾 Сохранить", use_container_width=True)
    with c2:
        show_btn = st.button("📂 Показать JSON", use_container_width=True)
    with c3:
        build_btn = st.button("🚀 Построить маршрут (из файла)", use_container_width=True)
    with c4:
        clear_btn = st.button("🗑 Очистить маршрут", use_container_width=True)

st.caption("Рисуем **поле (Polygon)**, **ВПП (Polyline)** и при необходимости **NFZ (Polygon)**. "
           "Сначала «Сохранить», затем «Построить маршрут» — расчёт читает файл по имени проекта. "
           "Маршрут внутри поля строится **только** через Fields2Cover.")

# =============== HELPERS (рисовалка) ===============
def sample_linestring_m(ls_m: LineString, step_m: float) -> List[Point]:
    """Точки через каждые step_m + финальная точка."""
    if ls_m.is_empty:
        return []
    L = float(ls_m.length)
    if L <= 0:
        return [Point(ls_m.coords[0])]
    step = max(0.1, float(step_m))
    dists = [i * step for i in range(int(L // step))] + [L]
    return [ls_m.interpolate(d) for d in dists]

def build_qgc_wpl(points_wgs: List[Point], *, alt_agl: float, speed_ms: float, include_takeoff=True, include_rtl=True) -> str:
    """
    Собирает текст в формате QGC WPL 110 для Mission Planner.
    FRAME = 3 (GLOBAL_RELATIVE_ALT).
    Команды:
      - 22 TAKEOFF (первой точке)
      - 178 DO_CHANGE_SPEED (скорость в м/с)
      - 16 WAYPOINT для всех точек маршрута
      - 20 RTL в конце (опционально)
    """
    lines = ["QGC WPL 110"]
    seq = 0
    FRAME = 3  # GLOBAL_RELATIVE_ALT
    AUTO = 1

    # защита от пустого
    if not points_wgs:
        return "\n".join(lines)

    lat0, lon0 = points_wgs[0].y, points_wgs[0].x

    if include_takeoff:
        # 22 TAKEOFF: param1=мин взлётный угол (0), x=lat, y=lon, z=alt
        lines.append(f"{seq} 1 {FRAME} 22 0 0 0 0 {lat0:.7f} {lon0:.7f} {alt_agl:.2f} {AUTO}")
        seq += 1

    # 178 DO_CHANGE_SPEED: param1=0(airspeed), param2=speed m/s, x=y=z=0
    lines.append(f"{seq} 0 {FRAME} 178 0 {speed_ms:.3f} 0 0 0 0 0 {AUTO}")
    seq += 1

    # 16 WAYPOINT для всех точек
    for pt in points_wgs:
        lat, lon = pt.y, pt.x
        lines.append(f"{seq} 0 {FRAME} 16 0 0 0 0 {lat:.7f} {lon:.7f} {alt_agl:.2f} {AUTO}")
        seq += 1

    if include_rtl:
        # 20 RTL: x=y=z=0
        lines.append(f"{seq} 0 {FRAME} 20 0 0 0 0 0 0 0 {AUTO}")
        seq += 1

    return "\n".join(lines)


def _m_per_deg(lat_deg: float):
    # приближённые метры в градус широты/долготы у заданной широты
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * cos(radians(lat_deg))
    return m_per_deg_lat, m_per_deg_lon if m_per_deg_lon > 1e-6 else 1e-6

def _bearing_rad(a: Point, b: Point) -> float:
    # a,b: Point(lon,lat). Возврат: курс (рад) из a в b.
    lat = (a.y + b.y) * 0.5
    mpl, mplon = _m_per_deg(lat)
    dx = (b.x - a.x) * mplon
    dy = (b.y - a.y) * mpl
    return atan2(dx, dy)  # восток=+90°, север=0°

def _ll_offset(a: Point, brg_rad: float, dist_m: float) -> Point:
    mpl, mplon = _m_per_deg(a.y)
    dlat = (dist_m * cos(brg_rad)) / mpl
    dlon = (dist_m * (atan2(0,1)*2/360) * 0)  # placeholder to keep IDE happy
    dlon = (dist_m * (atan2(0,1)*2/360) * 0)  # (not used)
    # корректно:
    dlon = (dist_m * sin(brg_rad)) / mplon if mplon > 1e-6 else 0.0
    return Point(a.x + dlon, a.y + dlat)

def build_wpl_takeoff_route_land(
    *,
    runway_start_wgs: Point,          # порог ВПП (LAND точка и TAKEOFF)
    runway_end_wgs:   Point,          # второй конец ВПП (для курса)
    route_points_wgs: List[Point],    # ваш маршрут (Runway -> поле -> обратно -> к FAF)
    cruise_alt_agl:   float,          # высота для маршрута (AGL), м
    speed_ms:         float = 18.0,   # DO_CHANGE_SPEED (м/с)
    takeoff_alt_agl:  float = 10.0,   # высота завершения NAV_TAKEOFF, м
    roll_distance_m:  float = 150.0,  # отступ по оси до первой WP после TAKEOFF, м
    faf_alt_agl:      float = 60.0,   # высота FAF, м
    glide_angle_deg:  float = 4.0,    # угол глиссады, град
    min_faf_distance_m: float = 400.0,# минимальная дальность FAF, м
    include_midpoint: bool = False,   # опциональная точка посередине ВПП
    mid_fraction:     float = 0.5,    # где её ставить (0..1)
    include_rtl:      bool = True     # добавить RTL в самом конце
) -> str:
    """
    Возвращает текст QGC WPL 110:
      1) NAV_TAKEOFF @ runway_start
      2) DO_CHANGE_SPEED
      3) (опц.) MID-WP посередине ВПП на cruise_alt_agl
      4) первая WP на оси через roll_distance_m, alt=cruise_alt_agl
      5) ваш маршрут (каждая точка alt=cruise_alt_agl)
      6) DO_LAND_START @ FAF
      7) FAF-WP @ alt=faf_alt_agl (последний WP перед заходом)
      8) NAV_LAND @ runway_start (alt=0)
      9) (опц.) RTL
    FRAME = 3 (GLOBAL_RELATIVE_ALT), AUTO=1.
    """
    lines = ["QGC WPL 110"]
    FRAME = 3
    AUTO = 1
    seq = 0

    rw_brg = _bearing_rad(runway_start_wgs, runway_end_wgs)
    brg_back = (rw_brg + 3.141592653589793) % (2*3.141592653589793)

    # 1) TAKEOFF в пороге
    lat0, lon0 = runway_start_wgs.y, runway_start_wgs.x
    # 22 TAKEOFF: p1=минимальный угол (0 → использовать параметры), x=lat, y=lon, z=alt
    lines.append(f"{seq} 1 {FRAME} 22 0 0 0 0 {lat0:.7f} {lon0:.7f} {takeoff_alt_agl:.2f} {AUTO}"); seq += 1

    # 2) DO_CHANGE_SPEED
    lines.append(f"{seq} 0 {FRAME} 178 0 {speed_ms:.3f} 0 0 0 0 0 {AUTO}"); seq += 1

    # 3) (опц.) mid-WP на оси ВПП
    if include_midpoint:
        # расстояние по прямой между порогами:
        latm = (runway_start_wgs.y + runway_end_wgs.y) * 0.5
        mpl, mplon = _m_per_deg(latm)
        dx = (runway_end_wgs.x - runway_start_wgs.x) * mplon
        dy = (runway_end_wgs.y - runway_start_wgs.y) * mpl
        Lrw = (dx*dx + dy*dy) ** 0.5
        mid_s = max(0.0, min(1.0, mid_fraction)) * Lrw
        mid_pt = _ll_offset(runway_start_wgs, rw_brg, mid_s)
        lines.append(f"{seq} 0 {FRAME} 16 0 0 0 0 {mid_pt.y:.7f} {mid_pt.x:.7f} {cruise_alt_agl:.2f} {AUTO}"); seq += 1

    # 4) первая WP после TAKEOFF — на оси + roll_distance_m
    tko_wp = _ll_offset(runway_start_wgs, rw_brg, roll_distance_m)
    lines.append(f"{seq} 0 {FRAME} 16 0 0 0 0 {tko_wp.y:.7f} {tko_wp.x:.7f} {cruise_alt_agl:.2f} {AUTO}"); seq += 1

    # 5) ваш маршрут (alt=cruise_alt_agl)
    for pt in route_points_wgs:
        lines.append(f"{seq} 0 {FRAME} 16 0 0 0 0 {pt.y:.7f} {pt.x:.7f} {cruise_alt_agl:.2f} {AUTO}"); seq += 1

    # 6–7) FAF и DO_LAND_START
    # теоретическая дальность под данный угол
    ground_need = faf_alt_agl / max(tan(radians(glide_angle_deg)), 1e-6)
    S_faf = max(ground_need, min_faf_distance_m)
    faf_wp = _ll_offset(runway_start_wgs, brg_back, S_faf)

    # DO_LAND_START (189) — как маркер посадочной секвенции (перед FAF)
    lines.append(f"{seq} 0 {FRAME} 189 0 0 0 0 {faf_wp.y:.7f} {faf_wp.x:.7f} {faf_alt_agl:.2f} {AUTO}"); seq += 1
    # FAF как обычный WAYPOINT
    lines.append(f"{seq} 0 {FRAME} 16 0 0 0 0 {faf_wp.y:.7f} {faf_wp.x:.7f} {faf_alt_agl:.2f} {AUTO}"); seq += 1

    # 8) NAV_LAND @ runway_start (alt=0)
    lines.append(f"{seq} 0 {FRAME} 21 0 0 0 0 {lat0:.7f} {lon0:.7f} 0 {AUTO}"); seq += 1

    # 9) (опц.) RTL
    if include_rtl:
        lines.append(f"{seq} 0 {FRAME} 20 0 0 0 0 0 0 0 {AUTO}"); seq += 1

    return "\n".join(lines)


def sample_linestring_m(ls_m: LineString, step_m: float) -> List[Point]:
    """Возвращает список точек (Point) через каждые step_m по длине LineString + последний узел."""
    if ls_m.is_empty:
        return []
    L = float(ls_m.length)
    if L == 0:
        return [Point(ls_m.coords[0])]
    step = max(0.1, float(step_m))
    # равномерные расстояния + финальная точка
    dists = [i * step for i in range(int(L // step))] + [L]
    pts = [ls_m.interpolate(d) for d in dists]
    return pts

def split_drawings(drawings: List[Dict[str, Any]]):
    """Первый Polygon — поле, остальные Polygon — NFZ; первая LineString — ВПП (ось)."""
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
    heading_deg, runway_length = 0.0, 0.0
    if len(coords) >= 2:
        (x0, y0), (x1, y1) = coords[0], coords[1]
        heading_rad = math.atan2(y1 - y0, x1 - x0)
        heading_deg = (math.degrees(heading_rad) + 360) % 360
        lon1, lat1 = coords[0]
        lon2, lat2 = coords[1]
        _, _, runway_length = _geod.inv(lon1, lat1, lon2, lat2)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [start_lon, start_lat]},
        "properties": {"heading_deg": heading_deg, "length": runway_length},
    }

# =============== КАРТА РИСОВАНИЯ (всегда сверху) ===============
center = [55.75, 37.61]
m = folium.Map(location=center, zoom_start=12, control_scale=True, tiles=None)
folium.TileLayer("OpenStreetMap", name="OSM").add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr="Esri", name="Спутник (Esri)"
).add_to(m)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    attr="Esri Labels", name="Подписи", overlay=True, control=True, opacity=0.75
).add_to(m)

draw = folium.plugins.Draw(
    draw_options={
        "polygon":  {"shapeOptions": {"color": "green", "fillOpacity": 0.2}},
        "polyline": {"shapeOptions": {"color": "blue", "weight": 6}},
        "marker": False, "rectangle": False, "circle": False, "circlemarker": False,
    },
    edit_options={"edit": True, "remove": True},
)
draw.add_to(m)
folium.LayerControl(position="topleft", collapsed=False).add_to(m)

out = st_folium(m, width="100%", height=560, returned_objects=["all_drawings"])
drawings = out.get("all_drawings", [])
field_gj, runway_gj, nfz_gj_list = split_drawings(drawings)

# статус ввода
st.subheader("Статус ввода (то, что сейчас на карте)")
col1, col2, col3 = st.columns(3)
col1.metric("Поле", "OK" if field_gj else "—")
col2.metric("ВПП", "OK" if runway_gj else "—")
col3.metric("NFZ (шт.)", len(nfz_gj_list))
if runway_gj:
    rp = calc_runway_pose(runway_gj)
    if rp:
        lat = rp["geometry"]["coordinates"][1]
        lon = rp["geometry"]["coordinates"][0]
        hdg = rp["properties"]["heading_deg"]
        runway_length = rp["properties"]["length"]
        st.info(f"Старт (виртуально): lat {lat:.6f}, lon {lon:.6f} • курс ≈ {hdg:.1f}°, len: {runway_length}")

# =============== СОХРАНЕНИЕ / ПРОСМОТР ФАЙЛА ===============
payload = {
    "timestamp": int(time.time()),
    "aircraft": {
        "spray_width_m": float(spray_width_m),
        "turn_radius_m": float(turn_radius_m),
        "total_capacity_l": float(total_capacity_l),
        "fuel_reserve_l": float(fuel_reserve_l),
        "mix_rate_l_per_ha": float(mix_rate_l_per_ha),
        "fuel_burn_l_per_km": float(fuel_burn_l_per_km),
        "headland_factor": float(headland_factor),
        "route_order": route_order,
        "objective": objective,
        "use_cc": bool(use_cc),
    },
    "geoms": {
        "field": field_gj,
        "nfz": nfz_gj_list,
        "runway_centerline": runway_gj,
        "runway_pose": calc_runway_pose(runway_gj) if runway_gj else None,
    },
}
if save_btn:
    if not field_gj or not runway_gj:
        st.error("Чтобы сохранить проект, нужны минимум поле (Polygon) и ВПП (Polyline).")
    else:
        with open(project_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        st.success(f"Сохранено: {project_file}")

if show_btn:
    if os.path.exists(project_file):
        with open(project_file, "r", encoding="utf-8") as f:
            st.json(json.load(f))
    else:
        st.error(f"Файл не найден: {project_file}")

if clear_btn:
    st.session_state["route"] = None
    clear_log()
    st.success("Маршрут очищён.")

if build_btn:
    try:
        clear_log()
        st.session_state["route"] = build_route_from_file(project_file, log_fn=log)
        st.success("Маршрут построен. См. карту и логи ниже.")
    except Exception as e:
        tb = traceback.format_exc()
        log(f"❌ Ошибка: {e}")
        log(tb)
        st.error(f"Ошибка при построении маршрута: {e}")

# =============== ОТРИСОВКА МАРШРУТА (если есть) ===============
route = st.session_state["route"]
if route:
    st.subheader("Маршрут (последний рассчитанный)")
    m2 = folium.Map(location=center, zoom_start=12, control_scale=True, tiles=None)
    folium.TileLayer("OpenStreetMap", name="OSM").add_to(m2)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Спутник (Esri)"
    ).add_to(m2)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri Labels", name="Подписи", overlay=True, control=True, opacity=0.75
    ).add_to(m2)

    # фон: поле и NFZ
    if route["geo"].get("field"):
        folium.GeoJson(route["geo"]["field"], name="Поле",
                       style_function=lambda x: {"color":"#2ca02c","fillOpacity":0.1}).add_to(m2)
    for gj in route["geo"].get("nfz", []):
        folium.GeoJson(gj, name="NFZ",
                       style_function=lambda x: {"color":"#d62728","fillOpacity":0.15}).add_to(m2)

    # зона удобрения (опционально)
    if route["geo"].get("sprayed"):
        folium.GeoJson(route["geo"]["sprayed"], name="Зона удобрения",
                       style_function=lambda x: {"color":"#ff0000","fillOpacity":0.25}).add_to(m2)

    # маршруты
    folium.GeoJson(route["geo"]["cover_path"], name="Покрытие по полю",
                   style_function=lambda x: {"color":"#00aa00","weight":4}).add_to(m2)
    trips = route["geo"].get("trips") or []
    if trips:
        for idx, t in enumerate(trips):
            folium.GeoJson(t["to_field"],  name=f"Долёт #{idx+1}",
                           style_function=lambda x: {"color":"#1f77b4","weight":4,"dashArray":"5,5"}).add_to(m2)
            folium.GeoJson(t["back_home"], name=f"Возврат #{idx+1}",
                           style_function=lambda x: {"color":"#1f77b4","weight":4,"dashArray":"5,5"}).add_to(m2)
    else:
        if route["geo"].get("to_field"):
            folium.GeoJson(route["geo"]["to_field"],  name="Долёт",
                           style_function=lambda x: {"color":"#1f77b4","weight":4,"dashArray":"5,5"}).add_to(m2)
        if route["geo"].get("back_home"):
            folium.GeoJson(route["geo"]["back_home"], name="Возврат",
                           style_function=lambda x: {"color":"#1f77b4","weight":4,"dashArray":"5,5"}).add_to(m2)

    folium.LayerControl(position="topleft", collapsed=False).add_to(m2)
    st_folium(m2, width="100%", height=560)

    # метрики
    st.subheader("Статистика маршрута")
    mtr = route["metrics"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Длина, км",        f"{mtr['length_total_m']/1000:.2f}")
    c2.metric("Время, мин",       f"{mtr['time_total_min']:.1f}")
    c3.metric("Топливо, л",       f"{mtr['fuel_l']:.1f}")
    c4.metric("Удобрение, л",     f"{mtr['fert_l']:.1f}")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Транзит, км",      f"{mtr['length_transit_m']/1000:.2f}")
    c6.metric("Обработка, км",    f"{mtr['length_spray_m']/1000:.2f}")
    c7.metric("Площадь поля, га", f"{mtr['field_area_ha']:.3f}")
    c8.metric("Покрыто, га",      f"{mtr['sprayed_area_ha']:.3f}")
    c9, c10 = st.columns(2)
    c9.metric("Площадь поля, м²", f"{mtr['field_area_m2']:.1f}")
    c10.metric("Покрыто, м²",     f"{mtr['sprayed_area_m2']:.1f}")


# ======= ЭКСПОРТ МАРШРУТА (WGS84, с дискретизацией по шагу в метрах) =======
if route and export_btn:
    try:
        result = export_route_geojson_csv(
            route=route,
            project_file=project_file,
            export_name=export_name,
            export_step_m=export_step_m,
        )

        geojson_path = result["geojson_path"]
        csv_path = result["csv_path"]

        colg, colc = st.columns(2)
        with open(geojson_path, "rb") as fh:
            colg.download_button(
                "⬇️ GeoJSON (WGS84)",
                fh,
                file_name=os.path.basename(geojson_path),
                mime="application/geo+json",
                use_container_width=True,
            )
        with open(csv_path, "rb") as fh:
            colc.download_button(
                "⬇️ CSV (точки по шагу)",
                fh,
                file_name=os.path.basename(csv_path),
                mime="text/csv",
                use_container_width=True,
            )

        st.success(f"Экспорт готов: {geojson_path} и {csv_path}")

    except Exception as e:
        st.error(f"Ошибка экспорта: {e}")


# ======= ЭКСПОРТ В MISSION PLANNER (.waypoints) =======
if route and mp_export_btn:
    try:
        result = export_mission_planner(
            route=route,
            project_file=project_file,
            project_name=project_name,
            mp_filename=mp_filename,
            mp_step_m=mp_step_m,
            mp_alt_agl=mp_alt_agl,
        )
        wpl_path = result["wpl_path"]

        with open(wpl_path, "rb") as fh:
            st.download_button(
                "⬇️ Mission Planner (.waypoints)",
                fh,
                file_name=os.path.basename(wpl_path),
                mime="text/plain",
                use_container_width=True,
            )
        st.success(f"Готово: {wpl_path}")
    except Exception as e:
        st.error(f"Ошибка экспорта: {e}")


# =============== ЛОГИ ===============
if st.session_state["build_log"]:
    st.subheader("Логи построения")
    for line in st.session_state["build_log"]:
        st.text(line)
