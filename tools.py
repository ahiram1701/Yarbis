from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
MAX_LIST_ITEMS = 200
MAX_READ_BYTES = 16_000
MAX_WRITE_BYTES = 64_000
MAX_WRITE_PREVIEW_CHARS = 600


def _resolve_workspace_path(path: str) -> tuple[Path | None, str | None]:
    candidate = Path(path)
    resolved = (WORKSPACE_ROOT / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return None, f"Acceso denegado. Solo puedes usar rutas dentro de: {WORKSPACE_ROOT}"

    return resolved, None


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

    items = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))

    if not items:
        return "La carpeta esta vacia."

    rendered = []
    for item in items[:MAX_LIST_ITEMS]:
        kind = "DIR " if item.is_dir() else "FILE"
        rendered.append(f"[{kind}] {item.relative_to(WORKSPACE_ROOT).as_posix()}")

    if len(items) > MAX_LIST_ITEMS:
        rendered.append(f"... y {len(items) - MAX_LIST_ITEMS} elementos mas.")

    return "\n".join(rendered)


def read_text_file(path: str) -> str:
    """
    Lee un archivo de texto.

    Args:
        path (str): Ruta del archivo.

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
        raw_content = file_path.read_bytes()
    except OSError as exc:
        return f"Error leyendo archivo: {exc}"

    truncated = raw_content[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if len(raw_content) > MAX_READ_BYTES:
        return (
            f"Contenido truncado de {file_path.relative_to(WORKSPACE_ROOT).as_posix()} "
            f"a {MAX_READ_BYTES} bytes de {len(raw_content)}.\n{truncated}"
        )

    return truncated


def write_text_file(path: str, content: str) -> str:
    """
    Escribe texto en un archivo.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.

    Returns:
        str: Resultado de la operacion.
    """
    file_path, error = _resolve_workspace_path(path)
    if error:
        return error

    encoded_content = content.encode("utf-8")
    if len(encoded_content) > MAX_WRITE_BYTES:
        return (
            f"Contenido demasiado grande para escribir en una sola operacion: "
            f"{len(encoded_content)} bytes. Limite: {MAX_WRITE_BYTES} bytes."
        )

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error escribiendo archivo: {exc}"

    preview = content[:MAX_WRITE_PREVIEW_CHARS]
    if len(content) > MAX_WRITE_PREVIEW_CHARS:
        preview += "\n... (vista previa truncada)"

    return (
        f"Archivo guardado correctamente en: {file_path.relative_to(WORKSPACE_ROOT).as_posix()}\n"
        f"Caracteres escritos: {len(content)}\n"
        f"Vista previa:\n{preview}"
    )
