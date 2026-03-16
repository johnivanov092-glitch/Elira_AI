from ..core.memory import list_mem_profiles, create_mem_profile, delete_mem_profile

def get_profiles():
    return list_mem_profiles()

def create_profile(name: str, emoji: str = "👤"):
    ok = create_mem_profile(name=name, emoji=emoji)
    return {"ok": ok, "name": name, "emoji": emoji}

def remove_profile(name: str):
    delete_mem_profile(name)
    return {"ok": True, "name": name}
