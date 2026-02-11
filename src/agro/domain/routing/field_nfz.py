"""Altitude profile adjustments when overflying NFZ."""

from dataclasses import dataclass
from typing import List, Sequence, Tuple, Optional

from shapely.geometry import Point, Polygon, LineString
from shapely.ops import unary_union


@dataclass(frozen=True)
class OverflyAltParams:
    """Parameters for overfly altitude profile."""
    base_alt_m: float = 30.0          # обычная высота (м)
    overfly_alt_m: float = 60.0       # высота "перелёта" NFZ (м) — можно задавать абсолютом
    safety_buffer_m: float = 0.0      # буфер вокруг NFZ (м)
    d_before_m: float = 80.0          # начать подниматься за столько метров ДО входа в зону
    d_after_m: float = 80.0           # закончить снижение через столько метров ПОСЛЕ выхода
    ramp_len_m: float = 60.0          # длина подъёма/спуска (м) внутри расширенного участка
    sample_step_m: float = 20.0       # шаг вставки доп. точек на рампе/границах (м)


def apply_overfly_alt_profile(
    path_pts: List[Point],
    nfz_polys_m: Sequence[Polygon],
    params: OverflyAltParams = OverflyAltParams(),
) -> List[Tuple[Point, float]]:
    """Apply altitude profile over NFZ along a path.

    Args:
        path_pts: Path points in meters (UTM).
        nfz_polys_m: NFZ polygons in meters (UTM).
        params: Overfly altitude parameters.

    Returns:
        List of (Point, altitude_m) tuples along the path.
    """

    if len(path_pts) < 2:
        return [(p, params.base_alt_m) for p in path_pts]

    # ---------- Подготовка геометрии NFZ ----------
    valid_polys = [p for p in nfz_polys_m if p is not None and not p.is_empty]
    if not valid_polys:
        return [(p, params.base_alt_m) for p in path_pts]

    if params.safety_buffer_m > 0:
        buffered = [p.buffer(params.safety_buffer_m) for p in valid_polys]
    else:
        buffered = valid_polys

    nfz_union = unary_union(buffered)

    # ---------- Кумулятивные длины пути ----------
    xs = [float(p.x) for p in path_pts]
    ys = [float(p.y) for p in path_pts]

    seg_len: List[float] = []
    cum: List[float] = [0.0]
    for i in range(len(path_pts) - 1):
        dx = xs[i + 1] - xs[i]
        dy = ys[i + 1] - ys[i]
        L = (dx * dx + dy * dy) ** 0.5
        seg_len.append(L)
        cum.append(cum[-1] + L)

    total_len = cum[-1]
    if total_len <= 1e-9:
        return [(Point(xs[0], ys[0]), params.base_alt_m)]

    # ---------- 1) отмечаем сегменты, пересекающие NFZ ----------
    bad_seg = [False] * (len(path_pts) - 1)
    for i in range(len(path_pts) - 1):
        if seg_len[i] <= 1e-9:
            continue
        seg = LineString([(xs[i], ys[i]), (xs[i + 1], ys[i + 1])])
        if seg.intersects(nfz_union):
            bad_seg[i] = True

    if not any(bad_seg):
        return [(Point(x, y), params.base_alt_m) for x, y in zip(xs, ys)]

    # ---------- 2) собираем интервалы по "пройденной длине" ----------
    # интервал сегмента i: [cum[i], cum[i+1]]
    intervals: List[Tuple[float, float]] = []
    i = 0
    while i < len(bad_seg):
        if not bad_seg[i]:
            i += 1
            continue
        start = cum[i]
        j = i
        while j < len(bad_seg) and bad_seg[j]:
            j += 1
        end = cum[j]  # cum[j] = cum[(последний_bad)+1]
        intervals.append((start, end))
        i = j

    # ---------- 3) расширяем интервалы d_before/d_after и сливаем ----------
    expanded: List[Tuple[float, float]] = []
    for a, b in intervals:
        aa = max(0.0, a - params.d_before_m)
        bb = min(total_len, b + params.d_after_m)
        expanded.append((aa, bb))

    expanded.sort()
    merged: List[Tuple[float, float]] = []
    for a, b in expanded:
        if not merged or a > merged[-1][1]:
            merged.append((a, b))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))

    # ---------- Хелпер: интерполяция точки по расстоянию s вдоль полилинии ----------
    def point_at_s(s: float) -> Point:
        s = min(max(s, 0.0), total_len)
        # найти сегмент, где лежит s
        # линейный поиск ок для небольших миссий; при желании можно заменить на bisect
        k = 0
        while k < len(seg_len) and cum[k + 1] < s:
            k += 1
        if k >= len(seg_len) or seg_len[k] <= 1e-9:
            return Point(xs[-1], ys[-1])
        t = (s - cum[k]) / seg_len[k]
        x = xs[k] + t * (xs[k + 1] - xs[k])
        y = ys[k] + t * (ys[k + 1] - ys[k])
        return Point(x, y)

    # ---------- 4) профиль высоты (с рампой) ----------
    base = float(params.base_alt_m)
    over = float(params.overfly_alt_m)
    ramp = max(0.0, float(params.ramp_len_m))

    def alt_for_interval(s: float, a: float, b: float) -> float:
        # короткий интервал — "треугольник" (подъём/спуск без полки)
        length = b - a
        if length <= 0:
            return base
        if ramp <= 1e-9:
            return over if (a <= s <= b) else base

        # если интервал меньше 2*ramp: делаем треугольник
        if length < 2 * ramp:
            mid = (a + b) / 2.0
            if s < a or s > b:
                return base
            if s <= mid:
                # подъём
                t = (s - a) / (mid - a + 1e-12)
                return base + (over - base) * t
            else:
                # спуск
                t = (b - s) / (b - mid + 1e-12)
                return base + (over - base) * t

        # обычный случай: подъём -> полка -> спуск
        up_end = a + ramp
        down_start = b - ramp
        if s < a or s > b:
            return base
        if s <= up_end:
            t = (s - a) / (ramp + 1e-12)
            return base + (over - base) * t
        if s < down_start:
            return over
        # s >= down_start
        t = (b - s) / (ramp + 1e-12)
        return base + (over - base) * t

    def altitude_at_s(s: float) -> float:
        # если интервалы перекрываются — берём максимум (самый "высокий" профиль)
        alt = base
        for a, b in merged:
            alt = max(alt, alt_for_interval(s, a, b))
        return alt

    # ---------- 5) Сэмплируем новый набор расстояний (оригинальные + границы + рампы) ----------
    s_values = set(cum)  # расстояния оригинальных точек

    step = max(1.0, float(params.sample_step_m))

    for a, b in merged:
        # добавим границы и рампы
        s_values.add(a)
        s_values.add(b)

        # точки на подъёме
        s0 = a
        s1 = min(b, a + max(ramp, 0.0))
        x = s0
        while x < s1:
            s_values.add(x)
            x += step
        s_values.add(s1)

        # точки на спуске
        s2 = max(a, b - max(ramp, 0.0))
        x = s2
        while x < b:
            s_values.add(x)
            x += step
        s_values.add(s2)

    s_sorted = sorted(s_values)

    # ---------- 6) Собираем результат ----------
    out: List[Tuple[Point, float]] = []
    last_xy: Optional[Tuple[float, float]] = None
    for s in s_sorted:
        p = point_at_s(s)
        a = altitude_at_s(s)
        xy = (float(p.x), float(p.y))
        # убираем подряд дубли (на случай нулевых сегментов)
        if last_xy is not None and (abs(xy[0] - last_xy[0]) < 1e-9 and abs(xy[1] - last_xy[1]) < 1e-9):
            continue
        out.append((p, a))
        last_xy = xy

    return out
