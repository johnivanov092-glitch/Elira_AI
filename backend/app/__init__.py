
# The same sealed pure utilities are used by the backend and skill processes.
import sys as _sys
from pathlib import Path as _Path
_shared = _Path(__file__).resolve().parents[2] / "shared"
if str(_shared) not in _sys.path:
    _sys.path.insert(0, str(_shared))
