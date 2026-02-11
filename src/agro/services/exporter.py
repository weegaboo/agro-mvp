"""Export route geometry to GeoJSON and CSV."""

from __future__ import annotations

import json
import os
import csv
from typing import Dict, Any

from shapely.geometry import shape, Point, LineString

from agro.domain.geo.crs import context_from_many_geojson, to_utm_geom, to_wgs_geom


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


def export_route_geojson_csv(
    *,
    route: Dict[str, Any],
    project_file: str,
    export_name: str,
    export_step_m: float,
    export_dir: str = "data/exports",
) -> Dict[str, str]:
    """Export route geometry to GeoJSON and CSV files.

    Args:
        route: Route dict with WGS84 geometry in `route["geo"]`.
        project_file: Path to the project JSON (for CRS context).
        export_name: Base file name (without extension).
        export_step_m: Sampling step in meters.
        export_dir: Output directory.

    Returns:
        Dict with `geojson_path` and `csv_path`.

    Raises:
        FileNotFoundError: If project file is missing.
        ValueError: If required geometry is missing.
    """
    if not os.path.exists(project_file):
        raise FileNotFoundError("Файл проекта не найден для экспорта.")

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

    to_field_wgs_gj = route["geo"]["to_field"]
    back_home_wgs_gj = route["geo"]["back_home"]
    cover_wgs_gj = route["geo"]["cover_path"]

    to_field_m = _wgs_ls_to_m(to_field_wgs_gj)
    back_home_m = _wgs_ls_to_m(back_home_wgs_gj)
    cover_m = _wgs_ls_to_m(cover_wgs_gj)

    step = float(export_step_m)
    samples = {
        "to_field": _sample_linestring_m(to_field_m, step),
        "cover": _sample_linestring_m(cover_m, step),
        "back_home": _sample_linestring_m(back_home_m, step),
    }

    samples_wgs = {seg: [to_wgs_geom(p, ctx) for p in pts] for seg, pts in samples.items()}

    os.makedirs(export_dir, exist_ok=True)
    base = os.path.join(export_dir, f"{export_name.strip() or 'route'}_{int(step)}m")

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

    csv_path = f"{base}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["segment", "idx", "lat", "lon"])
        for seg, pts in samples_wgs.items():
            for i, p in enumerate(pts):
                lon, lat = p.x, p.y
                w.writerow([seg, i, f"{lat:.8f}", f"{lon:.8f}"])

    return {"geojson_path": geojson_path, "csv_path": csv_path}
