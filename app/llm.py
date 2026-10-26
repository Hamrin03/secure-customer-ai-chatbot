import httpx
from . import config


class LLMError(Exception):
    kind = "unavailable"


class LLMTimeout(LLMError):
    kind = "timeout"


class EmbeddingError(LLMError):
    kind = "embedding"


def _post(path, payload, timeout, err):
    try:
        r = httpx.post(f"{config.OLLAMA_URL}{path}", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        raise LLMTimeout("timeout") if err is LLMError else err("timeout")
    except (httpx.HTTPError, ValueError):
        raise err("unavailable")


def chat(messages, tools):
    data = _post(
        "/api/chat",
        {"model": config.CHAT_MODEL, "messages": messages, "tools": tools, "stream": False},
        config.LLM_TIMEOUT, LLMError,
    )
    return data["message"]


def embed(text):
    data = _post("/api/embed", {"model": config.EMBED_MODEL, "input": text}, 30, EmbeddingError)
    try:
        return data["embeddings"][0]
    except (KeyError, IndexError):
        raise EmbeddingError("bad response")
