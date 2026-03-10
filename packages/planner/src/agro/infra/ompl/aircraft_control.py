"""Kinodynamic OMPL.control planning for airplane-like 2D transitions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ompl import base as ob

try:
    from ompl import control as oc
except Exception:  # pragma: no cover
    oc = None


@dataclass(frozen=True)
class AircraftControlConfig:
    """Configuration for kinodynamic transition planning.

    Args:
        cruise_speed_mps: Cruise speed in meters per second.
        max_bank_deg: Maximum bank angle in degrees.
        roll_time_constant_s: Time constant of turn-rate response.
        propagation_step_s: Propagation step used by OMPL control SI.
        min_control_steps: Minimum control duration in steps.
        max_control_steps: Maximum control duration in steps.
        goal_tolerance_m: Position tolerance for goal check.
    """

    cruise_speed_mps: float = 22.0
    max_bank_deg: float = 35.0
    roll_time_constant_s: float = 1.2
    propagation_step_s: float = 0.15
    min_control_steps: int = 1
    max_control_steps: int = 10
    goal_tolerance_m: float = 2.0


def is_control_available() -> bool:
    """Return whether OMPL.control bindings are available."""
    return oc is not None and hasattr(oc, "SimpleSetup")


def _clamp(x: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, x))


def _wrap_pi(yaw: float) -> float:
    """Wrap angle to [-pi, pi]."""
    return math.atan2(math.sin(yaw), math.cos(yaw))


def plan_pose_to_pose_kinodynamic(
    *,
    start_xyyaw: Tuple[float, float, float],
    goal_xyyaw: Tuple[float, float, float],
    Rmin: float,
    bnds: ob.RealVectorBounds,
    config: AircraftControlConfig,
    time_limit: float = 1.5,
    range_hint: Optional[float] = None,
    interpolate_n: int = 600,
    xy_validity_fn: Optional[Callable[[Tuple[float, float]], bool]] = None,
) -> Optional[List[Tuple[float, float]]]:
    """Plan kinodynamic path using OMPL.control.

    State is modeled as SE2 + yaw-rate, with yaw-acceleration control:
    x_dot = v*cos(psi), y_dot = v*sin(psi), psi_dot = omega, omega_dot = u.

    Args:
        start_xyyaw: Start pose (x, y, yaw).
        goal_xyyaw: Goal pose (x, y, yaw).
        Rmin: Minimum turn radius in meters.
        bnds: Search bounds.
        config: Aircraft control model parameters.
        time_limit: Solve time limit in seconds.
        range_hint: Planner range hint.
        interpolate_n: Interpolation points for output path.
        xy_validity_fn: Optional XY validity callback.

    Returns:
        Polyline as list of XY points, or None if no solution.
    """
    if not is_control_available():
        return None

    v = max(1.0, float(config.cruise_speed_mps))
    max_bank_rad = math.radians(_clamp(float(config.max_bank_deg), 5.0, 80.0))
    omega_bank = 9.80665 * math.tan(max_bank_rad) / v
    omega_rmin = v / max(1.0, float(Rmin))
    omega_max = max(0.02, min(omega_bank, omega_rmin))
    alpha_max = max(0.05, omega_max / max(0.1, float(config.roll_time_constant_s)))

    se2 = ob.SE2StateSpace()
    se2.setBounds(bnds)
    yaw_rate_space = ob.RealVectorStateSpace(1)
    yaw_rate_bounds = ob.RealVectorBounds(1)
    yaw_rate_bounds.setLow(0, -omega_max)
    yaw_rate_bounds.setHigh(0, omega_max)
    yaw_rate_space.setBounds(yaw_rate_bounds)

    state_space = ob.CompoundStateSpace()
    state_space.addSubspace(se2, 1.0)
    state_space.addSubspace(yaw_rate_space, 0.05)

    control_space = oc.RealVectorControlSpace(state_space, 1)
    control_bounds = ob.RealVectorBounds(1)
    control_bounds.setLow(0, -alpha_max)
    control_bounds.setHigh(0, alpha_max)
    control_space.setBounds(control_bounds)

    setup = oc.SimpleSetup(control_space)
    si = setup.getSpaceInformation()

    def _is_valid(state) -> bool:
        x = float(state[0].getX())
        y = float(state[0].getY())
        if xy_validity_fn is None:
            return True
        return bool(xy_validity_fn((x, y)))

    si.setStateValidityChecker(ob.StateValidityCheckerFn(_is_valid))
    si.setStateValidityCheckingResolution(0.01)
    step_s = max(0.02, float(config.propagation_step_s))
    si.setPropagationStepSize(step_s)
    si.setMinMaxControlDuration(
        max(1, int(config.min_control_steps)),
        max(1, int(config.max_control_steps)),
    )

    def _propagate(start, control, duration, result) -> None:
        x = float(start[0].getX())
        y = float(start[0].getY())
        yaw = float(start[0].getYaw())
        omega = float(start[1][0])
        accel = _clamp(float(control[0]), -alpha_max, alpha_max)

        dt = max(0.02, min(0.08, step_s * 0.5))
        t = 0.0
        while t < duration - 1e-9:
            h = min(dt, duration - t)
            omega = _clamp(omega + accel * h, -omega_max, omega_max)
            yaw = _wrap_pi(yaw + omega * h)
            x += v * math.cos(yaw) * h
            y += v * math.sin(yaw) * h
            t += h

        result[0].setX(x)
        result[0].setY(y)
        result[0].setYaw(yaw)
        result[1][0] = omega

    si.setStatePropagator(oc.StatePropagatorFn(_propagate))

    start = ob.State(state_space)
    start()[0].setX(float(start_xyyaw[0]))
    start()[0].setY(float(start_xyyaw[1]))
    start()[0].setYaw(float(start_xyyaw[2]))
    start()[1][0] = 0.0

    goal = ob.State(state_space)
    goal()[0].setX(float(goal_xyyaw[0]))
    goal()[0].setY(float(goal_xyyaw[1]))
    goal()[0].setYaw(float(goal_xyyaw[2]))
    goal()[1][0] = 0.0

    if not si.isValid(start()) or not si.isValid(goal()):
        return None

    setup.setStartAndGoalStates(start, goal, max(0.5, float(config.goal_tolerance_m)))
    setup.getProblemDefinition().setOptimizationObjective(ob.PathLengthOptimizationObjective(si))

    planner = oc.SST(si) if hasattr(oc, "SST") else oc.KPIECE1(si)
    if hasattr(planner, "setGoalBias"):
        planner.setGoalBias(0.15)
    if range_hint is not None and hasattr(planner, "setRange"):
        planner.setRange(float(range_hint))
    if hasattr(planner, "setSelectionRadius"):
        planner.setSelectionRadius(max(2.0, 0.5 * float(Rmin)))
    if hasattr(planner, "setPruningRadius"):
        planner.setPruningRadius(max(1.0, 0.25 * float(Rmin)))
    setup.setPlanner(planner)

    solved = setup.solve(max(0.1, float(time_limit)))
    if not solved:
        return None

    path = setup.getSolutionPath()
    try:
        if interpolate_n >= 3:
            path.interpolate(int(interpolate_n))
        else:
            path.interpolate()
    except Exception:
        pass

    points: List[Tuple[float, float]] = []
    for state in path.getStates():
        pt = (float(state[0].getX()), float(state[0].getY()))
        if points and math.hypot(pt[0] - points[-1][0], pt[1] - points[-1][1]) < 1e-6:
            continue
        points.append(pt)
    if len(points) < 2:
        return None
    return points
