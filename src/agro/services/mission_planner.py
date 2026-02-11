"""Mission Planner (QGC WPL) export service."""

from __future__ import annotations

import json
import os
from typing import Dict, Any

from shapely.geometry import shape, Point, LineString

from agro.domain.geo.crs import context_from_many_geojson, to_utm_geom
from agro.domain.routing.field_nfz import apply_overfly_alt_profile
from agro.domain.routing.landing_and_takeoff import build_wpl_from_local_route


def _sample_linestring_m(ls_m: LineString, step_m: float) -> list[Point]:
    """Sample a LineString by step size in meters.

    Args:
        ls_m: LineString in meters (UTM).
        step_m: Sampling step in meters.

    Returns:
        List of sampled Points in meters.
    """
    if ls_m.is_empty:
        return []
    L = float(ls_m.length)
    if L <= 0:
        return [Point(ls_m.coords[0])]
    step = max(0.1, float(step_m))
    dists = [i * step for i in range(int(L // step))] + [L]
    return [ls_m.interpolate(d) for d in dists]


def export_mission_planner(
    *,
    route: Dict[str, Any],
    project_file: str,
    project_name: str,
    mp_filename: str,
    mp_step_m: float,
    mp_alt_agl: float,
    export_dir: str = "data/exports",
) -> Dict[str, str]:
    """Export route to Mission Planner WPL file.

    Args:
        route: Route dict with WGS84 geometry and configs.
        project_file: Path to project JSON for CRS context.
        project_name: Project name (fallback for filename).
        mp_filename: Output filename (without extension).
        mp_step_m: Sampling step in meters.
        mp_alt_agl: Cruise altitude above ground level (meters).
        export_dir: Output directory.

    Returns:
        Dict with `wpl_path`.

    Raises:
        FileNotFoundError: If project file is missing.
        ValueError: If required geometry is missing or no points to export.
    """
    if not os.path.exists(project_file):
        raise FileNotFoundError("Файл проекта не найден — не могу определить проекцию.")

    with open(project_file, "r", encoding="utf-8") as f:
        data_for_ctx = json.load(f)

    ge = data_for_ctx.get("geoms", {})
    field_for_ctx = ge.get("field")
    runway_for_ctx = ge.get("runway_centerline")
    nfz_for_ctx = ge.get("nfz", []) or []
    if not field_for_ctx or not runway_for_ctx:
        raise ValueError("В файле проекта нет поля или ВПП — не могу определить проекцию.")

    ctx = context_from_many_geojson([field_for_ctx, runway_for_ctx, *nfz_for_ctx])

    def _wgs_ls_to_m(ls_gj):
        return to_utm_geom(shape(ls_gj), ctx)

    to_field_m = _wgs_ls_to_m(route["geo"]["to_field"])
    cover_m = _wgs_ls_to_m(route["geo"]["cover_path"])
    back_home_m = _wgs_ls_to_m(route["geo"]["back_home"])

    step = float(mp_step_m)
    pts_to = _sample_linestring_m(to_field_m, step)
    pts_cov = _sample_linestring_m(cover_m, step)
    pts_back = _sample_linestring_m(back_home_m, step)

    nfz_m = [to_utm_geom(shape(g), ctx) for g in nfz_for_ctx]
    pts_cov = apply_overfly_alt_profile(path_pts=pts_cov, nfz_polys_m=nfz_m)

    pts_all_m = pts_to + pts_cov + pts_back
    if not pts_all_m:
        raise ValueError("Нет точек для экспорта.")

    runway_m = to_utm_geom(shape(runway_for_ctx), ctx)

    wpl_text = build_wpl_from_local_route(
        runway_m=runway_m,
        route_points_m=pts_all_m,
        ctx=ctx,
        takeoff_cfg=route["config"]["takeoff_cfg"],
        landing_cfg=route["config"]["landing_cfg"],
        cruise_alt_agl=float(mp_alt_agl),
    )

    os.makedirs(export_dir, exist_ok=True)
    base = (mp_filename.strip() or f"{project_name}_mission").replace(" ", "_")
    wpl_path = os.path.join(export_dir, f"{base}.waypoints")
    with open(wpl_path, "w", encoding="utf-8") as f:
        f.write(wpl_text)

    return {"wpl_path": wpl_path}
