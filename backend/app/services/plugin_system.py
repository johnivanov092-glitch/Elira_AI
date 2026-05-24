"""Plugin compatibility facade."""
from __future__ import annotations

from app.application.plugins.runtime import (
    PLUGINS_DIR,
    check_triggers,
    disable_plugin,
    enable_plugin,
    fire_hook,
    get_plugin_info,
    list_plugins,
    load_plugins,
    reload_plugins,
    run_plugin,
    run_triggered,
    update_plugin_settings,
)

__all__ = [
    "PLUGINS_DIR",
    "check_triggers",
    "disable_plugin",
    "enable_plugin",
    "fire_hook",
    "get_plugin_info",
    "list_plugins",
    "load_plugins",
    "reload_plugins",
    "run_plugin",
    "run_triggered",
    "update_plugin_settings",
]
