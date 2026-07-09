"""Tests for the shared feature-flags layer (app.application.feature_flags).

Covers the resolution contract that both deferred-track gates depend on:

  * default OFF (no file, no env);
  * the persisted file survives and is read back;
  * an env var is an override that wins over the file (truthy *and* falsy);
  * set_flag persists and reports the new effective state;
  * a malformed / unknown-key file degrades to all-off instead of raising.

The module resolves ``CONFIG_PATH`` once at import via ``data_file``, so each
test reloads it bound to a fresh temp data dir — the same pattern the LSP
runtime tests use (``ELIRA_DATA_DIR`` + importlib.reload).
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest


def _fresh_module(tmp_data: str):
    """Reload feature_flags bound to a temp data dir (clean CONFIG_PATH)."""
    os.environ["ELIRA_DATA_DIR"] = tmp_data
    from app.core import data_files
    importlib.reload(data_files)
    from app.application import feature_flags
    importlib.reload(feature_flags)
    return feature_flags


def _clear_env() -> None:
    os.environ.pop("ELIRA_DATA_DIR", None)
    os.environ.pop("ELIRA_REMOTE_MCP", None)
    os.environ.pop("ELIRA_ACTION_ENVELOPES", None)


class DefaultOffTest(unittest.TestCase):
    def test_all_flags_off_with_no_file_no_env(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                os.environ.pop("ELIRA_REMOTE_MCP", None)
                os.environ.pop("ELIRA_ACTION_ENVELOPES", None)
                ff = _fresh_module(data)
                self.assertFalse(ff.flag_enabled("remote_mcp"))
                self.assertFalse(ff.flag_enabled("action_envelopes"))
                self.assertEqual(
                    ff.get_flags(),
                    {"remote_mcp": False, "action_envelopes": False, "proactive": False, "catalog_assist": False, "web_corpus": False},
                )
            finally:
                _clear_env()

    def test_reading_does_not_create_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.flag_enabled("remote_mcp")
                ff.get_flags()
                self.assertFalse(ff.CONFIG_PATH.exists())
            finally:
                _clear_env()


class PersistenceTest(unittest.TestCase):
    def test_set_flag_persists_and_returns_state(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                state = ff.set_flag("action_envelopes", True)
                self.assertEqual(
                    state, {"remote_mcp": False, "action_envelopes": True, "proactive": False, "catalog_assist": False, "web_corpus": False}
                )
                self.assertTrue(ff.CONFIG_PATH.exists())
                self.assertTrue(ff.flag_enabled("action_envelopes"))
                self.assertFalse(ff.flag_enabled("remote_mcp"))
            finally:
                _clear_env()

    def test_persisted_value_read_back_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.set_flag("remote_mcp", True)
                # Simulate a process restart: reload the module, same data dir.
                ff2 = _fresh_module(data)
                self.assertTrue(ff2.flag_enabled("remote_mcp"))
                self.assertFalse(ff2.flag_enabled("action_envelopes"))
            finally:
                _clear_env()

    def test_set_flag_unknown_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                with self.assertRaises(KeyError):
                    ff.set_flag("bogus", True)
            finally:
                _clear_env()


class EnvOverrideTest(unittest.TestCase):
    def test_env_truthy_overrides_absent_file(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                for val in ("1", "on", "true", "yes", "ON", "  Yes  "):
                    os.environ["ELIRA_REMOTE_MCP"] = val
                    self.assertTrue(ff.flag_enabled("remote_mcp"), val)
                    self.assertTrue(ff.get_flags()["remote_mcp"], val)
            finally:
                _clear_env()

    def test_env_falsy_overrides_persisted_true(self) -> None:
        # File says ON, env says OFF → operator override wins (stays OFF).
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.set_flag("action_envelopes", True)
                for val in ("0", "off", "false", "no", "maybe"):
                    os.environ["ELIRA_ACTION_ENVELOPES"] = val
                    self.assertFalse(ff.flag_enabled("action_envelopes"), val)
                    self.assertFalse(ff.get_flags()["action_envelopes"], val)
            finally:
                _clear_env()

    def test_blank_env_falls_through_to_file(self) -> None:
        # An empty / whitespace env var is "not set" → use the file value.
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.set_flag("remote_mcp", True)
                for val in ("", "   "):
                    os.environ["ELIRA_REMOTE_MCP"] = val
                    self.assertTrue(ff.flag_enabled("remote_mcp"), repr(val))
            finally:
                _clear_env()


class MalformedFileTest(unittest.TestCase):
    def test_bad_json_degrades_to_all_off(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                ff.CONFIG_PATH.write_text("{not json", encoding="utf-8")
                self.assertFalse(ff.flag_enabled("remote_mcp"))
                self.assertEqual(
                    ff.get_flags(),
                    {"remote_mcp": False, "action_envelopes": False, "proactive": False, "catalog_assist": False, "web_corpus": False},
                )
            finally:
                _clear_env()

    def test_unknown_keys_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            try:
                ff = _fresh_module(data)
                ff.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                ff.CONFIG_PATH.write_text(
                    json.dumps({"action_envelopes": True, "ghost": True}),
                    encoding="utf-8",
                )
                self.assertTrue(ff.flag_enabled("action_envelopes"))
                # Unknown key must not leak into the canonical set.
                self.assertEqual(
                    set(ff.get_flags().keys()),
                    {"remote_mcp", "action_envelopes", "proactive", "catalog_assist", "web_corpus"},
                )
            finally:
                _clear_env()


if __name__ == "__main__":
    unittest.main()
