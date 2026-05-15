"""ollama_runtime_service — список моделей Ollama (совместимо со всеми версиями)."""
import ollama


async def list_ollama_models():
    """Получает список моделей. Работает с ollama>=0.1 и >=0.3."""
    try:
        resp = ollama.list()

        # Новые версии: resp.models (список объектов)
        # Старые версии: resp["models"] (список dict)
        raw = []
        if hasattr(resp, "models"):
            raw = resp.models or []
        elif isinstance(resp, dict):
            raw = resp.get("models", [])

        models = []
        for m in raw:
            # Объект: m.name или m.model
            # Dict: m["name"] или m["model"]
            name = ""
            if hasattr(m, "name"):
                name = m.name or ""
            elif hasattr(m, "model"):
                name = m.model or ""
            elif isinstance(m, dict):
                name = m.get("name", "") or m.get("model", "")

            if name:
                size = 0
                if hasattr(m, "size"):
                    size = m.size or 0
                elif isinstance(m, dict):
                    size = m.get("size", 0)
                models.append({"name": str(name), "size": size})

        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}
