"""Swath ordering and routing helpers."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional, Set, Dict

from shapely.geometry import LineString


@dataclass(frozen=True)
class OrientedSwath:
    """A swath with a chosen direction and side metadata."""
    swath_id: int
    dir: int  # 0 = A->B, 1 = B->A
    start: Tuple[float, float]
    end: Tuple[float, float]
    start_side: int  # 0 = A-side, 1 = B-side
    end_side: int    # 0 = A-side, 1 = B-side


# -----------------------------
# Geometry helpers
# -----------------------------

def _endpoints_xy(ls: LineString) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Return start/end coordinates of a LineString."""
    coords = list(ls.coords)
    if len(coords) < 2:
        raise ValueError("Each swath LineString must have at least 2 points.")
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dot(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Dot product of two 2D vectors."""
    return a[0] * b[0] + a[1] * b[1]


def _sub(a: Tuple[float, float], b: Tuple[float, float]) -> Tuple[float, float]:
    """Vector subtraction a - b."""
    return (a[0] - b[0], a[1] - b[1])


def _norm(v: Tuple[float, float]) -> float:
    """Vector norm."""
    return math.hypot(v[0], v[1])


def _unit(v: Tuple[float, float]) -> Tuple[float, float]:
    """Return unit vector (or default if near zero)."""
    n = _norm(v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (1.0, 0.0)


# -----------------------------
# Swath orientation logic
# -----------------------------

def estimate_swath_direction(swaths: List[LineString]) -> Tuple[float, float]:
    """Estimate dominant swath direction."""
    ref = None
    for ls in swaths:
        a, b = _endpoints_xy(ls)
        v = _sub(b, a)
        if _norm(v) > 1e-6:
            ref = _unit(v)
            break
    if ref is None:
        return (1.0, 0.0)

    sx, sy = 0.0, 0.0
    for ls in swaths:
        a, b = _endpoints_xy(ls)
        v = _sub(b, a)
        if _norm(v) < 1e-6:
            continue
        u = _unit(v)
        if _dot(u, ref) < 0:
            u = (-u[0], -u[1])
        sx += u[0]
        sy += u[1]
    return _unit((sx, sy))


def canonicalize_swath(ls: LineString, d_unit: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Order swath endpoints by projection along a direction."""
    p1, p2 = _endpoints_xy(ls)
    return (p1, p2) if _dot(p1, d_unit) <= _dot(p2, d_unit) else (p2, p1)


def build_oriented_swaths(swaths: List[LineString]) -> List[OrientedSwath]:
    """Build oriented swaths for both directions of each swath."""
    d = estimate_swath_direction(swaths)
    oriented: List[OrientedSwath] = []
    for i, ls in enumerate(swaths):
        A, B = canonicalize_swath(ls, d_unit=d)
        oriented.append(OrientedSwath(i, 0, start=A, end=B, start_side=0, end_side=1))
        oriented.append(OrientedSwath(i, 1, start=B, end=A, start_side=1, end_side=0))
    return oriented


# -----------------------------
# Constraint graph
# -----------------------------

def build_adjacency(
    oriented: List[OrientedSwath],
    min_turn_radius_m: float,
    dist_factor: float = 2.0,
    require_same_side_entry: bool = True,
) -> Dict[int, List[int]]:
    """Build adjacency list of possible swath transitions."""
    thr = dist_factor * min_turn_radius_m
    adj: Dict[int, List[int]] = {i: [] for i in range(len(oriented))}
    for ui, u in enumerate(oriented):
        for vi, v in enumerate(oriented):
            if u.swath_id == v.swath_id:
                continue
            if require_same_side_entry and (u.end_side != v.start_side):
                continue
            if _dist(u.end, v.start) >= thr:
                adj[ui].append(vi)
    return adj


# -----------------------------
# Route search (minimize hop distance)
# -----------------------------

def find_route_min_hops(
    swaths: List[LineString],
    min_turn_radius_m: float,
    dist_factor: float = 2.0,
    require_same_side_entry: bool = True,
    max_restarts: int = 200,
    backtrack_depth: int = 4,
    seed: int = 42,
) -> Optional[List[OrientedSwath]]:
    """Find a swath route using a greedy hop-minimization heuristic."""
    rnd = random.Random(seed)
    N = len(swaths)
    if N == 0:
        return []

    oriented = build_oriented_swaths(swaths)
    adj = build_adjacency(
        oriented,
        min_turn_radius_m=min_turn_radius_m,
        dist_factor=dist_factor,
        require_same_side_entry=require_same_side_entry,
    )

    # Чем меньше исходящих ребёр, тем более "опасный" старт
    starts = list(range(len(oriented)))
    starts.sort(key=lambda i: len(adj[i]))

    def future_deg(state_idx: int, used: Set[int]) -> int:
        """Count future options from a state excluding used swaths."""
        return sum(1 for v in adj[state_idx] if oriented[v].swath_id not in used)

    def hop_cost(u_idx: int, v_idx: int) -> float:
        """Compute hop cost between two oriented swaths."""
        return _dist(oriented[u_idx].end, oriented[v_idx].start)

    def try_from(start_idx: int) -> Optional[List[int]]:
        """Try to build a full route starting from a given node."""
        path = [start_idx]
        used: Set[int] = {oriented[start_idx].swath_id}
        stack: List[Tuple[int, List[int]]] = []

        while len(used) < N:
            cur = path[-1]
            options = [v for v in adj[cur] if oriented[v].swath_id not in used]

            if not options:
                # небольшой откат
                for _ in range(backtrack_depth):
                    if not stack:
                        return None
                    pos, alts = stack.pop()
                    while len(path) - 1 > pos:
                        used.remove(oriented[path.pop()].swath_id)
                    if alts:
                        nxt = alts.pop(0)  # alts already sorted best->worst
                        stack.append((pos, alts))
                        path.append(nxt)
                        used.add(oriented[nxt].swath_id)
                        break
                else:
                    return None
                continue

            # НОВОЕ: сортируем по минимальному перелёту, потом по "не загнать себя в тупик"
            # (и чуть-чуть рандома для разнообразия на рестартах)
            options.sort(
                key=lambda v: (
                    hop_cost(cur, v),
                    future_deg(v, used),
                    rnd.random(),
                )
            )

            nxt = options[0]
            alts = options[1:]
            stack.append((len(path) - 1, alts))
            path.append(nxt)
            used.add(oriented[nxt].swath_id)

        return path

    for r in range(max_restarts):
        start = starts[r] if r < len(starts) else rnd.choice(starts)
        idx_path = try_from(start)
        if idx_path:
            route = [oriented[i] for i in idx_path]
            if len({s.swath_id for s in route}) == N:
                return route

    return None


# -----------------------------
# Public API (returns start/end)
# -----------------------------

def build_swath_route_min_hops(
    min_turn_radius_m: float,
    swaths_linestring: List[LineString],
    dist_factor: float = 2.0,
    require_same_side_entry: bool = True,
) -> List[Dict]:
    """Build a swath route by minimizing hop distances.

    Args:
        min_turn_radius_m: Minimum turning radius.
        swaths_linestring: Swath LineStrings in meters.
        dist_factor: Distance factor for adjacency threshold.
        require_same_side_entry: Whether entry side must match.

    Returns:
        List of route dictionaries with swath ordering and direction.
    """
    route = find_route_min_hops(
        swaths=swaths_linestring,
        min_turn_radius_m=min_turn_radius_m,
        dist_factor=dist_factor,
        require_same_side_entry=require_same_side_entry,
    )

    if route is None:
        # fallback: змейка по индексу
        out = []
        for i, ls in enumerate(swaths_linestring):
            a, b = _endpoints_xy(ls)
            if i % 2 == 0:
                start, end, d = a, b, 0
            else:
                start, end, d = b, a, 1
            out.append(dict(swath_id=i, dir=d, start=start, end=end, start_side=None, end_side=None))
        return out

    return [
        dict(
            swath_id=s.swath_id,
            dir=s.dir,
            start=s.start,
            end=s.end,
            start_side=s.start_side,
            end_side=s.end_side,
        )
        for s in route
    ]
