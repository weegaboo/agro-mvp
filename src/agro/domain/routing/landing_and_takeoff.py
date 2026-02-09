from typing import List, Tuple, Callable, Dict, Optional, Union
from dataclasses import dataclass
import math
from shapely.geometry import LineString, Point
from agro.domain.geo.crs import CRSContext, to_wgs_geom


# ---------- утилиты (метры, локальная плоскость) ----------
def _uv_len(a: Tuple[float,float], b: Tuple[float,float]):
    vx, vy = b[0]-a[0], b[1]-a[1]
    L = math.hypot(vx, vy)
    if L == 0: raise ValueError("Runway endpoints coincide")
    return (vx/L, vy/L, L)

def _offset_along(p: Tuple[float,float], u: Tuple[float,float], s: float) -> Point:
    return Point(p[0] + u[0]*s, p[1] + u[1]*s)

# ==========================================================
# 1) ВЗЛЁТ: CCA-точка + конфиг для .waypoints
# ==========================================================
@dataclass
class TakeoffConfig:
    takeoff_alt_agl: float = 10.0       # высота завершения NAV_TAKEOFF (м)
    roll_distance_m: float = 150.0      # отступ до первой WP (разбег) (м)
    climb_angle_deg: float = 12.0       # угол набора до крейсерской (°)
    speed_ms: float = 18.0              # DO_CHANGE_SPEED (м/с)

def build_takeoff_anchor(
    runway_m: LineString,
    cruise_alt_agl: float = 30.0,
    cfg: TakeoffConfig = TakeoffConfig()
) -> Tuple[Point, Dict]:
    """
    Возвращает:
      - cca_point_m: Point (метры) — точка, где самолёт гарантированно вышел на cruise_alt_agl
      - takeoff_cfg: dict — компактный конфиг для .waypoints
    Логика: порог -> roll_distance (разбег) -> далее по оси добор (cruise - takeoff_alt)/tan(gamma).
    """
    (x0,y0), (x1,y1) = map(tuple, runway_m.coords[:2])
    ux, uy, Lrw = _uv_len((x0,y0), (x1,y1))

    # сколько по земле нужно после "TAKEOFF-этажа", чтобы добрать до cruise_alt
    grad = math.tan(math.radians(cfg.climb_angle_deg))  # ~ ROC / Vg
    s_climb = max(0.0, (cruise_alt_agl - cfg.takeoff_alt_agl)) / max(grad, 1e-6)

    s_cca = cfg.roll_distance_m + s_climb
    cca = _offset_along((x0,y0), (ux,uy), s_cca)

    takeoff_cfg = {
        "takeoff_alt_agl": cfg.takeoff_alt_agl,
        "roll_distance_m": cfg.roll_distance_m,
        "speed_ms": cfg.speed_ms
    }
    return cca, takeoff_cfg

# ==========================================================
# 2) ПОСАДКА: FAF-точка + конфиг для .waypoints
# ==========================================================
@dataclass
class LandingConfig:
    faf_alt_agl: float = 30.0         # высота FAF (м AGL)
    glide_angle_deg: float = 4.0      # угол глиссады (°)
    min_faf_distance_m: float = 400.0 # минимальная дальность FAF (м)
    include_rtl: bool = True          # добавлять RTL в конце миссии

def build_landing_anchor(
    runway_m: LineString,
    cfg: LandingConfig = LandingConfig(),
    *,
    towards: str = "start",   # "start" -> посадка в начало ВПП; "end" -> посадка в конец ВПП
) -> Tuple[Point, Dict]:
    """
    Возвращает:
      - faf_point_m: Point — точка FAF на одной оси с ВПП, откуда начинается прямая к LAND
      - landing_cfg: dict — конфиг для сборки .waypoints

    Логика:
      u = единичный вектор от runway_start -> runway_end
      Если towards=="start": LAND в runway_start, FAF = start + u * S_faf  (заход со стороны конца ВПП)
      Если towards=="end":   LAND в runway_end,   FAF = end   - u * S_faf  (заход со стороны начала ВПП)
    """
    (x0,y0), (x1,y1) = map(tuple, runway_m.coords[:2])   # (x0,y0)=runway_start, (x1,y1)=runway_end
    ux, uy, _ = _uv_len((x0,y0), (x1,y1))

    # требуемая дальность по глиссаде
    need = cfg.faf_alt_agl / max(math.tan(math.radians(cfg.glide_angle_deg)), 1e-6)
    S_faf = max(need, cfg.min_faf_distance_m)

    towards = towards.lower()
    if towards == "start":
        # LAND @ start, летим со стороны конца: FAF вперед по оси от порога
        faf = _offset_along((x0,y0), (ux,uy), S_faf)
    elif towards == "end":
        # LAND @ end, летим со стороны начала: FAF назад к порогу конца
        faf = _offset_along((x1,y1), (-ux,-uy), S_faf)
    else:
        raise ValueError("towards must be 'start' or 'end'")

    landing_cfg = {
        "faf_alt_agl": cfg.faf_alt_agl,
        "glide_angle_deg": cfg.glide_angle_deg,
        "min_faf_distance_m": cfg.min_faf_distance_m,
        "include_rtl": cfg.include_rtl,
        "towards": towards,   # чтобы знать куда планируем LAND при сборке WPL
    }
    return faf, landing_cfg

# ==========================================================
# 3) Сборка QGC WPL 110 из: runway, route (CCA->...->FAF), configs, конвертера в WGS84
# ==========================================================

def _uv_len(a: Tuple[float,float], b: Tuple[float,float]):
    vx, vy = b[0]-a[0], b[1]-a[1]
    L = math.hypot(vx, vy)
    if L == 0:
        raise ValueError("Runway endpoints coincide")
    return (vx/L, vy/L, L)


RoutePt = Union[Point, Tuple[Point, float]]  # Point или (Point, alt)


def build_wpl_from_local_route(
    runway_m: LineString,
    route_points_m: List[RoutePt],            # CCA..FAF (последняя точка = FAF). Может быть Point или (Point, alt)
    takeoff_cfg: Dict,                         # {'takeoff_alt_agl','roll_distance_m','speed_ms'}
    landing_cfg: Dict,                         # {'faf_alt_agl','include_rtl', ...}
    ctx,
    *,
    cruise_alt_agl: float = 30.0,
    include_midpoint_on_rw: bool = False,
    mid_fraction: float = 0.5,
    repeat_faf_waypoint: bool = False,
    dedupe_eps_m: float = 0.5
) -> str:
    (x0, y0), (x1, y1) = map(tuple, runway_m.coords[:2])
    vx, vy = x1 - x0, y1 - y0
    Lrw = math.hypot(vx, vy)
    if Lrw == 0:
        raise ValueError("Runway endpoints coincide")
    ux, uy = vx / Lrw, vy / Lrw

    rw_wgs: LineString = to_wgs_geom(runway_m, ctx)
    (lon0, lat0), (lon1, lat1) = rw_wgs.coords[:2]

    # ---- helper: нормализация точки к (Point, alt|None) ----
    def _as_pt_alt(it: RoutePt) -> Tuple[Point, Optional[float]]:
        # shapely Point
        if hasattr(it, "geom_type"):
            if it.geom_type != "Point":
                raise TypeError(f"route_points_m must contain Points; got {it.geom_type}")
            return it, None
        # (Point, alt)
        if isinstance(it, tuple) and len(it) == 2 and hasattr(it[0], "geom_type"):
            pt = it[0]
            if pt.geom_type != "Point":
                raise TypeError(f"route_points_m must contain Points; got {pt.geom_type}")
            return pt, float(it[1])
        raise TypeError(f"Unsupported route point type: {type(it)} -> {it}")

    # ---- helper: дедуп подряд идущих точек в локальных метрах (учитываем только Point) ----
    def _dedupe(seq_pts: List[RoutePt], eps=dedupe_eps_m) -> List[RoutePt]:
        out: List[RoutePt] = []
        last_pt: Optional[Point] = None
        for it in seq_pts:
            pt, alt = _as_pt_alt(it)
            if last_pt is None or pt.distance(last_pt) > eps:
                out.append(it)
                last_pt = pt
        return out

    route_points_m = _dedupe(route_points_m)

    lines = ["QGC WPL 110"]
    FRAME, AUTO = 3, 1
    seq = 0

    # 0) NAV_TAKEOFF @ порог (Current=1)
    lines.append(
        f"{seq} 1 {FRAME} 22 0 0 0 0 {lat0:.7f} {lon0:.7f} {float(takeoff_cfg['takeoff_alt_agl']):.2f} {AUTO}"
    ); seq += 1

    # 1) (опц.) mid-WP на ВПП (как нав. пункт)
    if include_midpoint_on_rw:
        s_mid = max(0.0, min(1.0, mid_fraction)) * Lrw
        mid_m = Point(x0 + ux * s_mid, y0 + uy * s_mid)
        mid_wgs: Point = to_wgs_geom(mid_m, ctx)
        lines.append(
            f"{seq} 0 {FRAME} 16 0 0 0 0 {mid_wgs.y:.7f} {mid_wgs.x:.7f} {cruise_alt_agl:.2f} {AUTO}"
        ); seq += 1

    # 2) первая WP после взлёта (через roll_distance_m)
    s_roll = float(takeoff_cfg["roll_distance_m"])
    wp_after_tko_m = Point(x0 + ux * s_roll, y0 + uy * s_roll)
    wp_after_tko_wgs: Point = to_wgs_geom(wp_after_tko_m, ctx)
    lines.append(
        f"{seq} 0 {FRAME} 16 0 0 0 0 {wp_after_tko_wgs.y:.7f} {wp_after_tko_wgs.x:.7f} {cruise_alt_agl:.2f} {AUTO}"
    ); seq += 1

    # 3) DO_CHANGE_SPEED (чтобы не шла первой в MP)
    lines.append(
        f"{seq} 0 {FRAME} 178 0 {float(takeoff_cfg['speed_ms']):.3f} 0 0 0 0 0 {AUTO}"
    ); seq += 1

    # 4) ваш маршрут (alt берём из (Point, alt), иначе cruise_alt_agl)
    if not route_points_m:
        raise ValueError("route_points_m is empty: требуется хотя бы одна точка (включая FAF).")

    route_wgs: List[Tuple[Point, Optional[float]]] = []
    for it in route_points_m:
        pt_m, alt = _as_pt_alt(it)
        pt_wgs: Point = to_wgs_geom(pt_m, ctx)
        route_wgs.append((pt_wgs, alt))

    for pt_wgs, alt in route_wgs:
        alt_used = cruise_alt_agl if alt is None else float(alt)
        lines.append(
            f"{seq} 0 {FRAME} 16 0 0 0 0 {pt_wgs.y:.7f} {pt_wgs.x:.7f} {alt_used:.2f} {AUTO}"
        ); seq += 1

    # 5) DO_LAND_START @ FAF (последняя точка маршрута = FAF)
    #    Тут высота посадочного FAF как и раньше — landing_cfg['faf_alt_agl'] (или cruise_alt_agl),
    #    НЕ берём из профиля "overfly", чтобы посадка не ломалась.
    faf_alt = float(landing_cfg.get("faf_alt_agl", cruise_alt_agl))
    faf_wgs: Point = route_wgs[-1][0]
    lines.append(
        f"{seq} 0 {FRAME} 189 0 0 0 0 {faf_wgs.y:.7f} {faf_wgs.x:.7f} {faf_alt:.2f} {AUTO}"
    ); seq += 1

    # 6) (опц.) повтор FAF как WAYPOINT
    if repeat_faf_waypoint:
        lines.append(
            f"{seq} 0 {FRAME} 16 0 0 0 0 {faf_wgs.y:.7f} {faf_wgs.x:.7f} {faf_wgs.x:.7f} {faf_alt:.2f} {AUTO}"
        ); seq += 1

    # 7) NAV_LAND @ порог (alt=0)
    lines.append(
        f"{seq} 0 {FRAME} 21 0 0 0 0 {lat0:.7f} {lon0:.7f} 0 {AUTO}"
    ); seq += 1

    # 8) (опц.) RTL
    if bool(landing_cfg.get("include_rtl", True)):
        lines.append(
            f"{seq} 0 {FRAME} 20 0 0 0 0 0 0 0 {AUTO}"
        ); seq += 1

    return "\n".join(lines)
