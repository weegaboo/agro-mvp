# metrics/estimates.py
"""
Оценки по маршруту: длина, время, топливо, удобрения.

Как считается:
- Длины: из LineString'ов (в UTM, м).
- Время: разная скорость для транзита и обработки.
- Топливо: burn_rate (л/ч) * время (ч).
- Удобрение: norm (л/га) * sprayed_area_ha, где sprayed_area_ha — площадь union
  буферов проходов (spray_width/2) обрезанных полем.

Входные геометрии должны быть в МЕТРАХ (UTM).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union


# --------------------------- опции и результат --------------------------- #

@dataclass
class EstimateOptions:
    # скорости
    transit_speed_ms: float = 20.0   # м/с на долёте/возврате (≈72 км/ч)
    spray_speed_ms: float = 15.0     # м/с на покрытии (≈54 км/ч)

    # ресурсы
    fuel_burn_lph: float = 8.0       # л/ч расход топлива
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

    # сырой breakdown (можно отобразить в UI при желании)
    extras: Dict[str, Any]


# ------------------------------ утилиты ------------------------------ #

def _len_m(ls: Optional[LineString]) -> float:
    return 0.0 if ls is None or ls.is_empty else float(ls.length)


def _area_ha(pg: Optional[Polygon]) -> float:
    return 0.0 if pg is None or pg.is_empty else float(pg.area) / 10_000.0


# ------------------------------ площадь покрытия ------------------------------ #

def compute_sprayed_area_ha(field_poly_m: Polygon, swaths: List[LineString], spray_width_m: float) -> float:
    """Площадь фактического покрытия: union буферов линий (spray_width/2), обрезанный полем."""
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
    return _area_ha(sprayed)


# ------------------------------ основная функция ------------------------------ #

def estimate_mission(
    field_poly_m: Polygon,
    swaths: List[LineString],
    cover_path_m: LineString,
    to_field_m: LineString,
    back_home_m: LineString,
    opts: EstimateOptions = EstimateOptions(),
) -> EstimateResult:
    """
    Считает метрики миссии по сегментам маршрута.

    Параметры
    ---------
    field_poly_m : Polygon (UTM) — поле
    swaths       : List[LineString] (UTM) — отдельные проходы
    cover_path_m : LineString (UTM) — сводная "змейка" внутри поля
    to_field_m   : LineString (UTM) — долёт
    back_home_m  : LineString (UTM) — возврат
    opts         : EstimateOptions — скорости, нормы, ширина захвата

    Возвращает
    ----------
    EstimateResult
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

    # топливо
    fuel_l = opts.fuel_burn_lph * (t_transit_h + t_spray_h)

    # площадь поля / покрытая площадь
    field_ha   = _area_ha(field_poly_m)
    sprayed_ha = compute_sprayed_area_ha(field_poly_m, swaths, opts.spray_width_m)

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

        extras = {
            "transit_speed_ms": opts.transit_speed_ms,
            "spray_speed_ms": opts.spray_speed_ms,
            "fuel_burn_lph": opts.fuel_burn_lph,
            "fert_rate_l_per_ha": opts.fert_rate_l_per_ha,
            "spray_width_m": opts.spray_width_m,
        }
    )
    return res
