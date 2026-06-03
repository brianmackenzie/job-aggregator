"""pytest conftest.py — adds src/ to sys.path so all test imports work.

When the original author runs `python -m pytest src/scoring/tests/ -v` from the repo root,
pytest discovers this file and inserts the src/ directory at the front of
sys.path. That makes `from scoring.engine import score` resolve to
`jobs/src/scoring/engine.py`, matching exactly what the Lambda runtime sees
(CodeUri: src/).
"""
import sys
from pathlib import Path

# Insert src/ once, at the front, so it takes priority over any installed
# packages with the same names.
_SRC = str(Path(__file__).resolve.parent)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
