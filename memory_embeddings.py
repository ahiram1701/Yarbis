"""Backend de embeddings OPCIONAL para la recuperacion de memoria.

Mejora semantica del recall lexico: cuando esta activado y hay un proveedor de
embeddings, `memory_recall` reordena por similitud coseno. Si no hay proveedor o
falla, todo cae al lexico en silencio; NUNCA es requisito (el core corre sin
dependencias en Termux/Raspberry). Usa solo `urllib`.

Soporta dos formas comunes:
  - Ollama:  POST {host}/api/embeddings  {model, prompt}  -> {embedding: [...]}
  - OpenAI-compatible: POST {host}/v1/embeddings {model, input} -> {data:[{embedding}]}
"""

import hashlib
import json
import urllib.error
import urllib.request

# Cache por (config, texto) -> vector. Evita re-embeder el mismo item cada turno.
_VEC_CACHE: dict = {}
_MAX_CACHE = 2000
_TIMEOUT = 20


def _config(state: dict) -> dict:
    recall = state.get("recall", {}) if isinstance(state, dict) else {}
    embeddings = recall.get("embeddings", {}) if isinstance(recall, dict) else {}
    return embeddings if isinstance(embeddings, dict) else {}


def _resolve_endpoint(state: dict) -> tuple[str, str, str]:
    """Devuelve (estilo, url, model): estilo 'ollama' u 'openai'."""
    cfg = _config(state)
    model = str(cfg.get("model", "")).strip() or "nomic-embed-text"
    host = str(cfg.get("host", "")).strip().rstrip("/")
    provider = str(cfg.get("provider", "")).strip().lower()

    if not host:
        # Sin host explicito: asume Ollama local (lo normal para embeddings).
        host = "http://localhost:11434"
        provider = provider or "ollama"

    if provider in ("openai", "openai_compat") or host.endswith("/v1"):
        base = host if host.endswith("/v1") else f"{host}/v1"
        return "openai", f"{base}/embeddings", model
    return "ollama", f"{host}/api/embeddings", model


def _cache_key(state_sig: str, text: str) -> str:
    return hashlib.sha1(f"{state_sig}\n{text}".encode("utf-8")).hexdigest()


def _post_json(url: str, payload: dict) -> dict | None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _embed_one(style: str, url: str, model: str, text: str) -> list[float] | None:
    if style == "openai":
        body = _post_json(url, {"model": model, "input": text})
        if isinstance(body, dict):
            data = body.get("data", [])
            if data and isinstance(data[0], dict):
                vec = data[0].get("embedding")
                if isinstance(vec, list):
                    return [float(x) for x in vec]
        return None
    body = _post_json(url, {"model": model, "prompt": text})
    if isinstance(body, dict):
        vec = body.get("embedding")
        if isinstance(vec, list):
            return [float(x) for x in vec]
    return None


def embed_texts(state: dict, texts: list[str]) -> list[list[float]]:
    """Vectores para cada texto, o [] si el proveedor no esta disponible.

    Cacheado por texto. Best-effort: si un texto falla, aborta y devuelve []
    para que el recall use el orden lexico (no una mezcla incompleta).
    """
    if not texts:
        return []
    style, url, model = _resolve_endpoint(state)
    state_sig = f"{style}:{url}:{model}"

    vectors: list[list[float]] = []
    for text in texts:
        key = _cache_key(state_sig, text)
        cached = _VEC_CACHE.get(key)
        if cached is not None:
            vectors.append(cached)
            continue
        vec = _embed_one(style, url, model, str(text))
        if not vec:
            return []  # sin proveedor o error -> fallback lexico completo
        if len(_VEC_CACHE) < _MAX_CACHE:
            _VEC_CACHE[key] = vec
        vectors.append(vec)
    return vectors
