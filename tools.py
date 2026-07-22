import ast
import difflib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from atomic_io import atomic_replace, read_json_bom_safe
from browser_automation import (
    _confirm_satisfies,
    act_on_live_page,
    observe_browser,
    open_persistent_browser,
    run_browser_automation,
)
import computer_control
import mcp_client
from credential_store import CredentialStoreError, load_secret, save_secret
from process_utils import no_window_creationflags
from integrations import (
    compose_email_draft,
    create_calendar_event_file,
    open_system_target as open_system_target_impl,
)
from internet import fetch_web_page as fetch_public_web_page
from internet import search_web as search_public_web
import memory_transfer
import vision
import yarbis_bus
import yarbis_instance
from memory import (
    EVOLUTION_SUGGESTION_KINDS,
    MAX_EVOLUTION_DIRECTIVE_CHARS,
    MAX_EVOLUTION_DIRECTIVE_REASON_CHARS,
    MAX_EVOLUTION_DIRECTIVES,
    MAX_EVOLUTION_SUGGESTION_CHARS,
    MAX_EVOLUTION_SUGGESTIONS,
    MAX_VISION_MODEL_CHARS,
    MAX_VISUAL_BOARDS_PER_PROJECT,
    DEFAULT_SELF_INSIGHT_CATEGORY,
    VALID_SELF_INSIGHT_CATEGORIES,
    VALID_IDEA_PROJECT_KIND,
    VALID_IDEA_PROJECT_STATUS,
    VALID_INTERNET_MODES,
    VALID_SEARCH_PROVIDERS,
    VALID_SOCIAL_PLATFORMS,
    VALID_TASK_PRIORITY,
    VALID_TASK_STATUS,
    VALID_VISUAL_BOARD_KIND,
    load_state,
    memory_protection_status as memory_protection_status_data,
    render_state_summary,
    state_transaction,
    verify_memory_backups as verify_memory_backups_data,
)
import self_changes
from self_knowledge import get_cached_source_signature, render_self_knowledge_summary
from social_oauth import SocialOAuthError, connect_social_account
from social_publishing import SocialPublishError, facebook_assisted_url, publish_publication

WORKSPACE_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = yarbis_instance.runtime_dir()
CHECKPOINTS_DIR = WORKSPACE_ROOT / ".yarbis_checkpoints"
CODING_PROPOSALS_DIR = RUNTIME_DIR / "coding_proposals"
VISUAL_BOARDS_DIR = RUNTIME_DIR / "visual_boards"
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
MAX_CODING_SEARCH_RESULTS = 100
MAX_CODING_READ_RANGE_LINES = 500
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
    if workspace_text:
        workspace_path = Path(workspace_text).resolve()
    else:
        # Gate hermetico: aunque una instancia no tenga workspace de coding
        # configurado, protegemos el propio repo de Yarbis para que ningun pulso
        # reescriba su codigo sin pasar por una propuesta aprobada.
        workspace_path = WORKSPACE_ROOT.resolve()

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
    return no_window_creationflags()


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
    atomic_replace(tmp_path, target_path)


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


def _python_syntax_error(file_path: Path, content: str) -> str | None:
    """Si file_path es .py y content tiene un error de sintaxis, devuelve el mensaje.

    Evita que se escriba Python roto que romperia el arranque de Yarbis (una sola
    instancia con un SyntaxError tumba a todas las que importan ese modulo).
    """
    if str(file_path).lower().endswith(".py"):
        try:
            ast.parse(content, filename=str(file_path))
        except SyntaxError as exc:
            return (
                f"BLOQUEADO: el contenido tiene un error de sintaxis de Python y no se escribio "
                f"({_workspace_relative(file_path)}): linea {exc.lineno}: {exc.msg}. "
                "Corrige el codigo y reintenta; no se toco el archivo."
            )
    return None


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

    syntax_error = _python_syntax_error(file_path, content)
    if syntax_error:
        return syntax_error

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

    self_changes.record_change(
        file_path=file_path,
        action="write" if existed_before else "create",
        reason="write_text_file",
        checkpoint_id=checkpoint_id or "",
        diff_preview=diff_preview,
    )

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


def coding_read_text_range(path: str, start_line: int = 1, line_count: int = 120) -> str:
    """
    Lee un rango de lineas dentro de un archivo del workspace de codigo activo.

    Args:
        path (str): Archivo relativo o absoluto dentro del workspace de codigo.
        start_line (int): Linea inicial, basada en 1.
        line_count (int): Cantidad maxima de lineas a devolver.

    Returns:
        str: Lineas numeradas del archivo.
    """
    file_path, relative_path, error = _resolve_coding_path(path)
    if error:
        return error
    if not file_path.exists():
        return f"No existe el archivo dentro del workspace de codigo: {path}"
    if not file_path.is_file():
        return f"No es un archivo valido dentro del workspace de codigo: {path}"

    try:
        normalized_start = max(1, int(start_line))
    except (TypeError, ValueError):
        return "start_line debe ser un entero."
    try:
        normalized_count = max(1, min(MAX_CODING_READ_RANGE_LINES, int(line_count)))
    except (TypeError, ValueError):
        return "line_count debe ser un entero."

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return f"Error leyendo archivo de codigo: {exc}"

    total_lines = len(lines)
    if normalized_start > max(1, total_lines):
        return (
            f"Rango fuera del archivo: {relative_path.as_posix()} tiene {total_lines} linea(s); "
            f"start_line={normalized_start}."
        )

    end_line = min(total_lines, normalized_start + normalized_count - 1)
    width = len(str(max(end_line, 1)))
    rendered = [
        f"{line_number:>{width}}: {lines[line_number - 1]}"
        for line_number in range(normalized_start, end_line + 1)
    ]
    if end_line < total_lines:
        rendered.append(f"... {total_lines - end_line} linea(s) mas.")
    return (
        f"Archivo: {relative_path.as_posix()}\n"
        f"Lineas: {normalized_start}-{end_line} de {total_lines}\n"
        + "\n".join(rendered)
    )


def _python_search_fallback(
    pattern: str,
    search_path: Path,
    relative_path: Path,
    glob: str,
    context_lines: int,
    max_results: int,
) -> str:
    '''
    Busqueda de respaldo en Python puro cuando ripgrep no esta disponible.
    '''
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f'Patron de busqueda invalido: {exc}'

    glob_pattern = str(glob).strip()

    def should_skip(file_path: Path) -> bool:
        rel_parts = file_path.relative_to(search_path).parts
        return any(part.startswith('.') or part == '__pycache__' for part in rel_parts)

    output_lines: list[str] = []
    total_matches = 0
    truncated = False

    if search_path.is_file():
        files = [search_path]
    else:
        files = sorted(p for p in search_path.rglob('*') if p.is_file())

    for file_path in files:
        if should_skip(file_path):
            continue
        if glob_pattern and not file_path.relative_to(search_path).match(glob_pattern):
            continue
        try:
            text = file_path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        lines = text.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not regex.search(line):
                continue
            total_matches += 1
            if total_matches > max_results:
                truncated = True
                break
            start_ctx = max(1, line_number - context_lines)
            end_ctx = min(len(lines), line_number + context_lines)
            rel_name = file_path.relative_to(search_path).as_posix()
            dot = '.'
            for ctx_line in range(start_ctx, end_ctx + 1):
                prefix = f'{rel_name}:{ctx_line}:'
                output_lines.append(f'{prefix}{lines[ctx_line - 1]}')
            output_lines.append('--')
        if truncated:
            break

    if not output_lines:
        dot = '.'
        return f'Sin coincidencias para {pattern!r} en {relative_path.as_posix() or dot}.'

    if truncated:
        output_lines.append(f'... mas resultados omitidos (limite {max_results}).')

    output = '\n'.join(output_lines)
    dot = '.'
    dash = '-'
    return (
        'Busqueda coding (fallback sin rg).\n'
        f'Patron: {pattern}\n'
        f'Ruta: {relative_path.as_posix() or dot}\n'
        f'Glob: {glob or dash}\n'
        f'Max resultados: {max_results}\n'
        'Salida:\n'
        f'{_bounded_text(output, MAX_COMMAND_OUTPUT_CHARS)}'
    )
def coding_search_text(pattern: str, path: str = ".", glob: str = "", context_lines: int = 2, max_results: int = 50) -> str:
    """
    Busca texto dentro del workspace de codigo activo usando ripgrep.

    Args:
        pattern (str): Patron de busqueda para rg.
        path (str): Ruta relativa o absoluta dentro del workspace.
        glob (str): Filtro glob opcional de rg, por ejemplo "*.py".
        context_lines (int): Lineas de contexto alrededor de cada match.
        max_results (int): Maximo de matches a devolver.

    Returns:
        str: Resultados acotados de rg.
    """
    cleaned_pattern = str(pattern).strip()
    if not cleaned_pattern:
        return "Debes indicar pattern para buscar."

    search_path, relative_path, error = _resolve_coding_path(path)
    if error:
        return error
    if not search_path.exists():
        return f"La ruta no existe dentro del workspace de codigo: {path}"

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    try:
        normalized_context = max(0, min(10, int(context_lines)))
    except (TypeError, ValueError):
        normalized_context = 2
    try:
        normalized_max = max(1, min(MAX_CODING_SEARCH_RESULTS, int(max_results)))
    except (TypeError, ValueError):
        normalized_max = 50

    command = [
        "rg",
        "--line-number",
        "--column",
        "--max-count",
        str(normalized_max),
        "--context",
        str(normalized_context),
        "--color",
        "never",
    ]
    cleaned_glob = str(glob).strip()
    if cleaned_glob:
        command.extend(["--glob", cleaned_glob])
    command.extend([cleaned_pattern, str(search_path)])

    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return _python_search_fallback(
            cleaned_pattern,
            search_path,
            relative_path,
            cleaned_glob,
            normalized_context,
            normalized_max,
        )
    except subprocess.TimeoutExpired:
        return "La busqueda excedio el timeout de 30 segundos."
    except OSError as exc:
        return f"No pude ejecutar la busqueda: {exc}"

    output = "\n".join(
        part.strip()
        for part in (completed.stdout, completed.stderr)
        if str(part).strip()
    )
    if completed.returncode == 1 and not output:
        return f"Sin coincidencias para {cleaned_pattern!r} en {relative_path.as_posix() or '.'}."
    if completed.returncode not in {0, 1}:
        return f"Busqueda con fallos (exit={completed.returncode}).\nSalida:\n{_bounded_text(output, MAX_COMMAND_OUTPUT_CHARS)}"
    if not output:
        output = "Sin salida visible."

    return (
        "Busqueda coding.\n"
        f"Patron: {cleaned_pattern}\n"
        f"Ruta: {relative_path.as_posix() or '.'}\n"
        f"Glob: {cleaned_glob or '-'}\n"
        f"Max resultados por archivo: {normalized_max}\n"
        "Salida:\n"
        f"{_bounded_text(output, MAX_COMMAND_OUTPUT_CHARS)}"
    )


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


def _format_coding_validation_summary(validation: dict) -> str:
    if not isinstance(validation, dict):
        validation = {}
    command = str(validation.get("command", "")).strip()
    ran_at = str(validation.get("ran_at", "")).strip()
    if not command and not ran_at:
        return "-"
    exit_code = validation.get("exit_code")
    exit_text = str(exit_code) if exit_code is not None else "-"
    return f"{command or '-'} (exit={exit_text}) {ran_at}".strip()


def _preflight_coding_proposal(
    proposal_id: str,
) -> tuple[dict | None, Path | None, Path | None, list[dict] | None, str | None]:
    raw_proposal, proposal_path, error = _load_coding_proposal(proposal_id)
    if error:
        return None, None, None, None, error
    proposal = _normalize_coding_proposal(raw_proposal)

    if _proposal_status(proposal) != CODING_PROPOSAL_PENDING:
        return None, None, None, None, f"La propuesta {proposal.get('id', proposal_id)} no esta pendiente."

    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return None, None, None, None, workspace_error

    proposal_workspace = _proposal_workspace_path(proposal)
    if proposal_workspace is None or proposal_workspace != workspace_path:
        return None, None, None, None, (
            "La propuesta pertenece a otro workspace de codigo.\n"
            f"Propuesta: {proposal_workspace}\n"
            f"Activo: {workspace_path}"
        )

    files = proposal.get("files", [])
    if not isinstance(files, list) or not files:
        return None, None, None, None, "La propuesta no contiene archivos para aplicar."

    preflight = []
    for item in files:
        if not isinstance(item, dict):
            return None, None, None, None, "La propuesta contiene un archivo con formato invalido."
        relative_text = str(item.get("path", "")).strip()
        target_path, relative_path, resolve_error = _resolve_coding_path(relative_text)
        if resolve_error:
            return None, None, None, None, resolve_error

        write_error = _validate_coding_write_path(relative_path)
        if write_error:
            return None, None, None, None, write_error

        operation = str(item.get("operation", CODING_FILE_OPERATION_WRITE)).strip().lower()
        if operation not in VALID_CODING_FILE_OPERATIONS:
            return None, None, None, None, f"Operacion invalida en {relative_path.as_posix()}: {operation}"

        existed_before = bool(item.get("existed_before"))
        previous_content = str(item.get("previous_content", ""))
        proposed_content = str(item.get("proposed_content", ""))
        if existed_before:
            if not target_path.exists() or not target_path.is_file():
                return None, None, None, None, f"El archivo original ya no existe como archivo: {relative_path.as_posix()}"
            try:
                current_content = target_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return None, None, None, None, f"No pude verificar {relative_path.as_posix()} antes de aplicar: {exc}"
            if current_content != previous_content:
                return None, None, None, None, (
                    "El archivo cambio desde que se creo la propuesta. "
                    f"Genera una nueva propuesta para: {relative_path.as_posix()}"
                )
        elif target_path.exists():
            return None, None, None, None, (
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

    return proposal, proposal_path, workspace_path, preflight, None


def _format_coding_preflight_files(preflight: list[dict]) -> str:
    lines = []
    for item in preflight:
        operation = item.get("operation", CODING_FILE_OPERATION_WRITE)
        if operation == CODING_FILE_OPERATION_DELETE:
            action = "eliminar"
        elif item.get("existed_before"):
            action = "actualizar"
        else:
            action = "crear"
        relative_path = item.get("relative_path")
        relative_text = relative_path.as_posix() if isinstance(relative_path, Path) else str(relative_path)
        lines.append(f"- {operation} {relative_text} ({action})")
    return "\n".join(lines)


def coding_workflow_status(include_diff: bool = False) -> str:
    """
    Resume el flujo completo del modo coding activo.

    Args:
        include_diff (bool): Si es True, agrega diff Git y diff de propuestas pendientes.

    Returns:
        str: Estado integrado del workspace, Git, validacion y propuestas.
    """
    state = load_state()
    coding = _coding_state(state)
    workspace_path, workspace_error = _active_coding_workspace(state)
    if workspace_error:
        return workspace_error

    pending_ids = coding.get("pending_proposal_ids", [])
    validation_command = str(coding.get("validation_command", "")).strip() or "-"
    last_validation = coding.get("last_validation", {})
    if not isinstance(last_validation, dict):
        last_validation = {}

    git_exit_code, git_command, git_output = _run_coding_subprocess(
        ["git", "status", "--short", "--branch"],
        30,
    )
    if git_exit_code is None:
        git_status = git_output
    else:
        git_status_line = "OK" if git_exit_code == 0 else f"fallos exit={git_exit_code}"
        git_status = f"{git_status_line}\nComando: {git_command}\n{git_output}"

    proposals = []
    for proposal in _iter_coding_proposals():
        proposal_workspace = _proposal_workspace_path(proposal)
        if proposal_workspace != workspace_path:
            continue
        if _proposal_status(proposal) != CODING_PROPOSAL_PENDING:
            continue
        proposals.append(proposal)

    if proposals:
        proposal_lines = []
        for proposal in proposals[:10]:
            files = proposal.get("files", [])
            files_count = len(files) if isinstance(files, list) else 0
            validation_text = _format_coding_validation_summary(_proposal_validation(proposal))
            proposal_lines.append(
                f"[{proposal.get('id', '')}] {proposal.get('title', '')} "
                f"({files_count} archivo(s), validacion={validation_text})"
            )
        if len(proposals) > 10:
            proposal_lines.append(f"... {len(proposals) - 10} propuesta(s) mas.")
        proposals_text = "\n".join(proposal_lines)
    else:
        proposals_text = "No hay propuestas pendientes."

    result = (
        "Estado de workflow coding.\n"
        f"Workspace: {workspace_path}\n"
        f"Modo: {coding.get('mode', DEFAULT_CODING_MODE)}\n"
        f"Validacion guardada: {validation_command}\n"
        f"Ultima validacion: {_format_coding_validation_summary(last_validation)}\n"
        f"Propuestas pendientes registradas: {len(pending_ids)}\n\n"
        f"Git status:\n{git_status}\n\n"
        f"Propuestas:\n{proposals_text}"
    )

    if _coerce_bool(include_diff):
        diff_exit_code, diff_command, diff_output = _run_coding_subprocess(["git", "diff", "--", "."], 30)
        if diff_exit_code is None:
            git_diff = diff_output
        else:
            diff_status = "OK" if diff_exit_code == 0 else f"fallos exit={diff_exit_code}"
            git_diff = f"{diff_status}\nComando: {diff_command}\n{diff_output}"

        proposal_diffs = []
        for proposal in proposals[:5]:
            proposal_diffs.append(
                f"## {proposal.get('id', '')} {proposal.get('title', '')}\n{_proposal_diff_text(proposal)}"
            )
        result += (
            "\n\nGit diff:\n"
            f"{git_diff}\n\n"
            "Diffs de propuestas pendientes:\n"
            f"{chr(10).join(proposal_diffs) if proposal_diffs else 'Sin propuestas pendientes.'}"
        )

    return result


def coding_check_proposal(proposal_id: str) -> str:
    """
    Ejecuta un preflight no mutante para una propuesta pendiente.

    Args:
        proposal_id (str): Id o prefijo de la propuesta.

    Returns:
        str: Resultado del preflight y archivos verificados.
    """
    proposal, _proposal_path, workspace_path, preflight, error = _preflight_coding_proposal(proposal_id)
    if error:
        return f"Preflight con fallos.\n{error}"

    return (
        "Preflight OK.\n"
        f"Propuesta: {proposal.get('id', proposal_id)}\n"
        f"Titulo: {proposal.get('title', '')}\n"
        f"Workspace: {workspace_path}\n"
        f"Archivos verificados: {len(preflight)}\n"
        f"{_format_coding_preflight_files(preflight)}\n"
        "Siguiente paso: espera aprobacion explicita y usa `coding_apply_proposal` "
        "o `coding_apply_and_validate`."
    )


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


def _load_coding_edits_spec(edits_json) -> tuple[list[dict] | None, str | None]:
    if isinstance(edits_json, str):
        cleaned = edits_json.strip()
        if not cleaned:
            return None, "Debes indicar edits_json con una lista de ediciones."
        try:
            raw_edits = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return None, f"edits_json no es JSON valido: {exc}"
    else:
        raw_edits = edits_json

    if not isinstance(raw_edits, list):
        return None, "edits_json debe ser una lista de ediciones."
    if not raw_edits:
        return None, "edits_json debe incluir al menos una edicion."

    normalized_edits = []
    for index, raw_item in enumerate(raw_edits, start=1):
        if not isinstance(raw_item, dict):
            return None, f"La edicion #{index} debe ser un objeto."
        path = str(raw_item.get("path", raw_item.get("relative_path", ""))).strip()
        if not path:
            return None, f"La edicion #{index} no tiene path."
        edit_type = str(raw_item.get("type", raw_item.get("operation", ""))).strip().lower()
        if edit_type not in {"exact_replace", "line_range"}:
            return None, f"Tipo de edicion invalido en {path}: {edit_type}. Usa exact_replace o line_range."
        normalized_edits.append({
            **raw_item,
            "path": path,
            "type": edit_type,
        })

    return normalized_edits, None


def _replace_nth_occurrence(text: str, old_text: str, new_text: str, occurrence: int) -> tuple[str | None, str | None]:
    if not old_text:
        return None, "old_text no puede estar vacio."

    positions = []
    start = 0
    while True:
        index = text.find(old_text, start)
        if index < 0:
            break
        positions.append(index)
        start = index + max(1, len(old_text))

    if not positions:
        return None, "old_text no aparece en el archivo."
    if occurrence <= 0:
        if len(positions) > 1:
            return None, (
                f"old_text aparece {len(positions)} veces. Indica occurrence para evitar ambiguedad."
            )
        occurrence = 1
    if occurrence > len(positions):
        return None, f"occurrence={occurrence} excede las {len(positions)} coincidencia(s)."

    target_index = positions[occurrence - 1]
    return text[:target_index] + new_text + text[target_index + len(old_text):], None


def coding_propose_edits(
    title: str,
    edits_json: str,
    summary: str = "",
    reason: str = "",
    validation_command: str = "",
) -> str:
    """
    Crea una propuesta a partir de ediciones localizadas sin modificar el workspace.

    Args:
        title (str): Titulo corto de la unidad de trabajo.
        edits_json (str): JSON con ediciones exact_replace o line_range.
        summary (str): Resumen de la intencion del cambio.
        reason (str): Motivo breve de la propuesta.
        validation_command (str): Comando sugerido para validar esta propuesta.

    Returns:
        str: Resultado de `coding_propose_changes` con diff persistido.
    """
    edits, parse_error = _load_coding_edits_spec(edits_json)
    if parse_error:
        return parse_error

    proposed_by_path = {}
    order = []
    for index, edit in enumerate(edits, start=1):
        target_path, relative_path, resolve_error = _resolve_coding_path(edit["path"])
        if resolve_error:
            return resolve_error
        write_error = _validate_coding_write_path(relative_path)
        if write_error:
            return write_error
        if not target_path.exists():
            return f"No existe el archivo dentro del workspace de codigo: {relative_path.as_posix()}"
        if not target_path.is_file():
            return f"No es un archivo valido dentro del workspace de codigo: {relative_path.as_posix()}"

        relative_text = relative_path.as_posix()
        if relative_text not in proposed_by_path:
            try:
                proposed_by_path[relative_text] = target_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return f"No pude leer {relative_text} antes de editar: {exc}"
            order.append(relative_text)

        current_content = proposed_by_path[relative_text]
        if edit["type"] == "exact_replace":
            try:
                occurrence = int(edit.get("occurrence", 0) or 0)
            except (TypeError, ValueError):
                return f"occurrence debe ser entero en edicion #{index} ({relative_text})."
            new_content, replace_error = _replace_nth_occurrence(
                current_content,
                str(edit.get("old_text", "")),
                str(edit.get("new_text", "")),
                occurrence,
            )
            if replace_error:
                return f"Edicion #{index} ({relative_text}) invalida: {replace_error}"
            proposed_by_path[relative_text] = new_content
            continue

        try:
            start_line = int(edit.get("start_line", 0))
            end_line = int(edit.get("end_line", start_line))
        except (TypeError, ValueError):
            return f"start_line/end_line deben ser enteros en edicion #{index} ({relative_text})."
        if start_line <= 0 or end_line < start_line:
            return f"Rango invalido en edicion #{index} ({relative_text})."

        lines = current_content.splitlines(keepends=True)
        if end_line > len(lines):
            return (
                f"Rango fuera del archivo en edicion #{index} ({relative_text}): "
                f"el archivo tiene {len(lines)} linea(s)."
            )
        replacement = str(edit.get("replacement", ""))
        if replacement and not replacement.endswith(("\n", "\r")) and lines[end_line - 1].endswith(("\n", "\r\n")):
            replacement += "\n"
        replacement_lines = replacement.splitlines(keepends=True)
        proposed_by_path[relative_text] = "".join(
            lines[: start_line - 1] + replacement_lines + lines[end_line:]
        )

    files_json = [
        {
            "path": relative_text,
            "operation": CODING_FILE_OPERATION_WRITE,
            "content": proposed_by_path[relative_text],
        }
        for relative_text in order
    ]
    return coding_propose_changes(
        title=title,
        files_json=files_json,
        summary=summary,
        reason=reason,
        validation_command=validation_command,
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
    proposal, proposal_path, _workspace_path, preflight, error = _preflight_coding_proposal(proposal_id)
    if error:
        return error

    # Validar sintaxis de TODOS los .py antes de escribir nada (evita dejar el
    # codigo a medias y que un SyntaxError tumbe el arranque de Yarbis).
    for item in preflight:
        if item["operation"] == CODING_FILE_OPERATION_DELETE:
            continue
        syntax_error = _python_syntax_error(item["target_path"], item["proposed_content"])
        if syntax_error:
            return (
                f"Propuesta NO aplicada: {syntax_error}\n"
                f"Archivo con el problema: {item['relative_path'].as_posix()}"
            )

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

        if item["operation"] == CODING_FILE_OPERATION_DELETE:
            proposal_diff = ""
            proposal_action = "delete"
        else:
            proposal_diff = _render_diff_preview_with_labels(
                item["previous_content"],
                item["proposed_content"],
                fromfile=f"a/{item['relative_path'].as_posix()}",
                tofile=f"b/{item['relative_path'].as_posix()}",
            )
            proposal_action = "proposal_applied"
        self_changes.record_change(
            file_path=item["target_path"],
            action=proposal_action,
            reason=str(proposal.get("title", "")).strip() or "coding_apply_proposal",
            checkpoint_id=checkpoint_id or "",
            proposal_id=str(proposal.get("id", proposal_id)),
            diff_preview=proposal_diff,
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


def coding_apply_and_validate(proposal_id: str, command: str = "", timeout_seconds: int = 120) -> str:
    """
    Aplica una propuesta explicita y ejecuta la validacion asociada.

    Args:
        proposal_id (str): Id o prefijo de la propuesta aprobada.
        command (str): Comando opcional de validacion. Si queda vacio usa el guardado o detectado.
        timeout_seconds (int): Timeout maximo de validacion.

    Returns:
        str: Resultado de aplicacion y validacion.
    """
    cleaned_proposal_id = str(proposal_id).strip()
    if not cleaned_proposal_id:
        return "Debes indicar un id de propuesta."

    apply_result = coding_apply_proposal(cleaned_proposal_id)
    if not apply_result.startswith("Propuesta aplicada:"):
        return (
            "Aplicacion detenida; no se ejecuto validacion.\n"
            f"{apply_result}"
        )

    validation_result = coding_run_validation(
        command=command,
        timeout_seconds=timeout_seconds,
        proposal_id=cleaned_proposal_id,
    )
    return (
        "Aplicacion y validacion completadas.\n\n"
        "Aplicacion:\n"
        f"{apply_result}\n\n"
        "Validacion:\n"
        f"{validation_result}"
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


def coding_validation_plan(proposal_id: str = "") -> str:
    """
    Recomienda una validacion enfocada para el workspace o una propuesta.

    Args:
        proposal_id (str): Id opcional de propuesta para considerar archivos tocados.

    Returns:
        str: Comando recomendado y motivo.
    """
    workspace_path, workspace_error = _active_coding_workspace()
    if workspace_error:
        return workspace_error

    coding = _coding_state()
    saved_command = str(coding.get("validation_command", "")).strip()
    detected_command = _detect_coding_validation_command_for_workspace(workspace_path)
    command = saved_command or detected_command
    source = "guardado" if saved_command else "detectado" if detected_command else "no disponible"

    touched_files = []
    cleaned_proposal_id = str(proposal_id).strip()
    if cleaned_proposal_id:
        proposal, _proposal_path, error = _load_coding_proposal(cleaned_proposal_id)
        if error:
            return f"No pude planear validacion para la propuesta: {error}"
        normalized_proposal = _normalize_coding_proposal(proposal)
        proposal_workspace = _proposal_workspace_path(normalized_proposal)
        if proposal_workspace != workspace_path:
            return (
                "La propuesta pertenece a otro workspace de codigo.\n"
                f"Propuesta: {proposal_workspace}\n"
                f"Activo: {workspace_path}"
            )
        files = normalized_proposal.get("files", [])
        if isinstance(files, list):
            touched_files = [
                str(item.get("path", "")).strip()
                for item in files
                if isinstance(item, dict) and str(item.get("path", "")).strip()
            ]

    hints = []
    if touched_files:
        test_like_files = [
            path
            for path in touched_files
            if Path(path).name.startswith("test_") or "/tests/" in f"/{path}" or path.startswith("tests/")
        ]
        py_files = [path for path in touched_files if path.endswith(".py")]
        js_files = [path for path in touched_files if path.endswith((".js", ".jsx", ".ts", ".tsx"))]
        docs_files = [path for path in touched_files if path.lower().endswith((".md", ".txt"))]
        if test_like_files:
            hints.append("La propuesta toca tests; prioriza correr esos tests o la suite Python.")
        if py_files and not test_like_files:
            hints.append("La propuesta toca Python; usa la validacion guardada/detectada o tests cercanos.")
        if js_files:
            hints.append("La propuesta toca JS/TS; si existe package.json, `npm test` es candidato.")
        if docs_files and len(docs_files) == len(touched_files):
            hints.append("Solo toca documentacion/texto; puede bastar revision de diff si no hay codigo.")

    if not command:
        return (
            "Plan de validacion coding.\n"
            f"Workspace: {workspace_path}\n"
            f"Propuesta: {cleaned_proposal_id or '-'}\n"
            f"Archivos tocados: {', '.join(touched_files) if touched_files else '-'}\n"
            "Comando recomendado: -\n"
            "Motivo: no hay comando guardado ni candidato detectado.\n"
            f"Pistas: {' '.join(hints) if hints else '-'}"
        )

    return (
        "Plan de validacion coding.\n"
        f"Workspace: {workspace_path}\n"
        f"Propuesta: {cleaned_proposal_id or '-'}\n"
        f"Archivos tocados: {', '.join(touched_files) if touched_files else '-'}\n"
        f"Comando recomendado: {command}\n"
        f"Fuente: {source}\n"
        f"Para ejecutar: `coding_run_validation(command={json.dumps(command)}, proposal_id={json.dumps(cleaned_proposal_id)})`\n"
        f"Pistas: {' '.join(hints) if hints else '-'}"
    )


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

    self_changes.record_change(
        file_path=target_path,
        action="checkpoint_restored",
        reason=f"restore_checkpoint:{metadata.get('id', checkpoint_id)}",
        checkpoint_id=str(metadata.get("id", checkpoint_id)),
    )

    return (
        f"{action}\n"
        f"Checkpoint usado: {metadata.get('id')}\n"
        f"Archivo: {metadata.get('target_path')}\n"
        "Siguiente paso recomendado: ejecuta `run_project_tests` para validar el estado restaurado."
    )


def list_self_code_changes(limit: int = 10, include_diff: bool = False) -> str:
    """
    Lista los auto-cambios de codigo que Yarbis se ha aplicado a si mismo.

    Es la bitacora de cambios pendientes de versionar en el repositorio git.
    Cada vez que Yarbis modifica su propio codigo fuente (write_text_file,
    coding_apply_proposal o restore_checkpoint) queda registrado aqui.

    Args:
        limit (int): Cuantas entradas recientes mostrar (1-50).
        include_diff (bool): Si es True, incluye la vista previa del diff de cada cambio.

    Returns:
        str: Resumen de los auto-cambios registrados.
    """
    try:
        cleaned_limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        cleaned_limit = 10

    entries = self_changes.load_changes()
    if not entries:
        return (
            "No hay auto-cambios de codigo registrados.\n"
            "La bitacora se llena cuando modifico mi propio codigo fuente."
        )

    total = len(entries)
    recent = entries[-cleaned_limit:]
    lines = [
        f"Auto-cambios de codigo registrados: {total} (mostrando {len(recent)} mas recientes).",
        "Estos cambios viven solo en esta copia: hay que versionarlos en el repositorio git.",
        "",
    ]
    for entry in recent:
        stamp = str(entry.get("timestamp", ""))[:19].replace("T", " ")
        detail = f"- [{stamp} UTC] {entry.get('instance', '?')} | {entry.get('action', '?')} | {entry.get('file', '?')}"
        reason = str(entry.get("reason", "")).strip()
        if reason and reason != "write_text_file":
            detail += f" | {reason}"
        if entry.get("proposal_id"):
            detail += f" | propuesta {entry['proposal_id']}"
        if entry.get("checkpoint_id"):
            detail += f" | checkpoint {entry['checkpoint_id']}"
        lines.append(detail)
        if include_diff and str(entry.get("diff_preview", "")).strip():
            lines.append(str(entry["diff_preview"]).strip())
            lines.append("")

    lines.append("")
    lines.append(
        "Cuando estos cambios ya esten en git, usa `mark_self_code_changes_versioned` para archivar la bitacora."
    )
    return "\n".join(lines)


def mark_self_code_changes_versioned(note: str = "") -> str:
    """
    Archiva la bitacora de auto-cambios de codigo (ya subidos a git) y la vacia.

    Usalo SOLO despues de confirmar que los cambios listados en
    list_self_code_changes ya fueron adoptados en el repositorio git.

    Args:
        note (str): Nota opcional (por ejemplo, el commit donde se versionaron).

    Returns:
        str: Resultado del archivado.
    """
    archived = self_changes.mark_all_versioned(note=note)
    if archived == 0:
        return "La bitacora de auto-cambios ya estaba vacia."
    return (
        f"Bitacora archivada: {archived} auto-cambio(s) marcados como versionados.\n"
        "El historial queda en un archivo .yarbis_self_changes.versioned-*.jsonl del workspace."
    )


def _format_memory_backup_counts(counts: dict) -> str:
    return (
        f"mensajes={counts.get('messages', 0)}, "
        f"notas={counts.get('notes', 0)}, "
        f"tareas={counts.get('tasks', 0)}, "
        f"proyectos={counts.get('idea_projects', 0)}, "
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


_TEST_SANDBOX_PREFIX = "test-sandbox-"


def _isolated_test_env() -> tuple[dict, str]:
    """Entorno para el subproceso de tests apuntado a una instancia desechable.

    Critico: el subproceso de `unittest` hereda el entorno del proceso que lo
    lanza. Si Yarbis corre sus propios tests desde una instancia real (p. ej.
    `asistente`), cualquier test que escriba estado sin aislar apunta al
    `state.json` REAL de esa instancia y borra la memoria del usuario. Forzamos
    un `YARBIS_INSTANCE` desechable para que los tests solo puedan tocar un
    estado sandbox descartable, nunca el de una instancia productiva.
    """
    sandbox_id = f"{_TEST_SANDBOX_PREFIX}{uuid4().hex[:8]}"
    env = dict(os.environ)
    env[yarbis_instance.ENV_INSTANCE] = sandbox_id
    env.pop(yarbis_instance.ENV_SERVICE_NAME, None)
    return env, sandbox_id


def _cleanup_test_sandbox(sandbox_id: str) -> None:
    """Borra el directorio de la instancia sandbox creada para los tests."""
    if not str(sandbox_id).startswith(_TEST_SANDBOX_PREFIX):
        return
    try:
        sandbox_dir = yarbis_instance.instance_root(sandbox_id)
    except Exception:
        return
    try:
        resolved = sandbox_dir.resolve()
        instances_root = yarbis_instance.INSTANCES_ROOT.resolve()
    except OSError:
        return
    # Seguridad: solo borrar dentro de .yarbis_instances y con el prefijo sandbox.
    if resolved.parent != instances_root or resolved.name != sandbox_id:
        return
    shutil.rmtree(resolved, ignore_errors=True)


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

    sandbox_env, sandbox_id = _isolated_test_env()
    try:
        completed = subprocess.run(
            command,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=normalized_timeout,
            env=sandbox_env,
        )
    except subprocess.TimeoutExpired:
        return (
            f"Los tests excedieron el timeout de {normalized_timeout} segundos.\n"
            f"Comando: {' '.join(command)}"
        )
    except OSError as exc:
        return f"No pude ejecutar los tests: {exc}"
    finally:
        _cleanup_test_sandbox(sandbox_id)

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


def _computer_control_state(state: dict | None = None) -> dict:
    cc = (state or load_state()).get("computer_control", {})
    return cc if isinstance(cc, dict) else {}


def _require_computer_control(*, need_os: bool = False) -> str:
    cc = _computer_control_state()
    if not cc.get("enabled"):
        return (
            "El control de la PC esta desactivado. Actívalo con set_computer_control(enabled=True) "
            "o desde los ajustes antes de usar esta capacidad."
        )
    if need_os and not cc.get("settings", {}).get("os_control", True):
        return "El control del sistema operativo esta desactivado en los ajustes de control de PC."
    return ""


def set_computer_control(
    enabled: bool | None = None,
    browser_channel: str = "",
    os_control: bool | None = None,
    confirm_sensitive: bool | None = None,
    browser_profile_mode: str = "",
    browser_user_data_dir: str | None = None,
    browser_profile_directory: str | None = None,
) -> str:
    """
    Activa o ajusta el control de la PC (navegador propio y sistema operativo).

    Args:
        enabled (bool): Activa/desactiva la capacidad completa (apagada por defecto).
        browser_channel (str): Canal del navegador: msedge, chrome, brave.
        os_control (bool): Permitir control del SO (mouse/teclado) ademas del navegador.
        confirm_sensitive (bool): Exigir confirmacion antes de acciones sensibles (publicar/pagar/eliminar).
        browser_profile_mode (str): 'isolated' (perfil propio de Yarbis) o 'system' (tu perfil real, con tus sesiones).
        browser_user_data_dir (str): Ruta personalizada de "User Data" del navegador (vacio = automatico segun el modo).
        browser_profile_directory (str): Perfil dentro del navegador, ej. 'Default' o 'Profile 1' (vacio = el predeterminado).

    Returns:
        str: Estado resultante del control de PC.
    """
    def mutate(state):
        cc = state.setdefault("computer_control", {})
        settings = cc.setdefault("settings", {})
        if enabled is not None:
            cc["enabled"] = bool(enabled)
        if str(browser_channel).strip():
            settings["browser_channel"] = str(browser_channel).strip().lower()
        if os_control is not None:
            settings["os_control"] = bool(os_control)
        if confirm_sensitive is not None:
            settings["confirm_sensitive"] = bool(confirm_sensitive)
        if str(browser_profile_mode).strip():
            settings["browser_profile_mode"] = str(browser_profile_mode).strip().lower()
        if browser_user_data_dir is not None:
            settings["browser_user_data_dir"] = str(browser_user_data_dir).strip()
        if browser_profile_directory is not None:
            settings["browser_profile_directory"] = str(browser_profile_directory).strip()

    state_transaction("set_computer_control", mutate)
    cc = _computer_control_state()
    s = cc.get("settings", {})
    perfil = s.get("browser_profile_mode", "isolated")
    perfil_txt = "aislado (Yarbis)" if perfil == "isolated" else "sistema (tu navegador)"
    if str(s.get("browser_user_data_dir", "")).strip():
        perfil_txt = f"personalizado ({s['browser_user_data_dir']})"
    if str(s.get("browser_profile_directory", "")).strip():
        perfil_txt += f", perfil '{s['browser_profile_directory']}'"
    return (
        f"Control de PC: {'activado' if cc.get('enabled') else 'desactivado'}. "
        f"Navegador={s.get('browser_channel', 'msedge')}, "
        f"perfil={perfil_txt}, "
        f"control SO={'si' if s.get('os_control', True) else 'no'}, "
        f"confirmar acciones sensibles={'si' if s.get('confirm_sensitive', True) else 'no'}."
    )


def set_social_confirmation(enabled: bool = True) -> str:
    """
    Activa o desactiva la confirmacion obligatoria (PUBLICAR <id>) antes de publicar
    en redes por API. Con enabled=False, confirm_social_publication publica sin exigir
    la frase. No afecta la seguridad del control de PC (esa usa confirm_sensitive).

    Args:
        enabled (bool): True exige confirmacion; False publica sin confirmar.

    Returns:
        str: Estado resultante.
    """
    def mutate(state):
        settings = state.setdefault("social", {}).setdefault("settings", {})
        settings["require_confirmation"] = bool(enabled)

    state_transaction("set_social_confirmation", mutate)
    value = load_state().get("social", {}).get("settings", {}).get("require_confirmation", True)
    return f"Confirmacion antes de publicar en redes (API): {'activada' if value else 'desactivada'}."


def browser_open(url: str = "", channel: str = "") -> str:
    """
    Abre (o enfoca) el navegador propio de Yarbis, con ventana visible y sesion
    persistente (tu login se guarda entre usos). Requiere control de PC activado.

    Args:
        url (str): URL inicial opcional, por ejemplo https://www.facebook.com.
        channel (str): Canal del navegador (msedge, chrome, brave); vacio usa el configurado.

    Returns:
        str: Estado del navegador y la URL actual.
    """
    gate = _require_computer_control()
    if gate:
        return gate
    settings = _computer_control_state().get("settings", {})
    channel = str(channel).strip() or settings.get("browser_channel", "msedge")
    try:
        return open_persistent_browser(
            url=url,
            channel=channel,
            workspace_root=WORKSPACE_ROOT,
            profile_mode=str(settings.get("browser_profile_mode", "isolated")),
            user_data_dir=str(settings.get("browser_user_data_dir", "")),
            profile_directory=str(settings.get("browser_profile_directory", "")),
        )
    except Exception as exc:
        return f"No pude abrir el navegador: {exc}"


def browser_observe(screenshot: bool = False) -> str:
    """
    Observa la pagina viva del navegador de Yarbis: devuelve la URL, el titulo y
    una lista numerada de elementos interactivos (usa el numero como `ref` en
    browser_act). Con screenshot=True analiza una captura con vision.

    Args:
        screenshot (bool): Si es True, adjunta un analisis visual de la captura.

    Returns:
        str: Estado de la pagina y elementos clicables.
    """
    gate = _require_computer_control()
    if gate:
        return gate
    try:
        return observe_browser(screenshot=bool(screenshot), settings=load_state(), workspace_root=WORKSPACE_ROOT)
    except Exception as exc:
        return f"No pude observar la pagina: {exc}"


def browser_act(actions_json: str = "", confirm: str = "") -> str:
    """
    Ejecuta acciones sobre la pagina viva del navegador (sin cerrarlo). Cada accion
    es un objeto JSON: {"action":"click","ref":"5"} o {"action":"click","text":"Crear publicacion"}
    o {"action":"fill","ref":"3","value":"mi texto"}; tambien goto/press/scroll/wait.
    Clica preferentemente por `ref` (de browser_observe) o por `text`.

    Las acciones sensibles (publicar/pagar/enviar/eliminar) se BLOQUEAN salvo que
    pases confirm="<texto exacto del boton>", tras pedir confirmacion al usuario.

    Args:
        actions_json (str): Lista JSON de acciones.
        confirm (str): Texto exacto del boton sensible que el usuario autorizo.

    Returns:
        str: Resultado de las acciones y la URL final.
    """
    gate = _require_computer_control()
    if gate:
        return gate
    confirm_sensitive = bool(_computer_control_state().get("settings", {}).get("confirm_sensitive", True))
    try:
        return act_on_live_page(
            actions_json=actions_json,
            confirm=confirm,
            confirm_sensitive=confirm_sensitive,
            workspace_root=WORKSPACE_ROOT,
        )
    except Exception as exc:
        return f"No pude actuar en la pagina: {exc}"


# --- Cliente MCP: conectarse a servidores MCP y usar sus herramientas ---

_MCP_CRED_PREFIX = "cred:"


def _mcp_state(state: dict | None = None) -> dict:
    mcp = (state or load_state()).get("mcp", {})
    return mcp if isinstance(mcp, dict) else {}


def _require_mcp() -> str:
    if not _mcp_state().get("enabled"):
        return (
            "La conexion a servidores MCP esta desactivada. Actívala con set_mcp_enabled(True) "
            "o desde los ajustes antes de usar esta capacidad."
        )
    return ""


def _find_mcp_server(name: str, state: dict | None = None) -> dict | None:
    cleaned = str(name).strip().lower()
    for server in _mcp_state(state).get("servers", []):
        if str(server.get("name", "")).strip().lower() == cleaned:
            return server
    return None


def _mcp_runtime_config(server: dict) -> dict:
    """Copia del server con los headers 'cred:<ref>' resueltos via credential_store."""
    config = dict(server)
    headers = dict(server.get("headers", {}) or {})
    resolved = {}
    for key, value in headers.items():
        text = str(value)
        if _MCP_CRED_PREFIX in text:
            prefix, _, ref = text.partition(_MCP_CRED_PREFIX)
            try:
                secret = load_secret(ref.strip())
                text = f"{prefix}{secret}"
            except CredentialStoreError:
                pass
        resolved[key] = text
    config["headers"] = resolved
    return config


def _parse_arg_list(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_kv(raw: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def set_mcp_enabled(enabled: bool = True) -> str:
    """
    Activa o desactiva la capacidad de conectarse a servidores MCP.

    Apagada por defecto: conectar servidores MCP arbitrarios ejecuta comandos
    locales (stdio) o llama endpoints (HTTP).

    Args:
        enabled (bool): True para permitir conexiones MCP.

    Returns:
        str: Estado resultante.
    """
    value = bool(enabled)

    def mutate(state):
        mcp = state.setdefault("mcp", {})
        mcp["enabled"] = value

    state_transaction("set_mcp_enabled", mutate)
    return f"Conexion MCP: {'activada' if value else 'desactivada'}."


def mcp_add_server(
    name: str,
    transport: str = "stdio",
    command: str = "",
    args: str = "",
    url: str = "",
    cwd: str = "",
    env: str = "",
    headers: str = "",
    auth_token: str = "",
) -> str:
    """
    Registra un servidor MCP (no lo conecta todavia; usa mcp_connect despues).

    Args:
        name (str): Nombre corto del servidor (letras, numeros, guion).
        transport (str): 'stdio' (comando local) o 'http' (url remota).
        command (str): Comando a lanzar (transport stdio), ej. 'npx'.
        args (str): Argumentos: lista JSON o uno por linea.
        url (str): URL del servidor (transport http).
        cwd (str): Carpeta de trabajo para stdio (opcional).
        env (str): Variables de entorno como JSON o lineas CLAVE=valor.
        headers (str): Headers HTTP como JSON o lineas Clave=valor (transport http).
        auth_token (str): Token de autenticacion; se guarda cifrado y se envia como Bearer.

    Returns:
        str: Resultado del registro.
    """
    cleaned_name = str(name).strip()
    if not cleaned_name:
        return "Debes indicar un nombre para el servidor MCP."
    cleaned_transport = str(transport).strip().lower() or "stdio"
    if cleaned_transport not in ("stdio", "http"):
        return "Transporte invalido: usa 'stdio' o 'http'."
    if cleaned_transport == "stdio" and not str(command).strip():
        return "El transporte stdio requiere 'command'."
    if cleaned_transport == "http" and not str(url).strip():
        return "El transporte http requiere 'url'."

    parsed_headers = _parse_kv(headers)
    token = str(auth_token).strip()
    if token:
        try:
            ref = save_secret(token, kind="mcp", metadata={"server": cleaned_name})
        except CredentialStoreError as exc:
            return f"No pude guardar el token de forma segura: {exc}"
        parsed_headers["Authorization"] = f"Bearer {_MCP_CRED_PREFIX}{ref}"

    server = {
        "name": cleaned_name,
        "transport": cleaned_transport,
        "command": str(command).strip(),
        "args": _parse_arg_list(args),
        "url": str(url).strip(),
        "cwd": str(cwd).strip(),
        "env": _parse_kv(env),
        "headers": parsed_headers,
        "enabled": True,
    }

    def mutate(state):
        mcp = state.setdefault("mcp", {})
        servers = mcp.setdefault("servers", [])
        if not isinstance(servers, list):
            servers = []
            mcp["servers"] = servers
        normalized_name = cleaned_name.strip().lower()
        mcp["servers"] = [s for s in servers if str(s.get("name", "")).strip().lower() != normalized_name]
        mcp["servers"].append(server)

    state_transaction("mcp_add_server", mutate)
    stored = _find_mcp_server(cleaned_name)
    final_name = stored["name"] if stored else cleaned_name
    return (
        f"Servidor MCP '{final_name}' registrado ({cleaned_transport}). "
        "Actívalo con set_mcp_enabled(True) si hace falta y conecta con mcp_connect."
    )


def mcp_remove_server(name: str) -> str:
    """
    Elimina un servidor MCP registrado (y lo desconecta si estaba conectado).

    Args:
        name (str): Nombre del servidor.

    Returns:
        str: Resultado.
    """
    cleaned = str(name).strip().lower()
    if not cleaned:
        return "Debes indicar el nombre del servidor."
    try:
        mcp_client.disconnect(cleaned)
    except Exception:
        pass

    def mutate(state):
        mcp = state.setdefault("mcp", {})
        servers = mcp.get("servers", [])
        if not isinstance(servers, list):
            return False
        before = len(servers)
        mcp["servers"] = [s for s in servers if str(s.get("name", "")).strip().lower() != cleaned]
        return len(mcp["servers"]) < before

    removed = state_transaction("mcp_remove_server", mutate)
    return f"Servidor MCP '{cleaned}' eliminado." if removed else f"No encontre un servidor MCP llamado '{cleaned}'."


def mcp_list_servers() -> str:
    """
    Lista los servidores MCP registrados y si estan conectados.

    Returns:
        str: Resumen de servidores.
    """
    mcp = _mcp_state()
    servers = mcp.get("servers", [])
    header = f"Conexion MCP: {'activada' if mcp.get('enabled') else 'desactivada'}."
    if not servers:
        return header + "\nNo hay servidores MCP registrados. Usa mcp_add_server para agregar uno."
    lines = [header, "Servidores:"]
    for server in servers:
        name = str(server.get("name", ""))
        transport = str(server.get("transport", ""))
        target = server.get("command") or server.get("url") or ""
        connected = mcp_client.is_connected(name)
        lines.append(f"- {name} ({transport}): {target} | {'conectado' if connected else 'desconectado'}")
    return "\n".join(lines)


def mcp_connect(name: str) -> str:
    """
    Conecta a un servidor MCP registrado y descubre sus herramientas.

    Tras conectar, sus herramientas quedan disponibles para Yarbis con el nombre
    mcp__<servidor>__<herramienta>.

    Args:
        name (str): Nombre del servidor a conectar.

    Returns:
        str: Herramientas descubiertas o el error.
    """
    gate = _require_mcp()
    if gate:
        return gate
    server = _find_mcp_server(name)
    if not server:
        return f"No encontre un servidor MCP llamado '{name}'. Regístralo con mcp_add_server."
    try:
        tools = mcp_client.connect(_mcp_runtime_config(server))
    except Exception as exc:
        return f"No pude conectar a '{server['name']}': {exc}"
    if not tools:
        return f"Conectado a '{server['name']}', pero no expone herramientas."
    names = ", ".join(mcp_client.qualified_tool_name(server["name"], t.get("name", "")) for t in tools)
    return f"Conectado a '{server['name']}'. Herramientas disponibles ({len(tools)}): {names}"


def mcp_refresh_tools(name: str = "") -> str:
    """
    Reconecta/refresca las herramientas de un servidor MCP (o de todos los habilitados).

    Args:
        name (str): Servidor a refrescar; vacio = todos los habilitados.

    Returns:
        str: Resultado del refresco.
    """
    gate = _require_mcp()
    if gate:
        return gate
    if str(name).strip():
        return mcp_connect(name)
    servers = [s for s in _mcp_state().get("servers", []) if s.get("enabled", True)]
    if not servers:
        return "No hay servidores MCP habilitados para refrescar."
    results = []
    for server in servers:
        try:
            tools = mcp_client.connect(_mcp_runtime_config(server))
            results.append(f"- {server['name']}: {len(tools)} herramienta(s)")
        except Exception as exc:
            results.append(f"- {server['name']}: error ({exc})")
    return "Refresco MCP:\n" + "\n".join(results)


def mcp_list_tools(name: str = "") -> str:
    """
    Lista las herramientas MCP disponibles de los servidores conectados.

    Args:
        name (str): Servidor concreto; vacio = todos los conectados.

    Returns:
        str: Herramientas con su nombre namespaced y descripcion.
    """
    specs = mcp_client.tool_specs()
    if not specs:
        return "No hay herramientas MCP disponibles. Conecta un servidor con mcp_connect."
    prefix = f"mcp__{str(name).strip().lower()}__" if str(name).strip() else "mcp__"
    lines = ["Herramientas MCP disponibles:"]
    for spec in specs:
        fn = spec.get("function", {})
        fn_name = str(fn.get("name", ""))
        if not fn_name.startswith(prefix):
            continue
        lines.append(f"- {fn_name}: {str(fn.get('description', ''))[:120]}")
    if len(lines) == 1:
        return "No hay herramientas MCP para ese filtro."
    return "\n".join(lines)


def mcp_call_tool(server: str, tool: str, args_json: str = "") -> str:
    """
    Llama manualmente una herramienta de un servidor MCP conectado.

    Normalmente no hace falta: las herramientas MCP ya estan disponibles para
    Yarbis directamente como mcp__<servidor>__<tool>. Usa esto para depurar.

    Args:
        server (str): Nombre del servidor MCP.
        tool (str): Nombre de la herramienta (tal cual la expone el servidor).
        args_json (str): Argumentos como objeto JSON.

    Returns:
        str: Resultado de la herramienta.
    """
    gate = _require_mcp()
    if gate:
        return gate
    arguments = {}
    if str(args_json).strip():
        try:
            arguments = json.loads(args_json)
        except json.JSONDecodeError as exc:
            return f"args_json no es JSON valido: {exc}"
        if not isinstance(arguments, dict):
            return "args_json debe ser un objeto JSON."
    try:
        return mcp_client.call_tool(str(server).strip(), str(tool).strip(), arguments)
    except Exception as exc:
        return f"Error llamando la herramienta MCP: {exc}"


def desktop_look(prompt: str = "") -> str:
    """
    Captura la pantalla del escritorio y la describe con el modelo de vision, para
    decidir donde hacer clic. Requiere control de PC + control de SO activados.

    Args:
        prompt (str): Que quieres saber de la pantalla.

    Returns:
        str: Descripcion visual de la pantalla.
    """
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    try:
        return computer_control.look(prompt, settings=load_state())
    except Exception as exc:
        return f"No pude mirar la pantalla: {exc}"


def desktop_screen_size() -> str:
    """Devuelve el tamano de la pantalla (ancho x alto) para calcular coordenadas."""
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    try:
        width, height = computer_control.screen_size()
        return f"Pantalla: {width} x {height} pixeles."
    except Exception as exc:
        return f"No pude leer el tamano de pantalla: {exc}"


def desktop_click(x: int, y: int, button: str = "left", clicks: int = 1, label: str = "", confirm: str = "") -> str:
    """
    Hace clic en la coordenada (x, y) del escritorio. Requiere control de PC + SO.
    Pasa `label` con lo que crees estar clicando; si parece sensible
    (publicar/pagar/eliminar) se bloquea hasta que pases confirm="<label>".

    Args:
        x (int): Coordenada X en pixeles.
        y (int): Coordenada Y en pixeles.
        button (str): left, right o middle.
        clicks (int): Numero de clics (1-3).
        label (str): Descripcion de lo que se clica (para el gate de seguridad).
        confirm (str): Igual al label si el usuario autorizo una accion sensible.

    Returns:
        str: Resultado del clic.
    """
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    lbl = str(label).strip()
    if (
        _computer_control_state().get("settings", {}).get("confirm_sensitive", True)
        and computer_control.label_is_sensitive(lbl)
        and not _confirm_satisfies(confirm, lbl)
    ):
        return (
            f"BLOQUEADO por seguridad: '{lbl}' parece una accion sensible. "
            f'Pide el visto bueno al usuario y reintenta con confirm="{lbl}" (o confirm="si").'
        )
    try:
        return computer_control.click(x, y, button, clicks)
    except Exception as exc:
        return f"No pude hacer click: {exc}"


def desktop_move(x: int, y: int) -> str:
    """Mueve el cursor del mouse a (x, y). Requiere control de PC + SO."""
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    try:
        return computer_control.move_mouse(x, y)
    except Exception as exc:
        return f"No pude mover el mouse: {exc}"


def desktop_type(text: str = "") -> str:
    """Escribe texto con el teclado en la app enfocada. Requiere control de PC + SO."""
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    try:
        return computer_control.type_text(text)
    except Exception as exc:
        return f"No pude escribir: {exc}"


def desktop_press(keys: str = "") -> str:
    """
    Presiona una tecla o combinacion (ej. 'enter' o 'ctrl+v'). Requiere control de PC + SO.

    Args:
        keys (str): Tecla o combinacion separada por + o espacio.

    Returns:
        str: Resultado.
    """
    gate = _require_computer_control(need_os=True)
    if gate:
        return gate
    try:
        return computer_control.press_keys(keys)
    except Exception as exc:
        return f"No pude presionar teclas: {exc}"


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


def _find_idea_project(projects: list[dict], project_id: str) -> dict | None:
    cleaned = str(project_id).strip().lower()
    if not cleaned:
        return None

    for project in projects:
        if project["id"].lower() == cleaned:
            return project

    prefix_matches = [project for project in projects if project["id"].lower().startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    title_matches = [project for project in projects if project["title"].strip().lower() == cleaned]
    if len(title_matches) == 1:
        return title_matches[0]

    return None


def _idea_project_text(value: str, *, current: str = "") -> str:
    cleaned = str(value).strip()
    if cleaned == CLEAR_VALUE:
        return ""
    return cleaned if cleaned else current


def _idea_project_items(value: str, *, current: list[str] | None = None) -> list[str]:
    cleaned = str(value).strip()
    if cleaned == CLEAR_VALUE:
        return []
    return _split_text_items(cleaned) if cleaned else list(current or [])


def _format_idea_project(project: dict) -> str:
    lines = [
        f"[{project['id']}] {project['title']}",
        f"Tipo: {project['kind']}",
        f"Estado: {project['status']}",
    ]
    for label, key in (
        ("Resumen", "summary"),
        ("Audiencia", "audience"),
        ("Resultado deseado", "desired_outcome"),
        ("Problema", "problem"),
        ("Direccion elegida", "selected_direction"),
    ):
        if project.get(key):
            lines.append(f"{label}: {project[key]}")

    for label, key in (
        ("Direcciones creativas", "creative_directions"),
        ("Criterios de exito", "success_criteria"),
        ("Restricciones", "constraints"),
        ("Riesgos", "risks"),
        ("Preguntas abiertas", "open_questions"),
        ("Proximos pasos", "next_steps"),
    ):
        items = project.get(key) or []
        if items:
            lines.append(label + ":")
            lines.extend(f"- {item}" for item in items)

    if project.get("created_at"):
        lines.append(f"Creado: {project['created_at']}")
    if project.get("updated_at"):
        lines.append(f"Actualizado: {project['updated_at']}")
    return "\n".join(lines)


VISUAL_BOARD_KIND_LABELS = {
    "idea_canvas": "Canvas de idea",
    "decision_matrix": "Matriz de decision",
    "roadmap_kanban": "Roadmap/Kanban",
    "mind_map": "Mapa mental",
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _visual_lines(items: list[str], fallback: str = "Por definir") -> str:
    cleaned_items = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned_items:
        return fallback
    return "\n".join(f"- {item}" for item in cleaned_items)


def _visual_text(value: str, fallback: str = "Por definir") -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


def _visual_node(
    node_id: str,
    title: str,
    text: str,
    x: int,
    y: int,
    *,
    width: int = 240,
    height: int = 132,
    node_type: str = "note",
    lane: str = "",
    color: str = "",
    meta: dict | None = None,
) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "title": title,
        "text": text,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "lane": lane,
        "color": color,
        "meta": meta or {},
    }


def _visual_edge(source: str, target: str, label: str = "") -> dict:
    edge_id = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", f"edge-{source}-{target}").strip("-")
    return {
        "id": edge_id[:80],
        "source": source,
        "target": target,
        "label": label,
    }


def _default_visual_board_title(project: dict, board_kind: str) -> str:
    label = VISUAL_BOARD_KIND_LABELS.get(board_kind, board_kind.replace("_", " ").title())
    return f"{label}: {project.get('title', 'Proyecto')}"


def _idea_canvas_template(project: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    nodes = [
        _visual_node("problem", "Problema", _visual_text(project.get("problem", "")), 40, 50),
        _visual_node("audience", "Audiencia", _visual_text(project.get("audience", "")), 320, 50),
        _visual_node("outcome", "Resultado", _visual_text(project.get("desired_outcome", "")), 600, 50),
        _visual_node(
            "direction",
            "Direccion elegida",
            _visual_text(project.get("selected_direction", "") or "\n".join(project.get("creative_directions", [])[:3])),
            320,
            230,
            width=280,
            height=150,
            node_type="focus",
        ),
        _visual_node("criteria", "Criterios", _visual_lines(project.get("success_criteria", [])), 40, 430),
        _visual_node("constraints", "Restricciones", _visual_lines(project.get("constraints", [])), 320, 430),
        _visual_node("risks", "Riesgos", _visual_lines(project.get("risks", [])), 600, 430),
        _visual_node("questions", "Preguntas", _visual_lines(project.get("open_questions", [])), 40, 610),
        _visual_node("next_steps", "Proximos pasos", _visual_lines(project.get("next_steps", [])), 320, 610, width=520),
    ]
    edges = [
        _visual_edge("problem", "direction"),
        _visual_edge("audience", "direction"),
        _visual_edge("outcome", "direction"),
        _visual_edge("direction", "criteria"),
        _visual_edge("direction", "risks"),
        _visual_edge("direction", "next_steps"),
    ]
    return nodes, edges, [], {"x": 0, "y": 0, "zoom": 1}


def _decision_matrix_template(project: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    lanes = [
        {"id": "quick_wins", "title": "Alto impacto / bajo esfuerzo", "x": 40, "y": 50, "width": 300, "height": 260},
        {"id": "strategic_bets", "title": "Alto impacto / alto esfuerzo", "x": 370, "y": 50, "width": 300, "height": 260},
        {"id": "fill_ins", "title": "Bajo impacto / bajo esfuerzo", "x": 40, "y": 340, "width": 300, "height": 260},
        {"id": "reconsider", "title": "Bajo impacto / alto esfuerzo", "x": 370, "y": 340, "width": 300, "height": 260},
    ]
    directions = [
        str(item).strip()
        for item in project.get("creative_directions", [])
        if str(item).strip()
    ]
    if project.get("selected_direction"):
        directions.insert(0, str(project["selected_direction"]).strip())
    if not directions:
        directions = [project.get("summary") or project.get("title") or "Alternativa por definir"]

    nodes = []
    for index, direction in enumerate(directions[:12]):
        lane = lanes[index % len(lanes)]
        row = index // len(lanes)
        nodes.append(_visual_node(
            f"option-{index + 1}",
            f"Alternativa {index + 1}",
            (
                f"{direction}\n\n"
                "Impacto: por estimar\n"
                "Esfuerzo: por estimar\n"
                "Confianza: por validar\n"
                "Riesgo: por revisar"
            ),
            int(lane["x"]) + 20,
            int(lane["y"]) + 52 + (row * 120),
            width=260,
            height=108,
            node_type="option",
            lane=str(lane["id"]),
            meta={
                "impact": "",
                "effort": "",
                "confidence": "",
                "risk": "",
            },
        ))
    return nodes, [], lanes, {"x": 0, "y": 0, "zoom": 1}


def _roadmap_kanban_template(project: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    lane_specs = [
        ("backlog", "Backlog"),
        ("now", "Ahora"),
        ("next", "Siguiente"),
        ("later", "Despues"),
        ("done", "Hecho"),
    ]
    lanes = [
        {"id": lane_id, "title": title, "x": 40 + (index * 250), "y": 50, "width": 220, "height": 560}
        for index, (lane_id, title) in enumerate(lane_specs)
    ]
    steps = [str(item).strip() for item in project.get("next_steps", []) if str(item).strip()]
    if not steps:
        steps = [project.get("selected_direction") or project.get("summary") or "Definir primer paso"]

    nodes = []
    for index, step in enumerate(steps[:20]):
        lane_id = "now" if index == 0 else "next" if index == 1 else "later"
        lane = next(item for item in lanes if item["id"] == lane_id)
        lane_count = sum(1 for node in nodes if node["lane"] == lane_id)
        nodes.append(_visual_node(
            f"step-{index + 1}",
            f"Paso {index + 1}",
            step,
            int(lane["x"]) + 14,
            int(lane["y"]) + 52 + (lane_count * 122),
            width=192,
            height=96,
            node_type="task",
            lane=lane_id,
        ))
    return nodes, [], lanes, {"x": 0, "y": 0, "zoom": 0.85}


def _mind_map_template(project: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    center_text = project.get("summary") or project.get("selected_direction") or project.get("title", "")
    nodes = [
        _visual_node(
            "center",
            project.get("title", "Idea"),
            _visual_text(center_text),
            420,
            300,
            width=260,
            height=140,
            node_type="center",
        ),
        _visual_node("problem", "Problema", _visual_text(project.get("problem", "")), 80, 120),
        _visual_node("audience", "Audiencia", _visual_text(project.get("audience", "")), 760, 120),
        _visual_node("directions", "Direcciones", _visual_lines(project.get("creative_directions", [])), 80, 500),
        _visual_node("risks", "Riesgos", _visual_lines(project.get("risks", [])), 760, 500),
        _visual_node("questions", "Preguntas", _visual_lines(project.get("open_questions", [])), 420, 70),
        _visual_node("steps", "Proximos pasos", _visual_lines(project.get("next_steps", [])), 420, 560),
    ]
    edges = [
        _visual_edge("center", "problem"),
        _visual_edge("center", "audience"),
        _visual_edge("center", "directions"),
        _visual_edge("center", "risks"),
        _visual_edge("center", "questions"),
        _visual_edge("center", "steps"),
    ]
    return nodes, edges, [], {"x": 0, "y": 0, "zoom": 0.9}


def _build_visual_board(project: dict, board_kind: str, title: str = "") -> dict:
    cleaned_kind = str(board_kind).strip().lower()
    if cleaned_kind not in VALID_VISUAL_BOARD_KIND:
        raise ValueError("Tipo de board visual invalido. Usa uno de: " + ", ".join(sorted(VALID_VISUAL_BOARD_KIND)))

    template = {
        "idea_canvas": _idea_canvas_template,
        "decision_matrix": _decision_matrix_template,
        "roadmap_kanban": _roadmap_kanban_template,
        "mind_map": _mind_map_template,
    }[cleaned_kind]
    nodes, edges, lanes, viewport = template(project)
    now = _utc_timestamp()
    return {
        "id": _new_id("board"),
        "kind": cleaned_kind,
        "title": str(title).strip() or _default_visual_board_title(project, cleaned_kind),
        "nodes": nodes,
        "edges": edges,
        "lanes": lanes,
        "viewport": viewport,
        "export_paths": {},
        "created_at": now,
        "updated_at": now,
    }


def _find_visual_board(project: dict, board_id: str) -> dict | None:
    cleaned = str(board_id).strip().lower()
    if not cleaned:
        return None

    boards = project.get("visual_boards", []) or []
    for board in boards:
        if str(board.get("id", "")).lower() == cleaned:
            return board

    prefix_matches = [board for board in boards if str(board.get("id", "")).lower().startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    title_matches = [board for board in boards if str(board.get("title", "")).strip().lower() == cleaned]
    if len(title_matches) == 1:
        return title_matches[0]

    return None


def _visual_board_summary(board: dict) -> str:
    return (
        f"[{board.get('id', '')}] {board.get('title', 'Board visual')} "
        f"(tipo={board.get('kind', '-')}, nodos={len(board.get('nodes', []) or [])}, "
        f"edges={len(board.get('edges', []) or [])})"
    )


def _visual_export_dir(project_id: str) -> Path:
    safe_project_id = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(project_id).strip()).strip("-") or "project"
    export_dir = VISUAL_BOARDS_DIR / safe_project_id
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _visual_board_bounds(board: dict) -> tuple[float, float, float, float]:
    min_x = 0.0
    min_y = 0.0
    max_x = 960.0
    max_y = 720.0
    items = list(board.get("lanes", []) or []) + list(board.get("nodes", []) or [])
    if not items:
        return min_x, min_y, max_x, max_y

    min_x = min(float(item.get("x", 0)) for item in items)
    min_y = min(float(item.get("y", 0)) for item in items)
    max_x = max(float(item.get("x", 0)) + float(item.get("width", 220)) for item in items)
    max_y = max(float(item.get("y", 0)) + float(item.get("height", 120)) for item in items)
    margin = 60.0
    return min_x - margin, min_y - margin, max_x + margin, max_y + margin


def _wrap_svg_lines(value: str, line_chars: int = 30, max_lines: int = 6) -> list[str]:
    words = str(value or "").replace("\r", "").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > line_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines = [""]
    return lines[:max_lines]


def _svg_text_block(lines: list[str], x: float, y: float, *, css_class: str, line_height: int = 17) -> str:
    rendered = []
    for index, line in enumerate(lines):
        rendered.append(
            f'<text class="{css_class}" x="{x:.1f}" y="{(y + index * line_height):.1f}">'
            f"{html.escape(line)}</text>"
        )
    return "\n".join(rendered)


def _render_visual_board_svg(board: dict) -> str:
    min_x, min_y, max_x, max_y = _visual_board_bounds(board)
    width = max(320.0, max_x - min_x)
    height = max(240.0, max_y - min_y)
    nodes_by_id = {str(node.get("id", "")): node for node in board.get("nodes", []) or []}
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="{min_x:.1f} {min_y:.1f} {width:.1f} {height:.1f}" role="img">'
        ),
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#6d7a8d" />',
        "</marker>",
        "</defs>",
        "<style>",
        ".background{fill:#0f1115}.lane{fill:#151b24;stroke:#2f3948;stroke-width:1.2}.laneTitle{fill:#d9e2ef;font:700 15px system-ui}.edge{stroke:#6d7a8d;stroke-width:2;fill:none}.node{fill:#202838;stroke:#526074;stroke-width:1.4}.node.focus,.node.center{fill:#243552;stroke:#6fa1ff}.node.option{fill:#223247}.node.task{fill:#20362d}.title{fill:#f7f9fc;font:700 15px system-ui}.body{fill:#c0c9d6;font:12px system-ui}",
        "</style>",
        f'<rect class="background" x="{min_x:.1f}" y="{min_y:.1f}" width="{width:.1f}" height="{height:.1f}" />',
    ]

    for lane in board.get("lanes", []) or []:
        x = float(lane.get("x", 0))
        y = float(lane.get("y", 0))
        lane_width = float(lane.get("width", 260))
        lane_height = float(lane.get("height", 420))
        parts.append(
            f'<rect class="lane" x="{x:.1f}" y="{y:.1f}" width="{lane_width:.1f}" '
            f'height="{lane_height:.1f}" rx="8" />'
        )
        parts.append(
            f'<text class="laneTitle" x="{x + 14:.1f}" y="{y + 28:.1f}">'
            f"{html.escape(str(lane.get('title', 'Lane')))}</text>"
        )

    for edge in board.get("edges", []) or []:
        source = nodes_by_id.get(str(edge.get("source", "")))
        target = nodes_by_id.get(str(edge.get("target", "")))
        if not source or not target:
            continue
        x1 = float(source.get("x", 0)) + float(source.get("width", 220)) / 2
        y1 = float(source.get("y", 0)) + float(source.get("height", 120)) / 2
        x2 = float(target.get("x", 0)) + float(target.get("width", 220)) / 2
        y2 = float(target.get("y", 0)) + float(target.get("height", 120)) / 2
        parts.append(
            f'<line class="edge" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'marker-end="url(#arrow)" />'
        )

    for node in board.get("nodes", []) or []:
        x = float(node.get("x", 0))
        y = float(node.get("y", 0))
        node_width = float(node.get("width", 220))
        node_height = float(node.get("height", 120))
        node_type = html.escape(str(node.get("type", "note")))
        parts.append(
            f'<rect class="node {node_type}" x="{x:.1f}" y="{y:.1f}" width="{node_width:.1f}" '
            f'height="{node_height:.1f}" rx="8" />'
        )
        parts.append(_svg_text_block([str(node.get("title", "Nodo"))], x + 14, y + 26, css_class="title"))
        body_lines = _wrap_svg_lines(str(node.get("text", "")), max(18, int(node_width // 8)), 6)
        parts.append(_svg_text_block(body_lines, x + 14, y + 52, css_class="body"))

    parts.append("</svg>")
    return "\n".join(parts)


def _render_visual_board_html(project: dict, board: dict, svg_text: str) -> str:
    data = json.dumps({"project": project, "board": board}, ensure_ascii=False, indent=2)
    return "\n".join([
        "<!doctype html>",
        '<html lang="es">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(str(board.get('title', 'Board visual')))}</title>",
        "<style>",
        "body{margin:0;background:#0f1115;color:#f5f7fa;font-family:system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1200px;margin:0 auto;padding:18px}h1{font-size:22px}pre{white-space:pre-wrap;background:#171d27;border:1px solid #2f3948;border-radius:8px;padding:12px;overflow:auto}svg{max-width:100%;height:auto;border:1px solid #2f3948;border-radius:8px}",
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        f"<h1>{html.escape(str(board.get('title', 'Board visual')))}</h1>",
        svg_text,
        "<h2>Datos</h2>",
        f"<pre>{html.escape(data)}</pre>",
        "</main>",
        "</body>",
        "</html>",
    ])


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


def create_idea_project(
    title: str,
    kind: str = "mixto",
    summary: str = "",
    audience: str = "",
    desired_outcome: str = "",
    problem: str = "",
    creative_directions: str = "",
    selected_direction: str = "",
    success_criteria: str = "",
    constraints: str = "",
    risks: str = "",
    open_questions: str = "",
    next_steps: str = "",
    status: str = "exploring",
) -> str:
    """
    Crea un proyecto de idea para explorar producto, negocio o proyectos personales.

    Args:
        title (str): Nombre breve de la idea o proyecto.
        kind (str): producto_negocio, vida_proyecto, mixto u otro.
        summary (str): Brief corto de la idea.
        audience (str): Audiencia o personas beneficiarias.
        desired_outcome (str): Resultado deseado.
        problem (str): Problema u oportunidad que se busca resolver.
        creative_directions (str): Direcciones alternativas separadas por lineas, comas o punto y coma.
        selected_direction (str): Direccion elegida, si ya existe.
        success_criteria (str): Criterios de exito separados por lineas, comas o punto y coma.
        constraints (str): Restricciones separadas por lineas, comas o punto y coma.
        risks (str): Riesgos separados por lineas, comas o punto y coma.
        open_questions (str): Preguntas abiertas separadas por lineas, comas o punto y coma.
        next_steps (str): Proximos pasos separados por lineas, comas o punto y coma.
        status (str): exploring, planned, active, paused, done o archived.

    Returns:
        str: Confirmacion con el proyecto creado.
    """
    cleaned_title = str(title).strip()
    if not cleaned_title:
        return "Debes indicar un titulo para el proyecto de idea."

    cleaned_kind = str(kind).strip().lower() or "mixto"
    if cleaned_kind not in VALID_IDEA_PROJECT_KIND:
        return "Tipo invalido. Usa uno de: " + ", ".join(sorted(VALID_IDEA_PROJECT_KIND))

    cleaned_status = str(status).strip().lower() or "exploring"
    if cleaned_status not in VALID_IDEA_PROJECT_STATUS:
        return "Estado invalido. Usa uno de: " + ", ".join(sorted(VALID_IDEA_PROJECT_STATUS))

    now = datetime.now(timezone.utc).isoformat()
    project = {
        "id": _new_id("idea"),
        "title": cleaned_title,
        "kind": cleaned_kind,
        "status": cleaned_status,
        "summary": str(summary).strip(),
        "audience": str(audience).strip(),
        "desired_outcome": str(desired_outcome).strip(),
        "problem": str(problem).strip(),
        "creative_directions": _split_text_items(creative_directions),
        "selected_direction": str(selected_direction).strip(),
        "success_criteria": _split_text_items(success_criteria),
        "constraints": _split_text_items(constraints),
        "risks": _split_text_items(risks),
        "open_questions": _split_text_items(open_questions),
        "next_steps": _split_text_items(next_steps),
        "created_at": now,
        "updated_at": now,
    }

    state_transaction("create_idea_project", lambda state: state["idea_projects"].append(project))
    return "Proyecto de idea creado.\n" + _format_idea_project(project)


def list_idea_projects(status: str = "open", limit: int = 20) -> str:
    """
    Lista proyectos de ideas guardados.

    Args:
        status (str): open, all o un estado concreto.
        limit (int): Maximo de proyectos a mostrar.

    Returns:
        str: Listado resumido de proyectos.
    """
    state = load_state()
    projects = state.get("idea_projects", [])
    cleaned_status = str(status).strip().lower() or "open"
    if cleaned_status == "open":
        projects = [project for project in projects if project["status"] not in {"done", "archived"}]
    elif cleaned_status != "all":
        if cleaned_status not in VALID_IDEA_PROJECT_STATUS:
            return "Estado invalido. Usa open, all o uno de: " + ", ".join(sorted(VALID_IDEA_PROJECT_STATUS))
        projects = [project for project in projects if project["status"] == cleaned_status]

    if not projects:
        return "No hay proyectos de ideas que coincidan."

    try:
        normalized_limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 20

    lines = []
    for project in projects[:normalized_limit]:
        summary = project["summary"][:180]
        if len(project["summary"]) > 180:
            summary += "..."
        lines.append(
            f"[{project['id']}] {project['title']} "
            f"(tipo={project['kind']}, estado={project['status']})"
            + (f" - {summary}" if summary else "")
        )
    return "\n".join(lines)


def get_idea_project(project_id: str) -> str:
    """
    Muestra un proyecto de idea completo por id, prefijo unico o titulo exacto.

    Args:
        project_id (str): Id, prefijo o titulo exacto.

    Returns:
        str: Proyecto completo.
    """
    state = load_state()
    project = _find_idea_project(state.get("idea_projects", []), project_id)
    if not project:
        return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"
    return _format_idea_project(project)


def update_idea_project(
    project_id: str,
    title: str = "",
    kind: str = "",
    status: str = "",
    summary: str = "",
    audience: str = "",
    desired_outcome: str = "",
    problem: str = "",
    creative_directions: str = "",
    selected_direction: str = "",
    success_criteria: str = "",
    constraints: str = "",
    risks: str = "",
    open_questions: str = "",
    next_steps: str = "",
) -> str:
    """
    Actualiza un proyecto de idea. Usa [clear] para limpiar un campo o lista.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.

    Returns:
        str: Proyecto actualizado.
    """
    cleaned_kind = str(kind).strip().lower()
    if cleaned_kind and cleaned_kind != CLEAR_VALUE and cleaned_kind not in VALID_IDEA_PROJECT_KIND:
        return "Tipo invalido. Usa uno de: " + ", ".join(sorted(VALID_IDEA_PROJECT_KIND))

    cleaned_status = str(status).strip().lower()
    if cleaned_status and cleaned_status != CLEAR_VALUE and cleaned_status not in VALID_IDEA_PROJECT_STATUS:
        return "Estado invalido. Usa uno de: " + ", ".join(sorted(VALID_IDEA_PROJECT_STATUS))

    def mutate(state):
        project = _find_idea_project(state.get("idea_projects", []), project_id)
        if not project:
            return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

        if str(title).strip():
            project["title"] = str(title).strip()
        if cleaned_kind and cleaned_kind != CLEAR_VALUE:
            project["kind"] = cleaned_kind
        if cleaned_status and cleaned_status != CLEAR_VALUE:
            project["status"] = cleaned_status

        for field_name, value in (
            ("summary", summary),
            ("audience", audience),
            ("desired_outcome", desired_outcome),
            ("problem", problem),
            ("selected_direction", selected_direction),
        ):
            project[field_name] = _idea_project_text(value, current=project.get(field_name, ""))

        for field_name, value in (
            ("creative_directions", creative_directions),
            ("success_criteria", success_criteria),
            ("constraints", constraints),
            ("risks", risks),
            ("open_questions", open_questions),
            ("next_steps", next_steps),
        ):
            project[field_name] = _idea_project_items(value, current=project.get(field_name, []))

        project["updated_at"] = datetime.now(timezone.utc).isoformat()
        return "Proyecto de idea actualizado.\n" + _format_idea_project(project)

    return state_transaction("update_idea_project", mutate)


def promote_idea_project_to_work(project_id: str, priority: str = "media") -> str:
    """
    Convierte los proximos pasos de un proyecto de idea en plan actual y tareas.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.
        priority (str): Prioridad para tareas nuevas: alta, media o baja.

    Returns:
        str: Resumen del plan y tareas creadas.
    """
    cleaned_priority = str(priority).strip().lower() or "media"
    if cleaned_priority not in VALID_TASK_PRIORITY:
        cleaned_priority = "media"

    def mutate(state):
        project = _find_idea_project(state.get("idea_projects", []), project_id)
        if not project:
            return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

        next_steps = [step for step in project.get("next_steps", []) if str(step).strip()]
        if not next_steps:
            return "El proyecto no tiene proximos pasos para convertir en plan o tareas."

        now = datetime.now(timezone.utc).isoformat()
        project["status"] = "active"
        project["updated_at"] = now
        state["current_plan"] = next_steps

        existing_titles = {
            str(task.get("title", "")).strip().casefold()
            for task in state.get("tasks", [])
        }
        created_tasks = []
        for step in next_steps:
            marker = step.casefold()
            if marker in existing_titles:
                continue
            task = {
                "id": _new_id("task"),
                "title": step,
                "details": f"Proyecto de idea: {project['title']} ({project['id']})",
                "status": "pending",
                "priority": cleaned_priority,
                "result": "",
            }
            state["tasks"].append(task)
            created_tasks.append(task)
            existing_titles.add(marker)

        lines = [
            "Proyecto de idea activado.",
            f"Proyecto: [{project['id']}] {project['title']}",
            f"Plan actual: {len(next_steps)} paso(s)",
            f"Tareas nuevas: {len(created_tasks)}",
        ]
        for task in created_tasks:
            lines.append(f"- [{task['id']}] {task['title']}")
        return "\n".join(lines)

    return state_transaction("promote_idea_project_to_work", mutate)


def create_project_visual_board(project_id: str, board_kind: str, title: str = "") -> str:
    """
    Crea un board visual ligado a un proyecto de idea.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.
        board_kind (str): idea_canvas, decision_matrix, roadmap_kanban o mind_map.
        title (str): Titulo opcional del board.

    Returns:
        str: Confirmacion con el board creado.
    """
    cleaned_kind = str(board_kind).strip().lower()
    if cleaned_kind not in VALID_VISUAL_BOARD_KIND:
        return "Tipo de board visual invalido. Usa uno de: " + ", ".join(sorted(VALID_VISUAL_BOARD_KIND))

    def mutate(state):
        project = _find_idea_project(state.get("idea_projects", []), project_id)
        if not project:
            return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

        boards = project.setdefault("visual_boards", [])
        if len(boards) >= MAX_VISUAL_BOARDS_PER_PROJECT:
            return f"El proyecto ya tiene el maximo de {MAX_VISUAL_BOARDS_PER_PROJECT} boards visuales."

        board = _build_visual_board(project, cleaned_kind, title=title)
        boards.append(board)
        project["updated_at"] = _utc_timestamp()
        return "Board visual creado.\n" + _visual_board_summary(board)

    return state_transaction("create_project_visual_board", mutate)


def list_project_visual_boards(project_id: str) -> str:
    """
    Lista los boards visuales de un proyecto de idea.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.

    Returns:
        str: Listado resumido de boards.
    """
    state = load_state()
    project = _find_idea_project(state.get("idea_projects", []), project_id)
    if not project:
        return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

    boards = project.get("visual_boards", []) or []
    if not boards:
        return f"El proyecto [{project['id']}] {project['title']} no tiene boards visuales."

    lines = [f"Boards visuales de [{project['id']}] {project['title']}:"]
    lines.extend(f"- {_visual_board_summary(board)}" for board in boards)
    return "\n".join(lines)


def get_project_visual_board(project_id: str, board_id: str) -> str:
    """
    Devuelve un board visual completo como JSON.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.
        board_id (str): Id, prefijo o titulo exacto del board.

    Returns:
        str: JSON del board visual.
    """
    state = load_state()
    project = _find_idea_project(state.get("idea_projects", []), project_id)
    if not project:
        return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

    board = _find_visual_board(project, board_id)
    if not board:
        return f"No encontre un board visual con id, prefijo o titulo: {board_id}"

    return json.dumps(board, ensure_ascii=False, indent=2)


def update_project_visual_board(project_id: str, board_id: str, board_json: str) -> str:
    """
    Actualiza un board visual con JSON generado por la UI o por el agente.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.
        board_id (str): Id, prefijo o titulo exacto del board.
        board_json (str): JSON del board con nodes, edges, lanes y viewport.

    Returns:
        str: Confirmacion de actualizacion.
    """
    try:
        parsed = json.loads(str(board_json or "{}"))
    except json.JSONDecodeError as exc:
        return f"JSON de board visual invalido: {exc}"
    if not isinstance(parsed, dict):
        return "JSON de board visual invalido: debe ser un objeto."

    def mutate(state):
        project = _find_idea_project(state.get("idea_projects", []), project_id)
        if not project:
            return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"

        boards = project.get("visual_boards", []) or []
        board = _find_visual_board(project, board_id)
        if not board:
            return f"No encontre un board visual con id, prefijo o titulo: {board_id}"

        try:
            board_index = boards.index(board)
        except ValueError:
            return f"No encontre un board visual con id, prefijo o titulo: {board_id}"

        now = _utc_timestamp()
        next_board = {**board, **parsed}
        next_board["id"] = board.get("id", "")
        requested_kind = str(parsed.get("kind", board.get("kind", "idea_canvas"))).strip().lower()
        next_board["kind"] = requested_kind if requested_kind in VALID_VISUAL_BOARD_KIND else board.get("kind", "idea_canvas")
        next_board["created_at"] = board.get("created_at", "") or now
        next_board["updated_at"] = now
        boards[board_index] = next_board
        project["visual_boards"] = boards
        project["updated_at"] = now
        return "Board visual actualizado.\n" + _visual_board_summary(next_board)

    return state_transaction("update_project_visual_board", mutate)


def export_project_visual_board(
    project_id: str,
    board_id: str,
    formats: str = "html,svg,json",
    open_file: bool = False,
) -> str:
    """
    Exporta un board visual a archivos locales HTML, SVG y/o JSON.

    Args:
        project_id (str): Id, prefijo o titulo exacto del proyecto.
        board_id (str): Id, prefijo o titulo exacto del board.
        formats (str): Formatos separados por coma: html, svg, json.
        open_file (bool): Si es True, abre el HTML exportado o el primer archivo generado.

    Returns:
        str: Rutas exportadas.
    """
    requested_formats = {
        item.strip().lower()
        for item in re.split(r"[\n,;]+", str(formats or "html,svg,json"))
        if item.strip()
    }
    if not requested_formats:
        requested_formats = {"html", "svg", "json"}
    invalid_formats = requested_formats - {"html", "svg", "json"}
    if invalid_formats:
        return "Formato de exportacion invalido. Usa html, svg o json."

    state = load_state()
    project = _find_idea_project(state.get("idea_projects", []), project_id)
    if not project:
        return f"No encontre un proyecto de idea con id, prefijo o titulo: {project_id}"
    board = _find_visual_board(project, board_id)
    if not board:
        return f"No encontre un board visual con id, prefijo o titulo: {board_id}"

    export_dir = _visual_export_dir(project["id"])
    safe_board_id = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(board.get("id", "board"))).strip("-") or "board"
    exported_paths = {}
    svg_text = _render_visual_board_svg(board)

    if "json" in requested_formats:
        json_path = export_dir / f"{safe_board_id}.json"
        json_path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
        exported_paths["json"] = str(json_path)
    if "svg" in requested_formats:
        svg_path = export_dir / f"{safe_board_id}.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        exported_paths["svg"] = str(svg_path)
    if "html" in requested_formats:
        html_path = export_dir / f"{safe_board_id}.html"
        html_path.write_text(_render_visual_board_html(project, board, svg_text), encoding="utf-8")
        exported_paths["html"] = str(html_path)

    now = _utc_timestamp()

    def record_export_paths(state):
        current_project = _find_idea_project(state.get("idea_projects", []), project_id)
        if not current_project:
            return None
        current_board = _find_visual_board(current_project, board_id)
        if not current_board:
            return None
        current_board["export_paths"] = {**(current_board.get("export_paths", {}) or {}), **exported_paths}
        current_board["updated_at"] = now
        current_project["updated_at"] = now
        return None

    state_transaction("export_project_visual_board", record_export_paths)

    opened_text = ""
    if open_file and exported_paths:
        preferred_path = exported_paths.get("html") or next(iter(exported_paths.values()))
        opened_text = "\n" + open_system_target_impl(preferred_path)

    lines = ["Board visual exportado:"]
    lines.extend(f"- {key}: {path}" for key, path in exported_paths.items())
    if opened_text:
        lines.append(opened_text.strip())
    return "\n".join(lines)


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

        def mutate(state):
            existing = state.get("self_knowledge", {})
            insights = existing.get("insights", []) if isinstance(existing, dict) else []
            state["self_knowledge"] = {
                "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "source_signature": source_signature,
                "insights": insights,
            }

        state_transaction("self_overview", mutate)
    insights_block = render_self_insights(load_state())
    return f"{summary}\n\n{insights_block}" if insights_block else summary


def render_self_insights(state: dict | None = None) -> str:
    """Renderiza el self-model aprendido (insights sobre Yarbis mismo) desde el estado."""
    sk = (state or load_state()).get("self_knowledge", {})
    insights = sk.get("insights", []) if isinstance(sk, dict) else []
    if not insights:
        return ""
    lines = ["Autoconocimiento aprendido (lo que he aprendido sobre mi mismo):"]
    for item in insights:
        category = str(item.get("category", "leccion"))
        text = str(item.get("text", "")).strip()
        short_id = str(item.get("id", ""))
        lines.append(f"- [{category}] {text} (id: {short_id})")
    return "\n".join(lines)


def record_self_insight(text: str, category: str = "") -> str:
    """
    Guarda un aprendizaje SOBRE YARBIS MISMO (self-model actualizable).

    Distinto de save_note (que es contexto sobre el usuario/tareas). Usalo cuando
    descubras algo estable sobre ti: una fortaleza, un limite recurrente, una
    estrategia que te funciona o una leccion tras un fallo/correccion.

    Args:
        text (str): El aprendizaje concreto sobre ti mismo.
        category (str): fortaleza | limite | estrategia | leccion (default leccion).

    Returns:
        str: Confirmacion del insight guardado.
    """
    cleaned_text = str(text).strip()
    if not cleaned_text:
        return "Debes indicar el aprendizaje concreto sobre ti mismo."

    cleaned_category = str(category).strip().lower()
    if cleaned_category not in VALID_SELF_INSIGHT_CATEGORIES:
        cleaned_category = DEFAULT_SELF_INSIGHT_CATEGORY

    now = datetime.now(timezone.utc).isoformat()
    new_id = f"insight-{uuid4().hex[:10]}"

    def mutate(state):
        sk = state.setdefault("self_knowledge", {})
        if not isinstance(sk, dict):
            sk = {}
            state["self_knowledge"] = sk
        insights = sk.setdefault("insights", [])
        if not isinstance(insights, list):
            insights = []
            sk["insights"] = insights
        # Evitar duplicados por texto identico (case-insensitive).
        for existing in insights:
            if str(existing.get("text", "")).strip().lower() == cleaned_text.lower():
                existing["category"] = cleaned_category
                existing["updated_at"] = now
                return existing.get("id", "")
        insights.append({
            "id": new_id,
            "category": cleaned_category,
            "text": cleaned_text,
            "created_at": now,
            "updated_at": now,
        })
        return new_id

    saved_id = state_transaction("record_self_insight", mutate)
    return f"Insight guardado ({cleaned_category}): {cleaned_text}\nid: {saved_id}"


def list_self_insights() -> str:
    """
    Lista los aprendizajes que Yarbis tiene sobre si mismo (self-model).

    Returns:
        str: Insights por categoria, o aviso si no hay ninguno.
    """
    block = render_self_insights(load_state())
    if not block:
        return "Aun no tengo aprendizajes registrados sobre mi mismo. Usa record_self_insight cuando descubras alguno."
    return block


def remove_self_insight(insight_id: str) -> str:
    """
    Elimina un aprendizaje del self-model por su id.

    Args:
        insight_id (str): Id del insight a eliminar (ver list_self_insights).

    Returns:
        str: Resultado de la eliminacion.
    """
    cleaned_id = str(insight_id).strip()
    if not cleaned_id:
        return "Debes indicar el id del insight a eliminar."

    def mutate(state):
        sk = state.get("self_knowledge", {})
        if not isinstance(sk, dict):
            return False
        insights = sk.get("insights", [])
        if not isinstance(insights, list):
            return False
        before = len(insights)
        sk["insights"] = [i for i in insights if str(i.get("id", "")) != cleaned_id]
        return len(sk["insights"]) < before

    removed = state_transaction("remove_self_insight", mutate)
    return f"Insight {cleaned_id} eliminado." if removed else f"No encontre un insight con id {cleaned_id}."


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


def list_pending_user_questions() -> str:
    """
    Lista las otras instancias de Yarbis que estan PAUSADAS esperando una respuesta del usuario.

    Util cuando el usuario pide "desbloquea/responde por mi a las instancias que me
    esperan": muestra que instancia esta bloqueada, su pregunta pendiente y si esta
    activa. Luego usa answer_instance_for_user para desbloquear cada una.

    Returns:
        str: Instancias con pregunta pendiente (o aviso de que no hay ninguna).
    """
    current = yarbis_instance.current_instance_id()
    lines = []
    for item in yarbis_instance.list_instances():
        instance_id = str(item.get("id", "")).strip()
        if not instance_id or instance_id == current:
            continue
        try:
            state_payload = read_json_bom_safe(Path(item["state_file"]))
        except (OSError, ValueError, KeyError):
            continue
        awaiting = state_payload.get("awaiting_user_input", {})
        if not isinstance(awaiting, dict) or not awaiting.get("pending"):
            continue
        question = str(awaiting.get("question", "")).strip()
        if not question:
            continue
        reason = str(awaiting.get("reason", "")).strip()
        active = yarbis_bus.instance_is_active(instance_id)
        detail = f"- {instance_id} ({'activa' if active else 'inactiva'}): {question}"
        if reason:
            detail += f" | motivo: {reason}"
        lines.append(detail)

    if not lines:
        return "Ninguna otra instancia esta esperando tu respuesta ahora mismo."
    return (
        "Instancias esperando tu respuesta:\n"
        + "\n".join(lines)
        + "\n\nUsa answer_instance_for_user(instancia, respuesta) para desbloquear cada una."
    )


def answer_instance_for_user(target_instance: str, answer: str) -> str:
    """
    Responde EN NOMBRE DEL USUARIO a otra instancia que espera su respuesta, desbloqueandola.

    La instancia destino procesa la respuesta como si la hubiera dado el usuario:
    limpia su pausa (awaiting_user_input) y retoma. Queda atribuida en su historial.

    Args:
        target_instance (str): Id de la instancia a desbloquear.
        answer (str): Respuesta a entregar en nombre del usuario.

    Returns:
        str: Estado de la entrega y, si estuvo activa, la salida de la instancia al retomar.
    """
    cleaned_answer = str(answer).strip()
    if not cleaned_answer:
        return "Debes indicar la respuesta a entregar en nombre del usuario."
    try:
        result = yarbis_bus.send_user_answer(
            target_instance,
            cleaned_answer,
            wait_for_reply=True,
            timeout_seconds=120,
        )
    except Exception as exc:
        return f"No pude responder a la instancia: {exc}"

    status = str(result.get("status", "")).strip()
    status_text = str(result.get("status_text", "")).strip()
    target = str(result.get("to_instance", target_instance)).strip()
    message_id = str(result.get("id", "")).strip()
    if status == yarbis_bus.STATUS_DONE:
        response = str(result.get("response", "")).strip() or "Sin salida visible."
        return f"Desbloquee a {target} ({message_id}) con tu respuesta.\n\nRetomo asi:\n{response}"
    if status == yarbis_bus.STATUS_ERROR:
        error = str(result.get("error", "")).strip() or "Error desconocido."
        return f"La instancia {target} ({message_id}) fallo al retomar: {error}"
    if status_text == "timeout":
        return f"Respuesta entregada a {target} ({message_id}), pero no confirmo el retome antes del timeout."
    return f"Respuesta en cola para {target} ({message_id}); la aplicara cuando este activa."


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


def set_timezone(timezone: str = "") -> str:
    """
    Configura la zona horaria del usuario para que Yarbis interprete bien hoy,
    manana, ayer, horarios y la hora actual. Formato IANA (p.ej. America/Mexico_City).

    Args:
        timezone (str): Zona IANA (America/Mexico_City, Europe/Madrid, etc.).
            Vacio o CLEAR para volver a la zona del sistema.

    Returns:
        str: Confirmacion con la hora local resultante.
    """
    raw = str(timezone).strip()
    if not raw or raw == CLEAR_VALUE:
        cleaned = ""
    else:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(raw)
            cleaned = raw
        except Exception:
            return (
                f"Zona horaria invalida: {raw}. Usa formato IANA como "
                "America/Mexico_City, Europe/Madrid o America/Argentina/Buenos_Aires."
            )

    def mutate(state):
        state["profile"]["timezone"] = cleaned

    state_transaction("set_timezone", mutate)

    if cleaned:
        from zoneinfo import ZoneInfo

        now_local = datetime.now(ZoneInfo(cleaned))
        zona = cleaned
    else:
        now_local = datetime.now().astimezone()
        zona = f"sistema ({now_local.tzname()})"
    return (
        f"Zona horaria configurada: {zona}.\n"
        f"Hora local ahora: {now_local.strftime('%Y-%m-%d %H:%M')}."
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


def _find_media_inbox_entry(state: dict, media_id: str) -> dict | None:
    cleaned = str(media_id).strip().lower()
    if not cleaned:
        return None
    entries = state.get("social", {}).get("media_inbox", [])
    for entry in entries:
        if str(entry.get("id", "")).lower() == cleaned:
            return entry
    matches = [
        entry for entry in entries
        if str(entry.get("id", "")).lower().startswith(cleaned)
    ]
    return matches[0] if len(matches) == 1 else None


def _apply_media_source(target: dict, entry: dict) -> None:
    """Rellena media_path/media_type/alt_text de un draft o publicacion a partir
    de una entrada de media_inbox, y recuerda el telegram_file_id en metadata."""
    path = str(entry.get("path", "")).strip()
    if path and not str(target.get("media_path", "")).strip():
        target["media_path"] = path
    if not str(target.get("media_type", "")).strip():
        target["media_type"] = str(entry.get("media_type", "image")).strip().lower() or "image"
    if not str(target.get("alt_text", "")).strip() and str(entry.get("alt_text", "")).strip():
        target["alt_text"] = str(entry.get("alt_text", "")).strip()
    file_id = str(entry.get("telegram_file_id", "")).strip()
    if file_id:
        metadata = target.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["telegram_file_id"] = file_id
            metadata["media_id"] = str(entry.get("id", "")).strip()


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
    media_inbox = social.get("media_inbox", [])

    lines = [
        "Redes sociales:",
        f"Cuentas conectadas: {len(accounts)}",
        f"Drafts: {len(drafts)}",
        f"Publicaciones pendientes: {len(pending)}",
        f"Imagenes recientes (de Telegram, listas para publicar): {len(media_inbox)}",
    ]
    if media_inbox:
        lines.append("Imagenes recientes:")
        for entry in reversed(media_inbox[-5:]):
            caption = str(entry.get("caption", "")).strip()
            suffix = f": {caption[:60]}" if caption else ""
            lines.append(f"- [{entry['id']}] {entry.get('received_at', '')}{suffix}")
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
    media_id: str = "",
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
        media_id (str): Id (media-xxx) de una imagen recibida por Telegram (ver
            list_recent_media). Si se indica, adjunta esa imagen a la publicacion.

    Returns:
        str: Confirmacion del draft guardado.
    """
    cleaned_platform = str(platform).strip().lower()
    if cleaned_platform not in VALID_SOCIAL_PLATFORMS:
        return "Plataforma social invalida. Usa facebook_page, facebook_personal, instagram o linkedin."

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
    if str(media_id).strip():
        entry = _find_media_inbox_entry(load_state(), media_id)
        if not entry:
            return f"No encontre una imagen reciente con id o prefijo: {media_id}"
        _apply_media_source(draft, entry)

    if not draft["body"] and not draft["link_url"] and not draft["media_url"] and not draft["media_path"]:
        return "El draft necesita copy, link o media."

    state_transaction("save_social_draft", lambda state: state.setdefault("social", {}).setdefault("drafts", []).append(draft))
    return f"Draft social guardado con id {draft['id']}: {draft['title']} ({draft['platform']})"


def list_recent_media(limit: int = 10) -> str:
    """
    Lista las imagenes recientes recibidas por Telegram, disponibles para publicar
    en redes sociales. Usa el id (media-xxx) como media_id en save_social_draft o
    prepare_social_publication.

    Args:
        limit (int): Maximo de imagenes a mostrar.

    Returns:
        str: Lista de imagenes recientes con su id, fecha y caption.
    """
    state = load_state()
    media_inbox = state.get("social", {}).get("media_inbox", [])
    if not media_inbox:
        return "No hay imagenes recientes. Envia una foto por Telegram para poder publicarla."
    try:
        normalized_limit = max(1, min(15, int(limit)))
    except (TypeError, ValueError):
        normalized_limit = 10
    lines = ["Imagenes recientes (usa el id como media_id para publicar):"]
    for entry in reversed(media_inbox[-normalized_limit:]):
        caption = str(entry.get("caption", "")).strip()
        suffix = f" - {caption[:80]}" if caption else ""
        lines.append(
            f"[{entry['id']}] {entry.get('media_type', 'image')} "
            f"{entry.get('received_at', '')}{suffix}"
        )
    return "\n".join(lines)


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
    media_id: str = "",
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
        media_id (str): Id (media-xxx) de una imagen recibida por Telegram (ver
            list_recent_media). Si se indica, adjunta esa imagen a la publicacion;
            para Instagram/Facebook Pagina la imagen se sirve al publicar.

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

    if str(media_id).strip():
        entry = _find_media_inbox_entry(state, media_id)
        if not entry:
            return f"No encontre una imagen reciente con id o prefijo: {media_id}"
        _apply_media_source(publication, entry)

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
    media_path = str(publication.get("media_path", "")).strip()
    if media_path:
        lines.append(f"Imagen para adjuntar a mano: {media_path}")
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

    require_confirmation = bool(state.get("social", {}).get("settings", {}).get("require_confirmation", True))
    required = f"PUBLICAR {publication['id']}"
    if require_confirmation and str(confirmation).strip() != required:
        return (
            "Publicacion bloqueada por seguridad.\n"
            f"Para confirmar, responde exactamente: {required}\n"
            "(O desactiva la confirmacion social con set_social_confirmation(False) / desde la UI.)"
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

    # Instagram/Facebook Pagina necesitan una URL publica de la imagen. Si la
    # publicacion viene de una foto de Telegram (metadata.telegram_file_id) y no
    # trae media_url, regeneramos una URL fresca de Telegram justo antes de
    # publicar (caduca en ~1h; Meta la descarga del lado servidor).
    if platform in {"instagram", "facebook_page"} and not str(publication.get("media_url", "")).strip():
        telegram_file_id = str(publication.get("metadata", {}).get("telegram_file_id", "")).strip()
        if telegram_file_id:
            try:
                from notifications import telegram_file_public_url

                publication["media_url"] = telegram_file_public_url(telegram_file_id)
            except Exception:
                pass

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


# ---------------------------------------------------------------------------
# Autoevolucion: control y capa de "directivas aprendidas" (comportamiento).
# Todo pasa por aprobacion del usuario; nada se aplica solo.
# ---------------------------------------------------------------------------

def _evolution_state(state: dict | None = None) -> dict:
    source_state = state if isinstance(state, dict) else load_state()
    evolution = source_state.get("evolution", {})
    return evolution if isinstance(evolution, dict) else {}


def evolution_status() -> str:
    """
    Muestra el estado de la autoevolucion de Yarbis.

    Returns:
        str: enabled, cadencia, limites, dimensiones y conteo de pendientes.
    """
    evolution = _evolution_state()
    directives = evolution.get("directives", []) if isinstance(evolution.get("directives"), list) else []
    pending = evolution.get("directives_pending", []) if isinstance(evolution.get("directives_pending"), list) else []
    suggestions = evolution.get("suggestions_pending", []) if isinstance(evolution.get("suggestions_pending"), list) else []
    dims = evolution.get("dimensions", []) if isinstance(evolution.get("dimensions"), list) else []
    return (
        "Autoevolucion de Yarbis:\n"
        f"- Estado: {'activada' if evolution.get('enabled') else 'desactivada'}\n"
        f"- Cadencia: cada {evolution.get('interval_hours', 6)}h\n"
        f"- Maximo de propuestas pendientes: {evolution.get('max_pending', 2)}\n"
        f"- Dimensiones: {', '.join(dims) or '-'}\n"
        f"- Directrices aprobadas: {len(directives)}\n"
        f"- Directrices propuestas por aprobar: {len(pending)}\n"
        f"- Sugerencias objetivo/memoria por aprobar: {len(suggestions)}"
    )


def evolution_set_enabled(enabled: bool) -> str:
    """
    Activa o desactiva la autoevolucion (revision periodica que propone mejoras).

    Args:
        enabled (bool): True para activar, False para desactivar.

    Returns:
        str: Confirmacion.
    """
    value = bool(enabled)

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        evolution_state["enabled"] = value

    state_transaction("evolution_set_enabled", mutate)
    return f"Autoevolucion {'activada' if value else 'desactivada'}."


def evolution_set_interval(hours: int) -> str:
    """
    Ajusta la cadencia de la autoevolucion en horas (1 a 168).

    Args:
        hours (int): Horas entre revisiones.

    Returns:
        str: Confirmacion.
    """
    try:
        cleaned = int(hours)
    except (TypeError, ValueError):
        return "La cadencia debe ser un numero de horas."
    cleaned = max(1, min(168, cleaned))

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        evolution_state["interval_hours"] = cleaned

    state_transaction("evolution_set_interval", mutate)
    return f"Cadencia de autoevolucion: cada {cleaned}h."


def evolution_propose_directive(text: str, reason: str = "") -> str:
    """
    Propone una directriz de comportamiento para que el usuario la apruebe.

    No cambia el comportamiento hasta que el usuario la apruebe con
    evolution_apply_directive.

    Args:
        text (str): La directriz concreta y accionable.
        reason (str): Motivo breve.

    Returns:
        str: Id de la directriz propuesta.
    """
    cleaned_text = str(text).strip()[:MAX_EVOLUTION_DIRECTIVE_CHARS]
    if not cleaned_text:
        return "La directriz no puede quedar vacia."
    directive_id = uuid4().hex[:12]
    entry = {
        "id": directive_id,
        "text": cleaned_text,
        "reason": str(reason).strip()[:MAX_EVOLUTION_DIRECTIVE_REASON_CHARS],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    overflow = {"value": False}

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        pending = evolution_state.setdefault("directives_pending", [])
        if not isinstance(pending, list):
            pending = []
        if len(pending) >= MAX_EVOLUTION_DIRECTIVES:
            overflow["value"] = True
            return
        pending.append(entry)
        evolution_state["directives_pending"] = pending

    state_transaction("evolution_propose_directive", mutate)
    if overflow["value"]:
        return "Ya hay demasiadas directrices propuestas; aprueba o descarta algunas antes."
    return (
        "Directriz propuesta (pendiente de aprobacion).\n"
        f"Id: {directive_id}\n"
        f"Texto: {cleaned_text}\n"
        "El usuario la aprueba con evolution_apply_directive o la descarta con evolution_discard_directive."
    )


def _format_directive_list(items: list) -> str:
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        directive_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        reason = str(item.get("reason", "")).strip()
        line = f"- [{directive_id}] {text}"
        if reason:
            line += f" (motivo: {reason})"
        lines.append(line)
    return "\n".join(lines) if lines else "- (ninguna)"


def evolution_list_pending() -> str:
    """
    Lista las directrices de comportamiento propuestas por aprobar.

    Returns:
        str: Directrices pendientes con su id.
    """
    evolution = _evolution_state()
    pending = evolution.get("directives_pending", []) if isinstance(evolution.get("directives_pending"), list) else []
    return "Directrices propuestas por aprobar:\n" + _format_directive_list(pending)


def evolution_list_directives() -> str:
    """
    Lista las directrices de comportamiento ya aprobadas y activas.

    Returns:
        str: Directrices activas con su id.
    """
    evolution = _evolution_state()
    directives = evolution.get("directives", []) if isinstance(evolution.get("directives"), list) else []
    return "Directrices aprobadas y activas:\n" + _format_directive_list(directives)


def evolution_apply_directive(directive_id: str) -> str:
    """
    Aprueba una directriz propuesta: pasa a activa e influye en el comportamiento.

    Args:
        directive_id (str): Id de la directriz pendiente.

    Returns:
        str: Confirmacion.
    """
    cleaned_id = str(directive_id).strip()
    if not cleaned_id:
        return "Indica el id de la directriz a aprobar."
    result = {"applied": None, "overflow": False}

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        pending = evolution_state.get("directives_pending", [])
        directives = evolution_state.get("directives", [])
        if not isinstance(pending, list):
            pending = []
        if not isinstance(directives, list):
            directives = []
        match = next((d for d in pending if isinstance(d, dict) and str(d.get("id", "")).strip() == cleaned_id), None)
        if match is None:
            return
        if len(directives) >= MAX_EVOLUTION_DIRECTIVES:
            result["overflow"] = True
            return
        pending = [d for d in pending if d is not match]
        directives.append(match)
        evolution_state["directives_pending"] = pending
        evolution_state["directives"] = directives
        result["applied"] = match

    state_transaction("evolution_apply_directive", mutate)
    if result["overflow"]:
        return "Ya hay demasiadas directrices activas; elimina alguna antes de aprobar mas."
    if result["applied"] is None:
        return f"No encontre una directriz pendiente con id {cleaned_id}."
    return f"Directriz aprobada y activa: {str(result['applied'].get('text', '')).strip()}"


def evolution_discard_directive(directive_id: str) -> str:
    """
    Descarta una directriz (propuesta o activa) por su id.

    Args:
        directive_id (str): Id de la directriz.

    Returns:
        str: Confirmacion.
    """
    cleaned_id = str(directive_id).strip()
    if not cleaned_id:
        return "Indica el id de la directriz a descartar."
    removed = {"value": False}

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        for key in ("directives_pending", "directives"):
            items = evolution_state.get(key, [])
            if not isinstance(items, list):
                continue
            filtered = [d for d in items if not (isinstance(d, dict) and str(d.get("id", "")).strip() == cleaned_id)]
            if len(filtered) != len(items):
                removed["value"] = True
            evolution_state[key] = filtered

    state_transaction("evolution_discard_directive", mutate)
    return "Directriz descartada." if removed["value"] else f"No encontre una directriz con id {cleaned_id}."


def _evolution_propose_suggestion(kind: str, text: str, reason: str) -> str:
    cleaned_text = str(text).strip()[:MAX_EVOLUTION_SUGGESTION_CHARS]
    if not cleaned_text:
        return "La sugerencia no puede quedar vacia."
    if kind not in EVOLUTION_SUGGESTION_KINDS:
        return f"Tipo de sugerencia invalido: {kind}."
    suggestion_id = uuid4().hex[:12]
    entry = {
        "id": suggestion_id,
        "kind": kind,
        "text": cleaned_text,
        "reason": str(reason).strip()[:MAX_EVOLUTION_DIRECTIVE_REASON_CHARS],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    overflow = {"value": False}

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        pending = evolution_state.setdefault("suggestions_pending", [])
        if not isinstance(pending, list):
            pending = []
        if len(pending) >= MAX_EVOLUTION_SUGGESTIONS:
            overflow["value"] = True
            return
        pending.append(entry)
        evolution_state["suggestions_pending"] = pending

    state_transaction("evolution_propose_suggestion", mutate)
    if overflow["value"]:
        return "Ya hay demasiadas sugerencias propuestas; aprueba o descarta algunas antes."
    label = "objetivo" if kind == "goal" else "memoria"
    return (
        f"Sugerencia de {label} propuesta (pendiente de aprobacion).\n"
        f"Id: {suggestion_id}\n"
        f"Texto: {cleaned_text}\n"
        "El usuario la aprueba con evolution_apply_suggestion o la descarta con evolution_discard_suggestion."
    )


def evolution_propose_goal(text: str, reason: str = "") -> str:
    """
    Propone un objetivo principal refinado para que el usuario lo apruebe.

    No cambia el objetivo hasta que el usuario apruebe con evolution_apply_suggestion.

    Args:
        text (str): El objetivo propuesto.
        reason (str): Motivo breve del refinamiento.

    Returns:
        str: Id de la sugerencia propuesta.
    """
    return _evolution_propose_suggestion("goal", text, reason)


def evolution_propose_memory(text: str, reason: str = "") -> str:
    """
    Propone una nota de memoria (aprendizaje a persistir) para aprobacion del usuario.

    No guarda nada hasta que el usuario apruebe con evolution_apply_suggestion.

    Args:
        text (str): El aprendizaje o nota propuesta.
        reason (str): Motivo breve.

    Returns:
        str: Id de la sugerencia propuesta.
    """
    return _evolution_propose_suggestion("memory", text, reason)


def evolution_list_suggestions() -> str:
    """
    Lista las sugerencias de objetivo/memoria propuestas por aprobar.

    Returns:
        str: Sugerencias pendientes con su id y tipo.
    """
    evolution = _evolution_state()
    pending = evolution.get("suggestions_pending", []) if isinstance(evolution.get("suggestions_pending"), list) else []
    if not pending:
        return "Sugerencias de objetivo/memoria por aprobar:\n- (ninguna)"
    lines = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        kind = "objetivo" if item.get("kind") == "goal" else "memoria"
        sid = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        reason = str(item.get("reason", "")).strip()
        line = f"- [{sid}] ({kind}) {text}"
        if reason:
            line += f" (motivo: {reason})"
        lines.append(line)
    return "Sugerencias de objetivo/memoria por aprobar:\n" + "\n".join(lines)


def evolution_apply_suggestion(suggestion_id: str) -> str:
    """
    Aprueba una sugerencia de objetivo/memoria: la aplica y la quita de pendientes.

    Para 'goal' actualiza el objetivo principal; para 'memory' guarda una nota.

    Args:
        suggestion_id (str): Id de la sugerencia pendiente.

    Returns:
        str: Resultado de aplicar la sugerencia.
    """
    cleaned_id = str(suggestion_id).strip()
    if not cleaned_id:
        return "Indica el id de la sugerencia a aprobar."

    match = None
    for item in _evolution_state().get("suggestions_pending", []):
        if isinstance(item, dict) and str(item.get("id", "")).strip() == cleaned_id:
            match = item
            break
    if match is None:
        return f"No encontre una sugerencia pendiente con id {cleaned_id}."

    kind = str(match.get("kind", "")).strip()
    text = str(match.get("text", "")).strip()
    if kind == "goal":
        applied_result = update_goal(text)
    elif kind == "memory":
        title = (text.split("\n", 1)[0])[:80] or "Aprendizaje de autoevolucion"
        applied_result = save_note(title, text, category="autoevolucion")
    else:
        return f"Tipo de sugerencia invalido: {kind}."

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        pending = evolution_state.get("suggestions_pending", [])
        if not isinstance(pending, list):
            pending = []
        evolution_state["suggestions_pending"] = [
            d for d in pending if not (isinstance(d, dict) and str(d.get("id", "")).strip() == cleaned_id)
        ]

    state_transaction("evolution_apply_suggestion", mutate)
    return f"Sugerencia aprobada y aplicada.\n{applied_result}"


def evolution_discard_suggestion(suggestion_id: str) -> str:
    """
    Descarta una sugerencia de objetivo/memoria por su id.

    Args:
        suggestion_id (str): Id de la sugerencia.

    Returns:
        str: Confirmacion.
    """
    cleaned_id = str(suggestion_id).strip()
    if not cleaned_id:
        return "Indica el id de la sugerencia a descartar."
    removed = {"value": False}

    def mutate(state):
        evolution_state = state.setdefault("evolution", {})
        pending = evolution_state.get("suggestions_pending", [])
        if not isinstance(pending, list):
            pending = []
        filtered = [d for d in pending if not (isinstance(d, dict) and str(d.get("id", "")).strip() == cleaned_id)]
        if len(filtered) != len(pending):
            removed["value"] = True
        evolution_state["suggestions_pending"] = filtered

    state_transaction("evolution_discard_suggestion", mutate)
    return "Sugerencia descartada." if removed["value"] else f"No encontre una sugerencia con id {cleaned_id}."


# ---------------------------------------------------------------------------
# Vision: analisis de imagenes con un modelo multimodal de Ollama Cloud.
# ---------------------------------------------------------------------------

def analyze_image(path: str, question: str = "") -> str:
    """
    Analiza una imagen del disco con el modelo de vision y devuelve una descripcion.

    Usala cuando el usuario comparta una imagen o cuando necesites entender el
    contenido de una imagen (captura, foto, diagrama). Puede transcribir texto (OCR).

    Args:
        path (str): Ruta de la imagen (absoluta o relativa al workspace de Yarbis).
        question (str): Pregunta o instruccion opcional sobre la imagen.

    Returns:
        str: Analisis o respuesta sobre la imagen.
    """
    cleaned = str(path).strip()
    if not cleaned:
        return "Indica la ruta de la imagen."
    candidate = Path(cleaned)
    resolved = (candidate if candidate.is_absolute() else (WORKSPACE_ROOT / candidate)).resolve()
    if not resolved.exists():
        return f"No encontre la imagen: {resolved}"
    if not resolved.is_file():
        return f"La ruta no es un archivo: {resolved}"
    try:
        return vision.analyze_image(resolved, question)
    except vision.VisionError as exc:
        return f"No pude analizar la imagen: {exc}"
    except Exception as exc:
        return f"Error inesperado analizando la imagen: {exc}"


def analyze_images(paths, question: str = "") -> str:
    """Analiza VARIAS imagenes del disco juntas en una sola respuesta.

    `paths` es una lista de rutas (o una sola ruta). Uso de canales
    escritorio/movil; el agente usa analyze_image por imagen.
    """
    if isinstance(paths, str):
        paths = [paths]
    resolved = []
    for path in paths or []:
        cleaned = str(path).strip()
        if not cleaned:
            continue
        candidate = Path(cleaned)
        target = (candidate if candidate.is_absolute() else (WORKSPACE_ROOT / candidate)).resolve()
        if not target.exists() or not target.is_file():
            return f"No encontre la imagen: {target}"
        resolved.append(target)
    if not resolved:
        return "Indica al menos una imagen."
    try:
        return vision.analyze_images(resolved, question)
    except vision.VisionError as exc:
        return f"No pude analizar las imagenes: {exc}"
    except Exception as exc:
        return f"Error inesperado analizando las imagenes: {exc}"


def vision_status() -> str:
    """
    Muestra la configuracion de vision (modelo, tamano max de imagen, timeout).

    Returns:
        str: Estado de vision.
    """
    model, max_dim, timeout, _ = vision._vision_settings(load_state())
    return (
        "Vision de Yarbis:\n"
        f"- Modelo: {model}\n"
        f"- Tamano maximo de imagen: {max_dim}px\n"
        f"- Timeout: {timeout}s"
    )


def vision_set_model(model: str) -> str:
    """
    Cambia el modelo de vision (debe ser un modelo multimodal de Ollama Cloud).

    Args:
        model (str): Nombre del modelo (ej. gemma3:12b-cloud).

    Returns:
        str: Confirmacion.
    """
    cleaned = str(model).strip()[:MAX_VISION_MODEL_CHARS]
    if not cleaned:
        return "Indica el nombre del modelo de vision."

    def mutate(state):
        state.setdefault("vision", {})["model"] = cleaned

    state_transaction("vision_set_model", mutate)
    return f"Modelo de vision configurado: {cleaned}"


# --------------------------------------------------------------------------- #
# Servicio de fondo multiplataforma (SCM en Windows; systemd/launchd en
# Linux/macOS). La fachada elige el backend por SO; Windows sigue usando
# service_manager (SCM) sin cambios.
# --------------------------------------------------------------------------- #

def _background_service_backend():
    """Devuelve (modulo_backend, etiqueta) segun el SO: SCM/systemd/launchd."""
    if os.name == "nt":
        import service_manager
        return service_manager, "SCM"
    import native_service
    kind = native_service.service_manager_kind()
    label = {"systemd": "systemd", "launchd": "launchd"}.get(kind, "servicio nativo")
    return native_service, label


def _format_background_service_status(status: dict, label: str) -> str:
    if not status.get("installed"):
        return f"Servicio de Yarbis ({label}): no instalado."
    estado = "activo" if status.get("running") else "detenido"
    pid = status.get("pid")
    pid_txt = f" (PID {pid})" if pid else ""
    auto = "si" if status.get("autostart_enabled") else "no"
    lines = [
        f"Servicio de Yarbis ({label}): {estado}{pid_txt}.",
        f"- Arranque automatico: {auto}",
    ]
    log_file = status.get("log_file") or ""
    if log_file:
        lines.append(f"- Log: {log_file}")
    if status.get("workspace_mismatch"):
        lines.append("- Aviso: el binario registrado apunta a otra carpeta de trabajo.")
    return "\n".join(lines)


def background_service_status() -> str:
    """
    Muestra el estado del servicio de fondo de Yarbis en este SO.

    Windows usa el gestor de servicios (SCM); Linux usa systemd (--user) y macOS
    usa launchd (LaunchAgent). Informa si esta instalado, activo, su PID y si
    arranca automaticamente.

    Returns:
        str: Resumen del estado del servicio.
    """
    backend, label = _background_service_backend()
    try:
        status = backend.get_service_status()
    except Exception as exc:
        return f"No pude consultar el servicio ({label}): {exc}"
    return _format_background_service_status(status, label)


def install_background_service(autostart: bool = True) -> str:
    """
    Instala el servicio de fondo de Yarbis en este SO (SCM/systemd/launchd).

    En Linux escribe una unit de systemd (--user); en macOS un LaunchAgent de
    launchd; en Windows crea el servicio en SCM (puede requerir privilegios de
    administrador y compilar el host .NET).

    Args:
        autostart (bool): Si arranca automaticamente al iniciar sesion/sistema.

    Returns:
        str: Resultado de la instalacion.
    """
    backend, label = _background_service_backend()
    try:
        return backend.install_service(start_auto=bool(autostart))
    except Exception as exc:
        return f"No pude instalar el servicio ({label}): {exc}"


def start_background_service() -> str:
    """
    Arranca el servicio de fondo de Yarbis (lo instala antes si hace falta).

    Returns:
        str: Resultado del arranque.
    """
    backend, label = _background_service_backend()
    try:
        return backend.start_service()
    except Exception as exc:
        return f"No pude iniciar el servicio ({label}): {exc}"


def stop_background_service() -> str:
    """
    Detiene el servicio de fondo de Yarbis.

    Returns:
        str: Resultado de la parada.
    """
    backend, label = _background_service_backend()
    try:
        return backend.stop_service()
    except Exception as exc:
        return f"No pude detener el servicio ({label}): {exc}"


def remove_background_service() -> str:
    """
    Quita el servicio de fondo de Yarbis del gestor del SO (SCM/systemd/launchd).

    Returns:
        str: Resultado de la desinstalacion.
    """
    backend, label = _background_service_backend()
    try:
        return backend.remove_service()
    except Exception as exc:
        return f"No pude quitar el servicio ({label}): {exc}"


def set_background_service_autostart(enabled: bool = True) -> str:
    """
    Configura si el servicio de fondo de Yarbis arranca automaticamente.

    Args:
        enabled (bool): True para arranque automatico, False para manual.

    Returns:
        str: Resultado del cambio.
    """
    backend, label = _background_service_backend()
    try:
        return backend.set_autostart_enabled(bool(enabled))
    except Exception as exc:
        return f"No pude cambiar el arranque del servicio ({label}): {exc}"
