# Local LLM example (Ollama)

Runs WebMind with no external services: generation and embeddings both come from a
local [Ollama](https://ollama.com) instance.

```bash
ollama pull llama3.1
ollama pull nomic-embed-text
webmind doctor --config examples/local-llm/webmind.yaml
webmind index  --config examples/local-llm/webmind.yaml
webmind ask "What are the eligibility requirements?" --config examples/local-llm/webmind.yaml
```

Any OpenAI-compatible local server (vLLM, LM Studio, llama.cpp server) works the same way:

```yaml
llm:
  provider: openai
  model: your-model-name
  base_url: http://localhost:8000/v1
  api_key: unused   # most local servers ignore it
```

If you switch embedding models after indexing, run `webmind index --reembed`. WebMind
records the embedding model in the index and refuses to mix vectors from different models.
