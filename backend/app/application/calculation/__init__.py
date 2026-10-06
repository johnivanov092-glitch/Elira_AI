"""Exact, side-effect-free calculations for the agent's math contour.

Owner's decision 2026-10-06: the rule "check calculations with a tool" stays,
and the model gets read-only tools for it (no approval card in "ask" mode):
expressions and algebra, units, finance formulas and table aggregation.
Nothing here executes code from its input.
"""
