import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parent
INSTANCES_ROOT = WORKSPACE_ROOT / ".yarbis_instances"
BUS_DIR_NAME = "_bus"
ARCHIVED_DIR_NAME = "_archived"
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


def archived_instances_dir() -> Path:
    return INSTANCES_ROOT / ARCHIVED_DIR_NAME


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
    archived_instances = payload.get("archived_instances", {})
    if not isinstance(archived_instances, dict):
        archived_instances = {}
    return {
        "schema_version": 1,
        "instances": instances,
        "archived_instances": archived_instances,
    }


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
    archived_instances_dir().mkdir(parents=True, exist_ok=True)
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


def rename_instance(instance_id: object, display_name: object) -> dict:
    normalized = normalize_instance_id(instance_id)
    cleaned_name = str(display_name or "").strip()
    if not cleaned_name:
        raise YarbisInstanceError("El nombre visible de la instancia no puede quedar vacio.")

    payload = _load_registry()
    instances = payload["instances"]
    existing = instances.get(normalized, {}) if isinstance(instances.get(normalized, {}), dict) else {}
    if normalized not in instances:
        existing = ensure_instance_registered(normalized)
        payload = _load_registry()
        instances = payload["instances"]

    updated = {
        **existing,
        "id": normalized,
        "display_name": cleaned_name[:80],
        "state_file": str(state_file(normalized)),
        "runtime_dir": str(runtime_dir(normalized)),
        "service_name": service_name(normalized),
        "updated_at": _utc_now(),
    }
    instances[normalized] = updated
    _write_registry(payload)
    return dict(updated)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return 0


def instance_is_active(instance_id: object) -> bool:
    normalized = normalize_instance_id(instance_id)
    for name in ("service.pid", "desktop.pid"):
        pid = _read_pid(runtime_dir(normalized) / name)
        if pid and _pid_is_running(pid):
            return True
    return False


def _archive_key(instance_id: str, archived_at: str) -> str:
    timestamp = re.sub(r"[^0-9A-Za-z]", "", archived_at)[:20] or str(int(time.time()))
    return f"{instance_id}-{timestamp}"


def archive_instance(instance_id: object) -> dict:
    normalized = normalize_instance_id(instance_id)
    if normalized == DEFAULT_INSTANCE_ID:
        raise YarbisInstanceError("La instancia default no se puede archivar.")
    if normalized == current_instance_id():
        raise YarbisInstanceError("No puedes archivar la instancia que esta abierta ahora.")
    if instance_is_active(normalized):
        raise YarbisInstanceError("No puedes archivar una instancia activa. Cierrala o deten su servicio primero.")

    payload = _load_registry()
    instances = payload["instances"]
    existing = instances.get(normalized, {}) if isinstance(instances.get(normalized, {}), dict) else {}
    if normalized not in instances and not instance_root(normalized).exists():
        raise YarbisInstanceError(f"No existe la instancia {normalized}.")

    archived_at = _utc_now()
    key = _archive_key(normalized, archived_at)
    destination = archived_instances_dir() / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise YarbisInstanceError(f"Ya existe un archivo de instancia para {normalized}.")

    source_root = instance_root(normalized)
    if source_root.exists():
        shutil.move(str(source_root), str(destination))
    else:
        destination.mkdir(parents=True, exist_ok=True)

    archived = {
        "archive_id": key,
        "id": normalized,
        "display_name": str(existing.get("display_name", "")).strip() or normalized,
        "state_file": str(destination / "state.json"),
        "runtime_dir": str(destination / ".yarbis_runtime"),
        "service_name": service_name(normalized),
        "archive_dir": str(destination),
        "archived_at": archived_at,
        "created_at": existing.get("created_at", ""),
    }
    payload["archived_instances"][key] = archived
    instances.pop(normalized, None)
    _write_registry(payload)
    return dict(archived)


def list_archived_instances() -> list[dict]:
    payload = _load_registry()
    result = []
    for archive_id, item in payload["archived_instances"].items():
        if not isinstance(item, dict):
            continue
        normalized = normalize_instance_id(item.get("id", DEFAULT_INSTANCE_ID))
        archive_dir = Path(str(item.get("archive_dir", archived_instances_dir() / str(archive_id))))
        result.append({
            "archive_id": str(item.get("archive_id", archive_id)),
            "id": normalized,
            "display_name": str(item.get("display_name", "")).strip() or normalized,
            "archive_dir": str(archive_dir),
            "state_file": str(archive_dir / "state.json"),
            "runtime_dir": str(archive_dir / ".yarbis_runtime"),
            "service_name": service_name(normalized),
            "archived_at": str(item.get("archived_at", "")).strip(),
            "exists": archive_dir.exists(),
        })
    result.sort(key=lambda item: item["archived_at"], reverse=True)
    return result


def restore_archived_instance(archive_id: object) -> dict:
    cleaned_archive_id = str(archive_id or "").strip()
    if not cleaned_archive_id:
        raise YarbisInstanceError("Indica el id del archivo de instancia a restaurar.")

    payload = _load_registry()
    archived_instances = payload["archived_instances"]
    archived = archived_instances.get(cleaned_archive_id)
    if not isinstance(archived, dict):
        raise YarbisInstanceError(f"No encontre el archivo de instancia {cleaned_archive_id}.")

    normalized = normalize_instance_id(archived.get("id", ""))
    if normalized == DEFAULT_INSTANCE_ID:
        raise YarbisInstanceError("La instancia default no se restaura desde archivos.")
    if normalized in payload["instances"] or instance_root(normalized).exists():
        raise YarbisInstanceError(f"No puedo restaurar {normalized}: ya existe una instancia con ese id.")

    archive_dir = Path(str(archived.get("archive_dir", "")))
    if not archive_dir.exists():
        raise YarbisInstanceError(f"No existe la carpeta archivada para {normalized}.")

    destination = instance_root(normalized)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archive_dir), str(destination))

    restored = {
        "id": normalized,
        "display_name": str(archived.get("display_name", "")).strip() or normalized,
        "state_file": str(state_file(normalized)),
        "runtime_dir": str(runtime_dir(normalized)),
        "service_name": service_name(normalized),
        "created_at": archived.get("created_at") or _utc_now(),
        "updated_at": _utc_now(),
    }
    payload["instances"][normalized] = restored
    archived_instances.pop(cleaned_archive_id, None)
    _write_registry(payload)
    return dict(restored)


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
