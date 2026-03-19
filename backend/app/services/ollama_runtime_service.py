"""Stub: ollama_runtime_service."""
import ollama

async def list_ollama_models():
    try:
        resp = ollama.list()
        models = resp.get("models", [])
        return {"models": [{"name": m.get("name",""), "size": m.get("size",0)} for m in models]}
    except Exception as e:
        return {"models": [], "error": str(e)}
