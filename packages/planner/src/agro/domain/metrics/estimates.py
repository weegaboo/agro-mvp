"""Mission metrics: lengths, time, fuel, and mixture usage.

Assumptions:
    - Geometry is in meters (UTM).
    - Fuel burn is per kilometer.
    - Mixture use is per hectare of sprayed area.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union


# --------------------------- опции и результат --------------------------- #

@dataclass
class EstimateOptions:
    """Options for mission estimation."""
    # скорости
    transit_speed_ms: float = 20.0   # м/с на долёте/возврате (≈72 км/ч)
    spray_speed_ms: float = 15.0     # м/с на покрытии (≈54 км/ч)

    # ресурсы
    fuel_burn_l_per_km: float = 0.35 # л/км расход топлива
    fert_rate_l_per_ha: float = 10.0 # л/га норма внесения

    # техника обработки
    spray_width_m: float = 20.0      # ширина захвата (м)

    # округление для отображения
    round_len_m: int = 1
    round_time_min: int = 1
    round_liters: int = 1
    round_area_ha: int = 3


@dataclass
class EstimateResult:
    """Calculated mission metrics."""
    # длины
    length_total_m: float
    length_transit_m: float
    length_spray_m: float

    # время
    time_total_min: float
    time_transit_min: float
    time_spray_min: float

    # ресурсы
    fuel_l: float
    fert_l: float

    # площади
    field_area_ha: float
    sprayed_area_ha: float
    field_area_m2: float
    sprayed_area_m2: float

    # сырой breakdown (можно отобразить в UI при желании)
    extras: Dict[str, Any]


# ------------------------------ утилиты ------------------------------ #

def _len_m(ls: Optional[LineString]) -> float:
    """Return LineString length in meters (0 if empty)."""
    return 0.0 if ls is None or ls.is_empty else float(ls.length)


def _area_ha(pg: Optional[Polygon]) -> float:
    """Return polygon area in hectares (0 if empty)."""
    return 0.0 if pg is None or pg.is_empty else float(pg.area) / 10_000.0


def _area_m2(pg: Optional[Polygon]) -> float:
    """Return polygon area in square meters (0 if empty)."""
    return 0.0 if pg is None or pg.is_empty else float(pg.area)


# ------------------------------ площадь покрытия ------------------------------ #

def compute_sprayed_area_m2(field_poly_m: Polygon, swaths: List[LineString], spray_width_m: float) -> float:
    """Compute sprayed area in square meters.

    Args:
        field_poly_m: Field polygon in meters (UTM).
        swaths: Swath lines in meters (UTM).
        spray_width_m: Spray width in meters.

    Returns:
        Sprayed area in square meters.
    """
    if not field_poly_m or field_poly_m.is_empty or not swaths:
        return 0.0
    half = max(spray_width_m, 0.0) / 2.0
    if half <= 0.0:
        return 0.0
    # Жёсткие углы на соединениях, плоские концы у линий (cap_style=2=flat) — ближе к тракторным проходам
    buffers = [ln.buffer(half, join_style=2, cap_style=2) for ln in swaths if ln and not ln.is_empty]
    if not buffers:
        return 0.0
    cover = unary_union(buffers)
    sprayed = cover.intersection(field_poly_m)
    return _area_m2(sprayed)


# ------------------------------ основная функция ------------------------------ #

def estimate_mission(
    field_poly_m: Polygon,
    swaths: List[LineString],
    cover_path_m: LineString,
    to_field_m: LineString,
    back_home_m: LineString,
    opts: EstimateOptions = EstimateOptions(),
) -> EstimateResult:
    """Estimate mission metrics from explicit transit paths.

    Args:
        field_poly_m: Field polygon in meters (UTM).
        swaths: Swath lines in meters (UTM).
        cover_path_m: Full coverage path in meters (UTM).
        to_field_m: Transit to field in meters (UTM).
        back_home_m: Transit back to runway in meters (UTM).
        opts: Estimation options.

    Returns:
        EstimateResult with lengths, time, fuel, mixture, and areas.
    """
    # длины
    L_transit = _len_m(to_field_m) + _len_m(back_home_m)
    # длина обработки — по cover_path (не суммируем swaths, чтобы не задвоить соединения)
    L_spray = _len_m(cover_path_m)
    L_total = L_transit + L_spray

    # время (минуты)
    t_transit_h = (L_transit / max(opts.transit_speed_ms, 0.1)) / 3600.0
    t_spray_h   = (L_spray   / max(opts.spray_speed_ms,   0.1)) / 3600.0
    t_transit_min = t_transit_h * 60.0
    t_spray_min   = t_spray_h * 60.0
    t_total_min   = t_transit_min + t_spray_min

    # топливо (по длине)
    L_total_km = L_total / 1000.0
    fuel_l = opts.fuel_burn_l_per_km * L_total_km

    # площадь поля / покрытая площадь
    field_m2 = _area_m2(field_poly_m)
    sprayed_m2 = compute_sprayed_area_m2(field_poly_m, swaths, opts.spray_width_m)
    field_ha = field_m2 / 10_000.0
    sprayed_ha = sprayed_m2 / 10_000.0

    # удобрения (по норме на покрытую площадь)
    fert_l = opts.fert_rate_l_per_ha * sprayed_ha

    # округление для UI
    def rnd(x, nd):
        return round(x, nd)

    res = EstimateResult(
        length_total_m = rnd(L_total, opts.round_len_m),
        length_transit_m = rnd(L_transit, opts.round_len_m),
        length_spray_m = rnd(L_spray, opts.round_len_m),

        time_total_min = rnd(t_total_min, opts.round_time_min),
        time_transit_min = rnd(t_transit_min, opts.round_time_min),
        time_spray_min = rnd(t_spray_min, opts.round_time_min),

        fuel_l = rnd(fuel_l, opts.round_liters),
        fert_l = rnd(fert_l, opts.round_liters),

        field_area_ha = rnd(field_ha, opts.round_area_ha),
        sprayed_area_ha = rnd(sprayed_ha, opts.round_area_ha),
        field_area_m2 = rnd(field_m2, 1),
        sprayed_area_m2 = rnd(sprayed_m2, 1),

        extras = {
            "transit_speed_ms": opts.transit_speed_ms,
            "spray_speed_ms": opts.spray_speed_ms,
            "fuel_burn_l_per_km": opts.fuel_burn_l_per_km,
            "fert_rate_l_per_ha": opts.fert_rate_l_per_ha,
            "spray_width_m": opts.spray_width_m,
        }
    )
    return res


def estimate_mission_from_lengths(
    *,
    field_poly_m: Polygon,
    swaths: List[LineString],
    cover_path_m: LineString,
    transit_length_m: float,
    opts: EstimateOptions = EstimateOptions(),
) -> EstimateResult:
    """Estimate mission metrics using total transit length.

    Args:
        field_poly_m: Field polygon in meters (UTM).
        swaths: Swath lines in meters (UTM).
        cover_path_m: Full coverage path in meters (UTM).
        transit_length_m: Total transit length in meters (UTM).
        opts: Estimation options.

    Returns:
        EstimateResult with lengths, time, fuel, mixture, and areas.
    """
    L_transit = float(max(0.0, transit_length_m))
    L_spray = _len_m(cover_path_m)
    L_total = L_transit + L_spray

    t_transit_h = (L_transit / max(opts.transit_speed_ms, 0.1)) / 3600.0
    t_spray_h = (L_spray / max(opts.spray_speed_ms, 0.1)) / 3600.0
    t_transit_min = t_transit_h * 60.0
    t_spray_min = t_spray_h * 60.0
    t_total_min = t_transit_min + t_spray_min

    L_total_km = L_total / 1000.0
    fuel_l = opts.fuel_burn_l_per_km * L_total_km

    field_m2 = _area_m2(field_poly_m)
    sprayed_m2 = compute_sprayed_area_m2(field_poly_m, swaths, opts.spray_width_m)
    field_ha = field_m2 / 10_000.0
    sprayed_ha = sprayed_m2 / 10_000.0

    fert_l = opts.fert_rate_l_per_ha * sprayed_ha

    def rnd(x, nd):
        return round(x, nd)

    return EstimateResult(
        length_total_m=rnd(L_total, opts.round_len_m),
        length_transit_m=rnd(L_transit, opts.round_len_m),
        length_spray_m=rnd(L_spray, opts.round_len_m),
        time_total_min=rnd(t_total_min, opts.round_time_min),
        time_transit_min=rnd(t_transit_min, opts.round_time_min),
        time_spray_min=rnd(t_spray_min, opts.round_time_min),
        fuel_l=rnd(fuel_l, opts.round_liters),
        fert_l=rnd(fert_l, opts.round_liters),
        field_area_ha=rnd(field_ha, opts.round_area_ha),
        sprayed_area_ha=rnd(sprayed_ha, opts.round_area_ha),
        field_area_m2=rnd(field_m2, 1),
        sprayed_area_m2=rnd(sprayed_m2, 1),
        extras={
            "transit_speed_ms": opts.transit_speed_ms,
            "spray_speed_ms": opts.spray_speed_ms,
            "fuel_burn_l_per_km": opts.fuel_burn_l_per_km,
            "fert_rate_l_per_ha": opts.fert_rate_l_per_ha,
            "spray_width_m": opts.spray_width_m,
            "transit_length_m_override": L_transit,
        },
    )
