
def detect_intent(message: str):

    m = message.lower()

    if "plan" in m or "план" in m:
        return "planner"

    if "fix" in m or "исправь" in m:
        return "coder"

    if "search" in m or "найди" in m:
        return "research"

    if "analyze" in m or "проанализируй" in m:
        return "analyze"

    return "chat"
