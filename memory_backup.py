import copy
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yarbis_instance
from atomic_io import atomic_replace

WORKSPACE_ROOT = Path(__file__).resolve().parent
BACKUPS_DIR = yarbis_instance.memory_backups_dir()
BACKUP_FORMAT = "yarbis.memory_backup"
SCHEMA_VERSION = 1
REDACTED_VALUE = "[redacted]"
SECRET_PATHS = (
    "notifications.ntfy.token",
    "notifications.telegram.bot_token",
    "notifications.telegram.pending_power_confirmation.token",
    "ollama.api_key",
    "model_provider.ollama.api_key",
    "model_provider.openrouter.api_key",
)
AUTO_BACKUP_REASON = "auto_state_change"


class MemoryBackupError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_text() -> str:
    return utc_now().isoformat()


def new_backup_id() -> str:
    timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    return f"memory-backup-{timestamp}-{uuid4().hex[:6]}"


def copy_json(value):
    return copy.deepcopy(value)


def path_parts(path: str) -> list[str]:
    return [part for part in str(path).split(".") if part]


def get_nested(mapping: dict, path: str):
    current = mapping
    for part in path_parts(path):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return current


def set_nested(mapping: dict, path: str, value):
    parts = path_parts(path)
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


def redact_secrets(state: dict) -> tuple[dict, list[str]]:
    redacted_state = copy_json(state)
    redacted_paths = []

    for path in SECRET_PATHS:
        value = get_nested(redacted_state, path)
        if str(value).strip():
            set_nested(redacted_state, path, REDACTED_VALUE)
            redacted_paths.append(path)

    return redacted_state, redacted_paths


def state_counts(state: dict) -> dict:
    return {
        "messages": len(state.get("messages", [])),
        "notes": len(state.get("notes", [])),
        "tasks": len(state.get("tasks", [])),
        "plan_items": len(state.get("current_plan", [])),
        "idea_projects": len(state.get("idea_projects", [])),
    }


def write_json_atomic(
    target_path: Path | str,
    payload,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    verify: bool = True,
) -> Path:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp-{uuid4().hex}")

    try:
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=ensure_ascii, indent=indent)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        if verify:
            with open(tmp_path, "r", encoding="utf-8") as file:
                json.load(file)

        atomic_replace(tmp_path, target)

        if verify:
            with open(target, "r", encoding="utf-8") as file:
                stored = json.load(file)
            if stored != payload:
                raise MemoryBackupError(f"La verificacion posterior fallo para {target}.")
    except MemoryBackupError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise MemoryBackupError(f"No pude escribir JSON atomico en {target}: {exc}") from exc

    return target


def copy_file_atomic(source_path: Path | str, target_path: Path | str) -> Path:
    source = Path(source_path)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp-{uuid4().hex}")

    try:
        with open(source, "rb") as source_file:
            with open(tmp_path, "wb") as target_file:
                shutil.copyfileobj(source_file, target_file)
                target_file.flush()
                os.fsync(target_file.fileno())
        atomic_replace(tmp_path, target)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise MemoryBackupError(f"No pude copiar el respaldo a {target}: {exc}") from exc

    return target


def resolve_export_path(
    path: str = "",
    backup_id: str = "",
    *,
    backups_dir: Path | str = BACKUPS_DIR,
    workspace_root: Path | str = WORKSPACE_ROOT,
) -> Path:
    cleaned_path = str(path).strip()
    resolved_backups_dir = Path(backups_dir)
    resolved_workspace_root = Path(workspace_root)
    resolved_backup_id = str(backup_id).strip() or new_backup_id()

    if not cleaned_path:
        return resolved_backups_dir / f"{resolved_backup_id}.json"

    candidate = Path(cleaned_path).expanduser()
    if not candidate.is_absolute():
        if len(candidate.parts) == 1:
            candidate = resolved_backups_dir / candidate
        else:
            candidate = resolved_workspace_root / candidate

    if candidate.exists() and candidate.is_dir():
        candidate = candidate / f"{resolved_backup_id}.json"
    elif not candidate.suffix:
        candidate = candidate.with_suffix(".json")

    return candidate


def backup_files(backups_dir: Path | str = BACKUPS_DIR) -> list[Path]:
    resolved_dir = Path(backups_dir)
    if not resolved_dir.exists():
        return []
    return sorted(
        [
            item
            for item in resolved_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".json"
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _unique_paths(paths) -> list[Path]:
    unique = []
    seen = set()
    for path in paths:
        candidate = Path(path)
        marker = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if marker in seen:
            continue
        unique.append(candidate)
        seen.add(marker)
    return unique


def resolve_import_path(
    path_or_id: str,
    *,
    backups_dir: Path | str = BACKUPS_DIR,
    workspace_root: Path | str = WORKSPACE_ROOT,
    extra_dirs=(),
) -> Path:
    cleaned = str(path_or_id).strip()
    if not cleaned:
        raise MemoryBackupError("Debes indicar un respaldo de memoria.")

    resolved_backups_dir = Path(backups_dir)
    resolved_workspace_root = Path(workspace_root)
    raw_candidate = Path(cleaned).expanduser()
    candidates = []
    if raw_candidate.is_absolute():
        candidates.append(raw_candidate)
    else:
        candidates.append(resolved_workspace_root / raw_candidate)
        candidates.append(resolved_backups_dir / raw_candidate)
        if not raw_candidate.suffix:
            candidates.append(resolved_backups_dir / raw_candidate.with_suffix(".json"))
        for directory in extra_dirs:
            extra_dir = Path(directory)
            candidates.append(extra_dir / raw_candidate)
            if not raw_candidate.suffix:
                candidates.append(extra_dir / raw_candidate.with_suffix(".json"))

    for candidate in _unique_paths(candidates):
        if candidate.exists() and candidate.is_file():
            return candidate

    lowered = cleaned.casefold()
    search_dirs = _unique_paths([resolved_backups_dir, *extra_dirs])
    matches = []
    for directory in search_dirs:
        matches.extend(
            backup_path
            for backup_path in backup_files(directory)
            if backup_path.name.casefold().startswith(lowered)
            or backup_path.stem.casefold().startswith(lowered)
        )

    matches = _unique_paths(matches)
    if not matches:
        raise MemoryBackupError(f"No encontre un respaldo de memoria con ruta o id: {path_or_id}")
    if len(matches) > 1:
        rendered = ", ".join(item.stem for item in matches[:5])
        raise MemoryBackupError(f"El identificador coincide con varios respaldos: {rendered}")
    return matches[0]


def load_backup_package(path: Path | str, *, normalizer=None) -> dict:
    backup_path = Path(path)
    try:
        package = json.loads(backup_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MemoryBackupError(f"No existe el respaldo de memoria: {backup_path}") from exc
    except json.JSONDecodeError as exc:
        raise MemoryBackupError(f"El respaldo no es JSON valido: {exc}") from exc
    except OSError as exc:
        raise MemoryBackupError(f"No pude leer el respaldo de memoria: {exc}") from exc

    if not isinstance(package, dict):
        raise MemoryBackupError("El respaldo de memoria no tiene formato valido.")
    if package.get("format") != BACKUP_FORMAT:
        raise MemoryBackupError("El archivo no es un respaldo de memoria de Yarbis.")
    if package.get("schema_version") != SCHEMA_VERSION:
        raise MemoryBackupError(
            "Version de respaldo no soportada: "
            f"{package.get('schema_version', 'desconocida')}"
        )
    if not isinstance(package.get("state"), dict):
        raise MemoryBackupError("El respaldo no contiene un estado valido.")

    normalized_package = dict(package)
    if callable(normalizer):
        normalized_package["state"] = normalizer(package["state"])
    else:
        normalized_package["state"] = copy_json(package["state"])
    normalized_package["redacted_paths"] = [
        str(path)
        for path in package.get("redacted_paths", [])
        if str(path).strip()
    ]
    return normalized_package


def build_backup_package(
    state: dict,
    *,
    normalizer=None,
    include_secrets: bool = False,
    reason: str = "manual",
    backup_id: str = "",
    created_at: str = "",
) -> dict:
    normalized_state = normalizer(state) if callable(normalizer) else copy_json(state)
    redacted_paths = []
    package_state = copy_json(normalized_state)
    if not include_secrets:
        package_state, redacted_paths = redact_secrets(package_state)

    return {
        "format": BACKUP_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "id": str(backup_id).strip() or new_backup_id(),
        "created_at": str(created_at).strip() or utc_now_text(),
        "options": {
            "include_secrets": bool(include_secrets),
            "reason": str(reason).strip() or "manual",
        },
        "redacted_paths": redacted_paths,
        "state": package_state,
    }


def package_result(package: dict, path: Path | str) -> dict:
    package_state = package.get("state", {}) if isinstance(package, dict) else {}
    return {
        "id": str(package.get("id") or Path(path).stem),
        "path": str(path),
        "created_at": str(package.get("created_at", "")),
        "include_secrets": bool(package.get("options", {}).get("include_secrets", False)),
        "redacted_paths": list(package.get("redacted_paths", [])),
        "reason": str(package.get("options", {}).get("reason", "")),
        "counts": state_counts(package_state if isinstance(package_state, dict) else {}),
    }


def write_backup_package(
    state: dict,
    *,
    path: str = "",
    backups_dir: Path | str = BACKUPS_DIR,
    workspace_root: Path | str = WORKSPACE_ROOT,
    normalizer=None,
    include_secrets: bool = False,
    reason: str = "manual",
    created_at: str = "",
) -> dict:
    package = build_backup_package(
        state,
        normalizer=normalizer,
        include_secrets=include_secrets,
        reason=reason,
        created_at=created_at,
    )
    target_path = resolve_export_path(
        path,
        str(package["id"]),
        backups_dir=backups_dir,
        workspace_root=workspace_root,
    )
    write_json_atomic(target_path, package, ensure_ascii=False, indent=2, verify=True)
    return package_result(package, target_path)


def list_backups(
    *,
    backups_dir: Path | str = BACKUPS_DIR,
    normalizer=None,
    limit: int = 10,
) -> list[dict]:
    try:
        normalized_limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10

    entries = []
    for backup_path in backup_files(backups_dir):
        try:
            package = load_backup_package(backup_path, normalizer=normalizer)
        except MemoryBackupError:
            continue
        entries.append(package_result(package, backup_path))

    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries[:normalized_limit]


def mirror_backup(backup_path: Path | str, mirror_dir: str | Path = "") -> str:
    cleaned_mirror = str(mirror_dir).strip()
    if not cleaned_mirror:
        return ""

    source = Path(backup_path)
    target = Path(cleaned_mirror).expanduser() / source.name
    copy_file_atomic(source, target)
    return str(target)


def parse_created_at(value: str) -> datetime:
    rendered = str(value).strip()
    if not rendered:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _backup_sort_key(entry: dict):
    created = parse_created_at(entry.get("created_at", ""))
    path = Path(entry.get("path", ""))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    return created, mtime


def verify_backups(*, backup_dirs, normalizer=None) -> dict:
    valid = []
    invalid = []
    seen = set()
    for directory in backup_dirs:
        for backup_path in backup_files(directory):
            marker = str(backup_path.resolve())
            if marker in seen:
                continue
            seen.add(marker)
            try:
                package = load_backup_package(backup_path, normalizer=normalizer)
            except MemoryBackupError as exc:
                invalid.append({"path": str(backup_path), "error": str(exc)})
                continue
            valid.append(package_result(package, backup_path))

    valid.sort(key=_backup_sort_key, reverse=True)
    return {
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "latest": valid[0] if valid else None,
        "valid": valid,
        "invalid": invalid,
    }


def latest_valid_backup(*, backup_dirs, normalizer=None) -> dict | None:
    verified = verify_backups(backup_dirs=backup_dirs, normalizer=normalizer)
    latest = verified["latest"]
    if not latest:
        return None

    path = Path(latest["path"])
    package = load_backup_package(path, normalizer=normalizer)
    return {
        "path": path,
        "package": package,
        "info": latest,
    }


def prune_auto_backups(
    *,
    backups_dir: Path | str = BACKUPS_DIR,
    max_auto_backups: int = 250,
    keep_daily_days: int = 90,
) -> dict:
    try:
        max_backups = max(1, min(5000, int(max_auto_backups)))
    except (TypeError, ValueError):
        max_backups = 250

    try:
        daily_days = max(0, min(3650, int(keep_daily_days)))
    except (TypeError, ValueError):
        daily_days = 90

    entries = []
    for backup_path in backup_files(backups_dir):
        try:
            package = load_backup_package(backup_path)
        except MemoryBackupError:
            continue
        reason = str(package.get("options", {}).get("reason", ""))
        if reason != AUTO_BACKUP_REASON:
            continue
        info = package_result(package, backup_path)
        entries.append(info)

    entries.sort(key=_backup_sort_key, reverse=True)
    keep_paths = {entry["path"] for entry in entries[:max_backups]}

    if daily_days:
        cutoff = utc_now() - timedelta(days=daily_days)
        kept_days = set()
        for entry in entries:
            created = parse_created_at(entry.get("created_at", ""))
            if created < cutoff:
                continue
            day = created.date().isoformat()
            if day in kept_days:
                continue
            keep_paths.add(entry["path"])
            kept_days.add(day)

    deleted = []
    for entry in entries:
        path_text = entry["path"]
        if path_text in keep_paths:
            continue
        try:
            Path(path_text).unlink()
            deleted.append(path_text)
        except OSError:
            pass

    return {
        "candidates": len(entries),
        "kept": len(keep_paths),
        "deleted": len(deleted),
    }


def prune_stale_temp_files(
    directory: Path | str,
    *,
    max_age_seconds: float = 60 * 60,
    name_prefix: str = "",
) -> dict:
    resolved_dir = Path(directory)
    try:
        cutoff = utc_now().timestamp() - max(0.0, float(max_age_seconds))
    except (TypeError, ValueError):
        cutoff = utc_now().timestamp() - (60 * 60)

    deleted = 0
    deleted_bytes = 0
    if not resolved_dir.exists():
        return {"deleted": deleted, "bytes": deleted_bytes}

    prefix = str(name_prefix)
    for candidate in resolved_dir.iterdir():
        if not candidate.is_file():
            continue
        if ".tmp-" not in candidate.name:
            continue
        if prefix and not candidate.name.startswith(prefix):
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            continue
        try:
            candidate.unlink()
        except OSError:
            continue
        deleted += 1
        deleted_bytes += stat.st_size

    return {"deleted": deleted, "bytes": deleted_bytes}
