"""Application adapter to the mutable linux-admin skill; no domain implementation."""
import sys
from app.core.skill_modules import load_skill_module

sys.modules[__name__] = load_skill_module('linux-admin', 'systemd_inspect.py')
