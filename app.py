import os, json, time, math, traceback
from typing import List, Dict, Any, Optional

import streamlit as st
from streamlit_folium import st_folium
import folium
from pyproj import Geod

from shapely.geometry import shape, Point, LineString, Polygon, mapping
from shapely.ops import unary_union
from math import radians, atan2, cos, sin, tan

# наши модули
from geo.crs import context_from_many_geojson, to_utm_geom, to_wgs_geom
from route.cover_f2c import build_cover            # ТЕПЕРЬ покрытие поля — только F2C
from route.transit import build_transit_full       # простая эвристика долёта/возврата
from metrics.estimates import estimate_mission, EstimateOptions

from route.landing_and_takeoff import build_wpl_from_local_route


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
    headland_factor = st.slider("Кромка (x ширины корпуса)", 0.0, 8.0, 3.0, 0.5)
    route_order = st.selectbox("Порядок обхода сватов", ["snake", "boustro", "spiral"], index=0)
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

def sprayed_polygon(field_poly_m: Polygon, swaths: List[LineString], spray_width_m: float) -> Optional[Polygon]:
    """Зона удобрения как union буферов проходов (spray_width/2), обрезанный полем."""
    if not field_poly_m or field_poly_m.is_empty or not swaths:
        return None
    half = max(spray_width_m, 0.0) / 2.0
    if half <= 0.0:
        return None
    bufs = [ln.buffer(half, join_style=2, cap_style=2) for ln in swaths if ln and not ln.is_empty]
    if not bufs:
        return None
    cover = unary_union(bufs)
    sprayed = cover.intersection(field_poly_m)
    if sprayed.is_empty:
        return None
    return sprayed

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

# =============== ПОСТРОЕНИЕ МАРШРУТА ИЗ ФАЙЛА ===============
def build_route_from_file(project_path: str):
    clear_log()
    log(f"🟦 Старт построения из файла: {project_path}")

    if not os.path.exists(project_path):
        log("❌ Файл проекта не найден")
        raise FileNotFoundError(f"Файл не найден: {project_path}")

    with open(project_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    log("📥 JSON прочитан")

    ge = data.get("geoms", {})
    field_gj_saved = ge.get("field")
    runway_gj_saved = ge.get("runway_centerline")
    nfz_gj_saved = ge.get("nfz", []) or []
    if not field_gj_saved or not runway_gj_saved:
        log("❌ В файле нет поля или ВПП")
        raise ValueError("В файле проекта нет поля или ВПП")

    # CRS и метры
    ctx = context_from_many_geojson([field_gj_saved, runway_gj_saved, *nfz_gj_saved])
    log(f"🗺️ CRS выбран (UTM EPSG={ctx.epsg}, зона={ctx.zone}{ctx.hemisphere})")

    field_m = to_utm_geom(shape(field_gj_saved), ctx)
    runway_m = to_utm_geom(shape(runway_gj_saved), ctx)
    nfz_m = [to_utm_geom(shape(g), ctx) for g in nfz_gj_saved]
    log("📐 Геометрии переведены в метры (UTM)")

    # покрытие поля — ТОЛЬКО F2C
    ac = data.get("aircraft", {})
    spray_w = float(ac.get("spray_width_m", 20.0))
    turn_r  = float(ac.get("turn_radius_m", 40.0))
    headland_factor = float(ac.get("headland_factor", 3.0))
    objective = ac.get("objective", "n_swath")
    route_order = ac.get("route_order", "snake")
    use_cc = bool(ac.get("use_cc", True))

    log(f"🌾 F2C покрытие: width={spray_w}м, Rmin={turn_r}м, headland={headland_factor}w, "
        f"objective={objective}, order={route_order}, CC={use_cc}")

    cover = build_cover(
        field_poly_m=field_m,
        runway_m=runway_m,
        spray_width_m=spray_w,
        headland_factor=headland_factor,
        objective=objective,
        route_order=route_order,
        use_continuous_curvature=use_cc,
        min_turn_radius_m=turn_r,
    )
    log(f"✅ Покрытие готово: swaths={len(cover.swaths)}, angle≈{cover.angle_used_deg:.1f}°")

    # транзиты (простая эвристика обхода NFZ)
    log("✈️ Строим долёт/возврат (простая эвристика обхода NFZ, буфер 10 м)")
    trans = build_transit_full(
        runway_m=runway_m,
        first_swath=cover.swaths[0],
        last_swath=cover.swaths[-1],
        nfz_polys_m=nfz_m,
        return_to="start",
        turn_r=turn_r,
        nfz_safety_buffer_m=10.0
    )
    log("✅ Транзиты построены")

    # зона удобрения
    sprayed_m = None
    try:
        sprayed_m = (sprayed_polygon(field_m, cover.swaths, spray_w) or None)
        log("🟥 Зона удобрения рассчитана")
    except Exception as e:
        log(f"⚠️ Не удалось построить зону удобрения: {e}")

    # метрики
    opts = EstimateOptions(
        transit_speed_ms=20.0, spray_speed_ms=15.0,
        fuel_burn_lph=8.0, fert_rate_l_per_ha=10.0,
        spray_width_m=spray_w,
    )
    est = estimate_mission(
        field_poly_m=field_m,
        swaths=cover.swaths,
        cover_path_m=cover.cover_path,
        to_field_m=trans.to_field,
        back_home_m=trans.back_home,
        opts=opts
    )
    log("📊 Метрики рассчитаны")

    # в WGS для отображения
    to_field_wgs   = to_wgs_geom(trans.to_field, ctx)
    back_home_wgs  = to_wgs_geom(trans.back_home, ctx)
    takeoff_cfg = trans.takeoff_cfg
    landing_cfg = trans.landing_cfg
    cover_path_wgs = to_wgs_geom(cover.cover_path, ctx)
    swaths_wgs     = [to_wgs_geom(s, ctx) for s in cover.swaths]
    sprayed_wgs    = to_wgs_geom(sprayed_m, ctx) if sprayed_m is not None else None
    field_wgs      = shape(field_gj_saved)  # уже WGS
    nfz_wgs        = [shape(g) for g in nfz_gj_saved]

    st.session_state["route"] = {
        "geo": {
            "to_field": mapping(to_field_wgs),
            "back_home": mapping(back_home_wgs),
            "cover_path": mapping(cover_path_wgs),
            "swaths": [mapping(s) for s in swaths_wgs],
            "sprayed": mapping(sprayed_wgs) if sprayed_wgs is not None else None,
            "field": mapping(field_wgs),
            "nfz": [mapping(g) for g in nfz_wgs],

        },
        "config": {
            "takeoff_cfg": takeoff_cfg,
            "landing_cfg": landing_cfg,
        },
        "metrics": {
            "length_total_m": est.length_total_m,
            "length_transit_m": est.length_transit_m,
            "length_spray_m": est.length_spray_m,
            "time_total_min": est.time_total_min,
            "time_transit_min": est.time_transit_min,
            "time_spray_min": est.time_spray_min,
            "fuel_l": est.fuel_l,
            "fert_l": est.fert_l,
            "field_area_ha": est.field_area_ha,
            "sprayed_area_ha": est.sprayed_area_ha,
        }
    }
    log("💾 Результат сохранён в session_state['route']")

if clear_btn:
    st.session_state["route"] = None
    clear_log()
    st.success("Маршрут очищён.")

if build_btn:
    try:
        build_route_from_file(project_file)
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
    folium.GeoJson(route["geo"]["to_field"],  name="Долёт",
                   style_function=lambda x: {"color":"#1f77b4","weight":4,"dashArray":"5,5"}).add_to(m2)
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


# ======= ЭКСПОРТ МАРШРУТА (WGS84, с дискретизацией по шагу в метрах) =======
if route and export_btn:
    try:
        # 1) Подгружаем проект, чтобы восстановить контекст CRS (для метра)
        if not os.path.exists(project_file):
            st.error("Файл проекта не найден для экспорта.")
        else:
            with open(project_file, "r", encoding="utf-8") as f:
                data_for_ctx = json.load(f)
            ge = data_for_ctx.get("geoms", {})
            field_for_ctx = ge.get("field")
            runway_for_ctx = ge.get("runway_centerline")
            nfz_for_ctx = ge.get("nfz", []) or []
            if not field_for_ctx or not runway_for_ctx:
                st.error("В файле проекта нет поля или ВПП — не могу определить проекцию.")
            else:
                # 2) Собираем CRS и переводим маршрутные линии в метры
                ctx = context_from_many_geojson([field_for_ctx, runway_for_ctx, *nfz_for_ctx])

                def _wgs_ls_to_m(ls_gj):
                    return to_utm_geom(shape(ls_gj), ctx)

                to_field_wgs_gj  = route["geo"]["to_field"]
                back_home_wgs_gj = route["geo"]["back_home"]
                cover_wgs_gj     = route["geo"]["cover_path"]

                to_field_m  = _wgs_ls_to_m(to_field_wgs_gj)
                back_home_m = _wgs_ls_to_m(back_home_wgs_gj)
                cover_m     = _wgs_ls_to_m(cover_wgs_gj)

                # 3) Дискретизация (в метрах), затем обратно в WGS
                step = float(export_step_m)
                samples = {
                    "to_field":  sample_linestring_m(to_field_m,  step),
                    "cover":     sample_linestring_m(cover_m,     step),
                    "back_home": sample_linestring_m(back_home_m, step),
                }

                samples_wgs = {
                    seg: [to_wgs_geom(p, ctx) for p in pts] for seg, pts in samples.items()
                }

                # 4) Пишем GeoJSON (FeatureCollection с LineString’ами) и CSV с точками
                export_dir = "data/exports"
                os.makedirs(export_dir, exist_ok=True)
                base = os.path.join(export_dir, f"{export_name.strip() or 'route'}_{int(step)}m")

                # 4.1 GeoJSON: исходные «неразреженные» LineString в WGS + свойства
                export_fc = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"segment": "to_field"},
                            "geometry": route["geo"]["to_field"],
                        },
                        {
                            "type": "Feature",
                            "properties": {"segment": "cover"},
                            "geometry": route["geo"]["cover_path"],
                        },
                        {
                            "type": "Feature",
                            "properties": {"segment": "back_home"},
                            "geometry": route["geo"]["back_home"],
                        },
                    ],
                }
                geojson_path = f"{base}.geojson"
                with open(geojson_path, "w", encoding="utf-8") as f:
                    json.dump(export_fc, f, ensure_ascii=False, indent=2)

                # 4.2 CSV точек (дискретизированные точки)
                import csv
                csv_path = f"{base}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["segment", "idx", "lat", "lon"])
                    for seg, pts in samples_wgs.items():
                        for i, p in enumerate(pts):
                            lon, lat = p.x, p.y
                            w.writerow([seg, i, f"{lat:.8f}", f"{lon:.8f}"])

                # 5) Кнопки скачивания
                colg, colc = st.columns(2)
                with open(geojson_path, "rb") as fh:
                    colg.download_button("⬇️ GeoJSON (WGS84)", fh, file_name=os.path.basename(geojson_path), mime="application/geo+json", use_container_width=True)
                with open(csv_path, "rb") as fh:
                    colc.download_button("⬇️ CSV (точки по шагу)", fh, file_name=os.path.basename(csv_path), mime="text/csv", use_container_width=True)

                st.success(f"Экспорт готов: {geojson_path} и {csv_path}")

    except Exception as e:
        st.error(f"Ошибка экспорта: {e}")


# ======= ЭКСПОРТ В MISSION PLANNER (.waypoints) =======
if route and mp_export_btn:
    # Нам нужен контекст CRS для метрической дискретизации
    if not os.path.exists(project_file):
        st.error("Файл проекта не найден — не могу определить проекцию.")
    else:
        with open(project_file, "r", encoding="utf-8") as f:
            data_for_ctx = json.load(f)
        ge = data_for_ctx.get("geoms", {})
        field_for_ctx = ge.get("field")
        runway_for_ctx = ge.get("runway_centerline")
        nfz_for_ctx = ge.get("nfz", []) or []
        if not field_for_ctx or not runway_for_ctx:
            st.error("В файле проекта нет поля или ВПП — не могу определить проекцию.")
        else:
            # CRS
            ctx = context_from_many_geojson([field_for_ctx, runway_for_ctx, *nfz_for_ctx])

            # Берём линии маршрута из session_state (в WGS), переводим в метры
            def _wgs_ls_to_m(ls_gj):
                return to_utm_geom(shape(ls_gj), ctx)

            to_field_m  = _wgs_ls_to_m(route["geo"]["to_field"])
            cover_m     = _wgs_ls_to_m(route["geo"]["cover_path"])
            back_home_m = _wgs_ls_to_m(route["geo"]["back_home"])

            # Дискретизация
            step = float(mp_step_m)
            pts_to   = sample_linestring_m(to_field_m,  step)
            pts_cov  = sample_linestring_m(cover_m,     step)
            pts_back = sample_linestring_m(back_home_m, step)

            # Склейка точек: to_field -> cover -> back_home
            pts_all_m = pts_to + pts_cov + pts_back
            if not pts_all_m:
                st.error("Нет точек для экспорта.")
            else:
                # Переводим в WGS84
                pts_all_wgs = [to_wgs_geom(p, ctx) for p in pts_all_m]

                # Строим .waypoints
                # wpl_text = build_qgc_wpl(
                #     pts_all_wgs,
                #     alt_agl=float(mp_alt_agl),
                #     speed_ms=float(mp_speed_ms),
                #     include_takeoff=True,
                #     include_rtl=True
                # )
                runway_m = to_utm_geom(shape(runway_for_ctx), ctx)
                runway_start_wgs = Point(runway_m.coords[0])

                wpl_text = build_wpl_from_local_route(
                    runway_m=runway_m,
                    route_points_m=pts_all_m,  # точки маршрута от CCA до FAF (включая поле)
                    ctx=ctx,
                    takeoff_cfg=route["config"]["takeoff_cfg"],
                    landing_cfg=route["config"]["landing_cfg"]
                )

                # Сохраняем и отдаём
                export_dir = "data/exports"
                os.makedirs(export_dir, exist_ok=True)
                base = (mp_filename.strip() or f"{project_name}_mission").replace(" ", "_")
                wpl_path = os.path.join(export_dir, f"{base}.waypoints")
                with open(wpl_path, "w", encoding="utf-8") as f:
                    f.write(wpl_text)

                with open(wpl_path, "rb") as fh:
                    st.download_button(
                        "⬇️ Mission Planner (.waypoints)",
                        fh,
                        file_name=os.path.basename(wpl_path),
                        mime="text/plain",
                        use_container_width=True
                    )
                st.success(f"Готово: {wpl_path}")


# =============== ЛОГИ ===============
if st.session_state["build_log"]:
    st.subheader("Логи построения")
    for line in st.session_state["build_log"]:
        st.text(line)