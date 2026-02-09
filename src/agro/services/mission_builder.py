from __future__ import annotations

import json
import os
from typing import Callable, Optional, Dict, Any, List

from shapely.geometry import shape, LineString, Polygon, mapping
from shapely.ops import unary_union

from agro.domain.geo.crs import context_from_many_geojson, to_utm_geom, to_wgs_geom
from agro.infra.f2c.cover_f2c import build_cover
from agro.domain.routing.transit import build_transit_full
from agro.domain.metrics.estimates import estimate_mission, EstimateOptions


def _log(log_fn: Optional[Callable[[str], None]], msg: str) -> None:
    if log_fn:
        log_fn(msg)


def _sprayed_polygon(field_poly_m: Polygon, swaths: List[LineString], spray_width_m: float) -> Optional[Polygon]:
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


def build_route_from_file(project_path: str, *, log_fn: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    _log(log_fn, f"🟦 Старт построения из файла: {project_path}")

    if not os.path.exists(project_path):
        _log(log_fn, "❌ Файл проекта не найден")
        raise FileNotFoundError(f"Файл не найден: {project_path}")

    with open(project_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _log(log_fn, "📥 JSON прочитан")

    ge = data.get("geoms", {})
    field_gj_saved = ge.get("field")
    runway_gj_saved = ge.get("runway_centerline")
    nfz_gj_saved = ge.get("nfz", []) or []
    if not field_gj_saved or not runway_gj_saved:
        _log(log_fn, "❌ В файле нет поля или ВПП")
        raise ValueError("В файле проекта нет поля или ВПП")

    # CRS и метры
    ctx = context_from_many_geojson([field_gj_saved, runway_gj_saved, *nfz_gj_saved])
    _log(log_fn, f"🗺️ CRS выбран (UTM EPSG={ctx.epsg}, зона={ctx.zone}{ctx.hemisphere})")

    field_m = to_utm_geom(shape(field_gj_saved), ctx)
    runway_m = to_utm_geom(shape(runway_gj_saved), ctx)
    nfz_m = [to_utm_geom(shape(g), ctx) for g in nfz_gj_saved]
    _log(log_fn, "📐 Геометрии переведены в метры (UTM)")

    # покрытие поля — ТОЛЬКО F2C
    ac = data.get("aircraft", {})
    spray_w = float(ac.get("spray_width_m", 20.0))
    turn_r = float(ac.get("turn_radius_m", 40.0))
    headland_factor = float(ac.get("headland_factor", 3.0))
    objective = ac.get("objective", "n_swath")
    route_order = ac.get("route_order", "snake")
    use_cc = bool(ac.get("use_cc", True))

    _log(
        log_fn,
        f"🌾 F2C покрытие: width={spray_w}м, Rmin={turn_r}м, headland={headland_factor}w, "
        f"objective={objective}, order={route_order}, CC={use_cc}",
    )

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
    _log(log_fn, f"✅ Покрытие готово: swaths={len(cover.swaths)}, angle≈{cover.angle_used_deg:.1f}°")

    # транзиты
    _log(log_fn, "✈️ Строим долёт/возврат (простая эвристика обхода NFZ, буфер 10 м)")
    trans = build_transit_full(
        runway_m=runway_m,
        first_swath=cover.swaths[0],
        last_swath=cover.swaths[-1],
        nfz_polys_m=nfz_m,
        turn_r=turn_r,
    )
    _log(log_fn, "✅ Транзиты построены")

    # зона удобрения
    sprayed_m = None
    try:
        sprayed_m = (_sprayed_polygon(field_m, cover.swaths, spray_w) or None)
        _log(log_fn, "🟥 Зона удобрения рассчитана")
    except Exception as e:
        _log(log_fn, f"⚠️ Не удалось построить зону удобрения: {e}")

    # метрики
    opts = EstimateOptions(
        transit_speed_ms=20.0,
        spray_speed_ms=15.0,
        fuel_burn_lph=8.0,
        fert_rate_l_per_ha=10.0,
        spray_width_m=spray_w,
    )
    est = estimate_mission(
        field_poly_m=field_m,
        swaths=cover.swaths,
        cover_path_m=cover.cover_path,
        to_field_m=trans.to_field,
        back_home_m=trans.back_home,
        opts=opts,
    )
    _log(log_fn, "📊 Метрики рассчитаны")

    # в WGS для отображения
    to_field_wgs = to_wgs_geom(trans.to_field, ctx)
    back_home_wgs = to_wgs_geom(trans.back_home, ctx)
    takeoff_cfg = trans.takeoff_cfg
    landing_cfg = trans.landing_cfg
    cover_path_wgs = to_wgs_geom(cover.cover_path, ctx)
    swaths_wgs = [to_wgs_geom(s, ctx) for s in cover.swaths]
    sprayed_wgs = to_wgs_geom(sprayed_m, ctx) if sprayed_m is not None else None
    field_wgs = shape(field_gj_saved)  # уже WGS
    nfz_wgs = [shape(g) for g in nfz_gj_saved]

    route = {
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
        },
    }
    _log(log_fn, "💾 Результат сформирован")
    return route
