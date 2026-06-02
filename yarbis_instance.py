import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent
INSTANCES_ROOT = WORKSPACE_ROOT / ".yarbis_instances"
BUS_DIR_NAME = "_bus"
REGISTRY_FILE_NAME = "instances.json"
DEFAULT_INSTANCE_ID = "default"
ENV_INSTANCE = "YARBIS_INSTANCE"
ENV_SERVICE_NAME = "YARBIS_SERVICE_NAME"
SERVICE_BASE_NAME = "Yarbis"
DEFAULT_MOBILE_UI_PORT = 8787
MAX_INSTANCE_ID_CHARS = 48
INSTANCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


class YarbisInstanceError(ValueError):
    pass


def normalize_instance_id(value: object = "") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_INSTANCE_ID
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9_-]", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    if not text:
        return DEFAULT_INSTANCE_ID
    if text in {".", "..", BUS_DIR_NAME}:
        raise YarbisInstanceError(f"Id de instancia reservado: {text}")
    text = text[:MAX_INSTANCE_ID_CHARS].rstrip("-_")
    if not INSTANCE_ID_PATTERN.match(text):
        raise YarbisInstanceError(
            "Id de instancia invalido. Usa letras, numeros, guion o guion bajo; "
            "debe iniciar con letra o numero."
        )
    return text


def current_instance_id() -> str:
    return normalize_instance_id(os.environ.get(ENV_INSTANCE, DEFAULT_INSTANCE_ID))


def configure_instance(instance_id: object = "") -> str:
    normalized = normalize_instance_id(instance_id)
    os.environ[ENV_INSTANCE] = normalized
    os.environ[ENV_SERVICE_NAME] = service_name(normalized)
    return normalized


def configure_from_argv(argv: list[str] | None = None) -> str:
    args = list(argv if argv is not None else sys.argv[1:])
    instance_id = os.environ.get(ENV_INSTANCE, "")
    for index, arg in enumerate(args):
        if arg == "--instance" and index + 1 < len(args):
            instance_id = args[index + 1]
            break
        if arg.startswith("--instance="):
            instance_id = arg.split("=", 1)[1]
            break
    return configure_instance(instance_id)


def is_default_instance(instance_id: object | None = None) -> bool:
    return normalize_instance_id(current_instance_id() if instance_id is None else instance_id) == DEFAULT_INSTANCE_ID


def instance_root(instance_id: object | None = None) -> Path:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return WORKSPACE_ROOT
    return INSTANCES_ROOT / normalized


def state_file(instance_id: object | None = None) -> Path:
    if is_default_instance(instance_id):
        return Path("state.json")
    return instance_root(instance_id) / "state.json"


def runtime_dir(instance_id: object | None = None) -> Path:
    return instance_root(instance_id) / ".yarbis_runtime"


def state_lock_file(instance_id: object | None = None) -> Path:
    return runtime_dir(instance_id) / "state.lock"


def memory_backups_dir(instance_id: object | None = None) -> Path:
    return instance_root(instance_id) / ".yarbis_memory_backups"


def operation_lock_file(instance_id: object | None = None) -> Path:
    return runtime_dir(instance_id) / "session.lock"


def service_name(instance_id: object | None = None) -> str:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return SERVICE_BASE_NAME
    return f"{SERVICE_BASE_NAME}-{normalized}"


def service_display_name(instance_id: object | None = None) -> str:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return SERVICE_BASE_NAME
    return f"{SERVICE_BASE_NAME} ({normalized})"


def service_description(instance_id: object | None = None) -> str:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return "Yarbis local agent background service."
    return f"Yarbis local agent background service for instance {normalized}."


def default_mobile_ui_port(instance_id: object | None = None) -> int:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return DEFAULT_MOBILE_UI_PORT
    offset = sum((index + 1) * ord(char) for index, char in enumerate(normalized)) % 900
    return DEFAULT_MOBILE_UI_PORT + 1 + offset


def desktop_mutex_name(instance_id: object | None = None) -> str:
    return f"Local\\YarbisDesktopSingleInstance-{normalize_instance_id(current_instance_id() if instance_id is None else instance_id)}"


def pc_context_mutex_name(instance_id: object | None = None) -> str:
    return f"Local\\YarbisPcContextHelper-{normalize_instance_id(current_instance_id() if instance_id is None else instance_id)}"


def pc_context_task_name(instance_id: object | None = None) -> str:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return "YarbisLocalContext"
    return f"YarbisLocalContext-{normalized}"


def registry_file() -> Path:
    return INSTANCES_ROOT / REGISTRY_FILE_NAME


def bus_dir() -> Path:
    return INSTANCES_ROOT / BUS_DIR_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_registry() -> dict:
    try:
        payload = json.loads(registry_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    instances = payload.get("instances", {})
    if not isinstance(instances, dict):
        instances = {}
    return {"schema_version": 1, "instances": instances}


def _write_registry(payload: dict) -> None:
    registry_file().parent.mkdir(parents=True, exist_ok=True)
    tmp_path = registry_file().with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(registry_file())


def ensure_instance_dirs(instance_id: object | None = None) -> str:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    if normalized != DEFAULT_INSTANCE_ID:
        instance_root(normalized).mkdir(parents=True, exist_ok=True)
    runtime_dir(normalized).mkdir(parents=True, exist_ok=True)
    memory_backups_dir(normalized).mkdir(parents=True, exist_ok=True)
    bus_dir().mkdir(parents=True, exist_ok=True)
    return normalized


def ensure_instance_registered(instance_id: object | None = None, display_name: str = "") -> dict:
    normalized = ensure_instance_dirs(instance_id)
    now = _utc_now()
    payload = _load_registry()
    instances = payload["instances"]
    existing = instances.get(normalized, {}) if isinstance(instances.get(normalized, {}), dict) else {}
    instances[normalized] = {
        "id": normalized,
        "display_name": str(display_name).strip() or existing.get("display_name") or normalized,
        "state_file": str(state_file(normalized)),
        "runtime_dir": str(runtime_dir(normalized)),
        "service_name": service_name(normalized),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    _write_registry(payload)
    return dict(instances[normalized])


def create_instance(instance_id: object, display_name: str = "") -> dict:
    normalized = normalize_instance_id(instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        return ensure_instance_registered(normalized, display_name=display_name or DEFAULT_INSTANCE_ID)
    return ensure_instance_registered(normalized, display_name=display_name or normalized)


def list_instances() -> list[dict]:
    payload = _load_registry()
    instances = payload["instances"]
    if DEFAULT_INSTANCE_ID not in instances:
        instances[DEFAULT_INSTANCE_ID] = {
            "id": DEFAULT_INSTANCE_ID,
            "display_name": DEFAULT_INSTANCE_ID,
            "state_file": str(state_file(DEFAULT_INSTANCE_ID)),
            "runtime_dir": str(runtime_dir(DEFAULT_INSTANCE_ID)),
            "service_name": service_name(DEFAULT_INSTANCE_ID),
            "created_at": "",
            "updated_at": "",
        }
    result = []
    for item in instances.values():
        if not isinstance(item, dict):
            continue
        normalized = normalize_instance_id(item.get("id", DEFAULT_INSTANCE_ID))
        result.append({
            "id": normalized,
            "display_name": str(item.get("display_name", "")).strip() or normalized,
            "state_file": str(state_file(normalized)),
            "runtime_dir": str(runtime_dir(normalized)),
            "service_name": service_name(normalized),
            "exists": state_file(normalized).exists(),
            "updated_at": str(item.get("updated_at", "")).strip(),
        })
    result.sort(key=lambda item: (item["id"] != DEFAULT_INSTANCE_ID, item["id"]))
    return result


def with_instance_env(instance_id: object | None = None, base_env: dict | None = None) -> dict:
    normalized = normalize_instance_id(current_instance_id() if instance_id is None else instance_id)
    env = dict(os.environ if base_env is None else base_env)
    env[ENV_INSTANCE] = normalized
    env[ENV_SERVICE_NAME] = service_name(normalized)
    return env


def wait_for_file(path: Path, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()
