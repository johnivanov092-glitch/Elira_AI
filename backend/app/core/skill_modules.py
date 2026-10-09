"""Load mutable skill implementations for application integrations.

This is module loading, not another executor or tool registry. Model actions
still go through run_bash and Workflow. Application consumers (preview, bot,
remote cleanup) share the exact same implementation with the skill CLI.
"""
from __future__ import annotations
import importlib.util
import os
import re
import sys
from pathlib import Path
from threading import RLock
from types import ModuleType

_LOCK = RLock()
_CACHE: dict[Path, ModuleType] = {}

def skill_root(skill: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", skill):
        raise ValueError("Invalid skill name")
    from app.core.config import DATA_DIR, ROOT_DIR
    explicit = os.getenv("ELIRA_SKILLS_ROOT")
    if explicit:
        return (Path(explicit).resolve() / skill)
    installed = DATA_DIR / "skills" / skill
    return installed if installed.is_dir() else ROOT_DIR / "skills" / skill

def load_skill_module(skill: str, filename: str) -> ModuleType:
    root = skill_root(skill).resolve()
    source = (root / filename).resolve()
    if not source.is_relative_to(root) or source.suffix != ".py":
        raise ValueError("Invalid skill module path")
    with _LOCK:
        if source in _CACHE:
            return _CACHE[source]
        # Missing installed files are errors; never silently substitute factory code.
        if not source.is_file():
            raise FileNotFoundError(f"Skill module unavailable: {skill}/{filename}")
        import hashlib
        module_name = "elira_skill_" + hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:20]
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {skill}/{filename}")
        module = importlib.util.module_from_spec(spec)
        _CACHE[source] = module
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            _CACHE.pop(source, None)
            sys.modules.pop(module_name, None)
            raise
        return module
