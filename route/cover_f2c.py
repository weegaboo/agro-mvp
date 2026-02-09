"""Shim module: kept for backward compatibility after moving to src/agro/infra/f2c/cover_f2c.py."""
import os
import sys

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agro.infra.f2c.cover_f2c import *  # noqa: F401,F403
