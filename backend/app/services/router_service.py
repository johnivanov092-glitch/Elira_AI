class RouterService:
    def route(self, plan):
        task = plan.get("task")
        if task == "research":
            return "browser_agent"
        if task == "code":
            return "python_executor"
        return "chat"