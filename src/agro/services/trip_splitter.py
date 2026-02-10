from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Dict, Any, Optional

from shapely.geometry import LineString

from agro.domain.routing.transit import build_transit_with_nfz
from agro.domain.routing.landing_and_takeoff import build_takeoff_anchor, build_landing_anchor


@dataclass
class Trip:
    start_idx: int
    end_idx: int
    to_field: LineString
    back_home: LineString
    fuel_used_l: float
    mix_used_l: float

    @property
    def transit_len_m(self) -> float:
        return float(self.to_field.length + self.back_home.length)


@dataclass
class TripSplitResult:
    trips: List[Trip]
    transit_length_m: float


class TripSplitError(Exception):
    pass


def split_into_trips(
    *,
    runway_m: LineString,
    swaths: Sequence[LineString],
    cover_path_m: LineString,
    nfz_polys_m: Sequence,
    turn_r: float,
    total_capacity_l: float,
    fuel_reserve_l: float,
    fuel_burn_l_per_km: float,
    mix_rate_l_per_ha: float,
    spray_width_m: float,
) -> TripSplitResult:
    if total_capacity_l <= 0:
        raise TripSplitError("total_capacity_l must be > 0")
    if fuel_reserve_l < 0:
        raise TripSplitError("fuel_reserve_l must be >= 0")
    if not swaths:
        return TripSplitResult(trips=[], transit_length_m=0.0)

    fuel_per_m = fuel_burn_l_per_km / 1000.0
    mix_per_m = (mix_rate_l_per_ha / 10_000.0) * spray_width_m

    swath_lengths = [float(s.length) for s in swaths]
    total_swath_len = sum(swath_lengths) if swath_lengths else 0.0
    cover_len = float(cover_path_m.length)
    work_len_factor = (cover_len / total_swath_len) if total_swath_len > 1e-9 else 1.0

    fuel_work_per_swath = [L * work_len_factor * fuel_per_m for L in swath_lengths]
    mix_per_swath = [L * mix_per_m for L in swath_lengths]

    transit_cache: Dict[int, Dict[str, LineString]] = {}

    def _transit_for_swath(idx: int) -> Dict[str, LineString]:
        if idx in transit_cache:
            return transit_cache[idx]
        s = swaths[idx]
        begin_at, _ = build_takeoff_anchor(runway_m)
        back_to, _ = build_landing_anchor(runway_m)
        to_field, back_home = build_transit_with_nfz(
            runway_m=runway_m,
            begin_at_runway_end=(begin_at.x, begin_at.y),
            back_to_runway_end=(back_to.x, back_to.y),
            first_swath=s,
            last_swath=s,
            turn_r=turn_r,
            nfz_polys_m=nfz_polys_m,
        )
        transit_cache[idx] = {"to_field": to_field, "back_home": back_home}
        return transit_cache[idx]

    trips: List[Trip] = []
    i = 0
    n = len(swaths)
    while i < n:
        transit_i = _transit_for_swath(i)
        fuel_to_field = float(transit_i["to_field"].length) * fuel_per_m

        # проверим достижимость хотя бы одного свата
        fuel_to_home_i = float(transit_i["back_home"].length) * fuel_per_m
        min_fuel_need = fuel_to_field + fuel_work_per_swath[i] + fuel_to_home_i + fuel_reserve_l
        if min_fuel_need > total_capacity_l:
            raise TripSplitError(
                f"Swath {i} unreachable: need {min_fuel_need:.2f}L > capacity {total_capacity_l:.2f}L"
            )

        j = i - 1
        fuel_work_need = 0.0
        mix_need = 0.0
        last_back_home = transit_i["back_home"]

        while j + 1 < n:
            cand = j + 1
            transit_c = _transit_for_swath(cand)
            fuel_to_home = float(transit_c["back_home"].length) * fuel_per_m

            fuel_work_c = fuel_work_need + fuel_work_per_swath[cand]
            mix_c = mix_need + mix_per_swath[cand]

            fuel_need_total = fuel_to_field + fuel_work_c + fuel_to_home + fuel_reserve_l
            mix_capacity = total_capacity_l - fuel_need_total

            if mix_capacity < 0:
                break
            if mix_c <= mix_capacity:
                j = cand
                fuel_work_need = fuel_work_c
                mix_need = mix_c
                last_back_home = transit_c["back_home"]
                continue
            break

        if j < i:
            raise TripSplitError(f"Unable to include swath {i} in any trip")

        trips.append(
            Trip(
                start_idx=i,
                end_idx=j,
                to_field=transit_i["to_field"],
                back_home=last_back_home,
                fuel_used_l=fuel_to_field + fuel_work_need + float(last_back_home.length) * fuel_per_m,
                mix_used_l=mix_need,
            )
        )
        i = j + 1

    total_transit = sum(t.transit_len_m for t in trips)
    return TripSplitResult(trips=trips, transit_length_m=total_transit)
