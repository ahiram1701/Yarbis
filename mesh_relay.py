"""Cliente del relay de la malla de Yarbis (solo biblioteca estandar).

Habla con un relay que implemente el protocolo de `yarbis_mesh_worker.js`
(en produccion, un Puter Worker; en tests, un servidor local). Un relay es un
buzon + roster: los nodos se enrolan, se envian sobres y hacen polling de los
suyos. Usa solo `urllib`, para que un nodo funcione sin dependencias de terceros.

Modelo de confianza: un unico SECRETO DE RED compartido autentica a los miembros
(header `X-Yarbis-Net`) y firma los sobres (HMAC). Todos los nodos enrolados se
confian mutuamente: enrola solo nodos que controlas. No hay auto-propagacion.
"""

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

MAX_CONTENT_CHARS = 64_000
NETWORK_SECRET_BYTES = 32
DEFAULT_TIMEOUT_SECONDS = 15


class MeshRelayError(RuntimeError):
    """Fallo al hablar con el relay (red, auth o respuesta invalida)."""


def generate_network_secret() -> str:
    """Secreto de una red de malla nueva. Se guarda via credential_store."""
    return secrets.token_urlsafe(NETWORK_SECRET_BYTES)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signing_payload(envelope: dict) -> bytes:
    # Campos canonicos, en orden fijo: cualquier cambio invalida la firma.
    parts = [
        str(envelope.get("id", "")),
        str(envelope.get("from_node", "")),
        str(envelope.get("to_node", "")),
        str(envelope.get("kind", "")),
        str(envelope.get("content", "")),
        str(envelope.get("created_at", "")),
    ]
    return "\n".join(parts).encode("utf-8")


def sign_envelope(secret: str, envelope: dict) -> dict:
    envelope = dict(envelope)
    envelope.pop("sig", None)
    digest = hmac.new(str(secret).encode("utf-8"), _signing_payload(envelope), hashlib.sha256)
    envelope["sig"] = digest.hexdigest()
    return envelope


def verify_envelope(secret: str, envelope: dict) -> bool:
    presented = str(envelope.get("sig", "")).strip()
    if not presented:
        return False
    expected = sign_envelope(secret, envelope)["sig"]
    return hmac.compare_digest(presented, expected)


def make_envelope(from_node: str, to_node: str, kind: str, content: str) -> dict:
    return {
        "id": secrets.token_hex(12),
        "from_node": str(from_node).strip(),
        "to_node": str(to_node).strip(),
        "kind": str(kind).strip() or "direct",
        "content": str(content)[:MAX_CONTENT_CHARS],
        "created_at": _utc_now(),
    }


def _request(
    relay_url: str,
    secret: str,
    method: str,
    path: str,
    body: dict | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    base = str(relay_url or "").strip().rstrip("/")
    if not base:
        raise MeshRelayError("Falta la URL del relay de la malla.")
    url = f"{base}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("X-Yarbis-Net", str(secret))
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise MeshRelayError(f"El relay respondio {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise MeshRelayError(f"No pude contactar el relay: {exc}") from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise MeshRelayError("El relay devolvio una respuesta no-JSON.") from exc
    if not isinstance(payload, dict):
        raise MeshRelayError("El relay devolvio un JSON inesperado.")
    if not payload.get("ok", False):
        raise MeshRelayError(str(payload.get("error", "error desconocido en el relay")))
    return payload


def health(relay_url: str, secret: str = "", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> bool:
    try:
        _request(relay_url, secret, "GET", "/health", timeout_seconds=timeout_seconds)
        return True
    except MeshRelayError:
        return False


def enroll(relay_url: str, secret: str, node_id: str, name: str, capabilities: dict) -> dict:
    return _request(relay_url, secret, "POST", "/mesh/enroll", {
        "node_id": node_id,
        "name": name,
        "capabilities": capabilities or {},
    })


def send(relay_url: str, secret: str, envelope: dict) -> dict:
    signed = sign_envelope(secret, envelope)
    return _request(relay_url, secret, "POST", "/mesh/send", signed)


def poll(relay_url: str, secret: str, node_id: str, max_messages: int = 50) -> list[dict]:
    payload = _request(
        relay_url, secret, "GET",
        f"/mesh/poll?node={urllib.request.quote(str(node_id))}&max={int(max_messages)}",
    )
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return []
    # Descarta lo que no verifique la firma: el relay podria estar comprometido.
    verified = []
    for message in messages:
        if isinstance(message, dict) and verify_envelope(secret, message):
            verified.append(message)
    return verified


def roster(relay_url: str, secret: str) -> list[dict]:
    payload = _request(relay_url, secret, "GET", "/mesh/roster")
    nodes = payload.get("nodes", [])
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def leave(relay_url: str, secret: str, node_id: str) -> dict:
    return _request(relay_url, secret, "POST", "/mesh/leave", {"node_id": node_id})


def worker_source_with_secret(secret: str) -> str:
    """Devuelve el codigo del worker relay con el secreto de red inyectado."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent / "yarbis_mesh_worker.js").read_text(encoding="utf-8")
    # Reemplaza TODAS las ocurrencias del placeholder; el secreto es urlsafe (sin comillas).
    return source.replace("__YARBIS_NET_SECRET__", str(secret))


# Marca de tiempo util para diagnosticos del ultimo sync.
def now_epoch() -> float:
    return time.time()
