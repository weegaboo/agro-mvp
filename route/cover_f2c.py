# route/cover_f2c.py
"""
Покрытие поля (в UTM, метры):
- Пытаемся использовать Fields2Cover, если установлен.
- Если F2C недоступен, используем надёжный fallback на Shapely:
  boustrophedon (змейка) из параллельных линий с шагом ширины захвата, обрезанных полем.

Зависимости: shapely>=2.0
Опционально: fields2cover (Python bindings)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple

import math
from shapely.geometry import Polygon, LineString, Point, MultiLineString, box
from shapely.ops import linemerge
from shapely.affinity import rotate as shp_rotate

try:
    import fields2cover as f2c  # noqa: F401
    HAS_F2C = True
except Exception:
    HAS_F2C = False

from geo.utils import field_long_axis_angle_deg


@dataclass
class CoverResult:
    swaths: List[LineString]       # отдельные проходы
    cover_path: LineString         # сшитая "змейка" внутри поля
    entry_pt: Point                # первая точка первого прохода
    exit_pt: Point                 # последняя точка последнего прохода
    angle_used_deg: float          # какой угол реально использовали


# ------------------------------ F2C (заглушка) ------------------------------ #
def _build_cover_with_f2c(field_poly_m: Polygon, spray_width_m: float, angle_deg: float) -> Optional[CoverResult]:
    """
    Каркас под Fields2Cover. Если F2C присутствует и ты уже настроил конверсию shapely->F2C,
    можешь заполнить этот блок реальными вызовами.

    Сейчас возвращает None, чтобы автоматом сработал fallback.
    """
    if not HAS_F2C:
        return None

    # Ниже — ориентировочный псевдокод API F2C (может отличаться от твоей версии биндингов):
    # try:
    #     # 1) Конверсия shapely Polygon -> F2C Polygon (придётся написать helper)
    #     f_field = shapely_to_f2c_polygon(field_poly_m)
    #
    #     # 2) Задать ориентацию проходов
    #     # F2C обычно принимает угол радианами; boustrophedon swaths по angle
    #     theta = math.radians(angle_deg)
    #
    #     # 3) Генерация swaths
    #     gen = f2c.SwathGenerator()  # имя класса может отличаться
    #     swaths = gen.generate(f_field, spray_width_m, theta)
    #
    #     # 4) Построить маршрут по swaths (змейка)
    #     path_builder = f2c.PathPlannerSimple()  # примерное название
    #     path = path_builder.build(swaths)
    #
    #     # 5) Преобразовать F2C swaths/path обратно в shapely LineString
    #     swaths_ls = [f2c_linestring_to_shapely(s) for s in swaths]
    #     cover_path = f2c_linestring_to_shapely(path)
    #
    #     entry_pt = Point(cover_path.coords[0])
    #     exit_pt = Point(cover_path.coords[-1])
    #
    #     return CoverResult(
    #         swaths=swaths_ls, cover_path=cover_path,
    #         entry_pt=entry_pt, exit_pt=exit_pt,
    #         angle_used_deg=angle_deg
    #     )
    # except Exception:
    #     # Если что-то не так — сваливаемся на fallback
    #     return None

    return None


# --------------------------- Fallback: Shapely swaths --------------------------- #
def _generate_boustrophedon_swaths(
    field_poly_m: Polygon,
    spray_width_m: float,
    angle_deg: float,
) -> List[LineString]:
    """
    Строим набор параллельных линий (swaths) под заданным углом, обрезаем полем.
    Возвращаем упорядоченный список LineString, пронумерованный в стиле "змейки".
    """
    if spray_width_m <= 0:
        raise ValueError("spray_width_m must be > 0")

    # Вращаем поле так, чтобы swaths шли горизонтально (вдоль X), а мы шагали по Y
    # Поворачиваем на -angle вокруг центроида
    c = field_poly_m.centroid
    field_rot = shp_rotate(field_poly_m, -angle_deg, origin=(c.x, c.y), use_radians=False)

    minx, miny, maxx, maxy = field_rot.bounds
    height = maxy - miny
    if height <= 0:
        return []

    # Чуть расширим по X, чтобы линии гарантированно пересекали поле
    pad = max(field_rot.bounds[2] - field_rot.bounds[0], spray_width_m) * 0.5
    span_x0, span_x1 = minx - pad, maxx + pad

    # Стартовую линию ставим на miny + half_width, затем шаг = spray_width
    y = miny + (spray_width_m / 2.0)
    lines_rot: List[LineString] = []

    # Генерируем горизонтальные линии на всём диапазоне высоты с шагом ширины захвата
    while y <= maxy + 1e-6:
        line = LineString([(span_x0, y), (span_x1, y)])
        inter = field_rot.intersection(line)

        # intersection может быть LineString или MultiLineString
        if isinstance(inter, LineString):
            if inter.length > 0:
                lines_rot.append(inter)
        elif isinstance(inter, MultiLineString):
            # Берём все сегменты (удалим слишком короткие)
            for seg in inter.geoms:
                if seg.length > 0.1:  # отсечь мусор
                    lines_rot.append(seg)

        y += spray_width_m

    # Отсортируем по Y (возрастающе)
    lines_rot.sort(key=lambda ln: (ln.coords[0][1] + ln.coords[-1][1]) / 2.0)

    # Преобразуем в "змейку": каждый чётный проход слева->право, нечётный — справа->налево
    boustrophedon_rot: List[LineString] = []
    for i, ln in enumerate(lines_rot):
        p0 = ln.coords[0]
        p1 = ln.coords[-1]
        if i % 2 == 0:
            # слева -> право (как есть, предполагаем span_x0 < span_x1)
            boustrophedon_rot.append(LineString([p0, p1]))
        else:
            # справа -> лево (разворачиваем)
            boustrophedon_rot.append(LineString([p1, p0]))

    # Повернём swaths обратно на исходный угол
    swaths: List[LineString] = []
    for ln in boustrophedon_rot:
        swaths.append(shp_rotate(ln, angle_deg, origin=(c.x, c.y), use_radians=False))

    return swaths


def _stitch_swaths(swaths: List[LineString]) -> LineString:
    """
    Сшивает список проходов в один LineString: соединяем конец i-го с началом (i+1)-го.
    На Неделе 2 — просто линейно, без дуг/сглаживания.
    """
    if not swaths:
        return LineString()

    coords = []
    for i, sw in enumerate(swaths):
        if i == 0:
            coords.extend(list(sw.coords))
        else:
            # соединяем конец предыдущего с началом текущего прямым сегментом
            prev_end = coords[-1]
            cur_start = sw.coords[0]
            if prev_end != cur_start:
                coords.append(cur_start)
            coords.extend(list(sw.coords)[1:])  # не дублируем стартовую точку
    return LineString(coords)


# ------------------------------- Публичная API ------------------------------- #
def build_cover(
    field_poly_m: Polygon,
    spray_width_m: float,
    angle_deg: Optional[float] = None,
) -> CoverResult:
    """
    Основной вызов генерации покрытия поля.

    Parameters
    ----------
    field_poly_m : Polygon (UTM, метры)
    spray_width_m: ширина захвата, м
    angle_deg    : угол полос относительно оси X (в градусах).
                   Если None — берём длинную ось поля (minimum_rotated_rectangle).

    Returns
    -------
    CoverResult
    """
    if angle_deg is None:
        angle_deg = field_long_axis_angle_deg(field_poly_m)

    # Сначала пробуем F2C (если доступен и ты позже реализуешь конвертеры),
    # иначе — надёжный fallback на Shapely.
    res = _build_cover_with_f2c(field_poly_m, spray_width_m, angle_deg)
    if res is not None:
        return res

    # Fallback: shapely boustrophedon
    swaths = _generate_boustrophedon_swaths(field_poly_m, spray_width_m, angle_deg)
    cover_path = _stitch_swaths(swaths)

    # Entry / Exit
    if cover_path.is_empty or len(cover_path.coords) < 2:
        # поле слишком маленькое или ширина > размера поля
        return CoverResult(
            swaths=[], cover_path=LineString(),
            entry_pt=Point(), exit_pt=Point(),
            angle_used_deg=angle_deg
        )

    entry_pt = Point(cover_path.coords[0])
    exit_pt = Point(cover_path.coords[-1])

    return CoverResult(
        swaths=swaths,
        cover_path=cover_path,
        entry_pt=entry_pt,
        exit_pt=exit_pt,
        angle_used_deg=angle_deg,
    )