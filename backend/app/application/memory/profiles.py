from __future__ import annotations

from app.core.memory import create_mem_profile, delete_mem_profile, list_mem_profiles


def get_profiles():
    return list_mem_profiles()


def create_profile(name: str, emoji: str = "рџ‘¤"):
    ok = create_mem_profile(name=name, emoji=emoji)
    return {"ok": ok, "name": name, "emoji": emoji}


def remove_profile(name: str):
    delete_mem_profile(name)
    return {"ok": True, "name": name}
