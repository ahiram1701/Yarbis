import json
from datetime import datetime
from pathlib import Path

import memory
import memory_backup

WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKUPS_DIR = memory_backup.BACKUPS_DIR
BACKUP_FORMAT = memory_backup.BACKUP_FORMAT
SCHEMA_VERSION = memory_backup.SCHEMA_VERSION
REDACTED_VALUE = memory_backup.REDACTED_VALUE
SECRET_PATHS = memory_backup.SECRET_PATHS
VALID_IMPORT_MODES = {"replace", "merge"}


class MemoryTransferError(ValueError):
    pass


def _utc_now() -> datetime:
    return memory_backup.utc_now()


def _new_backup_id() -> str:
    return memory_backup.new_backup_id()


def _copy_json(value):
    return memory_backup.copy_json(value)


def _path_parts(path: str) -> list[str]:
    return memory_backup.path_parts(path)


def _get_nested(mapping: dict, path: str):
    return memory_backup.get_nested(mapping, path)


def _set_nested(mapping: dict, path: str, value):
    memory_backup.set_nested(mapping, path, value)


def _redact_secrets(state: dict) -> tuple[dict, list[str]]:
    return memory_backup.redact_secrets(state)


def _resolve_export_path(path: str, backup_id: str) -> Path:
    return memory_backup.resolve_export_path(
        path,
        backup_id,
        backups_dir=BACKUPS_DIR,
        workspace_root=WORKSPACE_ROOT,
    )


def _backup_files() -> list[Path]:
    return memory_backup.backup_files(BACKUPS_DIR)


def _resolve_import_path(path_or_id: str) -> Path:
    try:
        return memory_backup.resolve_import_path(
            path_or_id,
            backups_dir=BACKUPS_DIR,
            workspace_root=WORKSPACE_ROOT,
        )
    except memory_backup.MemoryBackupError as exc:
        raise MemoryTransferError(str(exc)) from exc


def _load_backup_package(path: Path) -> dict:
    try:
        return memory_backup.load_backup_package(path, normalizer=memory.normalize_state)
    except memory_backup.MemoryBackupError as exc:
        raise MemoryTransferError(str(exc)) from exc


def _state_counts(state: dict) -> dict:
    return memory_backup.state_counts(state)


def _write_backup_package(
    state: dict,
    path: str = "",
    include_secrets: bool = False,
    reason: str = "manual",
) -> dict:
    try:
        return memory_backup.write_backup_package(
            state,
            path=path,
            backups_dir=BACKUPS_DIR,
            workspace_root=WORKSPACE_ROOT,
            normalizer=memory.normalize_state,
            include_secrets=bool(include_secrets),
            reason=reason,
        )
    except memory_backup.MemoryBackupError as exc:
        raise MemoryTransferError(str(exc)) from exc


def create_backup(path: str = "", include_secrets: bool = False, reason: str = "manual") -> dict:
    return _write_backup_package(
        memory.load_state(),
        path=path,
        include_secrets=bool(include_secrets),
        reason=reason,
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
