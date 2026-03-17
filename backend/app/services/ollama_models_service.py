
import httpx

OLLAMA = "http://localhost:11434"

async def list_models():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{OLLAMA}/api/tags")
        data = r.json()

        models = []

        for m in data.get("models", []):
            models.append({
                "name": m["name"],
                "type": "local"
            })

        return models
