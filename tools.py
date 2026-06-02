import difflib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from browser_automation import run_browser_automation
from credential_store import CredentialStoreError, load_secret, save_secret
from integrations import (
    compose_email_draft,
    create_calendar_event_file,
    open_system_target as open_system_target_impl,
)
from internet import fetch_web_page as fetch_public_web_page
from internet import search_web as search_public_web
import memory_transfer
import yarbis_bus
import yarbis_instance
from memory import (
    VALID_INTERNET_MODES,
    VALID_SEARCH_PROVIDERS,
    VALID_SOCIAL_PLATFORMS,
    VALID_TASK_PRIORITY,
    VALID_TASK_STATUS,
    load_state,
    memory_protection_status as memory_protection_status_data,
    render_state_summary,
    state_transaction,
    verify_memory_backups as verify_memory_backups_data,
)
from self_knowledge import get_cached_source_signature, render_self_knowledge_summary
from social_oauth import SocialOAuthError, connect_social_account
from social_publishing import SocialPublishError, facebook_assisted_url, publish_publication

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = yarbis_instance.runtime_dir()
CHECKPOINTS_DIR = WORKSPACE_ROOT / ".yarbis_checkpoints"
CODING_PROPOSALS_DIR = RUNTIME_DIR / "coding_proposals"
MAX_LIST_ITEMS = 200
MAX_READ_BYTES = 16_000
MAX_WRITE_BYTES = 64_000
MAX_WRITE_PREVIEW_CHARS = 600
MAX_DIFF_LINES = 160
MAX_TEST_OUTPUT_CHARS = 6_000
MAX_COMMAND_OUTPUT_CHARS = 12_000
CODING_PROPOSAL_SCHEMA_VERSION = 2
MAX_CODING_PROPOSAL_TITLE_CHARS = 160
MAX_CODING_PROPOSAL_SUMMARY_CHARS = 2_000
MAX_CODING_PROPOSAL_REASON_CHARS = 1_000
MAX_CODING_PROPOSAL_LIST_ITEMS = 50
MAX_CODING_VALIDATION_COMMAND_CHARS = 1_000
CODING_FILE_OPERATION_WRITE = "write"
CODING_FILE_OPERATION_DELETE = "delete"
VALID_CODING_FILE_OPERATIONS = {CODING_FILE_OPERATION_WRITE, CODING_FILE_OPERATION_DELETE}
IGNORED_LISTING_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "tests_runtime",
    ".yarbis_checkpoints",
    ".yarbis_memory_backups",
    ".yarbis_instances",
    ".yarbis_runtime",
}
PROTECTED_WRITE_ROOT_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".yarbis_checkpoints",
    ".yarbis_memory_backups",
    ".yarbis_instances",
    ".yarbis_runtime",
}
PROTECTED_WRITE_PATHS = {"state.json"}
CLEAR_VALUE = "[clear]"
DEFAULT_CODING_MODE = "propose_first"
CODING_PROPOSAL_PENDING = "pending"
CODING_PROPOSAL_APPLIED = "applied"
CODING_PROPOSAL_DISCARDED = "discarded"
VALID_CODING_PROPOSAL_STATUS = {
    CODING_PROPOSAL_PENDING,
    CODING_PROPOSAL_APPLIED,
    CODING_PROPOSAL_DISCARDED,
}


def _resolve_workspace_path(path: str) -> tuple[Path | None, str | None]:
    candidate = Path(path)
    resolved = (WORKSPACE_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    return resolved, None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _coding_state(state: dict | None = None) -> dict:
    source_state = state if isinstance(state, dict) else load_state()
    coding = source_state.get("coding", {})
    return coding if isinstance(coding, dict) else {}


def _active_coding_workspace(state: dict | None = None) -> tuple[Path | None, str | None]:
    coding = _coding_state(state)
    workspace_text = str(coding.get("workspace_path", "")).strip()
    if not workspace_text:
        return None, "No hay workspace de codigo configurado. Usa `coding_set_workspace` primero."

    workspace_path = Path(workspace_text).resolve()
    if not workspace_path.exists():
        return None, f"El workspace de codigo no existe: {workspace_text}"
    if not workspace_path.is_dir():
        return None, f"El workspace de codigo no es una carpeta: {workspace_text}"

    return workspace_path, None


def _resolve_coding_path(path: str = ".") -> tuple[Path | None, Path | None, str | None]:
    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return None, None, workspace_error

    cleaned_path = str(path).strip() or "."
    candidate = Path(cleaned_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (workspace_path / candidate).resolve()

    try:
        relative_path = resolved.relative_to(workspace_path)
    except ValueError:
        return None, None, (
            "Ruta fuera del workspace de codigo activo. "
            f"Workspace: {workspace_path}; ruta solicitada: {cleaned_path}"
        )

    return resolved, relative_path, None


def _coding_write_block_reason(path: Path) -> str | None:
    coding = _coding_state()
    if str(coding.get("mode", DEFAULT_CODING_MODE)).strip().lower() != DEFAULT_CODING_MODE:
        return None

    workspace_text = str(coding.get("workspace_path", "")).strip()
    if not workspace_text:
        return None

    workspace_path = Path(workspace_text).resolve()
    if _is_relative_to(path.resolve(), workspace_path):
        return (
            "Escritura bloqueada dentro del workspace de codigo activo en modo propose_first. "
            "Usa `coding_propose_text_file` para generar una propuesta y aplicala solo tras aprobacion."
        )

    return None


def _validate_coding_write_path(relative_path: Path) -> str | None:
    protected_parts = {
        ".git",
        ".venv",
        "__pycache__",
        ".yarbis_checkpoints",
        ".yarbis_memory_backups",
        ".yarbis_instances",
        ".yarbis_runtime",
    }
    parts = relative_path.parts
    if any(part in protected_parts for part in parts):
        return f"Escritura de coding bloqueada en ruta protegida: {relative_path.as_posix()}"
    if relative_path.as_posix() == "state.json":
        return "Escritura de coding bloqueada en state.json."
    return None


def _workspace_relative(path: Path) -> str:
    try:
        return path.relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        return str(path)


def _validate_write_path(path: Path) -> str | None:
    try:
        relative_path = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return None

    if relative_path.as_posix() in PROTECTED_WRITE_PATHS:
        return (
            f"Escritura bloqueada en {relative_path.as_posix()}. "
            "Usa las tools del agente para modificar ese estado."
        )

    if relative_path.parts and relative_path.parts[0] in PROTECTED_WRITE_ROOT_NAMES:
        return (
            f"Escritura bloqueada en {relative_path.parts[0]}. "
            "Esa ruta esta protegida."
        )

    return None


def _preview_text(text: str, limit: int) -> str:
    rendered = str(text)
    if limit <= 0 or len(rendered) <= limit:
        return rendered

    omitted = len(rendered) - limit
    return rendered[:limit].rstrip() + f"\n...[truncado {omitted} caracteres]"


def _bounded_text(text: str, limit: int) -> str:
    rendered = str(text)
    if limit <= 0 or len(rendered) <= limit:
        return rendered

    marker = f"\n...[truncado {len(rendered) - limit} caracteres]...\n"
    if len(marker) >= limit:
        return rendered[:limit]

    available = limit - len(marker)
    head_chars = available // 2
    tail_chars = available - head_chars
    return rendered[:head_chars].rstrip() + marker + rendered[-tail_chars:].lstrip()


def _subprocess_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=_subprocess_creationflags(),
            )
            return
        except Exception:
            pass

    try:
        process.kill()
    except OSError:
        pass


def _run_command_process(args, cwd: Path, shell: bool, timeout_seconds: int) -> tuple[int | None, str, str, bool]:
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_subprocess_creationflags(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        return None, stdout, stderr, True


def _bounded_diff_lines(diff_lines: list[str]) -> list[str]:
    if len(diff_lines) <= MAX_DIFF_LINES:
        return diff_lines

    marker = f"... diff truncado, {len(diff_lines) - MAX_DIFF_LINES} lineas mas."
    available = max(1, MAX_DIFF_LINES - 1)
    head_count = available // 2
    tail_count = available - head_count
    return diff_lines[:head_count] + [marker] + diff_lines[-tail_count:]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "si", "s", "on"}


def _new_checkpoint_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"checkpoint-{timestamp}-{uuid4().hex[:6]}"


def _new_coding_proposal_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"proposal-{timestamp}-{uuid4().hex[:6]}"


def _coding_proposal_path(proposal_id: str) -> Path:
    return CODING_PROPOSALS_DIR / f"{proposal_id}.json"


def _load_coding_proposal(proposal_id: str) -> tuple[dict | None, Path | None, str | None]:
    cleaned_id = str(proposal_id).strip()
    if not cleaned_id:
        return None, None, "Debes indicar un id de propuesta."

    candidates = []
    if CODING_PROPOSALS_DIR.exists():
        for proposal_path in CODING_PROPOSALS_DIR.glob("*.json"):
            if proposal_path.stem.lower().startswith(cleaned_id.lower()):
                candidates.append(proposal_path)

    if not candidates:
        return None, None, f"No encontre una propuesta con id o prefijo: {proposal_id}"
    if len(candidates) > 1:
        return None, None, f"El prefijo coincide con varias propuestas: {proposal_id}"

    proposal_path = candidates[0]
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"No pude leer la propuesta {proposal_path.stem}: {exc}"
    if not isinstance(proposal, dict):
        return None, None, f"La propuesta {proposal_path.stem} no tiene formato valido."

    return proposal, proposal_path, None


def _proposal_status(proposal: dict) -> str:
    status = str(proposal.get("status", CODING_PROPOSAL_PENDING)).strip().lower()
    return status if status in VALID_CODING_PROPOSAL_STATUS else CODING_PROPOSAL_PENDING


def _proposal_validation(proposal: dict) -> dict:
    validation = proposal.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}
    return {
        "command": str(validation.get("command", "")).strip(),
        "proposal_id": str(validation.get("proposal_id", proposal.get("id", ""))).strip(),
        "exit_code": validation.get("exit_code"),
        "output": str(validation.get("output", "")),
        "ran_at": str(validation.get("ran_at", "")).strip(),
    }


def _normalize_coding_proposal(proposal: dict) -> dict:
    proposal_id = str(proposal.get("id", "")).strip()
    now = datetime.now(timezone.utc).isoformat()
    status = _proposal_status(proposal)

    try:
        schema_version = int(proposal.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        schema_version = 1

    if schema_version >= CODING_PROPOSAL_SCHEMA_VERSION and isinstance(
        proposal.get("files"),
        list,
    ):
        files = []
        for item in proposal.get("files", []):
            if not isinstance(item, dict):
                continue
            operation = str(item.get("operation", CODING_FILE_OPERATION_WRITE)).strip().lower()
            if operation not in VALID_CODING_FILE_OPERATIONS:
                operation = CODING_FILE_OPERATION_WRITE
            files.append({
                "path": str(item.get("path", item.get("relative_path", ""))).strip(),
                "operation": operation,
                "existed_before": bool(item.get("existed_before")),
                "previous_content": str(item.get("previous_content", "")),
                "proposed_content": str(item.get("proposed_content", "")),
                "diff": str(item.get("diff", "")),
            })

        return {
            "schema_version": CODING_PROPOSAL_SCHEMA_VERSION,
            "id": proposal_id,
            "title": str(proposal.get("title", "")).strip()[:MAX_CODING_PROPOSAL_TITLE_CHARS]
            or proposal_id
            or "Propuesta de coding",
            "summary": str(proposal.get("summary", "")).strip()[:MAX_CODING_PROPOSAL_SUMMARY_CHARS],
            "reason": str(proposal.get("reason", "")).strip()[:MAX_CODING_PROPOSAL_REASON_CHARS],
            "status": status,
            "workspace_path": str(proposal.get("workspace_path", "")).strip(),
            "files": files,
            "validation": _proposal_validation(proposal),
            "created_at": str(proposal.get("created_at", now)).strip(),
            "updated_at": str(proposal.get("updated_at", now)).strip(),
        }

    relative_path = str(proposal.get("relative_path", proposal.get("path", ""))).strip()
    return {
        "schema_version": CODING_PROPOSAL_SCHEMA_VERSION,
        "id": proposal_id,
        "title": str(proposal.get("title", "")).strip()[:MAX_CODING_PROPOSAL_TITLE_CHARS]
        or (f"Cambio en {relative_path}" if relative_path else proposal_id or "Propuesta de coding"),
        "summary": str(proposal.get("summary", "")).strip()[:MAX_CODING_PROPOSAL_SUMMARY_CHARS],
        "reason": str(proposal.get("reason", "")).strip()[:MAX_CODING_PROPOSAL_REASON_CHARS],
        "status": status,
        "workspace_path": str(proposal.get("workspace_path", "")).strip(),
        "files": [{
            "path": relative_path,
            "operation": CODING_FILE_OPERATION_WRITE,
            "existed_before": bool(proposal.get("existed_before")),
            "previous_content": str(proposal.get("previous_content", "")),
            "proposed_content": str(proposal.get("proposed_content", "")),
            "diff": str(proposal.get("diff", "")),
        }],
        "validation": _proposal_validation(proposal),
        "created_at": str(proposal.get("created_at", now)).strip(),
        "updated_at": str(proposal.get("updated_at", now)).strip(),
    }


def _proposal_workspace_path(proposal: dict) -> Path | None:
    workspace_text = str(proposal.get("workspace_path", "")).strip()
    if not workspace_text:
        return None
    return Path(workspace_text).resolve()


def _iter_coding_proposals() -> list[dict]:
    if not CODING_PROPOSALS_DIR.exists():
        return []

    proposals = []
    for proposal_path in CODING_PROPOSALS_DIR.glob("*.json"):
        try:
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(proposal, dict):
            proposals.append(_normalize_coding_proposal(proposal))

    proposals.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return proposals


def _save_coding_proposal(proposal: dict, proposal_path: Path | None = None) -> None:
    proposal_id = str(proposal.get("id", "")).strip()
    if not proposal_id:
        raise ValueError("La propuesta no tiene id.")

    CODING_PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = proposal_path or _coding_proposal_path(proposal_id)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")
    tmp_path.write_text(json.dumps(proposal, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp_path.replace(target_path)


def _set_coding_pending_proposal(proposal_id: str, pending: bool) -> None:
    cleaned_id = str(proposal_id).strip()
    if not cleaned_id:
        return

    def mutate(state):
        coding = state.setdefault("coding", {})
        pending_ids = [
            str(item).strip()
            for item in coding.get("pending_proposal_ids", [])
            if str(item).strip() and str(item).strip() != cleaned_id
        ]
        if pending:
            pending_ids.append(cleaned_id)
        coding["pending_proposal_ids"] = pending_ids

    state_transaction("coding_pending_proposal", mutate)


def _create_checkpoint(
    file_path: Path,
    previous_content: str,
    existed_before: bool,
    reason: str,
) -> tuple[str | None, str | None]:
    checkpoint_id = _new_checkpoint_id()
    checkpoint_dir = CHECKPOINTS_DIR / checkpoint_id
    metadata = {
        "id": checkpoint_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_path": _workspace_relative(file_path),
        "existed_before": bool(existed_before),
        "reason": str(reason).strip() or "auto_before_write",
    }

    try:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        if existed_before:
            (checkpoint_dir / "before.txt").write_text(previous_content, encoding="utf-8")
        (checkpoint_dir / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return None, f"No pude crear el checkpoint previo: {exc}"

    return checkpoint_id, None


def _load_checkpoint_metadata(checkpoint_dir: Path) -> dict | None:
    meta_path = checkpoint_dir / "meta.json"
    if not meta_path.exists():
        return None

    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _iter_checkpoint_entries() -> list[tuple[dict, Path]]:
    if not CHECKPOINTS_DIR.exists():
        return []

    entries = []
    for checkpoint_dir in CHECKPOINTS_DIR.iterdir():
        if not checkpoint_dir.is_dir():
            continue

        metadata = _load_checkpoint_metadata(checkpoint_dir)
        if metadata is None:
            continue

        entries.append((metadata, checkpoint_dir))

    entries.sort(key=lambda item: str(item[0].get("timestamp", "")), reverse=True)
    return entries


def _find_checkpoint_dir(checkpoint_id: str) -> tuple[Path | None, dict | None, str | None]:
    cleaned_id = str(checkpoint_id).strip().lower()
    if not cleaned_id:
        return None, None, "Debes indicar un checkpoint."

    matches = []
    for metadata, checkpoint_dir in _iter_checkpoint_entries():
        if str(metadata.get("id", "")).lower().startswith(cleaned_id):
            matches.append((checkpoint_dir, metadata))

    if not matches:
        return None, None, f"No encontre un checkpoint con id o prefijo: {checkpoint_id}"

    if len(matches) > 1:
        return None, None, f"El prefijo coincide con varios checkpoints: {checkpoint_id}"

    checkpoint_dir, metadata = matches[0]
    return checkpoint_dir, metadata, None


def _render_diff_preview_with_labels(
    previous_content: str,
    new_content: str,
    fromfile: str,
    tofile: str,
) -> str:
    diff_lines = list(
        difflib.unified_diff(
            previous_content.splitlines(),
            new_content.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )

    if not diff_lines:
        return "Sin cambios detectados."

    return "\n".join(_bounded_diff_lines(diff_lines))


def _render_diff_preview(path: Path, previous_content: str, new_content: str) -> str:
    relative_path = _workspace_relative(path)
    return _render_diff_preview_with_labels(
        previous_content,
        new_content,
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
    )


def list_files(path: str = ".") -> str:
    """
    Lista archivos y carpetas de una ruta.

    Args:
        path (str): Ruta de la carpeta a inspeccionar.

    Returns:
        str: Lista de archivos y carpetas encontrada.
    """
    directory, error = _resolve_workspace_path(path)
    if error:
        return error
    if not directory.exists():
        return f"La ruta no existe: {path}"
    if not directory.is_dir():
        return f"No es una carpeta valida: {path}"

    items = sorted(
        (
            item
            for item in directory.iterdir()
            if item.name not in IGNORED_LISTING_NAMES
        ),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )

    if not items:
        return "La carpeta esta vacia."

    rendered = []
    for item in items:
        kind = "DIR " if item.is_dir() else "FILE"
        rendered.append(f"[{kind}] {_workspace_relative(item)}")

    return "\n".join(rendered)


def read_text_file(path: str, max_bytes: int = 0) -> str:
    """
    Lee un archivo de texto.

    Args:
        path (str): Ruta del archivo.
        max_bytes (int): Limite opcional de bytes a leer. Por defecto devuelve el archivo completo.

    Returns:
        str: Contenido del archivo.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error
    if not file_path.exists():
        return f"No existe el archivo: {path}"
    if not file_path.is_file():
        return f"No es un archivo valido: {path}"

    try:
        requested_limit = int(max_bytes)
    except (TypeError, ValueError):
        return "max_bytes debe ser un entero."

    try:
        if requested_limit > 0:
            effective_limit = max(1, min(MAX_READ_BYTES, requested_limit))
            raw_content = file_path.read_bytes()[: effective_limit + 1]
            was_truncated = len(raw_content) > effective_limit
            raw_content = raw_content[:effective_limit]
        else:
            raw_content = file_path.read_bytes()
            was_truncated = False
    except OSError as exc:
        return f"Error leyendo archivo: {exc}"

    content = raw_content.decode("utf-8", errors="replace")
    if was_truncated:
        content += f"\n...[truncado por max_bytes={effective_limit}]"
    return content


def _write_text_file_impl(path: str, content: str, enforce_coding_guard: bool = True) -> str:
    """
    Escribe texto en un archivo de forma segura.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.
        enforce_coding_guard (bool): Si es True, respeta el modo coding propose_first.

    Returns:
        str: Resultado de la operacion, con checkpoint y diff.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error

    if enforce_coding_guard:
        coding_block_reason = _coding_write_block_reason(file_path)
        if coding_block_reason:
            return coding_block_reason

    write_error = _validate_write_path(file_path)
    if write_error:
        return write_error

    encoded_content = content.encode("utf-8")
    if len(encoded_content) > MAX_WRITE_BYTES:
        return (
            f"Contenido demasiado grande para escribir en una sola operacion: "
            f"{len(encoded_content)} bytes. Limite: {MAX_WRITE_BYTES} bytes."
        )

    existed_before = file_path.exists()
    if existed_before and not file_path.is_file():
        return f"No es un archivo valido: {path}"

    previous_content = ""
    if existed_before:
        try:
            previous_content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return f"No pude leer el archivo antes de escribir: {exc}"

    if existed_before and previous_content == content:
        return (
            f"Sin cambios en: {_workspace_relative(file_path)}\n"
            "El contenido nuevo coincide con el archivo actual."
        )

    checkpoint_id, checkpoint_error = _create_checkpoint(
        file_path=file_path,
        previous_content=previous_content,
        existed_before=existed_before,
        reason="auto_before_write",
    )
    if checkpoint_error:
        return checkpoint_error

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return (
            f"Error escribiendo archivo despues de crear {checkpoint_id}: {exc}\n"
            "Puedes restaurar el checkpoint si hace falta."
        )

    diff_preview = _render_diff_preview(file_path, previous_content, content)
    operation = "Archivo actualizado" if existed_before else "Archivo creado"

    return (
        f"{operation} correctamente en: {_workspace_relative(file_path)}\n"
        f"Checkpoint previo: {checkpoint_id}\n"
        f"Caracteres escritos: {len(content)}\n"
        "Vista previa:\n"
        f"{_preview_text(content, MAX_WRITE_PREVIEW_CHARS)}\n"
        "Diff:\n"
        f"{diff_preview}\n"
        "Siguiente paso recomendado: ejecuta `run_project_tests` si tocaste codigo o tests."
    )


def write_text_file(path: str, content: str) -> str:
    """
    Escribe texto en un archivo de forma segura.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.

    Returns:
        str: Resultado de la operacion, con checkpoint y diff.
    """
    return _write_text_file_impl(path=path, content=content, enforce_coding_guard=True)


def coding_set_workspace(path: str) -> str:
    """
    Define el repositorio local activo para tareas de coding.

    Args:
        path (str): Carpeta del repositorio. Puede ser absoluta o relativa al workspace de Yarbis.

    Returns:
        str: Resumen de la configuracion aplicada.
    """
    cleaned_path = str(path).strip()
    if not cleaned_path:
        return "Debes indicar la carpeta del repositorio de codigo."

    candidate = Path(cleaned_path)
    workspace_path = candidate.resolve() if candidate.is_absolute() else (WORKSPACE_ROOT / candidate).resolve()
    if not workspace_path.exists():
        return f"El workspace de codigo no existe: {workspace_path}"
    if not workspace_path.is_dir():
        return f"El workspace de codigo no es una carpeta: {workspace_path}"

    pending_ids = [
        str(proposal.get("id", "")).strip()
        for proposal in _iter_coding_proposals()
        if str(proposal.get("status", CODING_PROPOSAL_PENDING)).strip() == CODING_PROPOSAL_PENDING
        and str(proposal.get("workspace_path", "")).strip()
        and Path(str(proposal.get("workspace_path", ""))).resolve() == workspace_path
        and str(proposal.get("id", "")).strip()
    ]

    def mutate(state):
        current_coding = state.get("coding", {}) if isinstance(state.get("coding", {}), dict) else {}
        current_workspace = str(current_coding.get("workspace_path", "")).strip()
        preserve_validation = current_workspace and Path(current_workspace).resolve() == workspace_path
        state["coding"] = {
            "workspace_path": str(workspace_path),
            "mode": DEFAULT_CODING_MODE,
            "pending_proposal_ids": pending_ids,
            "validation_command": str(current_coding.get("validation_command", "")).strip()
            if preserve_validation
            else "",
            "last_validation": current_coding.get("last_validation", {})
            if preserve_validation
            else {
                "command": "",
                "proposal_id": "",
                "exit_code": None,
                "output": "",
                "ran_at": "",
            },
        }

    state_transaction("coding_set_workspace", mutate)
    git_marker = "si" if (workspace_path / ".git").exists() else "no"
    return (
        "Workspace de codigo configurado.\n"
        f"Ruta: {workspace_path}\n"
        f"Modo: {DEFAULT_CODING_MODE}\n"
        f"Repo Git: {git_marker}\n"
        f"Propuestas pendientes: {len(pending_ids)}"
    )


def coding_workspace_overview() -> str:
    """
    Resume el workspace de codigo activo.

    Returns:
        str: Estado del workspace, modo y propuestas pendientes.
    """
    state = load_state()
    coding = _coding_state(state)
    workspace_path, workspace_error = _active_coding_workspace(state)
    if workspace_error:
        return workspace_error

    pending_ids = coding.get("pending_proposal_ids", [])
    top_level = coding_list_files(".")
    return (
        "Workspace de codigo activo.\n"
        f"Ruta: {workspace_path}\n"
        f"Modo: {coding.get('mode', DEFAULT_CODING_MODE)}\n"
        f"Propuestas pendientes: {len(pending_ids)}\n\n"
        f"Validacion: {coding.get('validation_command') or '-'}\n\n"
        f"Archivos principales:\n{top_level}"
    )


def coding_list_files(path: str = ".") -> str:
    """
    Lista archivos y carpetas dentro del workspace de codigo activo.

    Args:
        path (str): Carpeta relativa o absoluta dentro del workspace de codigo.

    Returns:
        str: Lista acotada de archivos y carpetas.
    """
    directory, _relative_path, error = _resolve_coding_path(path)
    if error:
        return error
    if not directory.exists():
        return f"La ruta no existe dentro del workspace de codigo: {path}"
    if not directory.is_dir():
        return f"No es una carpeta valida dentro del workspace de codigo: {path}"

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    items = sorted(
        (
            item
            for item in directory.iterdir()
            if item.name not in IGNORED_LISTING_NAMES
        ),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )
    if not items:
        return "La carpeta de codigo esta vacia."

    rendered = []
    for item in items[:MAX_LIST_ITEMS]:
        kind = "DIR " if item.is_dir() else "FILE"
        rendered.append(f"[{kind}] {item.relative_to(workspace_path).as_posix()}")
    if len(items) > MAX_LIST_ITEMS:
        rendered.append(f"... {len(items) - MAX_LIST_ITEMS} elemento(s) mas.")
    return "\n".join(rendered)


def coding_read_text_file(path: str, max_bytes: int = 0) -> str:
    """
    Lee un archivo de texto dentro del workspace de codigo activo.

    Args:
        path (str): Archivo relativo o absoluto dentro del workspace de codigo.
        max_bytes (int): Limite opcional de bytes a leer.

    Returns:
        str: Contenido del archivo.
    """
    file_path, _relative_path, error = _resolve_coding_path(path)
    if error:
        return error
    if not file_path.exists():
        return f"No existe el archivo dentro del workspace de codigo: {path}"
    if not file_path.is_file():
        return f"No es un archivo valido dentro del workspace de codigo: {path}"

    try:
        requested_limit = int(max_bytes)
    except (TypeError, ValueError):
        return "max_bytes debe ser un entero."

    try:
        if requested_limit > 0:
            effective_limit = max(1, min(MAX_READ_BYTES, requested_limit))
            raw_content = file_path.read_bytes()[: effective_limit + 1]
            was_truncated = len(raw_content) > effective_limit
            raw_content = raw_content[:effective_limit]
        else:
            raw_content = file_path.read_bytes()
            was_truncated = False
    except OSError as exc:
        return f"Error leyendo archivo de codigo: {exc}"

    content = raw_content.decode("utf-8", errors="replace")
    if was_truncated:
        content += f"\n...[truncado por max_bytes={effective_limit}]"
    return content


def _load_coding_files_spec(files_json) -> tuple[list[dict] | None, str | None]:
    if isinstance(files_json, str):
        cleaned = files_json.strip()
        if not cleaned:
            return None, "Debes indicar files_json con una lista de cambios."
        try:
            raw_files = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return None, f"files_json no es JSON valido: {exc}"
    else:
        raw_files = files_json

    if not isinstance(raw_files, list):
        return None, "files_json debe ser una lista de cambios."
    if not raw_files:
        return None, "files_json debe incluir al menos un cambio."

    normalized_files = []
    for index, raw_item in enumerate(raw_files, start=1):
        if not isinstance(raw_item, dict):
            return None, f"El cambio #{index} debe ser un objeto."
        path = str(raw_item.get("path", raw_item.get("relative_path", ""))).strip()
        if not path:
            return None, f"El cambio #{index} no tiene path."
        operation = str(raw_item.get("operation", CODING_FILE_OPERATION_WRITE)).strip().lower()
        if operation in {"create", "update"}:
            operation = CODING_FILE_OPERATION_WRITE
        if operation in {"remove", "delete_file"}:
            operation = CODING_FILE_OPERATION_DELETE
        if operation not in VALID_CODING_FILE_OPERATIONS:
            return None, f"Operacion invalida en {path}: {operation}. Usa write o delete."

        content = raw_item.get("proposed_content", raw_item.get("content", ""))
        normalized_files.append({
            "path": path,
            "operation": operation,
            "content": "" if operation == CODING_FILE_OPERATION_DELETE else str(content),
        })

    return normalized_files, None


def _build_coding_proposal_files(files_json) -> tuple[list[dict] | None, Path | None, str | None]:
    raw_files, parse_error = _load_coding_files_spec(files_json)
    if parse_error:
        return None, None, parse_error

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return None, None, workspace_error

    proposal_files = []
    seen_paths = set()
    for item in raw_files:
        target_path, relative_path, error = _resolve_coding_path(item["path"])
        if error:
            return None, None, error
        relative_text = relative_path.as_posix()
        if relative_text in seen_paths:
            return None, None, f"El archivo aparece mas de una vez en la propuesta: {relative_text}"
        seen_paths.add(relative_text)

        write_error = _validate_coding_write_path(relative_path)
        if write_error:
            return None, None, write_error

        existed_before = target_path.exists()
        if existed_before and not target_path.is_file():
            return None, None, f"No es un archivo valido dentro del workspace de codigo: {relative_text}"

        previous_content = ""
        if existed_before:
            try:
                previous_content = target_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return None, None, f"No pude leer {relative_text} antes de proponer: {exc}"

        operation = item["operation"]
        if operation == CODING_FILE_OPERATION_DELETE:
            if not existed_before:
                return None, None, f"No puedo proponer borrar un archivo inexistente: {relative_text}"
            proposed_content = ""
        else:
            proposed_content = item["content"]
            encoded_content = proposed_content.encode("utf-8")
            if len(encoded_content) > MAX_WRITE_BYTES:
                return None, None, (
                    f"Contenido demasiado grande para {relative_text}: "
                    f"{len(encoded_content)} bytes. Limite: {MAX_WRITE_BYTES} bytes."
                )

        if operation == CODING_FILE_OPERATION_WRITE and existed_before and previous_content == proposed_content:
            continue

        diff_preview = _render_diff_preview_with_labels(
            previous_content,
            proposed_content,
            fromfile=f"a/{relative_text}",
            tofile=f"b/{relative_text}",
        )
        proposal_files.append({
            "path": relative_text,
            "operation": operation,
            "existed_before": existed_before,
            "previous_content": previous_content,
            "proposed_content": proposed_content,
            "diff": diff_preview,
        })

    if not proposal_files:
        return None, None, "Sin cambios para proponer; los contenidos coinciden con el workspace actual."

    return proposal_files, workspace_path, None


def _format_coding_file_list(files: list[dict]) -> str:
    return "\n".join(
        f"- {item.get('operation', CODING_FILE_OPERATION_WRITE)} {item.get('path', '')}"
        for item in files
    )


def _proposal_diff_text(proposal: dict) -> str:
    files = proposal.get("files", [])
    if not isinstance(files, list):
        files = []
    parts = []
    for item in files:
        if not isinstance(item, dict):
            continue
        header = f"### {item.get('operation', CODING_FILE_OPERATION_WRITE)} {item.get('path', '')}".strip()
        parts.append(f"{header}\n{item.get('diff', 'Sin diff guardado.')}")
    return "\n\n".join(parts) if parts else "Sin diff guardado."


def coding_propose_changes(
    title: str,
    files_json: str,
    summary: str = "",
    reason: str = "",
    validation_command: str = "",
) -> str:
    """
    Crea una propuesta multiarchivo para el workspace de codigo activo sin aplicarla.

    Args:
        title (str): Titulo corto de la unidad de trabajo.
        files_json (str): JSON con lista de cambios: path, operation write/delete y content/proposed_content.
        summary (str): Resumen de la intencion del cambio.
        reason (str): Motivo breve de la propuesta.
        validation_command (str): Comando sugerido para validar esta propuesta.

    Returns:
        str: Id de propuesta, archivos y diff resumido.
    """
    proposal_files, workspace_path, error = _build_coding_proposal_files(files_json)
    if error:
        return error

    proposal_id = _new_coding_proposal_id()
    now = datetime.now(timezone.utc).isoformat()
    cleaned_title = str(title).strip()[:MAX_CODING_PROPOSAL_TITLE_CHARS] or "Propuesta de coding"
    proposal = {
        "schema_version": CODING_PROPOSAL_SCHEMA_VERSION,
        "id": proposal_id,
        "title": cleaned_title,
        "summary": str(summary).strip()[:MAX_CODING_PROPOSAL_SUMMARY_CHARS],
        "reason": str(reason).strip()[:MAX_CODING_PROPOSAL_REASON_CHARS],
        "status": CODING_PROPOSAL_PENDING,
        "workspace_path": str(workspace_path),
        "files": proposal_files,
        "validation": {
            "command": str(validation_command).strip()[:MAX_CODING_VALIDATION_COMMAND_CHARS],
            "proposal_id": proposal_id,
            "exit_code": None,
            "output": "",
            "ran_at": "",
        },
        "created_at": now,
        "updated_at": now,
    }

    try:
        _save_coding_proposal(proposal)
        _set_coding_pending_proposal(proposal_id, pending=True)
    except (OSError, ValueError) as exc:
        return f"No pude guardar la propuesta de coding: {exc}"

    return (
        "Propuesta de coding creada.\n"
        f"Id: {proposal_id}\n"
        f"Titulo: {cleaned_title}\n"
        f"Archivos: {len(proposal_files)}\n"
        f"{_format_coding_file_list(proposal_files)}\n"
        "Estado: pending\n"
        "Diff:\n"
        f"{_proposal_diff_text(proposal)}\n"
        f"Para aplicar: `coding_apply_proposal` con proposal_id={proposal_id}"
    )


def coding_propose_text_file(path: str, content: str, reason: str = "") -> str:
    """
    Crea una propuesta de cambio para un archivo del workspace de codigo activo sin aplicarla.

    Args:
        path (str): Archivo relativo o absoluto dentro del workspace de codigo.
        content (str): Contenido propuesto completo para el archivo.
        reason (str): Motivo breve de la propuesta.

    Returns:
        str: Id de propuesta y diff resumido.
    """
    files_json = json.dumps(
        [{"path": path, "operation": CODING_FILE_OPERATION_WRITE, "content": str(content)}],
        ensure_ascii=False,
    )
    return coding_propose_changes(
        title=f"Cambio en {path}",
        files_json=files_json,
        reason=reason,
    )


def coding_list_proposals(status: str = "pending", limit: int = 20) -> str:
    """
    Lista propuestas de coding guardadas.

    Args:
        status (str): Estado a filtrar: pending, applied, discarded o all.
        limit (int): Maximo de propuestas a mostrar.

    Returns:
        str: Resumen de propuestas.
    """
    cleaned_status = str(status).strip().lower() or CODING_PROPOSAL_PENDING
    if cleaned_status not in VALID_CODING_PROPOSAL_STATUS and cleaned_status != "all":
        return "Estado invalido. Usa pending, applied, discarded o all."

    try:
        normalized_limit = max(1, min(MAX_CODING_PROPOSAL_LIST_ITEMS, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 20

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    proposals = []
    for proposal in _iter_coding_proposals():
        proposal_workspace_text = str(proposal.get("workspace_path", "")).strip()
        if not proposal_workspace_text:
            continue
        proposal_workspace = Path(proposal_workspace_text).resolve()
        if proposal_workspace != workspace_path:
            continue
        proposal_status = str(proposal.get("status", CODING_PROPOSAL_PENDING)).strip()
        if cleaned_status != "all" and proposal_status != cleaned_status:
            continue
        proposals.append(proposal)

    if not proposals:
        return f"No hay propuestas de coding con estado {cleaned_status} para el workspace activo."

    rendered = []
    for proposal in proposals[:normalized_limit]:
        reason = str(proposal.get("reason", "")).strip() or "-"
        title = str(proposal.get("title", "")).strip() or proposal.get("id", "")
        files = proposal.get("files", [])
        files_count = len(files) if isinstance(files, list) else 0
        rendered.append(
            f"[{proposal.get('id', '')}] {proposal.get('status', '')} "
            f"{title} ({files_count} archivo(s)) - {reason}"
        )
    if len(proposals) > normalized_limit:
        rendered.append(f"... {len(proposals) - normalized_limit} propuesta(s) mas.")
    return "\n".join(rendered)


def coding_get_proposal(proposal_id: str) -> str:
    """
    Muestra el detalle de una propuesta de coding.

    Args:
        proposal_id (str): Id o prefijo de la propuesta.

    Returns:
        str: Metadatos y diff de la propuesta.
    """
    raw_proposal, _proposal_path, error = _load_coding_proposal(proposal_id)
    if error:
        return error
    proposal = _normalize_coding_proposal(raw_proposal)

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    proposal_workspace = _proposal_workspace_path(proposal)
    if proposal_workspace is None:
        return "La propuesta no registra workspace de codigo."
    if proposal_workspace != workspace_path:
        return (
            "La propuesta pertenece a otro workspace de codigo.\n"
            f"Propuesta: {proposal_workspace}\n"
            f"Activo: {workspace_path}"
        )

    reason = str(proposal.get("reason", "")).strip() or "-"
    validation = _proposal_validation(proposal)
    validation_text = "sin ejecutar"
    if validation.get("ran_at") or validation.get("command"):
        exit_code = validation.get("exit_code")
        validation_text = (
            f"{validation.get('command') or '-'} "
            f"(exit={exit_code if exit_code is not None else '-'}) "
            f"{validation.get('ran_at') or ''}"
        ).strip()
    return (
        f"Id: {proposal.get('id', '')}\n"
        f"Titulo: {proposal.get('title', '')}\n"
        f"Estado: {proposal.get('status', '')}\n"
        f"Archivos:\n{_format_coding_file_list(proposal.get('files', []))}\n"
        f"Resumen: {proposal.get('summary', '') or '-'}\n"
        f"Motivo: {reason}\n"
        f"Validacion: {validation_text}\n"
        f"Creada: {proposal.get('created_at', '')}\n"
        f"Actualizada: {proposal.get('updated_at', '')}\n\n"
        "Diff:\n"
        f"{_proposal_diff_text(proposal)}"
    )


def coding_apply_proposal(proposal_id: str) -> str:
    """
    Aplica una propuesta pendiente del workspace de codigo activo.

    Args:
        proposal_id (str): Id o prefijo de la propuesta.

    Returns:
        str: Resultado de aplicar el cambio con checkpoint.
    """
    raw_proposal, proposal_path, error = _load_coding_proposal(proposal_id)
    if error:
        return error
    proposal = _normalize_coding_proposal(raw_proposal)

    if _proposal_status(proposal) != CODING_PROPOSAL_PENDING:
        return f"La propuesta {proposal.get('id', proposal_id)} no esta pendiente."

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    proposal_workspace = _proposal_workspace_path(proposal)
    if proposal_workspace is None or proposal_workspace != workspace_path:
        return (
            "La propuesta pertenece a otro workspace de codigo.\n"
            f"Propuesta: {proposal_workspace}\n"
            f"Activo: {workspace_path}"
        )

    files = proposal.get("files", [])
    if not isinstance(files, list) or not files:
        return "La propuesta no contiene archivos para aplicar."

    preflight = []
    for item in files:
        if not isinstance(item, dict):
            return "La propuesta contiene un archivo con formato invalido."
        relative_text = str(item.get("path", "")).strip()
        target_path, relative_path, resolve_error = _resolve_coding_path(relative_text)
        if resolve_error:
            return resolve_error

        write_error = _validate_coding_write_path(relative_path)
        if write_error:
            return write_error

        operation = str(item.get("operation", CODING_FILE_OPERATION_WRITE)).strip().lower()
        if operation not in VALID_CODING_FILE_OPERATIONS:
            return f"Operacion invalida en {relative_path.as_posix()}: {operation}"

        existed_before = bool(item.get("existed_before"))
        previous_content = str(item.get("previous_content", ""))
        proposed_content = str(item.get("proposed_content", ""))
        if existed_before:
            if not target_path.exists() or not target_path.is_file():
                return f"El archivo original ya no existe como archivo: {relative_path.as_posix()}"
            try:
                current_content = target_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return f"No pude verificar {relative_path.as_posix()} antes de aplicar: {exc}"
            if current_content != previous_content:
                return (
                    "El archivo cambio desde que se creo la propuesta. "
                    f"Genera una nueva propuesta para: {relative_path.as_posix()}"
                )
        elif target_path.exists():
            return (
                "La propuesta creaba un archivo nuevo, pero esa ruta ya existe. "
                f"Genera una nueva propuesta para: {relative_path.as_posix()}"
            )

        preflight.append({
            "target_path": target_path,
            "relative_path": relative_path,
            "operation": operation,
            "existed_before": existed_before,
            "previous_content": previous_content,
            "proposed_content": proposed_content,
        })

    applied = []
    for item in preflight:
        checkpoint_id, checkpoint_error = _create_checkpoint(
            file_path=item["target_path"],
            previous_content=item["previous_content"],
            existed_before=item["existed_before"],
            reason=f"coding_apply_proposal:{proposal.get('id', proposal_id)}",
        )
        if checkpoint_error:
            return checkpoint_error

        try:
            if item["operation"] == CODING_FILE_OPERATION_DELETE:
                item["target_path"].unlink()
                operation_text = "Archivo eliminado"
            else:
                item["target_path"].parent.mkdir(parents=True, exist_ok=True)
                item["target_path"].write_text(item["proposed_content"], encoding="utf-8")
                operation_text = "Archivo actualizado" if item["existed_before"] else "Archivo creado"
        except OSError as exc:
            return (
                f"Error aplicando {item['relative_path'].as_posix()} despues de crear {checkpoint_id}: {exc}\n"
                "Puedes restaurar el checkpoint si hace falta."
            )

        applied.append(
            f"- {operation_text}: {item['relative_path'].as_posix()} (checkpoint {checkpoint_id})"
        )

    proposal["status"] = CODING_PROPOSAL_APPLIED
    proposal["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _save_coding_proposal(proposal, proposal_path)
        _set_coding_pending_proposal(str(proposal.get("id", proposal_id)), pending=False)
    except (OSError, ValueError) as exc:
        return (
            f"Cambio aplicado, pero no pude actualizar la propuesta: {exc}\n\n"
            "Archivos aplicados:\n"
            f"{chr(10).join(applied)}"
        )

    return (
        f"Propuesta aplicada: {proposal.get('id', proposal_id)}\n"
        f"Titulo: {proposal.get('title', '')}\n"
        "Archivos aplicados:\n"
        f"{chr(10).join(applied)}\n"
        "Siguiente paso recomendado: ejecuta `coding_run_validation`."
    )


def coding_discard_proposal(proposal_id: str) -> str:
    """
    Descarta una propuesta pendiente sin modificar archivos.

    Args:
        proposal_id (str): Id o prefijo de la propuesta.

    Returns:
        str: Confirmacion del descarte.
    """
    proposal, proposal_path, error = _load_coding_proposal(proposal_id)
    if error:
        return error

    proposal["status"] = CODING_PROPOSAL_DISCARDED
    proposal["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _save_coding_proposal(proposal, proposal_path)
        _set_coding_pending_proposal(str(proposal.get("id", proposal_id)), pending=False)
    except (OSError, ValueError) as exc:
        return f"No pude descartar la propuesta: {exc}"

    return (
        f"Propuesta descartada: {proposal.get('id', proposal_id)}\n"
        f"Archivo: {proposal.get('relative_path', '-')}"
    )


def _run_coding_subprocess(command, timeout_seconds: int, shell: bool = False) -> tuple[int | None, str, str]:
    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return None, "", workspace_error

    try:
        normalized_timeout = max(1, min(3600, int(timeout_seconds)))
    except (TypeError, ValueError):
        normalized_timeout = 120

    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace_path),
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=normalized_timeout,
        )
    except subprocess.TimeoutExpired:
        rendered_command = command if isinstance(command, str) else " ".join(command)
        return None, rendered_command, f"El comando excedio el timeout de {normalized_timeout} segundos."
    except OSError as exc:
        rendered_command = command if isinstance(command, str) else " ".join(command)
        return None, rendered_command, f"No pude ejecutar el comando: {exc}"

    combined_output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if str(part).strip()
    )
    if not combined_output:
        combined_output = "El comando no produjo salida visible."
    rendered_command = command if isinstance(command, str) else " ".join(command)
    return completed.returncode, rendered_command, _bounded_text(combined_output, MAX_COMMAND_OUTPUT_CHARS)


def _detect_coding_validation_command_for_workspace(workspace_path: Path) -> str:
    if (workspace_path / "scripts" / "check.ps1").exists():
        return "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\check.ps1"
    if (workspace_path / "pyproject.toml").exists() and (workspace_path / "tests").is_dir():
        return f'"{sys.executable}" -m unittest discover -s tests'
    if (workspace_path / "package.json").exists():
        return "npm test"
    csproj_files = sorted(workspace_path.glob("*.csproj"))
    if csproj_files:
        return f"dotnet build {csproj_files[0].name}"
    sln_files = sorted(workspace_path.glob("*.sln"))
    if sln_files:
        return f"dotnet build {sln_files[0].name}"
    return ""


def coding_detect_validation_command() -> str:
    """
    Detecta y guarda un comando de validacion para el workspace de codigo activo.

    Returns:
        str: Comando detectado o mensaje si no hay candidato.
    """
    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    command = _detect_coding_validation_command_for_workspace(workspace_path)
    if not command:
        return "No encontre una validacion por defecto para este workspace."

    def mutate(state):
        coding = state.setdefault("coding", {})
        coding["validation_command"] = command

    state_transaction("coding_detect_validation_command", mutate)
    return f"Comando de validacion detectado y guardado:\n{command}"


def coding_update_validation_command(command: str = "") -> str:
    """
    Guarda o limpia el comando preferido de validacion del workspace de codigo.

    Args:
        command (str): Comando a ejecutar desde el workspace activo. Vacio limpia el valor guardado.

    Returns:
        str: Confirmacion del cambio.
    """
    cleaned_command = str(command).strip()[:MAX_CODING_VALIDATION_COMMAND_CHARS]

    def mutate(state):
        coding = state.setdefault("coding", {})
        coding["validation_command"] = cleaned_command

    state_transaction("coding_update_validation_command", mutate)
    if cleaned_command:
        return f"Comando de validacion guardado:\n{cleaned_command}"
    return "Comando de validacion limpiado."


def coding_git_status() -> str:
    """
    Ejecuta `git status --short --branch` en el workspace de codigo activo.

    Returns:
        str: Estado Git acotado.
    """
    exit_code, command, output = _run_coding_subprocess(["git", "status", "--short", "--branch"], 30)
    if exit_code is None:
        return output
    status_line = "Git status OK." if exit_code == 0 else f"Git status con fallos (exit={exit_code})."
    return f"{status_line}\nComando: {command}\nSalida:\n{output}"


def coding_git_diff() -> str:
    """
    Ejecuta `git diff -- .` en el workspace de codigo activo.

    Returns:
        str: Diff Git acotado.
    """
    exit_code, command, output = _run_coding_subprocess(["git", "diff", "--", "."], 30)
    if exit_code is None:
        return output
    status_line = "Git diff OK." if exit_code == 0 else f"Git diff con fallos (exit={exit_code})."
    return f"{status_line}\nComando: {command}\nSalida:\n{output}"


def coding_run_validation(command: str = "", timeout_seconds: int = 120, proposal_id: str = "") -> str:
    """
    Ejecuta una validacion en el workspace de codigo activo.

    Args:
        command (str): Comando de tests/checks. Si queda vacio, usa el guardado o intenta detectar uno seguro.
        timeout_seconds (int): Timeout maximo de ejecucion.
        proposal_id (str): Id opcional de propuesta para asociar el resultado.

    Returns:
        str: Codigo de salida, comando y salida acotada.
    """
    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    cleaned_command = str(command).strip()
    if not cleaned_command:
        coding = _coding_state()
        cleaned_command = str(coding.get("validation_command", "")).strip()

    if not cleaned_command:
        cleaned_command = _detect_coding_validation_command_for_workspace(workspace_path)
        if cleaned_command:
            coding_update_validation_command(cleaned_command)
        else:
            return (
                "No encontre una validacion por defecto para este workspace. "
                "Indica un comando explicito en `command`."
            )

    exit_code, rendered_command, output = _run_coding_subprocess(
        cleaned_command,
        timeout_seconds=timeout_seconds,
        shell=True,
    )
    ran_at = datetime.now(timezone.utc).isoformat()
    validation = {
        "command": rendered_command,
        "proposal_id": str(proposal_id).strip(),
        "exit_code": exit_code,
        "output": output,
        "ran_at": ran_at,
    }

    def mutate(state):
        coding = state.setdefault("coding", {})
        coding["last_validation"] = validation

    state_transaction("coding_run_validation", mutate)

    cleaned_proposal_id = str(proposal_id).strip()
    if cleaned_proposal_id:
        proposal, proposal_path, proposal_error = _load_coding_proposal(cleaned_proposal_id)
        if proposal_error:
            output = f"{output}\n\nNo pude asociar la validacion a la propuesta: {proposal_error}"
        else:
            normalized_proposal = _normalize_coding_proposal(proposal)
            normalized_proposal["validation"] = validation
            normalized_proposal["updated_at"] = ran_at
            try:
                _save_coding_proposal(normalized_proposal, proposal_path)
            except (OSError, ValueError) as exc:
                output = f"{output}\n\nNo pude actualizar la validacion de la propuesta: {exc}"

    if exit_code is None:
        return f"Validacion no ejecutada.\nComando: {rendered_command}\nSalida:\n{output}"
    status_line = "Validacion OK." if exit_code == 0 else f"Validacion con fallos (exit={exit_code})."
    proposal_text = f"\nPropuesta: {cleaned_proposal_id}" if cleaned_proposal_id else ""
    return f"{status_line}\nComando: {rendered_command}\nDirectorio: {workspace_path}{proposal_text}\nSalida:\n{output}"


def list_checkpoints(path: str = "", limit: int = 10) -> str:
    """
    Lista checkpoints disponibles para restaurar cambios.

    Args:
        path (str): Ruta opcional para filtrar por archivo.
        limit (int): Maximo de checkpoints a mostrar.

    Returns:
        str: Listado resumido de checkpoints.
    """
    entries = _iter_checkpoint_entries()
    if not entries:
        return "No hay checkpoints guardados."

    filtered_entries = entries
    if str(path).strip():
        target_path, error = _resolve_workspace_path(path)
        if error:
            return error
        target_relative = _workspace_relative(target_path)
        filtered_entries = [
            (metadata, checkpoint_dir)
            for metadata, checkpoint_dir in entries
            if metadata.get("target_path") == target_relative
        ]

    if not filtered_entries:
        return "No hay checkpoints que coincidan."

    try:
        normalized_limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10

    lines = []
    for metadata, _ in filtered_entries[:normalized_limit]:
        existed_before = "si" if metadata.get("existed_before") else "no"
        lines.append(
            f"[{metadata.get('id', 'checkpoint-desconocido')}] "
            f"{metadata.get('target_path', '?')} "
            f"(antes_existia={existed_before}, motivo={metadata.get('reason', '-')}, "
            f"fecha={metadata.get('timestamp', '-')})"
        )

    return "\n".join(lines)


def restore_checkpoint(checkpoint_id: str) -> str:
    """
    Restaura un archivo usando un checkpoint previo.

    Args:
        checkpoint_id (str): Id completo o prefijo unico del checkpoint.

    Returns:
        str: Resultado de la restauracion.
    """
    checkpoint_dir, metadata, error = _find_checkpoint_dir(checkpoint_id)
    if error:
        return error

    target_path, resolve_error = _resolve_workspace_path(str(metadata.get("target_path", "")))
    if resolve_error:
        return resolve_error

    write_error = _validate_write_path(target_path)
    if write_error:
        return write_error

    existed_before = bool(metadata.get("existed_before"))
    try:
        if existed_before:
            before_path = checkpoint_dir / "before.txt"
            if not before_path.exists():
                return f"El checkpoint {metadata.get('id')} no tiene contenido para restaurar."
            previous_content = before_path.read_text(encoding="utf-8")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(previous_content, encoding="utf-8")
            action = "Archivo restaurado desde el checkpoint."
        else:
            if target_path.exists():
                if not target_path.is_file():
                    return f"No puedo restaurar sobre una ruta no valida: {_workspace_relative(target_path)}"
                target_path.unlink()
            action = "Checkpoint restaurado. Se elimino el archivo creado despues del checkpoint."
    except OSError as exc:
        return f"No pude restaurar el checkpoint {metadata.get('id')}: {exc}"

    return (
        f"{action}\n"
        f"Checkpoint usado: {metadata.get('id')}\n"
        f"Archivo: {metadata.get('target_path')}\n"
        "Siguiente paso recomendado: ejecuta `run_project_tests` para validar el estado restaurado."
    )


def _format_memory_backup_counts(counts: dict) -> str:
    return (
        f"mensajes={counts.get('messages', 0)}, "
        f"notas={counts.get('notes', 0)}, "
        f"tareas={counts.get('tasks', 0)}, "
        f"plan={counts.get('plan_items', 0)}"
    )


def create_memory_backup(path: str = "", include_secrets: bool = False) -> str:
    """
    Crea un respaldo portable de la memoria persistente de Yarbis.

    Args:
        path (str): Ruta destino opcional. Si se omite, usa .yarbis_memory_backups.
        include_secrets (bool): Si True, incluye tokens persistidos en state.json.

    Returns:
        str: Resumen del respaldo creado.
    """
    try:
        backup = memory_transfer.create_backup(
            path=path,
            include_secrets=_coerce_bool(include_secrets),
        )
    except memory_transfer.MemoryTransferError as exc:
        return str(exc)

    secret_status = "incluidos" if backup["include_secrets"] else "redactados"
    redacted = ", ".join(backup["redacted_paths"]) if backup["redacted_paths"] else "ninguno"
    return (
        "Respaldo de memoria creado.\n"
        f"Id: {backup['id']}\n"
        f"Archivo: {backup['path']}\n"
        f"Fecha: {backup['created_at']}\n"
        f"Secretos: {secret_status}\n"
        f"Campos redactados: {redacted}\n"
        f"Contenido: {_format_memory_backup_counts(backup['counts'])}"
    )


def list_memory_backups(limit: int = 10) -> str:
    """
    Lista respaldos de memoria disponibles en .yarbis_memory_backups.

    Args:
        limit (int): Maximo de respaldos a mostrar.

    Returns:
        str: Listado resumido de respaldos.
    """
    backups = memory_transfer.list_backups(limit=limit)
    if not backups:
        return "No hay respaldos de memoria guardados."

    lines = []
    for backup in backups:
        secret_status = "con secretos" if backup["include_secrets"] else "redactado"
        lines.append(
            f"[{backup['id']}] {backup['created_at']} "
            f"({secret_status}; {_format_memory_backup_counts(backup['counts'])}) "
            f"{backup['path']}"
        )
    return "\n".join(lines)


def inspect_memory_backup(path_or_id: str) -> str:
    """
    Inspecciona la metadata de un respaldo de memoria sin importarlo.

    Args:
        path_or_id (str): Ruta, nombre o prefijo unico del respaldo.

    Returns:
        str: Resumen seguro del respaldo.
    """
    try:
        backup = memory_transfer.inspect_backup(path_or_id)
    except memory_transfer.MemoryTransferError as exc:
        return str(exc)

    secret_status = "incluidos" if backup["include_secrets"] else "redactados"
    redacted = ", ".join(backup["redacted_paths"]) if backup["redacted_paths"] else "ninguno"
    return (
        "Respaldo de memoria.\n"
        f"Id: {backup['id']}\n"
        f"Archivo: {backup['path']}\n"
        f"Fecha: {backup['created_at']}\n"
        f"Objetivo: {backup['goal'] or '-'}\n"
        f"Perfil: {backup['profile_name'] or '-'}\n"
        f"Secretos: {secret_status}\n"
        f"Campos redactados: {redacted}\n"
        f"Contenido: {_format_memory_backup_counts(backup['counts'])}"
    )


def import_memory_backup(path_or_id: str, mode: str = "replace") -> str:
    """
    Trasplanta un respaldo de memoria sobre esta instalacion.

    Args:
        path_or_id (str): Ruta, nombre o prefijo unico del respaldo.
        mode (str): replace para sustituir o merge para fusionar recuerdos.

    Returns:
        str: Resultado de la importacion y ruta del respaldo de seguridad previo.
    """
    try:
        result = memory_transfer.import_backup(path_or_id, mode=mode)
    except memory_transfer.MemoryTransferError as exc:
        return str(exc)

    mode_label = "reemplazo" if result["mode"] == "replace" else "fusion"
    redacted = ", ".join(result["redacted_paths"]) if result["redacted_paths"] else "ninguno"
    safety_backup = result["safety_backup"]
    return (
        "Trasplante de memoria completado.\n"
        f"Modo: {mode_label}\n"
        f"Respaldo importado: {result['source_id']}\n"
        f"Archivo origen: {result['source_path']}\n"
        f"Respaldo previo local: {safety_backup['path']}\n"
        f"Campos redactados preservados desde destino: {redacted}\n"
        f"Contenido importado: {_format_memory_backup_counts(result['counts'])}"
    )


def _format_latest_memory_backup(latest: dict | None) -> str:
    if not latest:
        return "-"
    return (
        f"{latest.get('created_at', '-')} "
        f"[{latest.get('id', '-')}] {latest.get('path', '-')}"
    )


def memory_protection_status() -> str:
    """
    Muestra el estado de proteccion automatica de memoria y respaldos disponibles.

    Returns:
        str: Resumen de configuracion, ultimo respaldo y errores.
    """
    status = memory_protection_status_data()
    enabled = "activa" if status["enabled"] else "desactivada"
    cadence = "cada cambio" if status["backup_on_every_change"] else "manual"
    mirror = status["mirror_dir"] or "-"
    last_error = status["last_error"] or "-"
    latest = _format_latest_memory_backup(status.get("latest_backup"))
    retention = status.get("retention", {})

    return (
        "Proteccion de memoria.\n"
        f"Estado: {enabled}\n"
        f"Respaldo automatico: {cadence}\n"
        f"Directorio local: {status['local_dir']}\n"
        f"Espejo externo: {mirror}\n"
        f"Retencion: {retention.get('max_auto_backups', 0)} automaticos; "
        f"diarios por {retention.get('keep_daily_days', 0)} dias\n"
        f"Verificacion post-escritura: {'si' if status['verify_after_write'] else 'no'}\n"
        f"Auto-restauracion: {'si' if status['auto_restore'] else 'no'}\n"
        f"Ultimo backup: {status['last_backup_at'] or '-'}\n"
        f"Ultima recuperacion: {status['last_recovery_at'] or '-'}\n"
        f"Respaldos validos: {status['valid_backups']}\n"
        f"Respaldos invalidos: {status['invalid_backups']}\n"
        f"Mas reciente: {latest}\n"
        f"Ultimo aviso: {last_error}"
    )


def verify_memory_backups() -> str:
    """
    Verifica respaldos de memoria locales y del espejo configurado.

    Returns:
        str: Conteo de respaldos validos, invalidos y respaldo mas reciente.
    """
    verified = verify_memory_backups_data()
    latest = _format_latest_memory_backup(verified.get("latest"))
    lines = [
        "Verificacion de respaldos de memoria.",
        f"Directorio local: {verified['local_dir']}",
        f"Espejo externo: {verified['mirror_dir'] or '-'}",
        f"Validos: {verified['valid_count']}",
        f"Invalidos: {verified['invalid_count']}",
        f"Mas reciente: {latest}",
    ]
    for invalid in verified.get("invalid", [])[:5]:
        lines.append(f"Invalido: {invalid['path']} -> {invalid['error']}")
    return "\n".join(lines)


def update_memory_protection_settings(
    enabled: bool = True,
    backup_on_every_change: bool = True,
    mirror_dir: str = "",
    max_auto_backups: int = 50,
    keep_daily_days: int = 14,
    verify_after_write: bool = False,
    auto_restore: bool = True,
) -> str:
    """
    Configura la proteccion automatica de memoria persistente.

    Args:
        enabled (bool): Activa o desactiva la proteccion.
        backup_on_every_change (bool): Si True, respalda cada escritura de state.json.
        mirror_dir (str): Carpeta externa opcional para espejar respaldos.
        max_auto_backups (int): Maximo de respaldos automaticos recientes.
        keep_daily_days (int): Dias para conservar al menos un respaldo diario.
        verify_after_write (bool): Valida JSON despues de cada escritura atomica.
        auto_restore (bool): Restaura automaticamente si state.json falta o se dana.

    Returns:
        str: Resumen de la configuracion aplicada.
    """
    cleaned_mirror = str(mirror_dir).strip()
    if cleaned_mirror == CLEAR_VALUE:
        cleaned_mirror = ""
    if cleaned_mirror:
        candidate = Path(cleaned_mirror).expanduser()
        if not candidate.is_absolute():
            candidate = (WORKSPACE_ROOT / candidate).resolve()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"No pude preparar el espejo externo: {exc}"
        if not candidate.is_dir():
            return f"El espejo externo no es una carpeta: {candidate}"
        cleaned_mirror = str(candidate)

    try:
        cleaned_max_auto = max(1, min(5000, int(max_auto_backups)))
    except (TypeError, ValueError):
        return "max_auto_backups debe ser un numero entre 1 y 5000."

    try:
        cleaned_keep_daily = max(0, min(3650, int(keep_daily_days)))
    except (TypeError, ValueError):
        return "keep_daily_days debe ser un numero entre 0 y 3650."

    def mutate(state):
        current = state.get("memory_protection", {})
        if not isinstance(current, dict):
            current = {}
        state["memory_protection"] = {
            "enabled": _coerce_bool(enabled),
            "backup_on_every_change": _coerce_bool(backup_on_every_change),
            "mirror_dir": cleaned_mirror,
            "include_secrets": False,
            "retention": {
                "max_auto_backups": cleaned_max_auto,
                "keep_daily_days": cleaned_keep_daily,
            },
            "verify_after_write": _coerce_bool(verify_after_write),
            "auto_restore": _coerce_bool(auto_restore),
            "last_backup_at": str(current.get("last_backup_at", "")),
            "last_recovery_at": str(current.get("last_recovery_at", "")),
            "last_error": str(current.get("last_error", "")),
        }

    state_transaction("update_memory_protection_settings", mutate)
    return memory_protection_status()


def run_project_tests(
    test_target: str = "tests",
    pattern: str = "test*.py",
    timeout_seconds: int = 120,
) -> str:
    """
    Ejecuta tests del proyecto dentro del workspace.

    Args:
        test_target (str): Carpeta o archivo de tests a ejecutar.
        pattern (str): Patron para discovery si el objetivo es una carpeta.
        timeout_seconds (int): Timeout maximo para la corrida.

    Returns:
        str: Resumen del resultado y salida relevante.
    """
    cleaned_target = str(test_target).strip() or "tests"
    target_path, error = _resolve_workspace_path(cleaned_target)
    if error:
        return error
    if not target_path.exists():
        return f"No existe la ruta de tests: {cleaned_target}"
    try:
        target_path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return "Solo puedo ejecutar tests del proyecto dentro del workspace."

    try:
        normalized_timeout = max(10, min(600, int(timeout_seconds)))
    except (TypeError, ValueError):
        normalized_timeout = 120

    if target_path.is_dir():
        relative_target = _workspace_relative(target_path)
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            relative_target,
            "-p",
            str(pattern).strip() or "test*.py",
        ]
    elif target_path.is_file():
        if target_path.suffix.lower() != ".py":
            return "Solo puedo ejecutar archivos de tests Python."
        module_name = target_path.relative_to(WORKSPACE_ROOT).with_suffix("").as_posix().replace("/", ".")
        command = [sys.executable, "-m", "unittest", module_name]
    else:
        return f"No es una ruta valida para tests: {cleaned_target}"

    try:
        completed = subprocess.run(
            command,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=normalized_timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            f"Los tests excedieron el timeout de {normalized_timeout} segundos.\n"
            f"Comando: {' '.join(command)}"
        )
    except OSError as exc:
        return f"No pude ejecutar los tests: {exc}"

    combined_output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if str(part).strip()
    )
    if not combined_output:
        combined_output = "La corrida no produjo salida visible."

    combined_output = _bounded_text(combined_output, MAX_TEST_OUTPUT_CHARS)
    status_line = "Tests OK." if completed.returncode == 0 else f"Tests con fallos (exit={completed.returncode})."

    return (
        f"{status_line}\n"
        f"Comando: {' '.join(command)}\n"
        f"Salida:\n{combined_output}"
    )


def run_project_check(timeout_seconds: int = 240) -> str:
    """
    Ejecuta la validacion completa del proyecto: tests Python y build del host .NET.

    Args:
        timeout_seconds (int): Timeout maximo para cada etapa.

    Returns:
        str: Resumen de la validacion completa.
    """
    try:
        normalized_timeout = max(30, min(900, int(timeout_seconds)))
    except (TypeError, ValueError):
        normalized_timeout = 240

    test_result = run_project_tests(timeout_seconds=normalized_timeout)
    tests_ok = test_result.startswith("Tests OK.")

    project_path = WORKSPACE_ROOT / "service_host" / "YarbisServiceHost.csproj"
    if not project_path.exists():
        build_result = "No encontre service_host/YarbisServiceHost.csproj."
        build_ok = False
    else:
        command = ["dotnet", "build", str(project_path.relative_to(WORKSPACE_ROOT))]
        try:
            completed = subprocess.run(
                command,
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=normalized_timeout,
            )
        except subprocess.TimeoutExpired:
            build_result = (
                f"El build .NET excedio el timeout de {normalized_timeout} segundos.\n"
                f"Comando: {' '.join(command)}"
            )
            build_ok = False
        except OSError as exc:
            build_result = f"No pude ejecutar dotnet build: {exc}"
            build_ok = False
        else:
            combined_output = "\n".join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if str(part).strip()
            )
            if not combined_output:
                combined_output = "El build no produjo salida visible."
            combined_output = _bounded_text(combined_output, MAX_TEST_OUTPUT_CHARS)
            build_ok = completed.returncode == 0
            build_status = "Build .NET OK." if build_ok else f"Build .NET con fallos (exit={completed.returncode})."
            build_result = (
                f"{build_status}\n"
                f"Comando: {' '.join(command)}\n"
                f"Salida:\n{combined_output}"
            )

    overall = "Validacion completa OK." if tests_ok and build_ok else "Validacion completa con problemas."
    return (
        f"{overall}\n\n"
        f"Python:\n{test_result}\n\n"
        f"Servicio .NET:\n{build_result}"
    )


def run_system_command(
    command: str,
    cwd: str = "",
    timeout_seconds: int = 120,
    shell: bool = True,
) -> str:
    """
    Ejecuta un comando arbitrario del sistema operativo.

    Args:
        command (str): Comando completo a ejecutar.
        cwd (str): Directorio de trabajo opcional; acepta rutas absolutas o relativas al workspace.
        timeout_seconds (int): Timeout maximo de ejecucion.
        shell (bool): Si es True, ejecuta mediante el shell del sistema.

    Returns:
        str: Codigo de salida, comando, directorio y salida acotada.
    """
    cleaned_command = str(command).strip()
    if not cleaned_command:
        return "Debes indicar el comando del sistema a ejecutar."

    if str(cwd).strip():
        working_dir, error = _resolve_workspace_path(str(cwd).strip())
        if error:
            return error
    else:
        working_dir = WORKSPACE_ROOT

    if not working_dir.exists():
        return f"El directorio de trabajo no existe: {_workspace_relative(working_dir)}"
    if not working_dir.is_dir():
        return f"El directorio de trabajo no es una carpeta: {_workspace_relative(working_dir)}"

    try:
        normalized_timeout = max(1, min(3600, int(timeout_seconds)))
    except (TypeError, ValueError):
        normalized_timeout = 120

    use_shell = bool(shell)
    args = cleaned_command
    if not use_shell:
        try:
            args = shlex.split(cleaned_command, posix=os.name != "nt")
        except ValueError as exc:
            return f"Comando invalido: {exc}"
        if not args:
            return "Debes indicar el comando del sistema a ejecutar."

    try:
        returncode, stdout, stderr, timed_out = _run_command_process(
            args,
            cwd=working_dir,
            shell=use_shell,
            timeout_seconds=normalized_timeout,
        )
    except OSError as exc:
        return f"No pude ejecutar el comando del sistema: {exc}"

    if timed_out:
        combined_output = "\n".join(
            part.strip()
            for part in (stdout, stderr)
            if str(part).strip()
        )
        output_text = (
            "\nSalida parcial:\n" + _bounded_text(combined_output, MAX_COMMAND_OUTPUT_CHARS)
            if combined_output
            else ""
        )
        return (
            f"El comando excedio el timeout de {normalized_timeout} segundos.\n"
            f"Comando: {cleaned_command}"
            f"{output_text}"
        )

    combined_output = "\n".join(
        part.strip()
        for part in (stdout, stderr)
        if str(part).strip()
    )
    if not combined_output:
        combined_output = "El comando no produjo salida visible."
    combined_output = _bounded_text(combined_output, MAX_COMMAND_OUTPUT_CHARS)
    status_line = (
        "Comando del sistema completado."
        if returncode == 0
        else f"Comando del sistema con fallos (exit={returncode})."
    )
    return (
        f"{status_line}\n"
        f"Comando: {cleaned_command}\n"
        f"Directorio: {_workspace_relative(working_dir)}\n"
        f"Salida:\n{combined_output}"
    )


def browser_automation(
    start_url: str = "",
    actions_json: str = "",
    headless: bool = True,
    timeout_seconds: int = 30,
    browser_channel: str = "msedge",
    storage_state_path: str = "",
    screenshot_path: str = "",
) -> str:
    """
    Automatiza un navegador real con Playwright.

    Args:
        start_url (str): URL inicial opcional.
        actions_json (str): Lista JSON de acciones: goto, click, fill, press, wait, wait_for_selector,
            select_option, check, uncheck, screenshot, text o evaluate.
        headless (bool): Ejecutar sin ventana visible.
        timeout_seconds (int): Timeout general para acciones.
        browser_channel (str): Canal Chromium/Edge opcional; por defecto msedge.
        storage_state_path (str): Ruta opcional para leer/guardar cookies y sesion.
        screenshot_path (str): Captura final opcional.

    Returns:
        str: Resumen de navegacion, URL final, texto/capturas solicitadas y errores.
    """
    try:
        return run_browser_automation(
            start_url=start_url,
            actions_json=actions_json,
            headless=headless,
            timeout_seconds=timeout_seconds,
            browser_channel=browser_channel,
            storage_state_path=storage_state_path,
            screenshot_path=screenshot_path,
            workspace_root=WORKSPACE_ROOT,
        )
    except Exception as exc:
        return f"No pude completar la automatizacion del navegador: {exc}"


def create_calendar_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
    output_path: str = "",
    open_file: bool = False,
) -> str:
    """
    Crea un archivo .ics compatible con calendarios del sistema.

    Args:
        title (str): Titulo del evento.
        start (str): Inicio en formato ISO, por ejemplo 2026-05-06T15:00:00.
        end (str): Fin en formato ISO.
        description (str): Descripcion opcional.
        location (str): Ubicacion opcional.
        attendees (str): Correos separados por coma, punto y coma o salto de linea.
        output_path (str): Ruta opcional del .ics.
        open_file (bool): Si es True, abre el archivo con la app predeterminada.

    Returns:
        str: Ruta del evento creado.
    """
    try:
        return create_calendar_event_file(
            title=title,
            start=start,
            end=end,
            description=description,
            location=location,
            attendees=attendees,
            output_path=output_path,
            open_file=open_file,
            workspace_root=WORKSPACE_ROOT,
        )
    except Exception as exc:
        return f"No pude crear el evento de calendario: {exc}"


def compose_email(
    to: str,
    subject: str = "",
    body: str = "",
    cc: str = "",
    bcc: str = "",
    open_client: bool = True,
) -> str:
    """
    Crea un borrador de correo mediante el cliente predeterminado del sistema.

    Args:
        to (str): Destinatarios separados por coma, punto y coma o salto de linea.
        subject (str): Asunto.
        body (str): Cuerpo.
        cc (str): Copia.
        bcc (str): Copia oculta.
        open_client (bool): Si es True, abre el cliente de correo con mailto.

    Returns:
        str: Resultado y URI mailto generado.
    """
    try:
        return compose_email_draft(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            open_client=open_client,
        )
    except Exception as exc:
        return f"No pude preparar el correo: {exc}"


def open_system_target(target: str) -> str:
    """
    Abre una ruta, URL o URI usando el manejador predeterminado del sistema.

    Args:
        target (str): Ruta local, URL o URI del sistema.

    Returns:
        str: Confirmacion de apertura.
    """
    try:
        return open_system_target_impl(target=target, workspace_root=WORKSPACE_ROOT)
    except Exception as exc:
        return f"No pude abrir el objetivo del sistema: {exc}"


def _get_internet_settings() -> dict:
    state = load_state()
    return state.get("internet", {})


def update_internet_settings(
    mode: str = "",
    provider: str = "",
    max_search_results: int = 0,
    max_page_chars: int = 0,
    request_timeout_seconds: int = 0,
    allowed_domains: str = "",
    blocked_domains: str = "",
) -> str:
    """
    Actualiza la politica de acceso a internet y los limites de busqueda del agente.

    Args:
        mode (str): Modo de internet. Valores soportados: off, auto.
        provider (str): Proveedor de busqueda. Actualmente: duckduckgo_html.
        max_search_results (int): Maximo de resultados por busqueda.
        max_page_chars (int): Maximo de caracteres legibles por pagina.
        request_timeout_seconds (int): Timeout maximo por request.
        allowed_domains (str): Lista opcional de dominios permitidos.
        blocked_domains (str): Lista opcional de dominios bloqueados.

    Returns:
        str: Resumen de la configuracion persistida.
    """
    updates = {}

    if str(mode).strip():
        cleaned_mode = str(mode).strip().lower()
        if cleaned_mode not in VALID_INTERNET_MODES:
            return (
                "Modo de internet invalido. "
                f"Valores soportados: {', '.join(sorted(VALID_INTERNET_MODES))}."
            )
        updates["mode"] = cleaned_mode

    if str(provider).strip():
        cleaned_provider = str(provider).strip().lower()
        if cleaned_provider not in VALID_SEARCH_PROVIDERS:
            return (
                "Proveedor de busqueda invalido. "
                f"Valores soportados: {', '.join(sorted(VALID_SEARCH_PROVIDERS))}."
            )
        updates["provider"] = cleaned_provider

    if max_search_results:
        try:
            updates["max_search_results"] = int(max_search_results)
        except (TypeError, ValueError):
            return "max_search_results debe ser un entero."

    if max_page_chars:
        try:
            updates["max_page_chars"] = int(max_page_chars)
        except (TypeError, ValueError):
            return "max_page_chars debe ser un entero."

    if request_timeout_seconds:
        try:
            updates["request_timeout_seconds"] = int(request_timeout_seconds)
        except (TypeError, ValueError):
            return "request_timeout_seconds debe ser un entero."

    if str(allowed_domains).strip():
        if str(allowed_domains).strip() == CLEAR_VALUE:
            updates["allowed_domains"] = []
        else:
            updates["allowed_domains"] = _split_text_items(str(allowed_domains))

    if str(blocked_domains).strip():
        if str(blocked_domains).strip() == CLEAR_VALUE:
            updates["blocked_domains"] = []
        else:
            updates["blocked_domains"] = _split_text_items(str(blocked_domains))

    def mutate(state):
        internet = state.setdefault("internet", {})
        internet.update(updates)

    state_transaction("update_internet_settings", mutate)
    refreshed = _get_internet_settings()

    lines = [
        "Configuracion de internet actualizada.",
        f"Modo: {refreshed.get('mode', '-')}",
        f"Proveedor: {refreshed.get('provider', '-')}",
        f"Max resultados: {refreshed.get('max_search_results', '-')}",
        f"Max pagina: {refreshed.get('max_page_chars', '-')} chars",
        f"Timeout: {refreshed.get('request_timeout_seconds', '-')}s",
    ]

    allowed = refreshed.get("allowed_domains", [])
    blocked = refreshed.get("blocked_domains", [])
    lines.append("Dominios permitidos: " + (", ".join(allowed) if allowed else "sin restriccion"))
    lines.append("Dominios bloqueados: " + (", ".join(blocked) if blocked else "ninguno"))
    return "\n".join(lines)


def web_search(query: str, limit: int = 5, reason: str = "") -> str:
    """
    Busca informacion publica y reciente en internet.

    Args:
        query (str): Consulta a buscar en la web.
        limit (int): Numero maximo de resultados a devolver.
        reason (str): Motivo breve de por que hace falta buscar afuera.

    Returns:
        str: Resultados resumidos con titulo, URL y snippet.
    """
    settings = _get_internet_settings()
    if settings.get("mode") != "auto":
        return (
            "La busqueda web esta desactivada por politica "
            f"(modo={settings.get('mode', 'off')}). "
            "Usa `update_internet_settings` para cambiarlo."
        )

    try:
        requested_limit = int(limit)
    except (TypeError, ValueError):
        requested_limit = 5

    effective_limit = max(
        1,
        min(requested_limit, int(settings.get("max_search_results", 5))),
    )

    try:
        results = search_public_web(
            query=query,
            limit=effective_limit,
            timeout_seconds=int(settings.get("request_timeout_seconds", 10)),
            provider=str(settings.get("provider", "duckduckgo_html")),
            allowed_domains=list(settings.get("allowed_domains", [])),
            blocked_domains=list(settings.get("blocked_domains", [])),
        )
    except Exception as exc:
        return f"No pude completar la busqueda web: {exc}"

    lines = [
        "Busqueda web completada.",
        f"Consulta: {str(query).strip()}",
        f"Proveedor: {settings.get('provider', '-')}",
        f"Resultados devueltos: {len(results)}",
    ]

    if str(reason).strip():
        lines.append(f"Motivo: {str(reason).strip()}")

    if not results:
        lines.append("No encontre resultados publicos que cumplieran la politica configurada.")
        return "\n".join(lines)

    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result['title']}")
        lines.append(f"URL: {result['url']}")
        if result.get("snippet"):
            lines.append(f"Snippet: {result['snippet']}")

    return "\n".join(lines)


def fetch_web_page(url: str, max_chars: int = 0) -> str:
    """
    Lee una pagina web publica y devuelve su texto principal en formato compacto.

    Args:
        url (str): URL publica de la pagina.
        max_chars (int): Maximo de caracteres a devolver.

    Returns:
        str: Titulo, URL final y contenido legible de la pagina.
    """
    settings = _get_internet_settings()
    if settings.get("mode") != "auto":
        return (
            "La lectura de paginas web esta desactivada por politica "
            f"(modo={settings.get('mode', 'off')}). "
            "Usa `update_internet_settings` para cambiarlo."
        )

    configured_max_chars = int(settings.get("max_page_chars", 12_000))
    try:
        requested_max_chars = int(max_chars)
    except (TypeError, ValueError):
        requested_max_chars = 0

    effective_max_chars = configured_max_chars
    if requested_max_chars > 0:
        effective_max_chars = min(requested_max_chars, configured_max_chars)

    try:
        page = fetch_public_web_page(
            url=url,
            timeout_seconds=int(settings.get("request_timeout_seconds", 10)),
            max_page_chars=effective_max_chars,
            allowed_domains=list(settings.get("allowed_domains", [])),
            blocked_domains=list(settings.get("blocked_domains", [])),
        )
    except Exception as exc:
        return f"No pude leer la pagina web: {exc}"

    lines = [
        "Pagina web obtenida.",
        f"URL final: {page['url']}",
        f"Tipo: {page.get('content_type', '-')}",
        f"Contenido completo: {'no' if page.get('truncated') else 'si'}",
    ]
    if page.get("title"):
        lines.append(f"Titulo: {page['title']}")
    lines.append("Contenido:")
    lines.append(page["content"])
    return "\n".join(lines)


def _split_text_items(value: str) -> list[str]:
    if not value:
        return []

    items = [item.strip() for item in re.split(r"[\n,;]+", value)]
    return [item for item in items if item]


def _find_task(tasks: list[dict], task_id: str) -> dict | None:
    cleaned = str(task_id).strip().lower()
    if not cleaned:
        return None

    for task in tasks:
        if task["id"].lower() == cleaned:
            return task

    prefix_matches = [task for task in tasks if task["id"].lower().startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    return None


def agent_overview() -> str:
    """
    Devuelve un resumen del estado personal y operativo del agente.

    Returns:
        str: Resumen de objetivo, perfil, plan, tareas y notas.
    """
    state = load_state()
    return render_state_summary(state, include_runtime=False)


def update_goal(new_goal: str) -> str:
    """
    Cambia el objetivo principal cuando el usuario lo pide explicitamente.

    Args:
        new_goal (str): Nuevo objetivo operativo del usuario.

    Returns:
        str: Confirmacion del objetivo actualizado.
    """
    cleaned_goal = str(new_goal).strip()
    if not cleaned_goal:
        return "El objetivo no puede quedar vacio."

    def mutate(state):
        state["goal"] = cleaned_goal
        state["messages"] = []
        state["tasks"] = []
        state["current_plan"] = []
        state["last_result"] = ""
        state["awaiting_user_input"] = {
            "pending": False,
            "question": "",
            "reason": "",
            "fields": [],
        }
        state["messages"].append({
            "role": "user",
            "content": f"Tu objetivo actual es: {cleaned_goal}",
        })

    state_transaction("update_goal", mutate)
    return "Objetivo actualizado. Contexto operativo reiniciado para el nuevo objetivo."


def self_overview(refresh: bool = False) -> str:
    """
    Devuelve lo que Yarbis sabe de si mismo, su codigo fuente y su entorno local.

    Args:
        refresh (bool): Si es True, recalcula la informacion aunque haya cache reciente.

    Returns:
        str: Resumen de identidad, codigo fuente, sistema operativo y hardware.
    """
    should_refresh = bool(refresh)
    summary = render_self_knowledge_summary(refresh=should_refresh)
    if should_refresh:
        source_signature = get_cached_source_signature()
        state_transaction(
            "self_overview",
            lambda state: state.__setitem__(
                "self_knowledge",
                {
                    "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                    "source_signature": source_signature,
                },
            ),
        )
    return summary


def list_yarbis_instances() -> str:
    """
    Lista las instancias locales de Yarbis conocidas en esta PC.

    Returns:
        str: Instancias con id, servicio, actividad y rutas principales.
    """
    instances = yarbis_instance.list_instances()
    lines = [f"Instancia actual: {yarbis_instance.current_instance_id()}"]
    active_tokens = {}
    for item in instances:
        instance_id = item["id"]
        active = yarbis_bus.instance_is_active(instance_id)
        if active:
            try:
                state_payload = json.loads(Path(item["state_file"]).read_text(encoding="utf-8"))
                telegram = state_payload.get("notifications", {}).get("telegram", {})
                token = str(telegram.get("bot_token", "")).strip()
                if token:
                    active_tokens.setdefault(token, []).append(instance_id)
            except Exception:
                pass
        lines.append(
            f"- {instance_id}: servicio={item['service_name']}, "
            f"{'activa' if active else 'inactiva'}, "
            f"estado={item['state_file']}"
        )
    duplicate_sets = [ids for ids in active_tokens.values() if len(ids) > 1]
    if duplicate_sets:
        lines.append(
            "Advertencia: hay instancias activas compartiendo el mismo bot token de Telegram: "
            + "; ".join(", ".join(ids) for ids in duplicate_sets)
        )
    return "\n".join(lines)


def send_yarbis_message(
    target_instance: str,
    message: str,
    wait_for_reply: bool = True,
    timeout_seconds: int = 120,
) -> str:
    """
    Envia un mensaje directo a otra instancia local de Yarbis.

    Args:
        target_instance (str): Id de la instancia destino.
        message (str): Mensaje directo para esa instancia.
        wait_for_reply (bool): Si True, espera respuesta cuando el destino esta activo.
        timeout_seconds (int): Tiempo maximo de espera por respuesta.

    Returns:
        str: Estado de entrega y respuesta si estuvo disponible.
    """
    try:
        result = yarbis_bus.send_message(
            target_instance,
            message,
            wait_for_reply=bool(wait_for_reply),
            timeout_seconds=int(timeout_seconds or 120),
        )
    except Exception as exc:
        return f"No pude enviar el mensaje a Yarbis: {exc}"

    status = str(result.get("status", "")).strip()
    status_text = str(result.get("status_text", "")).strip()
    target = str(result.get("to_instance", target_instance)).strip()
    message_id = str(result.get("id", "")).strip()
    if status == yarbis_bus.STATUS_DONE:
        response = str(result.get("response", "")).strip() or "Sin respuesta visible."
        return f"Mensaje entregado a {target} ({message_id}).\n\nRespuesta:\n{response}"
    if status == yarbis_bus.STATUS_ERROR:
        error = str(result.get("error", "")).strip() or "Error desconocido."
        return f"Mensaje procesado con error en {target} ({message_id}): {error}"
    if status_text == "timeout":
        return f"Mensaje enviado a {target} ({message_id}), pero no llego respuesta antes del timeout."
    return f"Mensaje en cola para {target} ({message_id})."


def read_yarbis_messages(limit: int = 20, unread_only: bool = True) -> str:
    """
    Lee mensajes directos recibidos por esta instancia de Yarbis.

    Args:
        limit (int): Numero maximo de mensajes.
        unread_only (bool): Si True, muestra solo mensajes pendientes o respuestas no leidas.

    Returns:
        str: Resumen de mensajes recibidos.
    """
    messages = yarbis_bus.list_messages(
        limit=limit,
        unread_only=bool(unread_only),
        mark_read=True,
    )
    if not messages:
        return "No hay mensajes directos para esta instancia."

    lines = []
    for message in messages:
        kind = str(message.get("kind", "")).strip()
        sender = str(message.get("from_instance", "")).strip()
        status = str(message.get("status", "")).strip()
        content = str(message.get("response") or message.get("content", "")).strip()
        lines.append(
            f"- {message.get('id', '')} de {sender} ({kind}, {status}): "
            f"{content[:600] or '-'}"
        )
    return "\n".join(lines)


def update_profile(
    name: str = "",
    role: str = "",
    preferences: str = "",
    constraints: str = "",
) -> str:
    """
    Actualiza el perfil personal del usuario para personalizar el agente.

    Args:
        name (str): Nombre del usuario o como le gusta ser llamado.
        role (str): Rol, contexto o descripcion corta del usuario.
        preferences (str): Lista separada por comas o saltos de linea.
        constraints (str): Limites o reglas separadas por comas o saltos de linea.

    Returns:
        str: Resumen del perfil actualizado.
    """
    def mutate(state):
        profile = state["profile"]

        if str(name).strip():
            profile["name"] = "" if str(name).strip() == CLEAR_VALUE else str(name).strip()

        if str(role).strip():
            profile["role"] = "" if str(role).strip() == CLEAR_VALUE else str(role).strip()

        if str(preferences).strip():
            if str(preferences).strip() == CLEAR_VALUE:
                profile["preferences"] = []
            else:
                profile["preferences"] = _split_text_items(str(preferences))

        if str(constraints).strip():
            if str(constraints).strip() == CLEAR_VALUE:
                profile["constraints"] = []
            else:
                profile["constraints"] = _split_text_items(str(constraints))

    state_transaction("update_profile", mutate)
    refreshed = load_state()
    profile = refreshed["profile"]

    return (
        "Perfil actualizado.\n"
        f"Nombre: {profile['name'] or '-'}\n"
        f"Rol: {profile['role'] or '-'}\n"
        f"Preferencias: {', '.join(profile['preferences']) if profile['preferences'] else 'Sin definir.'}\n"
        f"Restricciones: {', '.join(profile['constraints']) if profile['constraints'] else 'Sin definir.'}"
    )


def request_user_input(question: str, reason: str = "", missing_fields: str = "") -> str:
    """
    Registra una pregunta pendiente para que el usuario complete informacion faltante.

    Args:
        question (str): Pregunta clara y concreta para el usuario.
        reason (str): Motivo breve de por que hace falta esa informacion.
        missing_fields (str): Campos faltantes separados por comas o saltos de linea.

    Returns:
        str: Confirmacion de la solicitud registrada.
    """
    cleaned_question = str(question).strip()
    if not cleaned_question:
        return "Debes indicar una pregunta concreta para el usuario."

    def mutate(state):
        state["awaiting_user_input"] = {
            "pending": True,
            "question": cleaned_question,
            "reason": str(reason).strip(),
            "fields": _split_text_items(str(missing_fields)),
        }

    state_transaction("request_user_input", mutate)

    refreshed = load_state()["awaiting_user_input"]
    lines = [
        "Solicitud de informacion registrada.",
        f"Pregunta: {refreshed['question']}",
    ]

    if refreshed["reason"]:
        lines.append(f"Motivo: {refreshed['reason']}")

    if refreshed["fields"]:
        lines.append("Datos faltantes: " + ", ".join(refreshed["fields"]))

    return "\n".join(lines)


def save_note(title: str, content: str, category: str = "general") -> str:
    """
    Guarda una nota breve y persistente en la memoria del agente.

    Args:
        title (str): Titulo corto de la nota.
        content (str): Contenido de la nota.
        category (str): Categoria simple para agrupar notas.

    Returns:
        str: Confirmacion con el identificador de la nota.
    """
    if not str(title).strip() and not str(content).strip():
        return "Debes indicar al menos un titulo o contenido para la nota."

    note = {
        "id": _new_id("note"),
        "title": str(title).strip() or "Nota sin titulo",
        "content": str(content).strip(),
        "category": str(category).strip() or "general",
    }

    state_transaction("save_note", lambda state: state["notes"].append(note))

    return f"Nota guardada con id {note['id']}: {note['title']} ({note['category']})"


def _matching_notes(notes: list[dict], identifier: str) -> list[dict]:
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        return []

    normalized_identifier = cleaned_identifier.casefold()
    exact_matches = [
        note for note in notes
        if note["id"].casefold() == normalized_identifier
        or note["title"].casefold() == normalized_identifier
    ]
    if exact_matches:
        return exact_matches

    return [
        note for note in notes
        if normalized_identifier in note["title"].casefold()
    ]


def _ambiguous_note_message(matches: list[dict]) -> str:
    lines = [
        "Encontre varias notas que coinciden. Usa el id exacto para elegir una:"
    ]
    for note in matches[:10]:
        lines.append(f"- [{note['id']}] {note['title']} ({note['category']})")
    return "\n".join(lines)


def list_notes(category: str = "", limit: int = 10) -> str:
    """
    Lista notas persistentes del agente.

    Args:
        category (str): Categoria opcional para filtrar.
        limit (int): Maximo de notas a devolver.

    Returns:
        str: Listado resumido de notas.
    """
    state = load_state()
    notes = state["notes"]

    if str(category).strip():
        category_filter = str(category).strip().casefold()
        notes = [note for note in notes if note["category"].casefold() == category_filter]

    if not notes:
        return "No hay notas que coincidan."

    try:
        normalized_limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10

    lines = []
    for note in reversed(notes[-normalized_limit:]):
        preview = note["content"][:180]
        if len(note["content"]) > 180:
            preview += "..."
        lines.append(f"[{note['id']}] {note['title']} ({note['category']}): {preview}")

    return "\n".join(lines)


def get_note(identifier: str) -> str:
    """
    Muestra el contenido completo de una nota por id o titulo.

    Args:
        identifier (str): Id exacto, titulo exacto o fragmento unico del titulo.

    Returns:
        str: Nota completa o mensaje de ayuda si no se encuentra.
    """
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        return "Indica el id o titulo de la nota que quieres ver."

    state = load_state()
    matches = _matching_notes(state["notes"], cleaned_identifier)
    if not matches:
        return f"No encontre una nota que coincida con '{cleaned_identifier}'."
    if len(matches) > 1:
        return _ambiguous_note_message(matches)

    note = matches[0]
    content = note["content"] or "Sin contenido."
    return (
        f"Nota [{note['id']}]\n"
        f"Titulo: {note['title']}\n"
        f"Categoria: {note['category']}\n\n"
        f"{content}"
    )


def delete_note(identifier: str) -> str:
    """
    Elimina una nota persistente por id o titulo.

    Args:
        identifier (str): Id exacto, titulo exacto o fragmento unico del titulo.

    Returns:
        str: Confirmacion o mensaje de ayuda si no se puede eliminar.
    """
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        return "Indica el id o titulo de la nota que quieres eliminar."

    def mutate(state):
        matches = _matching_notes(state["notes"], cleaned_identifier)
        if not matches:
            return f"No encontre una nota que coincida con '{cleaned_identifier}'."
        if len(matches) > 1:
            return _ambiguous_note_message(matches)

        note = matches[0]
        state["notes"] = [
            existing_note for existing_note in state["notes"]
            if existing_note["id"] != note["id"]
        ]
        return f"Nota eliminada: [{note['id']}] {note['title']} ({note['category']})"

    return state_transaction("delete_note", mutate)


def add_task(title: str, details: str = "", priority: str = "media") -> str:
    """
    Crea una tarea en la cola de trabajo del agente.

    Args:
        title (str): Titulo breve y accionable.
        details (str): Contexto adicional o criterio de exito.
        priority (str): alta, media o baja.

    Returns:
        str: Confirmacion con el identificador de la tarea.
    """
    cleaned_title = str(title).strip()
    if not cleaned_title:
        return "Debes indicar un titulo para la tarea."

    cleaned_priority = str(priority).strip().lower() or "media"
    if cleaned_priority not in VALID_TASK_PRIORITY:
        cleaned_priority = "media"

    task = {
        "id": _new_id("task"),
        "title": cleaned_title,
        "details": str(details).strip(),
        "status": "pending",
        "priority": cleaned_priority,
        "result": "",
    }
    state_transaction("add_task", lambda state: state["tasks"].append(task))

    return (
        f"Tarea creada con id {task['id']}.\n"
        f"Titulo: {task['title']}\n"
        f"Prioridad: {task['priority']}\n"
        f"Detalles: {task['details'] or 'Sin detalles adicionales.'}"
    )


def list_tasks(status: str = "all", limit: int = 20) -> str:
    """
    Lista tareas registradas por el agente.

    Args:
        status (str): all, pending, in_progress, blocked o done.
        limit (int): Maximo de tareas a mostrar.

    Returns:
        str: Listado legible de tareas.
    """
    state = load_state()
    tasks = state["tasks"]
    cleaned_status = str(status).strip().lower() or "all"

    if cleaned_status != "all":
        tasks = [task for task in tasks if task["status"] == cleaned_status]

    if not tasks:
        return "No hay tareas que coincidan."

    try:
        normalized_limit = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 20

    lines = []
    for task in tasks[:normalized_limit]:
        detail_preview = task["details"][:140]
        if len(task["details"]) > 140:
            detail_preview += "..."
        result_preview = task["result"][:140]
        if len(task["result"]) > 140:
            result_preview += "..."

        suffix = f" Resultado: {result_preview}" if result_preview else ""
        lines.append(
            f"[{task['id']}] {task['title']} "
            f"(estado={task['status']}, prioridad={task['priority']}) "
            f"- {detail_preview or 'Sin detalles.'}{suffix}"
        )

    return "\n".join(lines)


def update_task_status(task_id: str, status: str, result: str = "") -> str:
    """
    Cambia el estado de una tarea y opcionalmente registra su resultado.

    Args:
        task_id (str): Id completo o prefijo unico de la tarea.
        status (str): pending, in_progress, blocked o done.
        result (str): Resultado breve o motivo del cambio.

    Returns:
        str: Confirmacion del cambio aplicado.
    """
    cleaned_status = str(status).strip().lower()
    if cleaned_status not in VALID_TASK_STATUS:
        return (
            "Estado invalido. Usa uno de: "
            + ", ".join(sorted(VALID_TASK_STATUS))
        )

    def mutate(state):
        task = _find_task(state["tasks"], task_id)
        if not task:
            return f"No encontre una tarea con id o prefijo: {task_id}"

        task["status"] = cleaned_status
        if str(result).strip():
            task["result"] = str(result).strip()

        return (
            f"Tarea actualizada: {task['id']}\n"
            f"Titulo: {task['title']}\n"
            f"Nuevo estado: {task['status']}\n"
            f"Resultado: {task['result'] or 'Sin resultado registrado.'}"
        )

    return state_transaction("update_task_status", mutate)


def set_plan(plan_text: str = "") -> str:
    """
    Define el plan actual del agente como una lista corta de pasos.

    Args:
        plan_text (str): Pasos separados por lineas, comas o punto y coma.

    Returns:
        str: Resumen del plan guardado.
    """
    plan_items = _split_text_items(str(plan_text))
    state_transaction("set_plan", lambda state: state.__setitem__("current_plan", plan_items))

    if not plan_items:
        return "Plan actual borrado."

    rendered_items = "\n".join(
        f"{index}. {item}" for index, item in enumerate(plan_items, start=1)
    )
    return f"Plan actualizado.\n{rendered_items}"


def _social_settings(state: dict) -> dict:
    return state.get("social", {}).get("settings", {})


def _social_account_key(account: dict) -> tuple[str, str, str]:
    return (
        str(account.get("platform", "")).strip().lower(),
        str(account.get("account_type", "")).strip().lower(),
        str(account.get("external_id", "")).strip(),
    )


def _find_social_account(state: dict, account_id: str) -> dict | None:
    cleaned = str(account_id).strip().lower()
    if not cleaned:
        return None
    accounts = state.get("social", {}).get("accounts", [])
    for account in accounts:
        if str(account.get("id", "")).lower() == cleaned:
            return account
    matches = [
        account for account in accounts
        if str(account.get("id", "")).lower().startswith(cleaned)
    ]
    return matches[0] if len(matches) == 1 else None


def _find_social_draft(state: dict, draft_id: str) -> dict | None:
    cleaned = str(draft_id).strip().lower()
    if not cleaned:
        return None
    drafts = state.get("social", {}).get("drafts", [])
    for draft in drafts:
        if str(draft.get("id", "")).lower() == cleaned:
            return draft
    matches = [
        draft for draft in drafts
        if str(draft.get("id", "")).lower().startswith(cleaned)
    ]
    return matches[0] if len(matches) == 1 else None


def _find_social_publication(state: dict, publication_id: str) -> dict | None:
    cleaned = str(publication_id).strip().lower()
    if not cleaned:
        return None
    publications = state.get("social", {}).get("pending_publications", [])
    for publication in publications:
        if str(publication.get("id", "")).lower() == cleaned:
            return publication
    matches = [
        publication for publication in publications
        if str(publication.get("id", "")).lower().startswith(cleaned)
    ]
    return matches[0] if len(matches) == 1 else None


def _body_with_hashtags(publication: dict) -> str:
    body = str(publication.get("body", "")).strip()
    tags = []
    for tag in publication.get("hashtags", []):
        cleaned = str(tag).strip()
        if not cleaned:
            continue
        tags.append(cleaned if cleaned.startswith("#") else f"#{cleaned}")
    if tags:
        body = f"{body}\n\n{' '.join(tags)}".strip()
    return body


def _copy_text_to_clipboard(text: str) -> str:
    rendered = str(text)
    if not rendered:
        return "No habia texto para copiar al portapapeles."

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(rendered)
        root.update()
        root.destroy()
        return "Texto copiado al portapapeles."
    except Exception:
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["clip"],
                    input=rendered,
                    text=True,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return "Texto copiado al portapapeles."
            except Exception as exc:
                return f"No pude copiar al portapapeles: {exc}"
        return "No pude copiar al portapapeles en este entorno."


def _render_social_publication_preview(publication: dict) -> str:
    text = _body_with_hashtags(publication)
    lines = [
        f"[{publication['id']}] {publication['title']}",
        f"Plataforma: {publication['platform']}",
        f"Destino: {publication.get('target_label') or publication.get('target_account_id') or 'sin destino'}",
        f"Estado: {publication['status']}",
        f"Confirmacion: {publication['confirmation_phrase']}",
    ]
    if publication.get("scheduled_for"):
        lines.append(f"Programado para: {publication['scheduled_for']}")
    if publication.get("link_url"):
        lines.append(f"Link: {publication['link_url']}")
    if publication.get("media_url"):
        lines.append(f"Media URL: {publication['media_url']}")
    if publication.get("media_path"):
        lines.append(f"Media local: {publication['media_path']}")
    lines.append("Copy:")
    lines.append(_preview_text(text, 1_200))
    return "\n".join(lines)


def social_accounts_overview() -> str:
    """
    Muestra cuentas sociales conectadas, drafts y publicaciones pendientes.

    Returns:
        str: Resumen de cuentas, drafts y pendientes de redes sociales.
    """
    state = load_state()
    social = state.get("social", {})
    accounts = social.get("accounts", [])
    drafts = social.get("drafts", [])
    pending = social.get("pending_publications", [])

    lines = [
        "Redes sociales:",
        f"Cuentas conectadas: {len(accounts)}",
        f"Drafts: {len(drafts)}",
        f"Publicaciones pendientes: {len(pending)}",
    ]
    if accounts:
        lines.append("Cuentas:")
        for account in accounts:
            mode = "asistido" if account.get("platform") == "facebook_personal" else "API"
            lines.append(
                f"- [{account['id']}] {account['display_name']} "
                f"({account['account_type']}, {mode})"
            )
    if pending:
        lines.append("Pendientes:")
        for publication in pending[:10]:
            lines.append(
                f"- [{publication['id']}] {publication['platform']} -> "
                f"{publication.get('target_label') or publication.get('target_account_id') or 'sin destino'}; "
                f"confirmar con {publication['confirmation_phrase']}"
            )
    return "\n".join(lines)


def start_social_oauth(
    provider: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "",
    scopes: str = "",
    authorization_response_url: str = "",
    open_browser: bool = True,
    timeout_seconds: int = 180,
) -> str:
    """
    Conecta cuentas Meta o LinkedIn mediante OAuth local.

    Args:
        provider (str): meta o linkedin.
        client_id (str): Client/App ID de la app del proveedor.
        client_secret (str): Client/App Secret de la app del proveedor.
        redirect_uri (str): Callback registrado. Si se omite usa loopback local.
        scopes (str): Scopes opcionales separados por espacios o comas.
        authorization_response_url (str): URL de callback pegada manualmente para completar OAuth.
        open_browser (bool): Si debe abrir el navegador local para autorizar.
        timeout_seconds (int): Segundos para esperar el callback local.

    Returns:
        str: Resumen de conexion o instrucciones para completar OAuth.
    """
    state = load_state()
    settings = _social_settings(state)
    try:
        result = connect_social_account(
            provider=provider,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
            authorization_response_url=authorization_response_url,
            open_browser=bool(open_browser),
            timeout_seconds=int(timeout_seconds),
            meta_graph_version=settings.get("meta_graph_version", "v24.0"),
            linkedin_version=settings.get("linkedin_version", "202604"),
        )
    except (SocialOAuthError, ValueError) as exc:
        return f"No pude conectar la cuenta social: {exc}"

    if result.get("status") == "authorization_required":
        return (
            "Autorizacion social pendiente.\n"
            f"URL: {result.get('authorization_url')}\n"
            "Despues de autorizar, ejecuta de nuevo start_social_oauth con authorization_response_url."
        )

    discovered_accounts = result.get("accounts", [])
    stored_accounts = []
    for discovered in discovered_accounts:
        token = str(discovered.pop("token", "")).strip()
        token_ref = ""
        if token:
            try:
                token_ref = save_secret(
                    token,
                    kind=f"social-{discovered.get('platform', 'account')}",
                    metadata={
                        "provider": str(provider).strip().lower(),
                        "account_type": discovered.get("account_type", ""),
                        "external_id": discovered.get("external_id", ""),
                    },
                )
            except CredentialStoreError as exc:
                return f"No pude guardar la credencial social: {exc}"
        stored_accounts.append({
            "id": _new_id("social-account"),
            "platform": discovered.get("platform", ""),
            "account_type": discovered.get("account_type", ""),
            "display_name": discovered.get("display_name", ""),
            "external_id": discovered.get("external_id", ""),
            "token_ref": token_ref,
            "scopes": discovered.get("scopes", []),
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": discovered.get("expires_at", ""),
            "metadata": discovered.get("metadata", {}),
        })

    def mutate(current_state):
        social = current_state.setdefault("social", {})
        accounts = social.setdefault("accounts", [])
        existing_by_key = {_social_account_key(account): account for account in accounts}
        added = 0
        updated = 0
        for account in stored_accounts:
            key = _social_account_key(account)
            existing = existing_by_key.get(key)
            if existing:
                account["id"] = existing["id"]
                existing.update(account)
                updated += 1
            else:
                accounts.append(account)
                existing_by_key[key] = account
                added += 1
        return added, updated

    added, updated = state_transaction("start_social_oauth", mutate)
    lines = [
        "Cuentas sociales conectadas.",
        f"Nuevas: {added}",
        f"Actualizadas: {updated}",
    ]
    for account in stored_accounts:
        lines.append(
            f"- {account['display_name']} ({account['account_type']}, "
            f"{'asistido' if account['platform'] == 'facebook_personal' else 'API'})"
        )
    return "\n".join(lines)


def save_social_draft(
    title: str,
    platform: str,
    body: str,
    target_account_id: str = "",
    link_url: str = "",
    media_url: str = "",
    media_path: str = "",
    media_type: str = "",
    alt_text: str = "",
    hashtags: str = "",
    scheduled_for: str = "",
) -> str:
    """
    Guarda un draft de contenido para redes sociales.

    Args:
        title (str): Titulo interno del draft.
        platform (str): facebook_page, facebook_personal, instagram o linkedin.
        body (str): Copy principal.
        target_account_id (str): Cuenta destino opcional.
        link_url (str): Link asociado.
        media_url (str): URL publica de imagen/video.
        media_path (str): Ruta local de media cuando el proveedor lo soporte.
        media_type (str): image, video, reel o story.
        alt_text (str): Texto alternativo.
        hashtags (str): Hashtags separados por coma, punto y coma o salto de linea.
        scheduled_for (str): Fecha/hora deseada en texto ISO o natural.

    Returns:
        str: Confirmacion del draft guardado.
    """
    cleaned_platform = str(platform).strip().lower()
    if cleaned_platform not in VALID_SOCIAL_PLATFORMS:
        return "Plataforma social invalida. Usa facebook_page, facebook_personal, instagram o linkedin."
    if not str(body).strip() and not str(link_url).strip() and not str(media_url).strip() and not str(media_path).strip():
        return "El draft necesita copy, link o media."

    draft = {
        "id": _new_id("draft"),
        "title": str(title).strip() or "Draft social",
        "platform": cleaned_platform,
        "target_account_id": str(target_account_id).strip(),
        "body": str(body).strip(),
        "link_url": str(link_url).strip(),
        "media_url": str(media_url).strip(),
        "media_path": str(media_path).strip(),
        "media_type": str(media_type).strip().lower(),
        "alt_text": str(alt_text).strip(),
        "hashtags": _split_text_items(str(hashtags)),
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scheduled_for": str(scheduled_for).strip(),
        "metadata": {},
    }
    state_transaction("save_social_draft", lambda state: state.setdefault("social", {}).setdefault("drafts", []).append(draft))
    return f"Draft social guardado con id {draft['id']}: {draft['title']} ({draft['platform']})"


def list_social_drafts(status: str = "all", limit: int = 10) -> str:
    """
    Lista drafts sociales guardados.

    Args:
        status (str): all, draft, pending, published, failed o archived.
        limit (int): Maximo de drafts a mostrar.

    Returns:
        str: Lista resumida de drafts.
    """
    state = load_state()
    drafts = state.get("social", {}).get("drafts", [])
    cleaned_status = str(status).strip().lower() or "all"
    if cleaned_status != "all":
        drafts = [draft for draft in drafts if draft.get("status") == cleaned_status]
    if not drafts:
        return "No hay drafts sociales que coincidan."
    try:
        normalized_limit = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10
    lines = []
    for draft in reversed(drafts[-normalized_limit:]):
        preview = _preview_text(_body_with_hashtags(draft), 220).replace("\n", " ")
        lines.append(
            f"[{draft['id']}] {draft['title']} "
            f"({draft['platform']}, estado={draft['status']}): {preview}"
        )
    return "\n".join(lines)


def list_social_publications(status: str = "all", limit: int = 10) -> str:
    """
    Lista publicaciones sociales pendientes o historicas.

    Args:
        status (str): all, pending_confirmation, published, failed o assisted_opened.
        limit (int): Maximo de publicaciones a mostrar.

    Returns:
        str: Lista resumida de publicaciones.
    """
    state = load_state()
    social = state.get("social", {})
    publications = list(social.get("pending_publications", [])) + list(social.get("history", []))
    cleaned_status = str(status).strip().lower() or "all"
    if cleaned_status != "all":
        publications = [publication for publication in publications if publication.get("status") == cleaned_status]
    if not publications:
        return "No hay publicaciones sociales que coincidan."
    try:
        normalized_limit = max(1, min(30, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10
    lines = []
    for publication in reversed(publications[-normalized_limit:]):
        lines.append(
            f"[{publication['id']}] {publication['title']} "
            f"({publication['platform']}, estado={publication['status']}) "
            f"confirmacion={publication['confirmation_phrase']}"
        )
    return "\n".join(lines)


def prepare_social_publication(
    draft_id: str = "",
    platform: str = "",
    target_account_id: str = "",
    body: str = "",
    title: str = "",
    link_url: str = "",
    media_url: str = "",
    media_path: str = "",
    media_type: str = "",
    alt_text: str = "",
    hashtags: str = "",
    scheduled_for: str = "",
) -> str:
    """
    Prepara una publicacion social y deja una confirmacion pendiente.

    Args:
        draft_id (str): Draft existente opcional.
        platform (str): Plataforma si no se usa draft.
        target_account_id (str): Cuenta destino para publicacion por API.
        body (str): Copy si no se usa draft.
        title (str): Titulo interno.
        link_url (str): Link asociado.
        media_url (str): URL publica de media.
        media_path (str): Ruta local de media.
        media_type (str): image, video, reel o story.
        alt_text (str): Texto alternativo.
        hashtags (str): Hashtags separados por coma, punto y coma o salto de linea.
        scheduled_for (str): Fecha/hora deseada.

    Returns:
        str: Preview y frase exacta de confirmacion.
    """
    state = load_state()
    draft = _find_social_draft(state, draft_id) if str(draft_id).strip() else None
    if str(draft_id).strip() and not draft:
        return f"No encontre un draft social con id o prefijo: {draft_id}"

    source = draft or {
        "title": title,
        "platform": platform,
        "target_account_id": target_account_id,
        "body": body,
        "link_url": link_url,
        "media_url": media_url,
        "media_path": media_path,
        "media_type": media_type,
        "alt_text": alt_text,
        "hashtags": _split_text_items(str(hashtags)),
        "scheduled_for": scheduled_for,
    }
    cleaned_platform = str(source.get("platform", "")).strip().lower()
    if cleaned_platform not in VALID_SOCIAL_PLATFORMS:
        return "Plataforma social invalida. Usa facebook_page, facebook_personal, instagram o linkedin."

    account = None
    cleaned_target = str(target_account_id or source.get("target_account_id", "")).strip()
    if cleaned_platform != "facebook_personal":
        account = _find_social_account(state, cleaned_target)
        if not account:
            return "Para publicar por API necesitas indicar target_account_id de una cuenta conectada."
        if str(account.get("platform", "")).strip().lower() != cleaned_platform:
            return "La cuenta destino no coincide con la plataforma del draft."
    elif cleaned_target:
        account = _find_social_account(state, cleaned_target)

    publication_id = _new_id("pub")
    target_label = (
        str(account.get("display_name", "")).strip()
        if account
        else "Facebook personal (asistido)"
    )
    publication = {
        "id": publication_id,
        "draft_id": str(draft.get("id", "") if draft else "").strip(),
        "title": str(source.get("title", "")).strip() or "Publicacion social",
        "platform": cleaned_platform,
        "target_account_id": str(account.get("id", "") if account else "").strip(),
        "target_label": target_label,
        "body": str(source.get("body", "")).strip(),
        "link_url": str(source.get("link_url", "")).strip(),
        "media_url": str(source.get("media_url", "")).strip(),
        "media_path": str(source.get("media_path", "")).strip(),
        "media_type": str(source.get("media_type", "")).strip().lower(),
        "alt_text": str(source.get("alt_text", "")).strip(),
        "hashtags": list(source.get("hashtags", [])),
        "status": "pending_confirmation",
        "confirmation_phrase": f"PUBLICAR {publication_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scheduled_for": str(source.get("scheduled_for", "")).strip(),
        "published_at": "",
        "external_post_id": "",
        "last_error": "",
        "metadata": {"confirmation_required": True},
    }

    if not _body_with_hashtags(publication) and not publication["link_url"] and not publication["media_url"] and not publication["media_path"]:
        return "La publicacion necesita copy, link o media."

    def mutate(current_state):
        social = current_state.setdefault("social", {})
        social.setdefault("pending_publications", []).append(publication)
        if publication["draft_id"]:
            existing_draft = _find_social_draft(current_state, publication["draft_id"])
            if existing_draft:
                existing_draft["status"] = "pending"

    state_transaction("prepare_social_publication", mutate)
    return (
        "Publicacion social preparada. No se publicara sin confirmacion exacta.\n\n"
        f"{_render_social_publication_preview(publication)}"
    )


def _open_assisted_publication(publication: dict, copy_to_clipboard: bool, open_browser: bool) -> str:
    text = _body_with_hashtags(publication)
    share_url = facebook_assisted_url(publication.get("link_url", ""))
    lines = [
        "Publicacion asistida para Facebook personal.",
        "No hice ningun POST automatico ni pulse el boton final de publicar.",
    ]
    if copy_to_clipboard:
        lines.append(_copy_text_to_clipboard(text))
    if open_browser:
        lines.append(open_system_target_impl(share_url))
    lines.append(f"URL asistida: {share_url}")
    return "\n".join(lines)


def confirm_social_publication(publication_id: str, confirmation: str) -> str:
    """
    Publica una pieza social solo si la confirmacion exacta coincide.

    Args:
        publication_id (str): Id o prefijo unico de la publicacion preparada.
        confirmation (str): Debe ser exactamente PUBLICAR <id>.

    Returns:
        str: Resultado de publicacion o bloqueo de seguridad.
    """
    state = load_state()
    publication = _find_social_publication(state, publication_id)
    if not publication:
        return f"No encontre una publicacion social pendiente con id o prefijo: {publication_id}"

    required = f"PUBLICAR {publication['id']}"
    if str(confirmation).strip() != required:
        return (
            "Publicacion bloqueada por seguridad.\n"
            f"Para confirmar, responde exactamente: {required}"
        )

    platform = str(publication.get("platform", "")).strip().lower()
    if platform == "facebook_personal":
        result = _open_assisted_publication(publication, copy_to_clipboard=True, open_browser=True)

        def mark_assisted(current_state):
            social = current_state.setdefault("social", {})
            current_publication = _find_social_publication(current_state, publication["id"])
            if current_publication:
                current_publication["status"] = "assisted_opened"
                current_publication["published_at"] = datetime.now(timezone.utc).isoformat()
                social["pending_publications"] = [
                    item for item in social.get("pending_publications", [])
                    if item.get("id") != current_publication["id"]
                ]
                social.setdefault("history", []).append(current_publication)

        state_transaction("confirm_social_publication_assisted", mark_assisted)
        return result

    account = _find_social_account(state, publication.get("target_account_id", ""))
    if not account:
        return "No encontre la cuenta destino para publicar."
    token_ref = str(account.get("token_ref", "")).strip()
    if not token_ref:
        return "La cuenta destino no tiene credencial guardada."
    try:
        token = load_secret(token_ref)
        publish_result = publish_publication(account, token, publication, _social_settings(state))
    except (CredentialStoreError, SocialPublishError, OSError) as exc:
        error_text = str(exc)

        def mark_failed(current_state):
            current_publication = _find_social_publication(current_state, publication["id"])
            if current_publication:
                current_publication["status"] = "failed"
                current_publication["last_error"] = error_text

        state_transaction("confirm_social_publication_failed", mark_failed)
        return f"No pude publicar la pieza social: {error_text}"

    external_id = str(publish_result.get("external_post_id", "")).strip()

    def mark_published(current_state):
        social = current_state.setdefault("social", {})
        current_publication = _find_social_publication(current_state, publication["id"])
        if current_publication:
            current_publication["status"] = "published"
            current_publication["published_at"] = datetime.now(timezone.utc).isoformat()
            current_publication["external_post_id"] = external_id
            current_publication["metadata"] = {
                **current_publication.get("metadata", {}),
                "provider_result": publish_result.get("platform", platform),
            }
            social["pending_publications"] = [
                item for item in social.get("pending_publications", [])
                if item.get("id") != current_publication["id"]
            ]
            social.setdefault("history", []).append(current_publication)
            if current_publication.get("draft_id"):
                existing_draft = _find_social_draft(current_state, current_publication["draft_id"])
                if existing_draft:
                    existing_draft["status"] = "published"

    state_transaction("confirm_social_publication_published", mark_published)
    return (
        "Publicacion social completada.\n"
        f"Plataforma: {platform}\n"
        f"Id externo: {external_id or '-'}"
    )


def open_assisted_social_post(
    publication_id: str = "",
    draft_id: str = "",
    body: str = "",
    link_url: str = "",
    hashtags: str = "",
    copy_to_clipboard: bool = True,
    open_browser: bool = True,
) -> str:
    """
    Abre el flujo asistido para publicar en perfil personal de Facebook.

    Args:
        publication_id (str): Publicacion preparada opcional.
        draft_id (str): Draft social opcional.
        body (str): Copy directo si no se usa publicacion ni draft.
        link_url (str): Link para Share Dialog.
        hashtags (str): Hashtags separados por coma, punto y coma o salto de linea.
        copy_to_clipboard (bool): Copiar copy al portapapeles.
        open_browser (bool): Abrir Facebook o Share Dialog.

    Returns:
        str: Resultado del flujo asistido.
    """
    state = load_state()
    publication = None
    if str(publication_id).strip():
        publication = _find_social_publication(state, publication_id)
        if not publication:
            return f"No encontre una publicacion social con id o prefijo: {publication_id}"
    elif str(draft_id).strip():
        draft = _find_social_draft(state, draft_id)
        if not draft:
            return f"No encontre un draft social con id o prefijo: {draft_id}"
        publication = {
            "id": draft["id"],
            "title": draft["title"],
            "platform": "facebook_personal",
            "body": draft["body"],
            "link_url": draft["link_url"],
            "hashtags": draft.get("hashtags", []),
        }
    else:
        publication = {
            "id": "manual",
            "title": "Publicacion asistida",
            "platform": "facebook_personal",
            "body": str(body).strip(),
            "link_url": str(link_url).strip(),
            "hashtags": _split_text_items(str(hashtags)),
        }

    result = _open_assisted_publication(
        publication,
        copy_to_clipboard=bool(copy_to_clipboard),
        open_browser=bool(open_browser),
    )

    if str(publication_id).strip():
        def mark_opened(current_state):
            current_publication = _find_social_publication(current_state, publication["id"])
            if current_publication:
                current_publication["status"] = "assisted_opened"
                current_publication["published_at"] = datetime.now(timezone.utc).isoformat()

        state_transaction("open_assisted_social_post", mark_opened)

    return result
