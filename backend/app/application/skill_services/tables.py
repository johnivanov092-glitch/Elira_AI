"""Application adapter to the mutable data-analysis skill; no domain implementation."""
import sys
from app.core.skill_modules import load_skill_module

sys.modules[__name__] = load_skill_module('data-analysis', 'tables.py')
