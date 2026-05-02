import ast
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from memory import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_TIMEOUT_SECONDS, load_state

WORKSPACE_ROOT = Path(__file__).resolve().parent
MAX_SOURCE_FILES = 36
SELF_KNOWLEDGE_CACHE_SECONDS = 300
SOURCE_SUFFIXES = {
    ".cmd",
    ".cs",
    ".csproj",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".vbs",
    ".yaml",
    ".yml",
}
IMPORTANT_SOURCE_NAMES = {
    ".gitignore",
}
IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    ".yarbis_runtime",
    ".yarbis_checkpoints",
    "__pycache__",
    "bin",
    "obj",
    "tests_runtime",
}
IGNORED_FILE_NAMES = {
    "state.json",
    "state.json.tmp",
}
SOURCE_PRIORITY = {
    "README.md": 0,
    "agent.py": 1,
    "tools.py": 2,
    "memory.py": 3,
    "session.py": 4,
    "main.py": 5,
    "yarbis_desktop.py": 6,
    "service_manager.py": 7,
    "yarbis_service.py": 8,
    "service_host/Program.cs": 9,
}

_SELF_KNOWLEDGE_CACHE = {
    "created_at": 0.0,
    "text": "",
    "source_signature": "",
}


def _format_bytes(value: int | float | None) -> str:
    if not value:
        return "desconocido"

    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024

    return f"{amount:.1f} TB"


def _safe_run_command(args: list[str], timeout_seconds: float = 2.5) -> str:
    run_kwargs = {}
    if platform.system().lower() == "windows":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        completed = subprocess.run(
            args,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            **run_kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    if completed.returncode != 0:
        return ""

    return completed.stdout.strip()


def _read_project_identity() -> str:
    readme_path = WORKSPACE_ROOT / "README.md"
    fallback = (
        "Yarbis es un agente local para convertir objetivos generales en trabajo "
        "accionable usando Ollama, memoria persistente y herramientas seguras."
    )
    if not readme_path.exists():
        return fallback

    try:
        lines = readme_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return fallback

    paragraphs = []
    current = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if stripped.startswith("#"):
            continue
        current.append(stripped)
        if len(paragraphs) >= 1:
            break

    if current:
        paragraphs.append(" ".join(current))

    return paragraphs[0] if paragraphs else fallback


def _get_git_identity() -> list[str]:
    branch = _safe_run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout_seconds=2)
    commit = _safe_run_command(["git", "rev-parse", "--short", "HEAD"], timeout_seconds=2)
    lines = []
    if branch:
        lines.append(f"Rama git: {branch}")
    if commit:
        lines.append(f"Commit actual: {commit}")
    return lines


def _iter_source_files() -> list[Path]:
    files = []
    for path in WORKSPACE_ROOT.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(WORKSPACE_ROOT)
        if any(part in IGNORED_DIR_NAMES for part in relative_path.parts):
            continue
        if path.name in IGNORED_FILE_NAMES:
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in IMPORTANT_SOURCE_NAMES:
            continue

        files.append(path)

    def sort_key(path: Path) -> tuple[int, str]:
        relative = path.relative_to(WORKSPACE_ROOT).as_posix()
        return (SOURCE_PRIORITY.get(relative, 50), relative.lower())

    return sorted(files, key=sort_key)


def _build_source_signature() -> str:
    parts = []
    for path in _iter_source_files():
        try:
            stat = path.stat()
        except OSError:
            continue

        relative = path.relative_to(WORKSPACE_ROOT).as_posix()
        parts.append(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}")

    return "|".join(parts)


def _read_text_for_summary(path: Path, max_chars: int = 80_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _summarize_python_file(content: str) -> str:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "codigo Python; no pude parsear su AST"

    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    parts = []
    if classes:
        rendered = ", ".join(classes[:5])
        if len(classes) > 5:
            rendered += f", +{len(classes) - 5}"
        parts.append(f"clases={rendered}")
    if functions:
        rendered = ", ".join(functions[:8])
        if len(functions) > 8:
            rendered += f", +{len(functions) - 8}"
        parts.append(f"funciones={rendered}")
    return "; ".join(parts) if parts else "codigo Python sin clases/funciones de primer nivel"


def _summarize_non_python_file(path: Path, content: str) -> str:
    if path.name == "README.md":
        return "documentacion principal del proyecto"
    if path.name == "requirements.txt":
        return "dependencias Python declaradas"
    if path.suffix.lower() == ".md":
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return "documentacion Markdown"
    if path.suffix.lower() in {".cmd", ".vbs"}:
        return "lanzador local de la aplicacion"
    if path.suffix.lower() == ".cs":
        return "codigo C# del host nativo del servicio SCM"
    if path.suffix.lower() == ".csproj":
        return "proyecto .NET del host nativo del servicio SCM"
    if path.suffix.lower() in {".json", ".yaml", ".yml"}:
        return "configuracion estructurada"
    return "archivo de soporte del proyecto"


def _summarize_source_file(path: Path) -> str:
    content = _read_text_for_summary(path)
    relative = path.relative_to(WORKSPACE_ROOT).as_posix()
    line_count = content.count("\n") + (1 if content else 0)
    size = _format_bytes(path.stat().st_size if path.exists() else 0)

    if path.suffix.lower() == ".py":
        description = _summarize_python_file(content)
    else:
        description = _summarize_non_python_file(path, content)

    return f"- {relative} ({size}, {line_count} lineas): {description}"


def _render_source_inventory() -> list[str]:
    source_files = _iter_source_files()
    lines = [
        f"Workspace: {WORKSPACE_ROOT}",
        (
            "Archivos fuente detectados: "
            f"{len(source_files)} "
            "(excluye .git, .venv, state.json, checkpoints y runtime de tests)."
        ),
    ]
    lines.extend(_get_git_identity())

    for path in source_files[:MAX_SOURCE_FILES]:
        lines.append(_summarize_source_file(path))

    if len(source_files) > MAX_SOURCE_FILES:
        lines.append(f"- ... y {len(source_files) - MAX_SOURCE_FILES} archivos fuente mas.")

    return lines


def _get_total_memory_bytes() -> int:
    if platform.system().lower() == "windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

                def __init__(self):
                    super().__init__()
                    self.dwLength = ctypes.sizeof(self)

            memory_status = MEMORYSTATUSEX()
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
                return int(memory_status.ullTotalPhys)
        except (AttributeError, OSError, ValueError):
            return 0

    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages) * int(page_size)
        except (OSError, ValueError, TypeError):
            return 0

    return 0


def _get_windows_cim_hardware() -> dict:
    if platform.system().lower() != "windows":
        return {}

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return {}

    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors;"
        "$cs=Get-CimInstance Win32_ComputerSystem | Select-Object -First 1 Manufacturer,Model,TotalPhysicalMemory;"
        "$gpu=Get-CimInstance Win32_VideoController | Select-Object -First 3 Name,AdapterRAM;"
        "$out=[ordered]@{"
        "cpu_name=$cpu.Name;"
        "cpu_cores=$cpu.NumberOfCores;"
        "cpu_logical=$cpu.NumberOfLogicalProcessors;"
        "manufacturer=$cs.Manufacturer;"
        "model=$cs.Model;"
        "total_memory=$cs.TotalPhysicalMemory;"
        "gpus=@($gpu | ForEach-Object {[ordered]@{name=$_.Name;adapter_ram=$_.AdapterRAM}})"
        "};"
        "$out | ConvertTo-Json -Compress -Depth 4"
    )
    output = _safe_run_command(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout_seconds=4,
    )
    if not output:
        return {}

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def _render_runtime_environment() -> list[str]:
    uname = platform.uname()
    hardware = _get_windows_cim_hardware()
    total_memory = hardware.get("total_memory") or _get_total_memory_bytes()
    disk_target = Path(WORKSPACE_ROOT.anchor) if WORKSPACE_ROOT.anchor else WORKSPACE_ROOT

    try:
        disk_usage = shutil.disk_usage(disk_target)
        disk_text = (
            f"{_format_bytes(disk_usage.free)} libres de "
            f"{_format_bytes(disk_usage.total)} en {disk_target}"
        )
    except OSError:
        disk_text = "desconocido"

    cpu_name = hardware.get("cpu_name") or platform.processor() or uname.processor or "desconocido"
    logical_cpus = hardware.get("cpu_logical") or os.cpu_count() or "desconocido"
    physical_cpus = hardware.get("cpu_cores") or "desconocido"
    machine_model = ", ".join(
        item
        for item in (str(hardware.get("manufacturer", "")).strip(), str(hardware.get("model", "")).strip())
        if item
    ) or "desconocido"
    try:
        ollama = load_state().get("ollama", {})
    except Exception:
        ollama = {}
    if not isinstance(ollama, dict):
        ollama = {}
    ollama_model = (
        os.getenv("YARBIS_MODEL", "").strip()
        or str(ollama.get("model", DEFAULT_OLLAMA_MODEL)).strip()
        or DEFAULT_OLLAMA_MODEL
    )
    ollama_timeout = (
        os.getenv("YARBIS_OLLAMA_TIMEOUT_SECONDS", "").strip()
        or str(ollama.get("timeout_seconds", DEFAULT_OLLAMA_TIMEOUT_SECONDS)).strip()
        or str(DEFAULT_OLLAMA_TIMEOUT_SECONDS)
    )

    lines = [
        f"Sistema operativo: {platform.platform()}",
        f"Host: {socket.gethostname()}",
        f"Equipo: {machine_model}",
        f"Arquitectura: {platform.machine() or 'desconocida'}",
        f"CPU: {cpu_name}",
        f"Nucleos CPU: fisicos={physical_cpus}, logicos={logical_cpus}",
        f"RAM: {_format_bytes(int(total_memory) if total_memory else 0)}",
        f"Disco del workspace: {disk_text}",
        f"Python: {sys.version.split()[0]} ({sys.executable})",
        f"Proceso actual: pid={os.getpid()}, cwd={Path.cwd()}",
        f"Modelo Ollama configurado: {ollama_model} (timeout {ollama_timeout}s)",
    ]

    gpus = hardware.get("gpus")
    if isinstance(gpus, dict):
        gpus = [gpus]
    if isinstance(gpus, list) and gpus:
        rendered_gpus = []
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            name = str(gpu.get("name", "")).strip()
            adapter_ram = gpu.get("adapter_ram")
            if not name:
                continue
            if isinstance(adapter_ram, int) and adapter_ram > 0:
                rendered_gpus.append(f"{name} ({_format_bytes(adapter_ram)})")
            else:
                rendered_gpus.append(name)
        if rendered_gpus:
            lines.append("GPU: " + "; ".join(rendered_gpus))

    return lines


def _build_self_knowledge_summary() -> str:
    lines = [
        "Identidad:",
        "- Nombre: Yarbis.",
        f"- {_read_project_identity()}",
        "- Naturaleza: agente local de terminal/escritorio con memoria persistente, herramientas de workspace, internet bajo politica y notificaciones opcionales.",
        "- Capacidad sobre su codigo: puede listar, leer, editar con checkpoint, restaurar y ejecutar tests dentro del workspace.",
        "",
        "Codigo fuente:",
    ]
    lines.extend(_render_source_inventory())
    lines.extend([
        "",
        "Entorno actual:",
    ])
    lines.extend(_render_runtime_environment())
    lines.extend([
        "",
        "Uso operativo:",
        "- Si necesitas detalles exactos del codigo fuente, usa list_files y read_text_file antes de razonar sobre implementaciones.",
        "- Si modificas codigo o tests, usa write_text_file y despues run_project_tests.",
        "- No asumas datos privados del usuario; pide contexto cuando falte.",
    ])
    return "\n".join(lines)


def render_self_knowledge_summary(refresh: bool = False) -> str:
    """
    Renderiza una vista compacta de identidad, codigo fuente y entorno actual.

    Args:
        refresh (bool): Si es True, ignora el cache temporal.

    Returns:
        str: Resumen legible para el prompt o para una tool.
    """
    now = time.monotonic()
    cached_text = _SELF_KNOWLEDGE_CACHE["text"]
    cache_age = now - float(_SELF_KNOWLEDGE_CACHE["created_at"])
    source_signature = _build_source_signature()
    source_is_unchanged = source_signature == _SELF_KNOWLEDGE_CACHE["source_signature"]
    if (
        cached_text
        and not refresh
        and source_is_unchanged
        and cache_age < SELF_KNOWLEDGE_CACHE_SECONDS
    ):
        return cached_text

    summary = _build_self_knowledge_summary()
    _SELF_KNOWLEDGE_CACHE["created_at"] = now
    _SELF_KNOWLEDGE_CACHE["text"] = summary
    _SELF_KNOWLEDGE_CACHE["source_signature"] = source_signature
    return summary
