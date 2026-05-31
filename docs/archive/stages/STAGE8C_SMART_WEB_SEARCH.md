# Stage 8C — Smart Web Search

Что улучшено:
- planner rewrite для web query
- несколько поисковых запросов вместо одного
- bundle web research
- timeline показывает rewritten queries

Пример:
user_input:
  Find current Ollama documentation for local models and summarize it.

agent queries:
  - ollama local models docs site:ollama.com OR site:github.com/ollama
  - ollama docs local models site:ollama.com
  - github ollama models docs
