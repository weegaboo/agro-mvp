"""
geo/crs.py — утилиты для преобразования координат WGS84 <-> UTM

Зачем:
- считать длины/буферы/площади в МЕТРАХ (UTM), хранить исходные данные в WGS84.
- централизовать выбор UTM-зоны и трансформации.

Зависимости:
  pip install pyproj shapely>=2.0
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Iterable, List

from pyproj import Transformer
from shapely.geometry import (
    shape, mapping, Point, LineString, Polygon, MultiPolygon, base
)
from shapely.ops import unary_union


# ----------------------------- базовые хелперы ----------------------------- #

def pick_utm_epsg(lon: float, lat: float) -> Tuple[int, int, str]:
    """
    По долготе/широте подбирает EPSG кода UTM.
    Возвращает: (epsg, zone, hemisphere), где hemisphere ∈ {"N", "S"}.
    """
    if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
        raise ValueError("Longitude must be in [-180,180], latitude in [-90,90]")

    zone = int((lon + 180) // 6) + 1
    hemisphere = "N" if lat >= 0 else "S"
    epsg = (32600 if hemisphere == "N" else 32700) + zone
    return epsg, zone, hemisphere


@dataclass(frozen=True)
class CRSContext:
    """Контекст проекции WGS84 <-> UTM для одной UTM-зоны."""
    epsg: int
    zone: int
    hemisphere: str  # "N" / "S"
    to_utm: Transformer
    to_wgs: Transformer

    @classmethod
    def from_lonlat(cls, lon: float, lat: float) -> "CRSContext":
        epsg, zone, hemi = pick_utm_epsg(lon, lat)
        to_utm = Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        )
        to_wgs = Transformer.from_crs(
            f"EPSG:{epsg}", "EPSG:4326", always_xy=True
        )
        return cls(epsg=epsg, zone=zone, hemisphere=hemi, to_utm=to_utm, to_wgs=to_wgs)


# ------------------------- выбор контекста по геометрии ------------------------- #

def centroid_lonlat_of_geojson(geom_gj: Dict[str, Any]) -> Tuple[float, float]:
    """
    Берёт GeoJSON-геометрию в WGS84, возвращает (lon, lat) её центроида.
    Поддерживает Point / LineString / Polygon / MultiPolygon.
    """
    g = shape(geom_gj)
    c = g.centroid
    return float(c.x), float(c.y)


def context_from_geojson(geom_gj: Dict[str, Any]) -> CRSContext:
    """Создаёт CRSContext, подбирая UTM-зону по центроиду GeoJSON-геометрии."""
    lon, lat = centroid_lonlat_of_geojson(geom_gj)
    return CRSContext.from_lonlat(lon, lat)


# -------------------------- репроекция SHAPELY-геометрий -------------------------- #

def to_utm_geom(g: base.BaseGeometry, ctx: CRSContext) -> base.BaseGeometry:
    """Репроецирует Shapely-геометрию из WGS84 в UTM (метры) по контексту."""
    if isinstance(g, Point):
        x, y = ctx.to_utm.transform(g.x, g.y)
        return Point(x, y)
    elif isinstance(g, LineString):
        coords = [ctx.to_utm.transform(x, y) for x, y in g.coords]
        return LineString(coords)
    elif isinstance(g, Polygon):
        ext = [ctx.to_utm.transform(x, y) for x, y in g.exterior.coords]
        ints = [
            [ctx.to_utm.transform(x, y) for x, y in ring.coords]
            for ring in g.interiors
        ]
        return Polygon(ext, ints)
    elif isinstance(g, MultiPolygon):
        return MultiPolygon([to_utm_geom(p, ctx) for p in g.geoms])
    else:
        # На старте MVP поддерживаем основной набор. Для прочих типов можно расширить.
        raise TypeError(f"Unsupported geometry type for to_utm_geom: {g.geom_type}")


def to_wgs_geom(g: base.BaseGeometry, ctx: CRSContext) -> base.BaseGeometry:
    """Репроецирует Shapely-геометрию из UTM в WGS84 по контексту."""
    if isinstance(g, Point):
        x, y = ctx.to_wgs.transform(g.x, g.y)
        return Point(x, y)
    elif isinstance(g, LineString):
        coords = [ctx.to_wgs.transform(x, y) for x, y in g.coords]
        return LineString(coords)
    elif isinstance(g, Polygon):
        ext = [ctx.to_wgs.transform(x, y) for x, y in g.exterior.coords]
        ints = [
            [ctx.to_wgs.transform(x, y) for x, y in ring.coords]
            for ring in g.interiors
        ]
        return Polygon(ext, ints)
    elif isinstance(g, MultiPolygon):
        return MultiPolygon([to_wgs_geom(p, ctx) for p in g.geoms])
    else:
        raise TypeError(f"Unsupported geometry type for to_wgs_geom: {g.geom_type}")


# ----------------------------- репроекция GEOJSON ----------------------------- #

SUPPORTED_GJ_TYPES = {"Point", "LineString", "Polygon", "MultiPolygon"}

def to_utm_geojson(geom_gj: Dict[str, Any], ctx: CRSContext) -> Dict[str, Any]:
    """
    GeoJSON (WGS84) -> GeoJSON (UTM метры).
    Важно: тип остаётся тем же; меняются только числа координат.
    """
    if geom_gj.get("type") not in SUPPORTED_GJ_TYPES:
        raise TypeError(f"Unsupported GeoJSON type: {geom_gj.get('type')}")
    g = shape(geom_gj)
    return mapping(to_utm_geom(g, ctx))


def to_wgs_geojson(geom_gj_m: Dict[str, Any], ctx: CRSContext) -> Dict[str, Any]:
    """
    GeoJSON (UTM метры) -> GeoJSON (WGS84).
    """
    if geom_gj_m.get("type") not in SUPPORTED_GJ_TYPES:
        raise TypeError(f"Unsupported GeoJSON type: {geom_gj_m.get('type')}")
    g_m = shape(geom_gj_m)
    return mapping(to_wgs_geom(g_m, ctx))


# -------------------------- пакетные удобные функции -------------------------- #

def to_utm_many(geoms_wgs: Iterable[base.BaseGeometry], ctx: CRSContext) -> List[base.BaseGeometry]:
    """Пакетно репроецирует список Shapely-геометрий в UTM."""
    return [to_utm_geom(g, ctx) for g in geoms_wgs]

def to_wgs_many(geoms_m: Iterable[base.BaseGeometry], ctx: CRSContext) -> List[base.BaseGeometry]:
    """Пакетно репроецирует список Shapely-геометрий в WGS84."""
    return [to_wgs_geom(g, ctx) for g in geoms_m]


# ------------------------------- удобные шорткаты ------------------------------- #

def context_from_many_geojson(geoms_gj: Iterable[Dict[str, Any]]) -> CRSContext:
    """
    Собирает объединённый центроид по нескольким GeoJSON и выбирает UTM-зону.
    Удобно вызывать, если есть поле + ВПП + NFZ.
    """
    geoms = [shape(g) for g in geoms_gj if g]
    if not geoms:
        # дефолт — центр Москвы
        return CRSContext.from_lonlat(37.6173, 55.7558)
    union = unary_union(geoms)
    c = union.centroid
    return CRSContext.from_lonlat(float(c.x), float(c.y))


# ------------------------------- примеры использования ------------------------------- #
# from shapely.geometry import shape
# # 1) выбрать контекст по полю (GeoJSON polygon в WGS84)
# ctx = context_from_geojson(field_gj)
# # 2) перевести в метры
# field_m = to_utm_geom(shape(field_gj), ctx)
# runway_line_m = to_utm_geom(shape(runway_centerline_gj), ctx)
# nfz_m = [to_utm_geom(shape(gj), ctx) for gj in nfz_list_gj]
# # 3) вернуть результат маршрута назад в WGS для отрисовки
# route_wgs = to_wgs_geom(route_m, ctx)