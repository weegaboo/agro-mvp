"""Shim module: kept for backward compatibility after moving to src/agro/domain/routing/landing_and_takeoff.py."""
import os
import sys

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agro.domain.routing.landing_and_takeoff import *  # noqa: F401,F403
