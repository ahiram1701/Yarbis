"""Vision: analisis de imagenes con un modelo multimodal de Ollama Cloud.

Modulo hoja: reutiliza el host/api_key de Ollama (mismo del modelo principal) y
el modelo de vision configurable en `state.vision`. No vuelve multimodal el chat
principal; se usa on-demand desde la herramienta analyze_image o los canales.
"""

import base64
import io
import os

from memory import (
    DEFAULT_OLLAMA_API_KEY_ENV_VAR,
    DEFAULT_VISION_MAX_IMAGE_DIM,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_TIMEOUT_SECONDS,
    load_state,
)

_DEFAULT_PROMPT = "Describe la imagen con detalle util. Si contiene texto, transcribelo."


class VisionError(RuntimeError):
    pass


def _vision_settings(state):
    if not isinstance(state, dict):
        try:
            state = load_state()
        except Exception:
            state = {}
    vision = state.get("vision", {}) if isinstance(state, dict) else {}
    if not isinstance(vision, dict):
        vision = {}
    model = str(vision.get("model", DEFAULT_VISION_MODEL)).strip() or DEFAULT_VISION_MODEL
    try:
        max_dim = int(vision.get("max_image_dim", DEFAULT_VISION_MAX_IMAGE_DIM))
    except (TypeError, ValueError):
        max_dim = DEFAULT_VISION_MAX_IMAGE_DIM
    try:
        timeout = int(vision.get("timeout_seconds", DEFAULT_VISION_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = DEFAULT_VISION_TIMEOUT_SECONDS
    return model, max(256, min(4096, max_dim)), max(10, min(600, timeout)), state


def _ollama_host_and_key(state):
    provider = state.get("model_provider", {}) if isinstance(state, dict) else {}
    ollama = provider.get("ollama", state.get("ollama", {})) if isinstance(provider, dict) else {}
    if not isinstance(ollama, dict):
        ollama = {}
    host = str(ollama.get("host", "")).strip()
    api_key = str(ollama.get("api_key", "")).strip()
    if not api_key:
        env_var = str(ollama.get("api_key_env_var", DEFAULT_OLLAMA_API_KEY_ENV_VAR)).strip() or DEFAULT_OLLAMA_API_KEY_ENV_VAR
        api_key = (
            os.getenv("YARBIS_OLLAMA_API_KEY", "").strip()
            or os.getenv(env_var, "").strip()
            or os.getenv("OLLAMA_API_KEY", "").strip()
        )
    return host, api_key


def _encode_image(source, max_dim: int) -> str:
    from PIL import Image, ImageOps

    if isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(bytes(source)))
    else:
        image = Image.open(str(source))
    with image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest > max_dim:
            ratio = max_dim / float(longest)
            image = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def analyze_image(source, prompt: str = "", settings=None) -> str:
    """Analiza una imagen (ruta o bytes) con el modelo de vision configurado.

    Devuelve el texto del modelo o lanza VisionError.
    """
    model, max_dim, timeout, state = _vision_settings(settings)

    try:
        encoded = _encode_image(source, max_dim)
    except FileNotFoundError:
        raise VisionError(f"No encontre la imagen: {source}")
    except Exception as exc:
        raise VisionError(f"No pude leer la imagen: {exc}") from exc

    host, api_key = _ollama_host_and_key(state)
    try:
        from ollama import Client
    except Exception as exc:
        raise VisionError(f"No pude cargar la libreria ollama: {exc}") from exc

    kwargs = {"timeout": timeout}
    if host:
        kwargs["host"] = host
    if api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
    client = Client(**kwargs)

    question = str(prompt or "").strip() or _DEFAULT_PROMPT
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": question, "images": [encoded]}],
        )
    except Exception as exc:
        raise VisionError(
            f"El modelo de vision '{model}' fallo: {exc}. "
            "Revisa que sea un modelo multimodal valido de Ollama Cloud."
        ) from exc

    content = (getattr(getattr(response, "message", None), "content", "") or "").strip()
    return content or "El modelo de vision no devolvio texto."
