"""Application adapter to the mutable telegram skill; no domain implementation."""
import sys
from app.core.skill_modules import load_skill_module

sys.modules[__name__] = load_skill_module('telegram', 'runtime.py')
