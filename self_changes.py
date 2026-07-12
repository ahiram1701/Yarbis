"""Bitácora de auto-cambios de código de Yarbis.

Cuando Yarbis modifica su PROPIO código fuente (write_text_file,
coding_apply_proposal, restore_checkpoint), queda un registro JSONL
compartido en el workspace. Sin esto, las auto-mejoras solo viven en la
copia de ejecución y un espejo desde el repo git las borraría sin que
nadie supiera que existieron (pasó el 2026-07-12 con agent.py/tools.py).

El log NO se versiona (gitignored): es la cola de "cambios pendientes de
subir a git". El flujo esperado: revisar la bitácora → adoptar los cambios
en el repo → marcar como versionados (o vaciar).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yarbis_instance

WORKSPACE_ROOT = Path(__file__).resolve().parent
SELF_CHANGES_FILE = WORKSPACE_ROOT / ".yarbis_self_changes.jsonl"

MAX_DIFF_PREVIEW_CHARS = 4000
MAX_LOG_ENTRIES = 500

# Carpetas/archivos del workspace que NO son código fuente propio
# (datos de runtime, estado, entornos). Coincide con .gitignore.
_IGNORED_TOP_LEVEL = {
    ".venv",
    ".git",
    "__pycache__",
    ".yarbis_checkpoints",
    ".yarbis_instances",
    ".yarbis_memory_backups",
    ".yarbis_runtime",
    "tests_runtime",
    "_tmp_tools_test",
}
_IGNORED_SUFFIXES = {".log", ".pyc", ".pyo", ".pyd"}


def is_own_source_path(path: Path) -> bool:
    """True si la ruta es código fuente del propio Yarbis (dentro del workspace, no runtime)."""
    try:
        relative = Path(path).resolve().relative_to(WORKSPACE_ROOT)
    except (ValueError, OSError):
        return False

    parts = relative.parts
    if not parts:
        return False
    top = parts[0]
    if top in _IGNORED_TOP_LEVEL or top.startswith("tmp"):
        return False
    if relative.suffix.lower() in _IGNORED_SUFFIXES:
        return False
    name = relative.name
    if name == "state.json" or name.startswith("state.json.tmp"):
        return False
    if top == "service_host" and len(parts) > 1 and parts[1] in {"bin", "obj"}:
        return False
    return True


def record_change(
    file_path: Path,
    action: str,
    reason: str = "",
    checkpoint_id: str = "",
    proposal_id: str = "",
    diff_preview: str = "",
) -> None:
    """Registra un auto-cambio de código. Nunca lanza: el registro es best-effort.

    Solo registra si file_path es código fuente propio (ver is_own_source_path);
    para rutas fuera del workspace o de runtime es un no-op silencioso.
    """
    try:
        if not is_own_source_path(file_path):
            return
        relative = Path(file_path).resolve().relative_to(WORKSPACE_ROOT).as_posix()
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instance": yarbis_instance.current_instance_id(),
            "action": str(action).strip() or "write",
            "file": relative,
            "reason": str(reason).strip(),
            "checkpoint_id": str(checkpoint_id).strip(),
            "proposal_id": str(proposal_id).strip(),
            "diff_preview": str(diff_preview)[:MAX_DIFF_PREVIEW_CHARS],
        }
        line = json.dumps(entry, ensure_ascii=True)
        with open(SELF_CHANGES_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        # La bitácora jamás debe romper la operación que registra.
        pass


def load_changes(limit: int = 0) -> list[dict]:
    """Carga las entradas de la bitácora (más recientes al final).

    Tolera líneas corruptas (las ignora) y BOM UTF-8 al inicio del archivo.
    Con limit > 0 devuelve solo las últimas `limit` entradas.
    """
    try:
        raw = SELF_CHANGES_FILE.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except OSError:
        return []

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)

    if limit and limit > 0:
        return entries[-limit:]
    return entries


def mark_all_versioned(note: str = "") -> int:
    """Archiva la bitácora actual (cambios ya subidos a git) y la vacía.

    Mueve las entradas a un archivo de historial con marca de tiempo para no
    perder trazabilidad. Devuelve cuántas entradas se archivaron.
    """
    entries = load_changes()
    if not entries:
        try:
            SELF_CHANGES_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = WORKSPACE_ROOT / f".yarbis_self_changes.versioned-{stamp}.jsonl"
    try:
        payload_lines = []
        for entry in entries:
            entry = dict(entry)
            entry["versioned_at"] = datetime.now(timezone.utc).isoformat()
            if note:
                entry["versioned_note"] = str(note).strip()
            payload_lines.append(json.dumps(entry, ensure_ascii=True))
        archive_path.write_text("\n".join(payload_lines) + "\n", encoding="utf-8")
        SELF_CHANGES_FILE.unlink(missing_ok=True)
    except OSError:
        return 0
    return len(entries)


def prune_log() -> None:
    """Si la bitácora supera MAX_LOG_ENTRIES, conserva solo las más recientes."""
    entries = load_changes()
    if len(entries) <= MAX_LOG_ENTRIES:
        return
    kept = entries[-MAX_LOG_ENTRIES:]
    try:
        lines = [json.dumps(entry, ensure_ascii=True) for entry in kept]
        SELF_CHANGES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
