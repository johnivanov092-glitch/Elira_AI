import ollama
from app.core.config import AGENT_PROFILES, DEFAULT_PROFILE

def normalize_profile(name: str):
    if not name or name.lower() == "default":
        return DEFAULT_PROFILE
    return name if name in AGENT_PROFILES else DEFAULT_PROFILE

def run_chat(model_name, profile_name, user_input, history):
    profile = normalize_profile(profile_name)
    system = AGENT_PROFILES.get(profile, AGENT_PROFILES[DEFAULT_PROFILE])

    messages = [{"role": "system", "content": system}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_input})

    try:
        client = ollama.Client()
        resp = client.chat(model=model_name, messages=messages)
        text = resp["message"]["content"]
        return {"ok": True, "answer": text, "warnings": [], "meta": {"profile": profile}}
    except Exception as e:
        return {"ok": False, "answer": "", "warnings": [str(e)], "meta": {}}
