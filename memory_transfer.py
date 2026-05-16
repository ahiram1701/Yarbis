import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import memory

WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKUPS_DIR = WORKSPACE_ROOT / ".yarbis_memory_backups"
BACKUP_FORMAT = "yarbis.memory_backup"
SCHEMA_VERSION = 1
REDACTED_VALUE = "[redacted]"
SECRET_PATHS = (
    "notifications.ntfy.token",
    "notifications.telegram.bot_token",
    "notifications.telegram.pending_power_confirmation.token",
)
VALID_IMPORT_MODES = {"replace", "merge"}


class MemoryTransferError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_backup_id() -> str:
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    return f"memory-backup-{timestamp}-{uuid4().hex[:6]}"


def _copy_json(value):
    return copy.deepcopy(value)


def _path_parts(path: str) -> list[str]:
    return [part for part in str(path).split(".") if part]


def _get_nested(mapping: dict, path: str):
    current = mapping
    for part in _path_parts(path):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return current


def _set_nested(mapping: dict, path: str, value):
    parts = _path_parts(path)
    if not parts:
        return

    current = mapping
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _redact_secrets(state: dict) -> tuple[dict, list[str]]:
    redacted_state = _copy_json(state)
    redacted_paths = []

    for path in SECRET_PATHS:
        value = _get_nested(redacted_state, path)
        if str(value).strip():
            _set_nested(redacted_state, path, REDACTED_VALUE)
            redacted_paths.append(path)

    return redacted_state, redacted_paths


def _resolve_export_path(path: str, backup_id: str) -> Path:
    cleaned_path = str(path).strip()
    if not cleaned_path:
        return BACKUPS_DIR / f"{backup_id}.json"

    candidate = Path(cleaned_path).expanduser()
    if not candidate.is_absolute():
        if len(candidate.parts) == 1:
            candidate = BACKUPS_DIR / candidate
        else:
            candidate = WORKSPACE_ROOT / candidate

    if candidate.exists() and candidate.is_dir():
        candidate = candidate / f"{backup_id}.json"
    elif not candidate.suffix:
        candidate = candidate.with_suffix(".json")

    return candidate


def _backup_files() -> list[Path]:
    if not BACKUPS_DIR.exists():
        return []
    return sorted(
        [item for item in BACKUPS_DIR.iterdir() if item.is_file() and item.suffix.lower() == ".json"],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _resolve_import_path(path_or_id: str) -> Path:
    cleaned = str(path_or_id).strip()
    if not cleaned:
        raise MemoryTransferError("Debes indicar un respaldo de memoria.")

    raw_candidate = Path(cleaned).expanduser()
    candidates = []
    if raw_candidate.is_absolute():
        candidates.append(raw_candidate)
    else:
        candidates.append(WORKSPACE_ROOT / raw_candidate)
        candidates.append(BACKUPS_DIR / raw_candidate)
        if not raw_candidate.suffix:
            candidates.append(BACKUPS_DIR / raw_candidate.with_suffix(".json"))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    lowered = cleaned.casefold()
    matches = [
        backup_path
        for backup_path in _backup_files()
        if backup_path.name.casefold().startswith(lowered)
        or backup_path.stem.casefold().startswith(lowered)
    ]

    if not matches:
        raise MemoryTransferError(f"No encontre un respaldo de memoria con ruta o id: {path_or_id}")
    if len(matches) > 1:
        rendered = ", ".join(item.stem for item in matches[:5])
        raise MemoryTransferError(f"El identificador coincide con varios respaldos: {rendered}")
    return matches[0]


def _load_backup_package(path: Path) -> dict:
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MemoryTransferError(f"No existe el respaldo de memoria: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MemoryTransferError(f"El respaldo no es JSON valido: {exc}") from exc
    except OSError as exc:
        raise MemoryTransferError(f"No pude leer el respaldo de memoria: {exc}") from exc

    if not isinstance(package, dict):
        raise MemoryTransferError("El respaldo de memoria no tiene formato valido.")
    if package.get("format") != BACKUP_FORMAT:
        raise MemoryTransferError("El archivo no es un respaldo de memoria de Yarbis.")
    if package.get("schema_version") != SCHEMA_VERSION:
        raise MemoryTransferError(
            "Version de respaldo no soportada: "
            f"{package.get('schema_version', 'desconocida')}"
        )
    if not isinstance(package.get("state"), dict):
        raise MemoryTransferError("El respaldo no contiene un estado valido.")

    normalized_package = dict(package)
    normalized_package["state"] = memory.normalize_state(package["state"])
    normalized_package["redacted_paths"] = [
        str(path)
        for path in package.get("redacted_paths", [])
        if str(path).strip()
    ]
    return normalized_package


def _state_counts(state: dict) -> dict:
    return {
        "messages": len(state.get("messages", [])),
        "notes": len(state.get("notes", [])),
        "tasks": len(state.get("tasks", [])),
        "plan_items": len(state.get("current_plan", [])),
    }


def _write_backup_package(
    state: dict,
    path: str = "",
    include_secrets: bool = False,
    reason: str = "manual",
) -> dict:
    backup_id = _new_backup_id()
    normalized_state = memory.normalize_state(state)
    redacted_paths = []
    package_state = _copy_json(normalized_state)
    if not include_secrets:
        package_state, redacted_paths = _redact_secrets(package_state)

    created_at = _utc_now().isoformat()
    package = {
        "format": BACKUP_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "id": backup_id,
        "created_at": created_at,
        "options": {
            "include_secrets": bool(include_secrets),
            "reason": str(reason).strip() or "manual",
        },
        "redacted_paths": redacted_paths,
        "state": package_state,
    }

    target_path = _resolve_export_path(path, backup_id)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            json.dumps(package, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise MemoryTransferError(f"No pude guardar el respaldo de memoria: {exc}") from exc

    return {
        "id": backup_id,
        "path": str(target_path),
        "created_at": created_at,
        "include_secrets": bool(include_secrets),
        "redacted_paths": redacted_paths,
        "counts": _state_counts(package_state),
    }


def create_backup(path: str = "", include_secrets: bool = False) -> dict:
    return _write_backup_package(
        memory.load_state(),
        path=path,
        include_secrets=bool(include_secrets),
        reason="manual",
    )


def list_backups(limit: int = 10) -> list[dict]:
    try:
        normalized_limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10

    entries = []
    for backup_path in _backup_files():
        try:
            package = _load_backup_package(backup_path)
        except MemoryTransferError:
            continue

        state = package["state"]
        entries.append({
            "id": str(package.get("id") or backup_path.stem),
            "path": str(backup_path),
            "created_at": str(package.get("created_at", "")),
            "include_secrets": bool(package.get("options", {}).get("include_secrets", False)),
            "redacted_paths": list(package.get("redacted_paths", [])),
            "counts": _state_counts(state),
        })

    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries[:normalized_limit]


def inspect_backup(path_or_id: str) -> dict:
    backup_path = _resolve_import_path(path_or_id)
    package = _load_backup_package(backup_path)
    state = package["state"]
    profile = state.get("profile", {})
    return {
        "id": str(package.get("id") or backup_path.stem),
        "path": str(backup_path),
        "created_at": str(package.get("created_at", "")),
        "include_secrets": bool(package.get("options", {}).get("include_secrets", False)),
        "redacted_paths": list(package.get("redacted_paths", [])),
        "goal": str(state.get("goal", "")),
        "profile_name": str(profile.get("name", "")),
        "counts": _state_counts(state),
    }


def _preserve_redacted_secrets(imported_state: dict, current_state: dict, redacted_paths: list[str]):
    paths_to_preserve = set(redacted_paths)
    for path in SECRET_PATHS:
        if _get_nested(imported_state, path) == REDACTED_VALUE:
            paths_to_preserve.add(path)

    for path in paths_to_preserve:
        _set_nested(imported_state, path, _get_nested(current_state, path))


def _unique_text_items(*groups) -> list:
    merged = []
    seen = set()
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            marker = str(item).casefold()
            if marker in seen:
                continue
            merged.append(item)
            seen.add(marker)
    return merged


def _merge_items_by_id(current_items: list, incoming_items: list) -> list:
    merged = []
    seen_ids = set()
    for item in (current_items or []) + (incoming_items or []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "")).strip()
        marker = item_id.casefold() if item_id else json.dumps(item, sort_keys=True, ensure_ascii=False)
        if marker in seen_ids:
            continue
        merged.append(_copy_json(item))
        seen_ids.add(marker)
    return merged


def _message_marker(message: dict) -> str:
    return json.dumps(
        {
            "role": message.get("role", ""),
            "content": message.get("content", ""),
            "tool_name": message.get("tool_name", ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _merge_messages(current_messages: list, incoming_messages: list) -> list:
    merged = []
    seen = set()
    for message in (current_messages or []) + (incoming_messages or []):
        if not isinstance(message, dict):
            continue
        marker = _message_marker(message)
        if marker in seen:
            continue
        merged.append(_copy_json(message))
        seen.add(marker)
    return merged


def _merge_profile(current_profile: dict, incoming_profile: dict) -> dict:
    current_profile = current_profile if isinstance(current_profile, dict) else {}
    incoming_profile = incoming_profile if isinstance(incoming_profile, dict) else {}

    return {
        "name": str(incoming_profile.get("name", "")).strip()
        or str(current_profile.get("name", "")).strip(),
        "role": str(incoming_profile.get("role", "")).strip()
        or str(current_profile.get("role", "")).strip(),
        "preferences": _unique_text_items(
            current_profile.get("preferences", []),
            incoming_profile.get("preferences", []),
        ),
        "constraints": _unique_text_items(
            current_profile.get("constraints", []),
            incoming_profile.get("constraints", []),
        ),
    }


def _merge_states(current_state: dict, incoming_state: dict) -> dict:
    merged = _copy_json(current_state)
    merged["profile"] = _merge_profile(
        current_state.get("profile", {}),
        incoming_state.get("profile", {}),
    )
    merged["messages"] = _merge_messages(
        current_state.get("messages", []),
        incoming_state.get("messages", []),
    )
    merged["notes"] = _merge_items_by_id(
        current_state.get("notes", []),
        incoming_state.get("notes", []),
    )
    merged["tasks"] = _merge_items_by_id(
        current_state.get("tasks", []),
        incoming_state.get("tasks", []),
    )
    merged["current_plan"] = _unique_text_items(
        current_state.get("current_plan", []),
        incoming_state.get("current_plan", []),
    )

    incoming_self_knowledge = incoming_state.get("self_knowledge", {})
    if (
        isinstance(incoming_self_knowledge, dict)
        and (
            str(incoming_self_knowledge.get("summary", "")).strip()
            or str(incoming_self_knowledge.get("last_analyzed_at", "")).strip()
        )
    ):
        merged["self_knowledge"] = _copy_json(incoming_self_knowledge)

    return memory.normalize_state(merged)


def _state_for_import(
    current_state: dict,
    incoming_state: dict,
    redacted_paths: list[str],
    mode: str,
) -> dict:
    if mode == "replace":
        next_state = _copy_json(incoming_state)
        _preserve_redacted_secrets(next_state, current_state, redacted_paths)
    elif mode == "merge":
        next_state = _merge_states(current_state, incoming_state)
    else:
        raise MemoryTransferError("Modo de importacion invalido. Usa replace o merge.")

    next_state["runtime"] = memory.default_state()["runtime"]
    return memory.normalize_state(next_state)


def import_backup(path_or_id: str, mode: str = "replace") -> dict:
    cleaned_mode = str(mode).strip().lower() or "replace"
    if cleaned_mode not in VALID_IMPORT_MODES:
        raise MemoryTransferError("Modo de importacion invalido. Usa replace o merge.")

    backup_path = _resolve_import_path(path_or_id)
    package = _load_backup_package(backup_path)
    incoming_state = package["state"]
    redacted_paths = list(package.get("redacted_paths", []))

    def mutate(current_state):
        current_normalized = memory.normalize_state(current_state)
        safety_backup = _write_backup_package(
            current_normalized,
            include_secrets=True,
            reason=f"pre_import_{cleaned_mode}",
        )
        next_state = _state_for_import(
            current_normalized,
            incoming_state,
            redacted_paths,
            cleaned_mode,
        )
        current_state.clear()
        current_state.update(next_state)
        return safety_backup

    safety_backup = memory.state_transaction("import_memory_backup", mutate)
    return {
        "source_id": str(package.get("id") or backup_path.stem),
        "source_path": str(backup_path),
        "mode": cleaned_mode,
        "safety_backup": safety_backup,
        "redacted_paths": redacted_paths,
        "counts": _state_counts(incoming_state),
    }
