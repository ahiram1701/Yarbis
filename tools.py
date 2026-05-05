import difflib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from internet import fetch_web_page as fetch_public_web_page
from internet import search_web as search_public_web
from memory import (
    VALID_INTERNET_MODES,
    VALID_SEARCH_PROVIDERS,
    VALID_TASK_PRIORITY,
    VALID_TASK_STATUS,
    load_state,
    render_state_summary,
    state_transaction,
)
from self_knowledge import render_self_knowledge_summary

WORKSPACE_ROOT = Path(__file__).resolve().parent
CHECKPOINTS_DIR = WORKSPACE_ROOT / ".yarbis_checkpoints"
MAX_LIST_ITEMS = 200
MAX_READ_BYTES = 16_000
MAX_WRITE_BYTES = 64_000
MAX_WRITE_PREVIEW_CHARS = 600
MAX_DIFF_LINES = 160
MAX_TEST_OUTPUT_CHARS = 6_000
IGNORED_LISTING_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "tests_runtime",
    ".yarbis_checkpoints",
}
PROTECTED_WRITE_ROOT_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".yarbis_checkpoints",
}
PROTECTED_WRITE_PATHS = {"state.json"}
CLEAR_VALUE = "[clear]"


def _resolve_workspace_path(path: str) -> tuple[Path | None, str | None]:
    candidate = Path(path)
    resolved = (WORKSPACE_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return None, f"Acceso denegado. Solo puedes usar rutas dentro de: {WORKSPACE_ROOT}"

    return resolved, None


def _workspace_relative(path: Path) -> str:
    return path.relative_to(WORKSPACE_ROOT).as_posix()


def _validate_write_path(path: Path) -> str | None:
    try:
        relative_path = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return f"Acceso denegado. Solo puedes usar rutas dentro de: {WORKSPACE_ROOT}"

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


def _new_checkpoint_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"checkpoint-{timestamp}-{uuid4().hex[:6]}"


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


def _render_diff_preview(path: Path, previous_content: str, new_content: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            previous_content.splitlines(),
            new_content.splitlines(),
            fromfile=f"a/{_workspace_relative(path)}",
            tofile=f"b/{_workspace_relative(path)}",
            lineterm="",
        )
    )

    if not diff_lines:
        return "Sin cambios detectados."

    return "\n".join(_bounded_diff_lines(diff_lines))


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
        rendered.append(f"[{kind}] {item.relative_to(WORKSPACE_ROOT).as_posix()}")

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


def write_text_file(path: str, content: str) -> str:
    """
    Escribe texto en un archivo de forma segura.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.

    Returns:
        str: Resultado de la operacion, con checkpoint y diff.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error

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
        except OSError as exc:
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
        state_transaction(
            "self_overview",
            lambda state: state.__setitem__(
                "self_knowledge",
                {
                    "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                },
            ),
        )
    return summary


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
