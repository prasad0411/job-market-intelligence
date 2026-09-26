"""Installed for every scripts/*.py entry point.

resilience.py was wired into aggregator/__main__.py and outreach/__main__.py
only. Everything under scripts/ runs as `python3 scripts/NAME.py`, so it had
no socket ceiling - the 26 Sep cleanup ran 4,432s against a 600s cap.

Importing this at the top of a script installs the same guarantees.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from aggregator import resilience as _r
    _r.install()
except Exception as _e:          # never block a script from starting
    import logging as _l
    _l.warning("resilience not installed: %s", _e)
