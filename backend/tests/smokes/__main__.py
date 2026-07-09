# -*- coding: utf-8 -*-
"""`python -m tests.smokes` — the module entrypoint from the R5 DoD.

Runs via PEP 420 namespace packages, so no tests/__init__.py is needed (adding one
would change pytest's collection semantics for the whole suite). Delegates to
run.py, which stays directly runnable as `python tests/smokes/run.py` too.
"""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("run.py")), run_name="__main__")
