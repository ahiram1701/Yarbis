from pathlib import Path


def list_files(path: str = ".") -> str:
    """
    Lista archivos y carpetas de una ruta.

    Args:
        path (str): Ruta de la carpeta a inspeccionar.

    Returns:
        str: Lista de archivos y carpetas encontrada.
    """
    p = Path(path)
    if not p.exists():
        return f"La ruta no existe: {path}"

    items = []
    for item in p.iterdir():
        tipo = "DIR " if item.is_dir() else "FILE"
        items.append(f"[{tipo}] {item.name}")

    if not items:
        return "La carpeta está vacía."

    return "\n".join(items)


def read_text_file(path: str) -> str:
    """
    Lee un archivo de texto.

    Args:
        path (str): Ruta del archivo.

    Returns:
        str: Contenido del archivo.
    """
    p = Path(path)
    if not p.exists():
        return f"No existe el archivo: {path}"
    if not p.is_file():
        return f"No es un archivo válido: {path}"

    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error leyendo archivo: {e}"


def write_text_file(path: str, content: str) -> str:
    """
    Escribe texto en un archivo.

    Args:
        path (str): Ruta destino.
        content (str): Contenido a guardar.

    Returns:
        str: Resultado de la operación.
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Archivo guardado correctamente en: {path}"
    except Exception as e:
        return f"Error escribiendo archivo: {e}"