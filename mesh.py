"""Malla de nodos de Yarbis: coordinacion entre maquinas del usuario.

Cada instancia de Yarbis con la malla activa es un NODO. Los nodos se enrolan en
un relay (Puter Worker, ver yarbis_mesh_worker.js), se descubren por el roster y
se pasan mensajes y tareas. El enrutamiento por capacidad permite que un nodo sin
pantalla delegue "toma una captura" a un nodo que si la tiene.

Diseno auto-contenido: la malla NO reescribe el bus local por archivos
(yarbis_bus, entre instancias de una misma maquina). En su lugar, `maybe_sync()`
—llamado desde el loop del servicio, con throttle— jala mensajes del relay, los
corre por el agente reusando el patron de yarbis_bus, y devuelve la respuesta al
nodo origen. Es inter-maquina; el bus sigue siendo intra-maquina.

Modelo de confianza (a proposito): todos los nodos enrolados comparten el secreto
de red y se confian. No hay auto-propagacion: cada nodo lo enrola el usuario.
"""

import time
import traceback

import mesh_relay
from memory import load_state, state_transaction

KIND_MESH_TASK = "mesh_task"
KIND_MESH_REPLY = "mesh_reply"

SYNC_INTERVAL_SECONDS = 25
MAX_MESSAGES_PER_SYNC = 3

_LAST_SYNC = {"at": 0.0}
_ENROLLED_THIS_PROCESS = {"done": False}


class MeshError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Configuracion / identidad del nodo
# --------------------------------------------------------------------------- #

def _mesh_settings(state: dict | None = None) -> dict:
    mesh = (state or load_state()).get("mesh", {})
    return mesh if isinstance(mesh, dict) else {}


def is_active(state: dict | None = None) -> bool:
    mesh = _mesh_settings(state)
    return bool(mesh.get("enabled")) and bool(str(mesh.get("relay_url", "")).strip()) \
        and bool(str(mesh.get("secret_ref", "")).strip())


def _load_secret(mesh: dict) -> str:
    from credential_store import CredentialStoreError, load_secret

    ref = str(mesh.get("secret_ref", "")).strip()
    if not ref:
        raise MeshError("La malla no tiene secreto configurado (usa mesh_create_network).")
    try:
        return load_secret(ref)
    except CredentialStoreError as exc:
        raise MeshError(f"No pude leer el secreto de la red: {exc}") from exc


def node_capabilities() -> dict:
    """Capacidades del nodo, para que el roster sepa a quien delegar que."""
    try:
        import device_profile

        profile = device_profile.get_profile()
        caps = dict(profile.get("capabilities", {}))
        caps["device_class"] = profile.get("device_class", "")
        return caps
    except Exception:
        return {}


def node_identity(state: dict | None = None) -> dict:
    mesh = _mesh_settings(state)
    node_id = str(mesh.get("node_id", "")).strip()
    if not node_id:
        raise MeshError("Este nodo no tiene node_id (usa mesh_create_network).")
    return {
        "node_id": node_id,
        "node_name": str(mesh.get("node_name", "")).strip() or node_id,
        "relay_url": str(mesh.get("relay_url", "")).strip(),
    }


# --------------------------------------------------------------------------- #
# Operaciones de red (enrolar, enviar, delegar, roster)
# --------------------------------------------------------------------------- #

def enroll_self(state: dict | None = None) -> str:
    mesh = _mesh_settings(state)
    identity = node_identity(state)
    secret = _load_secret(mesh)
    mesh_relay.enroll(
        identity["relay_url"], secret,
        identity["node_id"], identity["node_name"], node_capabilities(),
    )
    _ENROLLED_THIS_PROCESS["done"] = True
    return identity["node_id"]


def list_nodes() -> list[dict]:
    mesh = _mesh_settings()
    identity = node_identity()
    secret = _load_secret(mesh)
    return mesh_relay.roster(identity["relay_url"], secret)


def send_to_node(to_node: str, content: str, kind: str = KIND_MESH_TASK) -> str:
    mesh = _mesh_settings()
    identity = node_identity()
    secret = _load_secret(mesh)
    target = str(to_node).strip()
    if not target:
        raise MeshError("Indica el nodo destino.")
    if target == identity["node_id"]:
        raise MeshError("Ese es este mismo nodo.")
    envelope = mesh_relay.make_envelope(identity["node_id"], target, kind, content)
    result = mesh_relay.send(identity["relay_url"], secret, envelope)
    return str(result.get("id", ""))


def delegate(need_capability: str, task: str) -> str:
    """Elige un nodo del roster que tenga la capacidad pedida y le manda la tarea."""
    identity = node_identity()
    capability = str(need_capability).strip()
    nodes = list_nodes()
    candidates = [
        node for node in nodes
        if str(node.get("node_id", "")).strip()
        and node.get("node_id") != identity["node_id"]
        and bool((node.get("capabilities", {}) or {}).get(capability))
    ]
    if not candidates:
        raise MeshError(
            f"Ningun otro nodo de la malla tiene la capacidad '{capability}'. "
            "Enrola un nodo que la tenga o revisa el roster con mesh_list_nodes."
        )
    chosen = candidates[0]
    prompt = (
        f"Tarea delegada por el nodo `{identity['node_id']}` porque este equipo no puede hacerla "
        f"(requiere '{capability}'):\n\n{task}"
    )
    send_to_node(str(chosen["node_id"]), prompt, kind=KIND_MESH_TASK)
    return str(chosen["node_id"])


# --------------------------------------------------------------------------- #
# Procesamiento de mensajes entrantes (reusa el patron de yarbis_bus)
# --------------------------------------------------------------------------- #

def _run_mesh_task(envelope: dict) -> str:
    from session import _capture_operation_output, run_one_cycle, session_operation_lock

    prompt = (
        f"Mensaje de la malla desde el nodo `{envelope.get('from_node', '')}`:\n\n"
        f"{envelope.get('content', '')}\n\n"
        "Atiende la peticion de forma concreta. Tu respuesta se le devolvera a ese nodo."
    )
    with session_operation_lock("Malla Yarbis", blocking=False):
        state_transaction(
            "mesh_task_received",
            lambda state: state["messages"].append({"role": "user", "content": prompt}),
        )
        output, _ = _capture_operation_output(run_one_cycle)
    return output or "Tarea de la malla procesada sin salida visible."


def _record_reply(envelope: dict) -> None:
    import activity

    activity.append_activity(
        f"Malla: respuesta de {envelope.get('from_node', '')}",
        str(envelope.get("content", "")),
    )


def _handle_inbound(envelope: dict) -> None:
    """Procesa un sobre entrante y, si es una tarea, devuelve la respuesta al origen."""
    from session import SessionOperationBusy

    kind = str(envelope.get("kind", "")).strip()
    from_node = str(envelope.get("from_node", "")).strip()

    if kind == KIND_MESH_REPLY:
        _record_reply(envelope)
        return

    try:
        response = _run_mesh_task(envelope)
    except SessionOperationBusy:
        # Ocupado: re-encola devolviendose el sobre a si mismo para el proximo sync.
        _requeue_to_self(envelope)
        return

    if from_node:
        mesh = _mesh_settings()
        identity = node_identity()
        secret = _load_secret(mesh)
        reply = mesh_relay.make_envelope(
            identity["node_id"], from_node, KIND_MESH_REPLY, response,
        )
        try:
            mesh_relay.send(identity["relay_url"], secret, reply)
        except mesh_relay.MeshRelayError:
            pass  # el relay quedo inalcanzable; la respuesta ya quedo en la actividad local


def _requeue_to_self(envelope: dict) -> None:
    try:
        mesh = _mesh_settings()
        identity = node_identity()
        secret = _load_secret(mesh)
        again = mesh_relay.make_envelope(
            str(envelope.get("from_node", "")),
            identity["node_id"],
            str(envelope.get("kind", KIND_MESH_TASK)),
            str(envelope.get("content", "")),
        )
        mesh_relay.send(identity["relay_url"], secret, again)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Sync throttled desde el loop del servicio
# --------------------------------------------------------------------------- #

def sync_now(limit: int = MAX_MESSAGES_PER_SYNC) -> int:
    """Un ciclo de sincronizacion: enrola (una vez), jala y procesa entrantes."""
    mesh = _mesh_settings()
    if not is_active({"mesh": mesh}):
        return 0
    identity = node_identity()
    secret = _load_secret(mesh)

    if not _ENROLLED_THIS_PROCESS["done"]:
        try:
            enroll_self()
        except Exception:
            pass  # el enrolamiento se reintenta el proximo sync

    messages = mesh_relay.poll(identity["relay_url"], secret, identity["node_id"], max_messages=limit)
    processed = 0
    for envelope in messages:
        _handle_inbound(envelope)
        processed += 1

    if processed or not str(mesh.get("last_sync_at", "")).strip():
        from datetime import datetime, timezone

        state_transaction(
            "mesh_sync",
            lambda state: state.setdefault("mesh", {}).__setitem__(
                "last_sync_at", datetime.now(timezone.utc).isoformat()
            ),
        )
    return processed


def maybe_sync() -> int:
    """Llamada frecuente desde el loop del servicio; hace no-op hasta el intervalo.

    Nunca lanza: un fallo del relay no debe romper el loop. Ligero: un GET y, solo
    si hay mensajes, un ciclo del agente (bajo el lock de sesion no-bloqueante).
    """
    now = time.monotonic()
    if now - _LAST_SYNC["at"] < SYNC_INTERVAL_SECONDS:
        return 0
    _LAST_SYNC["at"] = now
    try:
        if not is_active():
            return 0
        return sync_now()
    except Exception:
        try:
            import activity

            activity.append_activity("Malla: error de sync", traceback.format_exc()[-800:])
        except Exception:
            pass
        return 0
