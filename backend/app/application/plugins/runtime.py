"""Plugin runtime facade — delegates to the hardened infrastructure layer.

Production callers import from app.application.plugins which re-exports from
here. Redirecting to
app.infrastructure.plugins.plugin_system ensures that:

  - manifest.json is required for every plugin
  - plugins are disabled by default (manifest.enabled = False)
  - run_plugin() executes out-of-process with a 30s timeout
"""
from app.infrastructure.plugins.plugin_system import (
    PLUGINS_DIR,
    PLUGIN_DEFAULT_TIMEOUT,
    check_triggers,
    create_plugin,
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
    upload_plugin,
)

__all__ = [
    "PLUGINS_DIR",
    "PLUGIN_DEFAULT_TIMEOUT",
    "check_triggers",
    "create_plugin",
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
    "upload_plugin",
]
