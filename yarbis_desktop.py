import ctypes
import json
import os
import re
import sys
import queue
import subprocess
import threading
import tkinter as tk
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import yarbis_instance

_PRECONFIGURED_INSTANCE = os.environ.get(yarbis_instance.ENV_INSTANCE, "").strip()
yarbis_instance.configure_from_argv()
yarbis_instance.ensure_instance_registered()

import activity
import conversation_ux
import yarbis_bus
import memory as memory_store
import voice as yarbis_voice
import voice_conversation
from tools import set_computer_control, set_social_confirmation
from memory import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    MODEL_PROVIDER_OPENAI_COMPAT,
    MODEL_PROVIDER_PUTER,
    format_cycle_count,
    load_state,
    normalize_cycle_count,
    render_state_summary,
    wait_for_memory_protection_maintenance,
)
from pc_context import local_context_enabled
from pc_context_runtime import (
    ensure_context_task,
    format_context_helper_status,
    get_context_helper_status,
    remove_context_task,
    start_context_helper,
    stop_context_helper,
)
from session import (
    add_task_text,
    clear_abandoned_runtime_operation,
    clear_activity_for_first_run_if_needed,
    coding_apply_and_validate_text,
    coding_apply_proposal_text,
    coding_check_proposal_text,
    coding_detect_validation_command_text,
    coding_discard_proposal_text,
    coding_get_proposal_text,
    coding_list_proposals_text,
    coding_read_text_range_text,
    coding_run_validation_text,
    coding_search_text_text,
    coding_set_workspace_text,
    coding_validation_plan_text,
    coding_workflow_status_text,
    evolution_status_text,
    evolution_set_enabled_text,
    evolution_set_interval_text,
    evolution_list_pending_text,
    evolution_list_suggestions_text,
    evolution_apply_directive_text,
    evolution_discard_directive_text,
    evolution_apply_suggestion_text,
    evolution_discard_suggestion_text,
    analyze_image_text,
    analyze_images_text,
    vision_status_text,
    create_memory_backup_text,
    get_local_context_settings,
    get_model_provider_settings,
    get_notification_settings,
    get_service_proactive_settings,
    get_status_text,
    get_ui_theme,
    has_pending_user_question,
    factory_reset_yarbis,
    import_memory_backup_text,
    inspect_memory_backup_text,
    list_social_publications_text,
    open_assisted_social_post_text,
    request_stop_current_operation,
    run_startup_self_analysis,
    save_note_text,
    send_test_notification,
    social_accounts_overview_text,
    start_social_oauth_text,
    update_local_context_settings,
    update_notification_settings,
    update_goal,
    update_model_provider,
    update_ollama_settings,
    update_openrouter_settings,
    update_openai_compat_settings,
    update_puter_settings,
    update_memory_protection_settings_text,
    update_profile_text,
    update_service_proactive_settings,
    update_ui_theme,
    verify_memory_backups_text,
)
from service_manager import (
    format_health_status,
    format_readiness_status,
    get_service_status,
    health_status,
    install_service,
    readiness_status,
    remove_service,
    service_account_requires_password,
    set_autostart_enabled,
    start_service,
    stop_service,
)
from telegram_inbox import start_telegram_polling, stop_telegram_polling
from ui_dialogs import (
    FirstRunDialog,
    IdeaProjectsDialog,
    MemoryImportModeDialog,
    MultilineTextDialog,
    NoteDialog,
    NotesDialog,
    ProfileDialog,
    CodingProposalsDialog,
    SocialOAuthDialog,
    TaskDialog,
)
from ui_settings_dialogs import (
    ComputerControlDialog,
    LocalContextDialog,
    MemoryProtectionDialog,
    NotificationsDialog,
    OllamaSettingsDialog,
    ServiceInstallDialog,
    ServiceMobileUiDialog,
    ServicePulseDialog,
    VoiceSettingsDialog,
)
from ui_theme import (
    THEMES,
    configure_app_styles,
    create_app_style,
    create_card,
    create_section,
    style_scrollbar_widget,
    style_text_widget,
    use_bootstrap_theme,
)
from yarbis_mobile import (
    current_tailscale_serve_targets,
    get_mobile_ui_settings,
    public_mobile_ui_status,
    update_mobile_ui_settings,
)

_SINGLE_INSTANCE_MUTEX_NAME = yarbis_instance.desktop_mutex_name()
_SINGLE_INSTANCE_MUTEX_HANDLE = None
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_lock() -> bool:
    global _SINGLE_INSTANCE_MUTEX_HANDLE

    if sys.platform != "win32":
        return True

    if _SINGLE_INSTANCE_MUTEX_HANDLE:
        return True

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            return True

        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False

        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def _release_single_instance_lock():
    global _SINGLE_INSTANCE_MUTEX_HANDLE

    if sys.platform != "win32" or not _SINGLE_INSTANCE_MUTEX_HANDLE:
        return

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle(_SINGLE_INSTANCE_MUTEX_HANDLE)
    except Exception:
        pass
    finally:
        _SINGLE_INSTANCE_MUTEX_HANDLE = None


def _show_already_running_message():
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Yarbis",
        "Yarbis ya esta abierto. Usa la ventana existente para evitar duplicar Telegram.",
        parent=root,
    )
    root.destroy()


_STATE_SYNC_INTERVAL_MS = 1000
_EVENT_SYNC_INTERVAL_MS = 500
_STATUS_REFRESH_INTERVAL_MS = 15000
_CONTEXT_HELPER_SYNC_MS = 10000
_INSTANCE_PANEL_SYNC_SECONDS = 10
_YARBIS_MESSAGE_SYNC_MS = 5000
_SERVICE_RUNTIME_EVENT_LABELS = {"pulso proactivo"}
_WORKSPACE_ROOT = Path(__file__).resolve().parent
_RUNTIME_DIR = yarbis_instance.runtime_dir()
_DESKTOP_PID_FILE = _RUNTIME_DIR / "desktop.pid"
_UPDATE_SCRIPT = _WORKSPACE_ROOT / "scripts" / "update.ps1"
_DESKTOP_OPERATION_TIMEOUT_SECONDS = 30 * 60
_DESKTOP_SESSION_OPERATION_SCRIPT = r"""
import json
import sys

from session import run_auto_with_output, run_cycle_with_output, submit_user_reply

request_path = sys.argv[1]
response_path = sys.argv[2]

with open(request_path, "r", encoding="utf-8") as file:
    request_payload = json.load(file)

operation = str(request_payload.get("operation", "")).strip()
payload = request_payload.get("payload", {})
if not isinstance(payload, dict):
    payload = {}

try:
    if operation == "run_cycle":
        result = run_cycle_with_output()
    elif operation == "run_auto":
        result = run_auto_with_output(cycles=payload.get("cycles"))
    elif operation == "submit_user_reply":
        result = submit_user_reply(str(payload.get("reply_text", "")))
    else:
        raise ValueError(f"Operación de escritorio desconocida: {operation}")
except Exception as exc:
    response_payload = {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    exit_code = 1
else:
    response_payload = {"ok": True, "result": str(result)}
    exit_code = 0

with open(response_path, "w", encoding="utf-8") as file:
    json.dump(response_payload, file, ensure_ascii=False)

try:
    import memory

    memory.wait_for_memory_protection_maintenance(timeout_seconds=15)
except Exception:
    pass

sys.exit(exit_code)
"""


def _powershell_single_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _launch_update_process(needs_admin: bool) -> None:
    if not _UPDATE_SCRIPT.exists():
        raise RuntimeError("No encontre scripts/update.ps1.")

    update_args = [
        "-NoExit",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_UPDATE_SCRIPT),
        "-RestartDesktop",
    ]
    argument_list = "@(" + ", ".join(_powershell_single_quote(arg) for arg in update_args) + ")"
    command = (
        "Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList {argument_list} "
        f"-WorkingDirectory {_powershell_single_quote(_WORKSPACE_ROOT)}"
    )
    if needs_admin:
        command += " -Verb RunAs"

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=str(_WORKSPACE_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _write_desktop_pid() -> None:
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        _DESKTOP_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _clear_desktop_pid() -> None:
    try:
        if _DESKTOP_PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _DESKTOP_PID_FILE.unlink()
    except (FileNotFoundError, OSError):
        pass


def _has_explicit_instance() -> bool:
    if _PRECONFIGURED_INSTANCE:
        return True
    for arg in sys.argv[1:]:
        if arg == "--instance" or arg.startswith("--instance="):
            return True
    return False


def _python_window_path() -> Path:
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    candidate = _WORKSPACE_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate
    return executable


def _launch_desktop_instance(instance_id: str, *, geometry: str = "") -> None:
    normalized = yarbis_instance.normalize_instance_id(instance_id)
    env = yarbis_instance.with_instance_env(normalized)
    if str(geometry or "").strip():
        env["YARBIS_DESKTOP_GEOMETRY"] = str(geometry).strip()
    else:
        env.pop("YARBIS_DESKTOP_GEOMETRY", None)
    subprocess.Popen(
        [
            str(_python_window_path()),
            str(_WORKSPACE_ROOT / "yarbis_desktop.py"),
            "--instance",
            normalized,
        ],
        cwd=str(_WORKSPACE_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _launch_instance_selector() -> None:
    env = dict(os.environ)
    env.pop(yarbis_instance.ENV_INSTANCE, None)
    env.pop(yarbis_instance.ENV_SERVICE_NAME, None)
    subprocess.Popen(
        [
            str(_python_window_path()),
            str(_WORKSPACE_ROOT / "yarbis_desktop.py"),
        ],
        cwd=str(_WORKSPACE_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _desktop_pid_of(instance_id: str) -> int:
    normalized = yarbis_instance.normalize_instance_id(instance_id)
    pid = yarbis_instance._read_pid(yarbis_instance.runtime_dir(normalized) / "desktop.pid")
    if pid and yarbis_instance._pid_is_running(pid):
        return pid
    return 0


def _resolve_workspace_path(path_text: str) -> Path:
    path = Path(str(path_text or ""))
    if path.is_absolute():
        return path
    return _WORKSPACE_ROOT / path


def _read_instance_state(instance_id: str) -> dict:
    path = _resolve_workspace_path(str(yarbis_instance.state_file(instance_id)))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _instance_telegram_token(state: dict) -> str:
    notifications = state.get("notifications", {}) if isinstance(state, dict) else {}
    if not isinstance(notifications, dict):
        return ""
    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        return ""
    return str(telegram.get("bot_token", "")).strip()


def _instance_mobile_port(instance_id: str, state: dict) -> int:
    service = state.get("service", {}) if isinstance(state, dict) else {}
    if not isinstance(service, dict):
        service = {}
    mobile_ui = service.get("mobile_ui", {})
    if not isinstance(mobile_ui, dict):
        mobile_ui = {}
    try:
        return int(mobile_ui.get("port") or yarbis_instance.default_mobile_ui_port(instance_id))
    except (TypeError, ValueError):
        return yarbis_instance.default_mobile_ui_port(instance_id)


def _instance_mobile_settings(state: dict) -> dict:
    service = state.get("service", {}) if isinstance(state, dict) else {}
    mobile_ui = service.get("mobile_ui", {}) if isinstance(service, dict) else {}
    return mobile_ui if isinstance(mobile_ui, dict) else {}


def _instance_mobile_https_target(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _instance_mobile_https_text(mobile_ui: dict, port: int, active: bool, current_targets: set[str]) -> str:
    target = _instance_mobile_https_target(port)
    if target in current_targets:
        return "duena" if active else "duena apagada"
    if not bool(mobile_ui.get("enabled")):
        return "apagada"
    if not str(mobile_ui.get("pin_hash", "")).strip():
        return "sin PIN"
    if not bool(mobile_ui.get("https_enabled", True)):
        return "solo HTTP"
    if not active:
        return "inactiva"
    return "lista"


def _instance_archive_enabled(row: dict, current_instance_id: str | None = None) -> bool:
    current = yarbis_instance.normalize_instance_id(current_instance_id or yarbis_instance.current_instance_id())
    return (
        row.get("id") != yarbis_instance.DEFAULT_INSTANCE_ID
        and row.get("id") != current
        and not bool(row.get("active"))
    )


def _instance_overview_rows() -> tuple[list[dict], list[str]]:
    rows = []
    active_tokens: dict[str, list[str]] = {}
    try:
        current_https_targets = set(current_tailscale_serve_targets())
    except Exception:
        current_https_targets = set()
    for item in yarbis_instance.list_instances():
        instance_id = item["id"]
        state = _read_instance_state(instance_id)
        active = yarbis_instance.instance_is_active(instance_id)
        token = _instance_telegram_token(state)
        if active and token:
            active_tokens.setdefault(token, []).append(instance_id)
        counts = yarbis_bus.message_counts(instance_id)
        mobile_ui = _instance_mobile_settings(state)
        mobile_port = _instance_mobile_port(instance_id, state)
        mobile_https_target = _instance_mobile_https_target(mobile_port)
        row = {
            "id": instance_id,
            "display_name": item.get("display_name") or instance_id,
            "active": active,
            "active_text": "activa" if active else "inactiva",
            "service_name": item.get("service_name") or yarbis_instance.service_name(instance_id),
            "mobile_port": mobile_port,
            "mobile_https_target": mobile_https_target,
            "mobile_https_text": _instance_mobile_https_text(mobile_ui, mobile_port, active, current_https_targets),
            "mobile_https_owner": mobile_https_target in current_https_targets,
            "mobile_https_available": bool(
                active
                and mobile_ui.get("enabled")
                and str(mobile_ui.get("pin_hash", "")).strip()
            ),
            "pending_messages": counts.get("total_unread", 0),
            "state_file": item.get("state_file", ""),
            "runtime_dir": item.get("runtime_dir", ""),
            "exists": bool(item.get("exists")),
        }
        row["can_archive"] = _instance_archive_enabled(row)
        rows.append(row)

    warnings = []
    for ids in active_tokens.values():
        if len(ids) > 1:
            warnings.append(
                "Telegram duplicado en instancias activas: " + ", ".join(sorted(ids))
            )
    owners = [row for row in rows if row.get("mobile_https_owner")]
    if owners:
        warnings.append(
            "HTTPS movil: "
            + ", ".join(f"{row['display_name']} ({row['id']}:{row['mobile_port']})" for row in owners)
            + "."
        )
    known_https_targets = {str(row.get("mobile_https_target", "")) for row in rows}
    unknown_targets = sorted(target for target in current_https_targets if target not in known_https_targets)
    if unknown_targets:
        warnings.append("HTTPS movil apunta fuera de estas instancias: " + ", ".join(unknown_targets))
    return rows, warnings


def _current_instance_chip_text(rows: list[dict] | None = None) -> str:
    current = yarbis_instance.current_instance_id()
    source_rows = rows if rows is not None else _instance_overview_rows()[0]
    row = next((item for item in source_rows if item.get("id") == current), None)
    if not row:
        return f"Instancia: {current}"
    return (
        f"Instancia: {row['display_name']} ({row['id']}) | "
        f"{row['service_name']} | puerto {row['mobile_port']}"
    )


def _select_instance_before_launch() -> bool:
    if _has_explicit_instance():
        return True

    selected = {"id": "", "continue": False}
    root = tk.Tk()
    root.title("Abrir Yarbis")
    root.geometry("860x420")
    root.minsize(760, 360)

    frame = ttk.Frame(root, padding=18)
    frame.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(2, weight=1)

    ttk.Label(frame, text="Elige una instancia de Yarbis").grid(row=0, column=0, sticky="w")
    warning_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=warning_var, foreground="#c97a16").grid(row=1, column=0, sticky="w", pady=(4, 8))

    columns = ("name", "id", "active", "service", "port", "https", "pending", "path")
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=9)
    headings = {
        "name": "Nombre",
        "id": "Id",
        "active": "Estado",
        "service": "Servicio",
        "port": "Puerto",
        "https": "HTTPS",
        "pending": "Mensajes",
        "path": "Estado",
    }
    widths = {
        "name": 150,
        "id": 110,
        "active": 80,
        "service": 120,
        "port": 70,
        "https": 95,
        "pending": 75,
        "path": 230,
    }
    for column in columns:
        tree.heading(column, text=headings[column])
        tree.column(column, width=widths[column], anchor="w", stretch=column in {"name", "path"})
    tree.grid(row=2, column=0, sticky="nsew")
    tree_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree_scroll.grid(row=2, column=1, sticky="ns")

    def selected_tree_id() -> str:
        selection = tree.selection()
        if not selection:
            return yarbis_instance.DEFAULT_INSTANCE_ID
        return str(tree.item(selection[0], "values")[1]).strip() or yarbis_instance.DEFAULT_INSTANCE_ID

    def refresh_values(select_id: str = ""):
        rows, warnings = _instance_overview_rows()
        for item_id in tree.get_children():
            tree.delete(item_id)
        for row in rows:
            tree.insert(
                "",
                "end",
                iid=row["id"],
                values=(
                    row["display_name"],
                    row["id"],
                    row["active_text"],
                    row["service_name"],
                    row["mobile_port"],
                    row["mobile_https_text"],
                    row["pending_messages"],
                    row["state_file"],
                ),
            )
        target = select_id or (rows[0]["id"] if rows else yarbis_instance.DEFAULT_INSTANCE_ID)
        if target in tree.get_children():
            tree.selection_set(target)
            tree.focus(target)
        warning_var.set(" | ".join(warnings))

    def create_instance_from_dialog():
        raw_id = simpledialog.askstring(
            "Nueva instancia",
            "Id corto para la nueva instancia:",
            parent=root,
        )
        if raw_id is None:
            return
        try:
            created = yarbis_instance.create_instance(raw_id)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude crear la instancia: {exc}", parent=root)
            return
        refresh_values(created["id"])

    def open_selected():
        selected["id"] = selected_tree_id()
        selected["continue"] = True
        root.destroy()

    def cancel():
        selected["continue"] = False
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    for column in range(4):
        buttons.columnconfigure(column, weight=1, uniform="selector_buttons")
    ttk.Button(buttons, text="Abrir", command=open_selected).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Crear", command=create_instance_from_dialog).grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Button(buttons, text="Refrescar", command=refresh_values).grid(row=0, column=2, sticky="ew", padx=6)
    ttk.Button(buttons, text="Cancelar", command=cancel).grid(row=0, column=3, sticky="ew", padx=(6, 0))
    tree.bind("<Double-1>", lambda _event: open_selected())
    refresh_values()
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()

    if not selected["continue"]:
        return False
    normalized = yarbis_instance.normalize_instance_id(selected["id"])
    if normalized == yarbis_instance.current_instance_id():
        return True
    _launch_desktop_instance(normalized)
    return False


def _python_console_path() -> Path:
    candidate = _WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate

    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        python = executable.with_name("python.exe")
        if python.exists():
            return python
    return executable


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                cwd=str(_WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass

    try:
        process.kill()
    except OSError:
        pass


def _session_operation_subprocess(operation: str, **payload) -> str:
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    token = f"{time.time_ns()}-{threading.get_ident()}"
    request_path = _RUNTIME_DIR / f"desktop-operation-{token}.request.json"
    response_path = _RUNTIME_DIR / f"desktop-operation-{token}.response.json"
    request_payload = {
        "operation": str(operation).strip(),
        "payload": payload,
    }

    try:
        with open(request_path, "w", encoding="utf-8") as file:
            json.dump(request_payload, file, ensure_ascii=False)

        process = subprocess.Popen(
            [
                str(_python_console_path()),
                "-c",
                _DESKTOP_SESSION_OPERATION_SCRIPT,
                str(request_path),
                str(response_path),
            ],
            cwd=str(_WORKSPACE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            stdout, stderr = process.communicate(timeout=_DESKTOP_OPERATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            try:
                request_stop_current_operation(source="desktop-timeout")
            except Exception:
                pass
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            raise RuntimeError(
                "La operación de escritorio excedió el tiempo límite y fue detenida."
            ) from exc

        response_payload = {}
        if response_path.exists():
            with open(response_path, "r", encoding="utf-8") as file:
                response_payload = json.load(file)

        if process.returncode == 0 and response_payload.get("ok"):
            return str(response_payload.get("result", ""))

        error_text = str(response_payload.get("error", "")).strip()
        if not error_text:
            error_text = "\n".join(
                part.strip()
                for part in (stdout, stderr)
                if str(part).strip()
            )
        raise RuntimeError(error_text or f"Operación {operation} terminó con exit={process.returncode}.")
    finally:
        for path in (request_path, response_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


class YarbisDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        instance_id = yarbis_instance.current_instance_id()
        self.title("Yarbis" if instance_id == yarbis_instance.DEFAULT_INSTANCE_ID else f"Yarbis - {instance_id}")
        self.geometry("1120x760")
        self.minsize(900, 620)
        requested_geometry = os.environ.pop("YARBIS_DESKTOP_GEOMETRY", "").strip()
        if requested_geometry and re.fullmatch(r"\d+x\d+[+-]-?\d+[+-]-?\d+", requested_geometry):
            try:
                self.geometry(requested_geometry)
            except tk.TclError:
                pass

        self.current_theme_name = get_ui_theme()
        self.theme_palette = THEMES.get(self.current_theme_name, THEMES["dark"])
        self.style, self._bootstrap_style_active = create_app_style(self, self.current_theme_name)

        self.goal_var = tk.StringVar()
        self.cycles_var = tk.StringVar()
        self.pending_var = tk.StringVar()
        self.thinking_var = tk.StringVar(value="No.")
        self.theme_var = tk.StringVar()
        self.ollama_var = tk.StringVar()
        self.coding_var = tk.StringVar()
        self.health_var = tk.StringVar()
        self.readiness_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Listo.")
        self.conversation_headline_var = tk.StringVar(value="Yarbis está listo.")
        self.conversation_detail_var = tk.StringVar(value="Escribe, dicta o inicia voz en vivo.")
        self.conversation_next_step_var = tk.StringVar(value="Dime qué quieres hacer y lo convertimos en el siguiente paso.")
        self.instance_chip_var = tk.StringVar()
        self.instance_warning_var = tk.StringVar()
        self.message_target_var = tk.StringVar()
        self.message_timeout_var = tk.StringVar(value="120")
        self.theme_button_text = tk.StringVar()
        self.service_var = tk.StringVar()
        self.service_button_text = tk.StringVar()
        self.service_autostart_var = tk.BooleanVar()
        self.service_autostart_text = tk.StringVar()
        self._current_view = "home"
        self._view_frames = {}
        self._nav_buttons = {}
        self._dashboard_badges = {}
        self._last_instance_panel_refresh_at = 0.0

        self._result_queue = queue.Queue()
        self._worker_thread = None
        self._busy = False
        self._busy_sources = set()
        self._action_buttons = []
        self._view_has_pending_question = False
        self._last_summary_text = ""
        self._last_result_text = ""
        self._last_result_widgets = []
        self._last_activity_text = ""
        self._last_activity_signature = None
        self._runtime_events_position = self._initial_runtime_events_position()
        self._last_status_refresh_at = 0.0
        self._status_refresh_in_flight = False
        self._status_refresh_pending_force = False
        self._status_refresh_pending_context_helper = False
        self._status_refresh_pending_context_task = False
        self._cached_health_status = None
        self._cached_readiness_status = None
        self._cached_context_helper_status = None
        self._last_state_signature = None
        self._cached_state = None
        self._last_state_file_signature = None
        self._local_telegram_polling = False
        self._closing = False
        self._first_run_checked = False
        self._instance_rows = []
        self._message_rows = {}
        self._archived_rows = {}
        self._voice_recording = False
        self._voice_record_stop_event = None
        self._voice_record_thread = None
        self._live_voice_stop_event = None
        self._live_voice_thread = None
        self._yarbis_message_worker_running = False

        self._build_ui()
        self._apply_theme(self.current_theme_name)
        self._clear_abandoned_runtime_operation()
        self.refresh_state_view()
        self.after(250, self._maybe_show_first_run)
        self.after(150, self._poll_worker_queue)
        self.after(750, self._ensure_context_helper)
        self.after(_YARBIS_MESSAGE_SYNC_MS, self._process_yarbis_messages)
        self.after(_EVENT_SYNC_INTERVAL_MS, self._sync_runtime_events)
        self.after(_STATE_SYNC_INTERVAL_MS, self._sync_state_view)

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 18, 16, 12))
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.columnconfigure(0, weight=1)

        ttk.Label(sidebar, text="Yarbis", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            sidebar,
            text="Panel operativo local",
            style="SidebarMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 18))

        nav_items = (
            ("home", "Inicio"),
            ("run", "Ejecutar"),
            ("instances", "Instancias"),
            ("context", "Contexto"),
            ("settings", "Configuración"),
            ("activity", "Respuestas"),
        )
        for index, (view_name, label) in enumerate(nav_items, start=2):
            self._nav_buttons[view_name] = ttk.Button(
                sidebar,
                text=label,
                style="Nav.TButton",
                command=lambda name=view_name: self._show_view(name),
            )
            self._nav_buttons[view_name].grid(row=index, column=0, sticky="ew", pady=(0, 6))

        quick = create_section(sidebar, "Rápido")
        quick.grid(row=8, column=0, sticky="ew", pady=(16, 0))
        self._pack_action_button(
            ttk.Button(quick, text="Ejecutar ciclo", command=self._run_cycle, style="Accent.TButton")
        )
        self._pack_action_button(
            ttk.Button(quick, text="Detener pensando", command=self._stop_current_operation, style="Danger.TButton"),
            disable_when_busy=False,
        )
        ttk.Button(quick, text="Ver respuestas", command=lambda: self._show_view("activity")).pack(
            fill="x",
            padx=8,
            pady=3,
        )
        ttk.Button(quick, text="Abrir otra instancia", command=self._open_another_instance).pack(
            fill="x",
            padx=8,
            pady=3,
        )
        ttk.Button(quick, text="Voz neural", command=self._choose_edge_voice).pack(
            fill="x",
            padx=8,
            pady=3,
        )
        ttk.Button(quick, textvariable=self.theme_button_text, command=self._toggle_theme).pack(
            fill="x",
            padx=8,
            pady=(3, 8),
        )

        content_shell = ttk.Frame(self, padding=(18, 14, 18, 8))
        content_shell.grid(row=0, column=1, sticky="nsew")
        content_shell.columnconfigure(0, weight=1)
        content_shell.rowconfigure(1, weight=1)

        header = ttk.Frame(content_shell)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Centro de control", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Estado, ejecución, configuración y actividad en una misma vista de trabajo.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        instance_header = ttk.Frame(header)
        instance_header.grid(row=0, column=1, sticky="e", padx=(12, 0))
        ttk.Label(instance_header, textvariable=self.instance_chip_var, style="Muted.TLabel").grid(row=0, column=0, sticky="e")
        ttk.Button(
            instance_header,
            text="Cambiar…",
            command=lambda: self._show_view("instances"),
        ).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(header, text="Refrescar", command=self.refresh_state_view).grid(row=1, column=1, sticky="e")

        views = ttk.Frame(content_shell)
        views.grid(row=1, column=0, sticky="nsew")
        views.columnconfigure(0, weight=1)
        views.rowconfigure(0, weight=1)

        for view_name in ("home", "run", "instances", "context", "settings", "activity"):
            frame = ttk.Frame(views)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self._view_frames[view_name] = frame

        self._build_home_view(self._view_frames["home"])
        self._build_run_view(self._view_frames["run"])
        self._build_instances_view(self._view_frames["instances"])
        self._build_context_view(self._view_frames["context"])
        self._build_settings_view(self._view_frames["settings"])
        self._build_activity_view(self._view_frames["activity"])

        status_shell = ttk.Frame(self, padding=(16, 0, 18, 10))
        status_shell.grid(row=1, column=0, columnspan=2, sticky="ew")
        status_shell.columnconfigure(0, weight=1)

        status_bar = ttk.Label(status_shell, textvariable=self.status_var, anchor="w")
        status_bar.grid(row=0, column=0, sticky="ew")

        self.factory_reset_button = ttk.Button(
            status_shell,
            text="",
            width=2,
            command=self._factory_reset,
            style="Hidden.TButton",
        )
        self.factory_reset_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._action_buttons.append(self.factory_reset_button)

        self._show_view("home")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_metric_card(self, parent, title: str, variable: tk.StringVar, column: int, row: int = 0):
        card = create_card(parent, title)
        card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=(0, 8))
        ttk.Label(card, textvariable=variable, style="Card.TLabel", wraplength=210).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )
        return card

    def _build_home_view(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        canvas = tk.Canvas(parent, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(canvas)
        content.columnconfigure(0, weight=1)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_content_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        def scroll_home(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        content.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_content_width)
        canvas.bind("<MouseWheel>", scroll_home)
        content.bind("<MouseWheel>", scroll_home)
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", scroll_home))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))
        self.home_canvas = canvas
        self.home_scrollbar = scrollbar

        metrics = ttk.Frame(content)
        metrics.grid(row=0, column=0, sticky="ew")
        for column in range(4):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
        self._build_metric_card(metrics, "Servicio", self.service_var, 0)
        self._build_metric_card(metrics, "Ciclos", self.cycles_var, 1)
        self._build_metric_card(metrics, "Modelo", self.ollama_var, 2)
        self._build_metric_card(metrics, "Pendiente", self.pending_var, 3)

        goal_card = create_card(content, "Objetivo actual")
        goal_card.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ttk.Label(goal_card, textvariable=self.goal_var, style="Card.TLabel", wraplength=760).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

        response_frame = create_section(content, "Respuesta de Yarbis", "Último resultado recibido.")
        response_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        response_frame.rowconfigure(1, weight=1)
        home_result_text = self._create_scrolled_text(response_frame, wrap="word", height=8)
        home_result_text.frame.grid(row=1, column=0, sticky="nsew")
        home_result_text.configure(state="disabled")
        self._last_result_widgets.append(home_result_text)

        detail = ttk.Frame(content)
        detail.grid(row=3, column=0, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(0, weight=1)

        summary_frame = create_section(detail, "Estado actual")
        summary_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        summary_frame.rowconfigure(0, weight=1)
        self.summary_text = self._create_scrolled_text(summary_frame, wrap="word", height=12)
        self.summary_text.frame.grid(row=0, column=0, sticky="nsew")
        self.summary_text.configure(state="disabled")

        health = create_section(detail, "Lectura rápida")
        health.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        health.columnconfigure(0, weight=1)
        for row, (label, variable) in enumerate(
            (
                ("Pensando", self.thinking_var),
                ("Salud", self.health_var),
                ("Preparación", self.readiness_var),
                ("Coding", self.coding_var),
                ("Tema", self.theme_var),
            )
        ):
            ttk.Label(health, text=label, style="Muted.TLabel").grid(row=row * 2, column=0, sticky="w", pady=(0, 2))
            ttk.Label(health, textvariable=variable, wraplength=380).grid(
                row=row * 2 + 1,
                column=0,
                sticky="ew",
                pady=(0, 7),
            )

    def _build_run_view(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        actions = create_section(parent, "Ejecutar", "Acciones principales del ciclo actual.")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for column in range(6):
            actions.columnconfigure(column, weight=1, uniform="run_actions")
        run_specs = (
            ("Ejecutar ciclo", self._run_cycle, "Accent.TButton", True),
            ("Voz en vivo", self._toggle_live_voice, "Secondary.TButton", False),
            ("Modo autónomo", self._run_auto, "Secondary.TButton", True),
            ("Detener pensando", self._stop_current_operation, "Danger.TButton", False),
            ("Leer último resultado", self._speak_last_result, "TButton", False),
            ("Detener voz", self._stop_speaking, "TButton", False),
        )
        for column, (text, command, style, disable_when_busy) in enumerate(run_specs):
            button = ttk.Button(actions, text=text, command=command, style=style)
            button.grid(row=1, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            if disable_when_busy:
                self._action_buttons.append(button)

        conversation = create_section(parent, "Conversacion", "Estado actual y siguiente paso.")
        conversation.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        conversation.columnconfigure(0, weight=1)
        ttk.Label(conversation, textvariable=self.conversation_headline_var, style="CardTitle.TLabel").grid(
            row=0,
            column=0,
            sticky="ew",
        )
        ttk.Label(conversation, textvariable=self.conversation_detail_var, wraplength=920).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(6, 0),
        )
        ttk.Label(conversation, textvariable=self.conversation_next_step_var, style="Muted.TLabel", wraplength=920).grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(4, 0),
        )

        workspace = ttk.Frame(parent)
        workspace.grid(row=2, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=3)
        workspace.columnconfigure(1, weight=2)
        workspace.rowconfigure(0, weight=1)

        result = create_section(workspace, "Respuesta de Yarbis", "Último resultado visible mientras trabajas.")
        result.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        result.rowconfigure(1, weight=1)
        run_result_text = self._create_scrolled_text(result, wrap="word", height=14)
        run_result_text.frame.grid(row=1, column=0, sticky="nsew")
        run_result_text.configure(state="disabled")
        self._last_result_widgets.append(run_result_text)

        composer = create_section(workspace, "Respuesta o contexto", "Escribe una respuesta, una instrucción o contexto libre.")
        composer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        composer.columnconfigure(0, weight=1)
        composer.rowconfigure(1, weight=1)
        self.reply_text = tk.Text(composer, height=9, wrap="word")
        self.reply_text.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

        composer_buttons = ttk.Frame(composer)
        composer_buttons.grid(row=2, column=0, sticky="ew")
        composer_buttons.columnconfigure(0, weight=0)
        composer_buttons.columnconfigure(1, weight=1)
        self.voice_button = ttk.Button(
            composer_buttons,
            text="Dictar",
            command=self._toggle_voice_recording,
            style="Secondary.TButton",
        )
        self.voice_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.send_button = ttk.Button(
            composer_buttons,
            text="Enviar y ejecutar",
            command=self._send_reply,
            style="Accent.TButton",
        )
        self.send_button.grid(row=0, column=1, sticky="e")
        self._action_buttons.extend([self.voice_button, self.send_button])

    def _build_instances_view(self, parent):
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent)
        right = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        instances = create_section(left, "Instancias", "Estado local, servicio, puerto y rutas.")
        instances.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        instances.columnconfigure(0, weight=1)
        instances.rowconfigure(1, weight=1)
        ttk.Label(instances, textvariable=self.instance_warning_var, style="Muted.TLabel").grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )
        columns = ("name", "id", "active", "service", "port", "https", "pending", "path")
        self.instances_tree = ttk.Treeview(instances, columns=columns, show="headings", height=9)
        for column, label, width in (
            ("name", "Nombre", 140),
            ("id", "Id", 95),
            ("active", "Estado", 75),
            ("service", "Servicio", 115),
            ("port", "Puerto", 60),
            ("https", "HTTPS", 90),
            ("pending", "Msg", 45),
            ("path", "Estado", 210),
        ):
            self.instances_tree.heading(column, text=label)
            self.instances_tree.column(column, width=width, anchor="w", stretch=column in {"name", "path"})
        self.instances_tree.grid(row=1, column=0, sticky="nsew")
        instances_scroll = ttk.Scrollbar(instances, orient="vertical", command=self.instances_tree.yview)
        self.instances_tree.configure(yscrollcommand=instances_scroll.set)
        instances_scroll.grid(row=1, column=1, sticky="ns")
        self.instances_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_instance_action_states())

        instance_buttons = ttk.Frame(instances)
        instance_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for column in range(5):
            instance_buttons.columnconfigure(column, weight=1, uniform="instance_buttons")
        self.open_instance_button = ttk.Button(instance_buttons, text="Abrir", command=self._open_selected_instance)
        self.switch_instance_button = ttk.Button(instance_buttons, text="Cambiar a esta", command=self._switch_selected_instance)
        self.create_instance_button = ttk.Button(instance_buttons, text="Crear", command=self._create_instance_from_panel)
        self.rename_instance_button = ttk.Button(instance_buttons, text="Renombrar", command=self._rename_selected_instance)
        self.copy_instance_path_button = ttk.Button(instance_buttons, text="Copiar ruta", command=self._copy_selected_instance_path)
        self.copy_instance_command_button = ttk.Button(instance_buttons, text="Copiar comando", command=self._copy_selected_instance_command)
        self.claim_instance_https_button = ttk.Button(instance_buttons, text="Usar HTTPS", command=self._claim_selected_instance_https)
        self.start_instance_service_button = ttk.Button(instance_buttons, text="Iniciar servicio", command=self._start_selected_instance_service)
        self.stop_instance_service_button = ttk.Button(instance_buttons, text="Detener servicio", command=self._stop_selected_instance_service)
        self.autostart_instance_service_button = ttk.Button(instance_buttons, text="Autostart", command=self._toggle_selected_instance_autostart)
        self.archive_instance_button = ttk.Button(
            instance_buttons,
            text="Archivar",
            command=self._archive_selected_instance,
            style="Danger.TButton",
        )
        for index, button in enumerate((
            self.open_instance_button,
            self.switch_instance_button,
            self.create_instance_button,
            self.rename_instance_button,
            self.copy_instance_path_button,
            self.copy_instance_command_button,
            self.claim_instance_https_button,
            self.start_instance_service_button,
            self.stop_instance_service_button,
            self.autostart_instance_service_button,
            self.archive_instance_button,
        )):
            button.grid(row=index // 5, column=index % 5, sticky="ew", padx=(0 if index % 5 == 0 else 6, 0), pady=(0, 6))
        self._action_buttons.extend([
            self.create_instance_button,
            self.rename_instance_button,
            self.claim_instance_https_button,
            self.start_instance_service_button,
            self.stop_instance_service_button,
            self.autostart_instance_service_button,
            self.archive_instance_button,
        ])

        archived = create_section(left, "Archivadas", "Instancias movidas a respaldo recuperable.")
        archived.grid(row=1, column=0, sticky="nsew")
        archived.columnconfigure(0, weight=1)
        archived.rowconfigure(0, weight=1)
        archive_columns = ("name", "id", "archived_at", "path")
        self.archived_tree = ttk.Treeview(archived, columns=archive_columns, show="headings", height=4)
        for column, label, width in (
            ("name", "Nombre", 150),
            ("id", "Id", 100),
            ("archived_at", "Archivada", 140),
            ("path", "Ruta", 260),
        ):
            self.archived_tree.heading(column, text=label)
            self.archived_tree.column(column, width=width, anchor="w", stretch=column == "path")
        self.archived_tree.grid(row=0, column=0, sticky="nsew")
        archived_scroll = ttk.Scrollbar(archived, orient="vertical", command=self.archived_tree.yview)
        self.archived_tree.configure(yscrollcommand=archived_scroll.set)
        archived_scroll.grid(row=0, column=1, sticky="ns")
        archived_buttons = ttk.Frame(archived)
        archived_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.restore_instance_button = ttk.Button(archived_buttons, text="Restaurar", command=self._restore_selected_archive)
        self.restore_instance_button.pack(side="left")
        self._action_buttons.append(self.restore_instance_button)

        composer = create_section(right, "Mensaje directo", "Enviar a otra instancia local.")
        composer.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        composer.columnconfigure(1, weight=1)
        ttk.Label(composer, text="Destino").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
        self.message_target_combo = ttk.Combobox(
            composer,
            textvariable=self.message_target_var,
            state="readonly",
            width=24,
        )
        self.message_target_combo.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        ttk.Label(composer, text="Timeout").grid(row=0, column=2, sticky="e", padx=(8, 4), pady=(0, 6))
        self.message_timeout_spin = tk.Spinbox(
            composer,
            from_=0,
            to=600,
            increment=15,
            width=6,
            textvariable=self.message_timeout_var,
        )
        self.message_timeout_spin.grid(row=0, column=3, sticky="e", pady=(0, 6))
        self.message_text = tk.Text(composer, height=5, wrap="word")
        self.message_text.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        message_buttons = ttk.Frame(composer)
        message_buttons.grid(row=2, column=0, columnspan=4, sticky="ew")
        self.send_instance_message_button = ttk.Button(
            message_buttons,
            text="Enviar",
            command=self._send_instance_message,
            style="Accent.TButton",
        )
        self.send_instance_message_button.pack(side="left")
        ttk.Button(message_buttons, text="Abrir destino", command=self._open_message_target_instance).pack(
            side="left",
            padx=(8, 0),
        )
        self._action_buttons.append(self.send_instance_message_button)

        inbox = create_section(right, "Bandeja", "Mensajes recibidos, enviados y respuestas.")
        inbox.grid(row=1, column=0, sticky="nsew")
        inbox.columnconfigure(0, weight=1)
        inbox.rowconfigure(0, weight=1)
        message_columns = ("direction", "peer", "kind", "status", "created", "preview")
        self.messages_tree = ttk.Treeview(inbox, columns=message_columns, show="headings", height=12)
        for column, label, width in (
            ("direction", "Dir", 70),
            ("peer", "Instancia", 90),
            ("kind", "Tipo", 85),
            ("status", "Estado", 70),
            ("created", "Fecha", 120),
            ("preview", "Mensaje", 260),
        ):
            self.messages_tree.heading(column, text=label)
            self.messages_tree.column(column, width=width, anchor="w", stretch=column == "preview")
        self.messages_tree.grid(row=0, column=0, sticky="nsew")
        messages_scroll = ttk.Scrollbar(inbox, orient="vertical", command=self.messages_tree.yview)
        self.messages_tree.configure(yscrollcommand=messages_scroll.set)
        messages_scroll.grid(row=0, column=1, sticky="ns")
        inbox_buttons = ttk.Frame(inbox)
        inbox_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(inbox_buttons, text="Refrescar", command=lambda: self._refresh_instance_panels(force=True)).pack(side="left")
        ttk.Button(inbox_buttons, text="Responder", command=self._prepare_message_reply).pack(side="left", padx=(8, 0))
        ttk.Button(inbox_buttons, text="Marcar leÃ­do", command=self._mark_selected_messages_read).pack(
            side="left",
            padx=(8, 0),
        )

    def _build_context_view(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        left = ttk.Frame(parent)
        right = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        for frame in (left, right):
            frame.columnconfigure(0, weight=1)

        self._build_action_group(
            left,
            "Objetivo y memoria de trabajo",
            (
                {"text": "Cambiar objetivo", "command": self._change_goal, "style": "Accent.TButton"},
                {"text": "Ideas/proyectos", "command": self._manage_idea_projects},
                {"text": "Editar perfil", "command": self._edit_profile},
                {"text": "Ver notas", "command": self._manage_notes},
                {"text": "Crear tarea", "command": self._create_task},
            ),
        )
        self._build_action_group(
            left,
            "Coding",
            (
                {"text": "Workspace de código", "command": self._choose_coding_workspace},
                {"text": "Propuestas", "command": self._manage_coding_proposals},
            ),
        )
        self._build_action_group(
            right,
            "Redes sociales",
            (
                {"text": "Conectar cuenta", "command": self._connect_social_account},
                {"text": "Ver cuentas", "command": self._show_social_accounts},
                {"text": "Ver pendientes", "command": self._show_social_publications},
                {"text": "Copiar confirmación", "command": self._copy_social_confirmation},
                {"text": "Abrir asistido", "command": self._open_assisted_social_post},
            ),
        )
        self._build_action_group(
            right,
            "Memoria",
            (
                {"text": "Protección", "command": self._edit_memory_protection},
                {"text": "Respaldar memoria", "command": self._backup_memory},
                {"text": "Trasplantar memoria", "command": self._import_memory},
                {"text": "Verificar respaldos", "command": self._verify_memory_backups},
            ),
        )

    def _build_settings_view(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        left = ttk.Frame(parent)
        right = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        for frame in (left, right):
            frame.columnconfigure(0, weight=1)

        self._build_action_group(
            left,
            "Modelo",
            ({"text": "Modelo y timeout", "command": self._edit_ollama_settings, "style": "Accent.TButton"},),
        )
        self._build_action_group(
            left,
            "Voz, neural y avisos",
            (
                {"text": "Elegir voz neural", "command": self._choose_edge_voice, "style": "Accent.TButton"},
                {"text": "Configurar voz", "command": self._edit_voice_settings},
                {"text": "Probar voz", "command": self._test_voice},
                {"text": "Notificaciones", "command": self._edit_notifications},
                {"text": "Probar notificación", "command": self._send_test_notification},
            ),
        )
        self._build_action_group(
            left,
            "Autoevolución y aprendizaje",
            (
                {"text": "Estado", "command": self._show_evolution_status},
                {"text": "Activar", "command": lambda: self._set_evolution_enabled(True), "style": "Accent.TButton"},
                {"text": "Desactivar", "command": lambda: self._set_evolution_enabled(False)},
                {"text": "Cadencia…", "command": self._set_evolution_interval},
                {"text": "Propuestas pendientes", "command": self._show_evolution_pending},
                {"text": "Aprobar propuesta…", "command": self._approve_evolution_item},
                {"text": "Descartar propuesta…", "command": self._discard_evolution_item},
            ),
        )
        self._build_action_group(
            left,
            "Imagen",
            (
                {"text": "Analizar imagen…", "command": self._analyze_image_file, "style": "Accent.TButton"},
                {"text": "Modelo de visión", "command": self._show_vision_status},
            ),
        )
        self._build_action_group(
            left,
            "Control de la PC",
            (
                {"text": "Estado", "command": self._show_computer_control_status},
                {"text": "Navegador y perfil…", "command": self._configure_computer_control, "style": "Accent.TButton"},
                {"text": "Activar", "command": lambda: self._set_computer_control_enabled(True)},
                {"text": "Desactivar", "command": lambda: self._set_computer_control_enabled(False)},
                {"text": "Publicar sin confirmar", "command": lambda: self._set_publish_confirmation(False)},
                {"text": "Pedir confirmación", "command": lambda: self._set_publish_confirmation(True)},
            ),
        )
        self._build_service_group(right)
        self._build_action_group(
            right,
            "Vista y mantenimiento",
            (
                {"textvariable": self.theme_button_text, "command": self._toggle_theme},
                {"text": "Refrescar estado", "command": self.refresh_state_view},
                {"text": "Actualizar Yarbis", "command": self._update_yarbis, "style": "Secondary.TButton"},
            ),
        )

    def _show_evolution_status(self):
        try:
            text = evolution_status_text()
        except Exception as exc:
            messagebox.showwarning("Autoevolución", f"No pude leer el estado: {exc}", parent=self)
            return
        messagebox.showinfo("Autoevolución", text, parent=self)

    def _set_evolution_enabled(self, enabled: bool):
        try:
            message = evolution_set_enabled_text(bool(enabled))
        except Exception as exc:
            messagebox.showwarning("Autoevolución", f"No pude cambiar el estado: {exc}", parent=self)
            return
        messagebox.showinfo("Autoevolución", message, parent=self)
        self.refresh_state_view()

    def _show_computer_control_status(self):
        try:
            cc = memory_store.load_state().get("computer_control", {})
            s = cc.get("settings", {})
            mode = s.get("browser_profile_mode", "isolated")
            perfil = "aislado (Yarbis)" if mode == "isolated" else "sistema (tu navegador)"
            if str(s.get("browser_user_data_dir", "")).strip():
                perfil = f"personalizado ({s.get('browser_user_data_dir')})"
            if str(s.get("browser_profile_directory", "")).strip():
                perfil += f", perfil '{s.get('browser_profile_directory')}'"
            text = (
                f"Control de la PC: {'activado' if cc.get('enabled') else 'desactivado'}.\n"
                f"Navegador: {s.get('browser_channel', 'msedge')}\n"
                f"Perfil: {perfil}\n"
                f"Control del sistema (mouse/teclado): {'si' if s.get('os_control', True) else 'no'}\n"
                f"Confirmar acciones sensibles: {'si' if s.get('confirm_sensitive', True) else 'no'}"
            )
        except Exception as exc:
            messagebox.showwarning("Control de la PC", f"No pude leer el estado: {exc}", parent=self)
            return
        messagebox.showinfo("Control de la PC", text, parent=self)

    def _configure_computer_control(self):
        try:
            current = memory_store.load_state().get("computer_control", {})
        except Exception as exc:
            messagebox.showwarning("Control de la PC", f"No pude leer los ajustes: {exc}", parent=self)
            return
        dialog = ComputerControlDialog(self, initial_settings=current)
        result = getattr(dialog, "result", None)
        if not result:
            return
        try:
            message = set_computer_control(
                enabled=bool(result.get("enabled", False)),
                os_control=bool(result.get("os_control", True)),
                confirm_sensitive=bool(result.get("confirm_sensitive", True)),
                browser_channel=str(result.get("browser_channel", "")),
                browser_profile_mode=str(result.get("browser_profile_mode", "")),
                browser_profile_directory=str(result.get("browser_profile_directory", "")),
                browser_user_data_dir=str(result.get("browser_user_data_dir", "")),
            )
        except Exception as exc:
            messagebox.showwarning("Control de la PC", f"No pude guardar: {exc}", parent=self)
            return
        messagebox.showinfo("Control de la PC", message, parent=self)
        self.refresh_state_view()

    def _set_computer_control_enabled(self, enabled: bool):
        if enabled and not messagebox.askyesno(
            "Control de la PC",
            "Vas a permitir que Yarbis maneje el navegador y el sistema (mouse/teclado). "
            "Las acciones sensibles (Publicar, Pagar, Enviar) pediran confirmacion. ¿Activar?",
            parent=self,
        ):
            return
        try:
            message = set_computer_control(enabled=bool(enabled))
        except Exception as exc:
            messagebox.showwarning("Control de la PC", f"No pude cambiar el estado: {exc}", parent=self)
            return
        messagebox.showinfo("Control de la PC", message, parent=self)
        self.refresh_state_view()

    def _set_publish_confirmation(self, require: bool):
        if not require and not messagebox.askyesno(
            "Publicaciones",
            "Yarbis publicara (navegador y API) SIN pedir confirmacion. "
            "Podria publicar algo por error. ¿Continuar?",
            parent=self,
        ):
            return
        try:
            m1 = set_computer_control(confirm_sensitive=bool(require))
            m2 = set_social_confirmation(enabled=bool(require))
        except Exception as exc:
            messagebox.showwarning("Publicaciones", f"No pude cambiar la confirmacion: {exc}", parent=self)
            return
        messagebox.showinfo("Publicaciones", f"{m1}\n{m2}", parent=self)
        self.refresh_state_view()

    def _set_evolution_interval(self):
        raw = simpledialog.askstring("Autoevolución", "Cadencia en horas (1-168):", parent=self)
        if raw is None or not str(raw).strip():
            return
        messagebox.showinfo("Autoevolución", evolution_set_interval_text(str(raw).strip()), parent=self)

    def _show_evolution_pending(self):
        try:
            text = (
                evolution_list_pending_text()
                + "\n\n"
                + evolution_list_suggestions_text()
                + "\n\nLas propuestas de código se ven en el panel de Coding."
            )
        except Exception as exc:
            messagebox.showwarning("Autoevolución", f"No pude leer las propuestas: {exc}", parent=self)
            return
        messagebox.showinfo("Propuestas pendientes", text, parent=self)

    def _approve_evolution_item(self):
        raw = simpledialog.askstring("Aprobar propuesta", "Id de la directriz o sugerencia:", parent=self)
        if raw is None or not str(raw).strip():
            return
        item_id = str(raw).strip()
        result = evolution_apply_directive_text(item_id)
        if "No encontre" in result:
            result = evolution_apply_suggestion_text(item_id)
        messagebox.showinfo("Aprobar propuesta", result, parent=self)
        self.refresh_state_view()

    def _discard_evolution_item(self):
        raw = simpledialog.askstring("Descartar propuesta", "Id de la directriz o sugerencia:", parent=self)
        if raw is None or not str(raw).strip():
            return
        item_id = str(raw).strip()
        result = evolution_discard_directive_text(item_id)
        if "No encontre" in result:
            result = evolution_discard_suggestion_text(item_id)
        messagebox.showinfo("Descartar propuesta", result, parent=self)
        self.refresh_state_view()

    def _analyze_image_file(self):
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Elegir imagen(es)",
            filetypes=[
                ("Imágenes", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("Todos los archivos", "*.*"),
            ],
        )
        paths = list(paths or [])
        if not paths:
            return
        prompt = "Pregunta opcional sobre la imagen:" if len(paths) == 1 else f"Pregunta sobre las {len(paths)} imágenes:"
        question = simpledialog.askstring("Analizar imagen", prompt, parent=self)
        try:
            if len(paths) == 1:
                result = analyze_image_text(paths[0], str(question or "").strip())
            else:
                result = analyze_images_text(paths, str(question or "").strip())
        except Exception as exc:
            messagebox.showwarning("Imagen", f"No pude analizar la imagen: {exc}", parent=self)
            return
        messagebox.showinfo("Análisis de imagen", result, parent=self)

    def _show_vision_status(self):
        messagebox.showinfo("Visión", vision_status_text(), parent=self)

    def _build_activity_view(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        activity_frame = create_section(parent, "Respuestas e historial", "Registro local reciente y eventos del servicio.")
        activity_frame.grid(row=0, column=0, sticky="nsew")
        activity_frame.rowconfigure(0, weight=1)
        self.activity_text = self._create_scrolled_text(activity_frame, wrap="word", height=22)
        self.activity_text.frame.grid(row=0, column=0, sticky="nsew")
        self.activity_text.configure(state="disabled")

        activity_buttons = ttk.Frame(activity_frame)
        activity_buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(activity_buttons, text="Refrescar", command=self.refresh_state_view).pack(side="left")
        clear_button = ttk.Button(
            activity_buttons,
            text="Limpiar actividad",
            command=self._clear_activity,
            style="Danger.TButton",
        )
        clear_button.pack(side="left", padx=(8, 0))
        self._action_buttons.append(clear_button)

    def _show_view(self, name: str):
        if name not in self._view_frames:
            name = "home"
        self._current_view = name
        self._view_frames[name].tkraise()
        for view_name, button in self._nav_buttons.items():
            button.configure(style="Active.Nav.TButton" if view_name == name else "Nav.TButton")
        if name == "instances":
            self._refresh_instance_panels(force=True)

    def _selected_instance_id(self) -> str:
        if "instances_tree" not in self.__dict__:
            return yarbis_instance.current_instance_id()
        selection = self.instances_tree.selection()
        if not selection:
            return yarbis_instance.current_instance_id()
        return str(selection[0]).strip() or yarbis_instance.current_instance_id()

    def _selected_archive_id(self) -> str:
        if "archived_tree" not in self.__dict__:
            return ""
        selection = self.archived_tree.selection()
        return str(selection[0]).strip() if selection else ""

    def _refresh_instance_panels(self, force: bool = False):
        now = time.monotonic()
        if (
            not force
            and self._current_view != "instances"
            and (now - self._last_instance_panel_refresh_at) < _INSTANCE_PANEL_SYNC_SECONDS
        ):
            return
        self._last_instance_panel_refresh_at = now
        rows, warnings = _instance_overview_rows()
        self._instance_rows = rows
        self.instance_chip_var.set(_current_instance_chip_text(rows))
        self.instance_warning_var.set(" | ".join(warnings) if warnings else "Sin advertencias entre instancias activas.")

        if "instances_tree" in self.__dict__:
            selected = self._selected_instance_id()
            for item_id in self.instances_tree.get_children():
                self.instances_tree.delete(item_id)
            for row in rows:
                self.instances_tree.insert(
                    "",
                    "end",
                    iid=row["id"],
                    values=(
                        row["display_name"],
                        row["id"],
                        row["active_text"],
                        row["service_name"],
                        row["mobile_port"],
                        row["mobile_https_text"],
                        row["pending_messages"],
                        row["state_file"],
                    ),
                )
            if selected in self.instances_tree.get_children():
                self.instances_tree.selection_set(selected)
                self.instances_tree.focus(selected)
            elif rows:
                self.instances_tree.selection_set(rows[0]["id"])
                self.instances_tree.focus(rows[0]["id"])

        if "message_target_combo" in self.__dict__:
            current = yarbis_instance.current_instance_id()
            targets = [row["id"] for row in rows if row["id"] != current]
            self.message_target_combo.configure(values=targets)
            if self.message_target_var.get() not in targets:
                self.message_target_var.set(targets[0] if targets else "")

        if "archived_tree" in self.__dict__:
            self._archived_rows = {
                item["archive_id"]: item
                for item in yarbis_instance.list_archived_instances()
            }
            selected_archive = self._selected_archive_id()
            for item_id in self.archived_tree.get_children():
                self.archived_tree.delete(item_id)
            for archive_id, item in self._archived_rows.items():
                self.archived_tree.insert(
                    "",
                    "end",
                    iid=archive_id,
                    values=(
                        item["display_name"],
                        item["id"],
                        item["archived_at"],
                        item["archive_dir"],
                    ),
                )
            if selected_archive in self.archived_tree.get_children():
                self.archived_tree.selection_set(selected_archive)

        self._refresh_messages_view()
        self._update_instance_action_states()

    def _refresh_messages_view(self):
        if "messages_tree" not in self.__dict__:
            return
        current = yarbis_instance.current_instance_id()
        self._message_rows = {}
        for item_id in self.messages_tree.get_children():
            self.messages_tree.delete(item_id)
        for message in yarbis_bus.list_instance_messages(instance_id=current, limit=80):
            message_id = str(message.get("id", "")).strip()
            if not message_id:
                continue
            sender = str(message.get("from_instance", "")).strip()
            receiver = str(message.get("to_instance", "")).strip()
            outgoing = sender == current
            peer = receiver if outgoing else sender
            preview = str(message.get("response") or message.get("content", "")).replace("\n", " ").strip()
            if len(preview) > 160:
                preview = preview[:157].rstrip() + "..."
            self._message_rows[message_id] = message
            self.messages_tree.insert(
                "",
                "end",
                iid=message_id,
                values=(
                    "enviado" if outgoing else "recibido",
                    peer,
                    message.get("kind", ""),
                    message.get("status", ""),
                    str(message.get("created_at", ""))[:19],
                    preview,
                ),
            )

    def _update_instance_action_states(self):
        if "instances_tree" not in self.__dict__:
            return
        selected_id = self._selected_instance_id()
        row = next((item for item in self._instance_rows if item["id"] == selected_id), None)
        has_row = row is not None
        can_archive = bool(row and row.get("can_archive"))
        has_archived = bool(self._selected_archive_id())
        is_current = has_row and selected_id == yarbis_instance.current_instance_id()
        for button_name, enabled in (
            ("open_instance_button", has_row),
            ("switch_instance_button", has_row and not is_current),
            ("rename_instance_button", has_row),
            ("copy_instance_path_button", has_row),
            ("copy_instance_command_button", has_row),
            ("claim_instance_https_button", bool(row and row.get("mobile_https_available"))),
            ("start_instance_service_button", has_row),
            ("stop_instance_service_button", has_row),
            ("autostart_instance_service_button", has_row),
            ("archive_instance_button", can_archive),
            ("restore_instance_button", has_archived),
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state="normal" if enabled and not self._busy else "disabled")

    def _create_instance_from_panel(self):
        raw_id = simpledialog.askstring("Nueva instancia", "Id corto para la nueva instancia:", parent=self)
        if raw_id is None:
            return
        display_name = simpledialog.askstring(
            "Nueva instancia",
            "Nombre visible opcional:",
            parent=self,
        )
        try:
            created = yarbis_instance.create_instance(raw_id, display_name=display_name or "")
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude crear la instancia: {exc}", parent=self)
            return
        self._append_activity("Instancias", f"Instancia creada: {created['display_name']} ({created['id']}).")
        self._refresh_instance_panels(force=True)

    def _rename_selected_instance(self):
        instance_id = self._selected_instance_id()
        row = next((item for item in self._instance_rows if item["id"] == instance_id), {})
        display_name = simpledialog.askstring(
            "Renombrar instancia",
            "Nombre visible:",
            initialvalue=row.get("display_name", instance_id),
            parent=self,
        )
        if display_name is None:
            return
        try:
            renamed = yarbis_instance.rename_instance(instance_id, display_name)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude renombrar la instancia: {exc}", parent=self)
            return
        self._append_activity("Instancias", f"Instancia renombrada: {renamed['display_name']} ({renamed['id']}).")
        self._refresh_instance_panels(force=True)

    def _open_selected_instance(self):
        instance_id = self._selected_instance_id()
        if instance_id == yarbis_instance.current_instance_id():
            messagebox.showinfo("Yarbis", "Esa instancia ya esta abierta en esta ventana.", parent=self)
            return
        try:
            _launch_desktop_instance(instance_id)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir la instancia: {exc}", parent=self)
            return
        self._append_activity("Instancias", f"Abriendo instancia {instance_id}.")

    def _switch_selected_instance(self):
        self._switch_to_instance(self._selected_instance_id())

    def _switch_to_instance(self, instance_id: str):
        target = yarbis_instance.normalize_instance_id(instance_id)
        if target == yarbis_instance.current_instance_id():
            messagebox.showinfo("Yarbis", "Esa instancia ya está abierta en esta ventana.", parent=self)
            return
        if self._busy and not messagebox.askyesno(
            "Cambiar de instancia",
            "Hay una acción en curso. ¿Quieres cambiar de instancia de todos modos?",
            parent=self,
        ):
            return
        if _desktop_pid_of(target):
            messagebox.showinfo("Yarbis", "Esa instancia ya está abierta en otra ventana.", parent=self)
            return
        previous_pid = yarbis_instance._read_pid(yarbis_instance.runtime_dir(target) / "desktop.pid")
        try:
            _launch_desktop_instance(target, geometry=self.winfo_geometry())
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir la instancia {target}: {exc}", parent=self)
            return
        self.status_var.set(f"Cambiando a {target}...")
        self._await_instance_switch(target, previous_pid, time.monotonic() + 15.0)

    def _await_instance_switch(self, target: str, previous_pid: int, deadline: float):
        if self._closing:
            return
        pid = yarbis_instance._read_pid(yarbis_instance.runtime_dir(target) / "desktop.pid")
        if pid and pid != previous_pid and yarbis_instance._pid_is_running(pid):
            self._append_activity("Instancias", f"Cambiando a {target}.")
            self._shutdown_window()
            return
        if time.monotonic() >= deadline:
            crash_log = yarbis_instance.runtime_dir(target) / "desktop_crash.log"
            messagebox.showwarning(
                "Yarbis",
                (
                    f"La instancia {target} no terminó de abrir a tiempo. "
                    f"Reviso el registro en {crash_log} si el problema continúa."
                ),
                parent=self,
            )
            self.status_var.set("Listo.")
            return
        self.after(250, lambda: self._await_instance_switch(target, previous_pid, deadline))

    def _copy_to_clipboard(self, label: str, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
        except tk.TclError as exc:
            messagebox.showwarning("Yarbis", f"No pude copiar {label}: {exc}", parent=self)
            return
        self.status_var.set(f"{label} copiado.")

    def _copy_selected_instance_path(self):
        instance_id = self._selected_instance_id()
        path = str(yarbis_instance.instance_root(instance_id))
        self._copy_to_clipboard("Ruta de instancia", path)

    def _copy_selected_instance_command(self):
        instance_id = self._selected_instance_id()
        command = f'"{_python_window_path()}" "{_WORKSPACE_ROOT / "yarbis_desktop.py"}" --instance "{instance_id}"'
        self._copy_to_clipboard("Comando de instancia", command)

    @staticmethod
    def _claim_instance_mobile_https(instance_id: str) -> str:
        normalized = yarbis_instance.normalize_instance_id(instance_id)
        process = subprocess.run(
            [
                str(_python_console_path()),
                "-c",
                "import yarbis_mobile; print(yarbis_mobile.claim_mobile_https_for_current_instance())",
            ],
            cwd=str(_WORKSPACE_ROOT),
            env=yarbis_instance.with_instance_env(normalized),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = "\n".join(part.strip() for part in (process.stdout, process.stderr) if part and part.strip()).strip()
        if process.returncode != 0:
            raise RuntimeError(output or f"No pude mover HTTPS a {normalized}.")
        return output or f"HTTPS movil reclamada por {normalized}."

    def _claim_selected_instance_https(self):
        instance_id = self._selected_instance_id()
        row = next((item for item in self._instance_rows if item["id"] == instance_id), {})
        if not row.get("mobile_https_available"):
            messagebox.showinfo(
                "Yarbis",
                "Esa instancia debe estar activa y tener la UI movil con PIN para usar HTTPS.",
                parent=self,
            )
            return
        self._start_background_job(
            f"HTTPS {instance_id}",
            self._claim_instance_mobile_https,
            instance_id,
        )

    def _start_selected_instance_service(self):
        self._start_background_job(
            f"Servicio {self._selected_instance_id()}",
            start_service,
            instance_id=self._selected_instance_id(),
        )

    def _stop_selected_instance_service(self):
        self._start_background_job(
            f"Servicio {self._selected_instance_id()}",
            stop_service,
            instance_id=self._selected_instance_id(),
        )

    @staticmethod
    def _toggle_instance_autostart(instance_id: str) -> str:
        status = get_service_status(force=True, instance_id=instance_id)
        enabled = not bool(status.get("autostart_enabled"))
        return set_autostart_enabled(enabled, instance_id=instance_id)

    def _toggle_selected_instance_autostart(self):
        self._start_background_job(
            f"Autostart {self._selected_instance_id()}",
            self._toggle_instance_autostart,
            self._selected_instance_id(),
        )

    @staticmethod
    def _archive_instance_with_service_cleanup(instance_id: str) -> str:
        status = get_service_status(force=True, instance_id=instance_id)
        service_text = ""
        if status.get("installed"):
            service_text = remove_service(instance_id=instance_id)
        archived = yarbis_instance.archive_instance(instance_id)
        archive_text = f"Instancia {instance_id} archivada en {archived['archive_dir']}."
        return "\n".join(part for part in (service_text, archive_text) if part)

    def _archive_selected_instance(self):
        instance_id = self._selected_instance_id()
        row = next((item for item in self._instance_rows if item["id"] == instance_id), {})
        if not _instance_archive_enabled(row):
            messagebox.showinfo("Yarbis", "Esta instancia no se puede archivar ahora.", parent=self)
            return
        confirmation = simpledialog.askstring(
            "Archivar instancia",
            f"Escribe {instance_id} para archivar la instancia. Sus datos se moveran a _archived.",
            parent=self,
        )
        if confirmation != instance_id:
            return
        self._start_background_job(
            f"Archivar {instance_id}",
            self._archive_instance_with_service_cleanup,
            instance_id,
        )

    def _restore_selected_archive(self):
        archive_id = self._selected_archive_id()
        if not archive_id:
            return
        self._start_background_job(
            "Restaurar instancia",
            lambda archive=archive_id: (
                f"Instancia restaurada: "
                f"{yarbis_instance.restore_archived_instance(archive)['id']}"
            ),
        )

    @staticmethod
    def _send_instance_message_for_ui(target_instance: str, message: str, timeout_seconds: int) -> str:
        result = yarbis_bus.send_message(
            target_instance,
            message,
            wait_for_reply=True,
            timeout_seconds=timeout_seconds,
        )
        status = str(result.get("status", "")).strip()
        message_id = str(result.get("id", "")).strip()
        if status == yarbis_bus.STATUS_DONE:
            response = str(result.get("response", "")).strip() or "Sin respuesta visible."
            return f"Mensaje entregado a {target_instance} ({message_id}).\n\nRespuesta:\n{response}"
        if status == yarbis_bus.STATUS_ERROR:
            return f"Mensaje con error en {target_instance} ({message_id}): {result.get('error', '')}"
        if result.get("status_text") == "timeout":
            return f"Mensaje enviado a {target_instance} ({message_id}), pero no llego respuesta antes del timeout."
        return f"Mensaje en cola para {target_instance} ({message_id})."

    def _send_instance_message(self):
        target = self.message_target_var.get().strip()
        if not target:
            messagebox.showinfo("Yarbis", "Elige una instancia destino.", parent=self)
            return
        if target == yarbis_instance.current_instance_id():
            messagebox.showinfo("Yarbis", "Elige una instancia distinta a la actual.", parent=self)
            return
        content = self.message_text.get("1.0", "end-1c").strip()
        if not content:
            messagebox.showinfo("Yarbis", "Escribe un mensaje para enviar.", parent=self)
            return
        try:
            timeout_seconds = max(0, min(600, int(self.message_timeout_var.get() or "120")))
        except ValueError:
            timeout_seconds = 120
            self.message_timeout_var.set("120")
        self._start_background_job(
            f"Mensaje a {target}",
            self._send_instance_message_for_ui,
            target,
            content,
            timeout_seconds,
        )

    def _prepare_message_reply(self):
        if "messages_tree" not in self.__dict__:
            return
        selection = self.messages_tree.selection()
        if not selection:
            return
        message = self._message_rows.get(str(selection[0]))
        if not message:
            return
        current = yarbis_instance.current_instance_id()
        sender = str(message.get("from_instance", "")).strip()
        receiver = str(message.get("to_instance", "")).strip()
        peer = sender if sender != current else receiver
        if peer and peer != current:
            self.message_target_var.set(peer)
        content = str(message.get("response") or message.get("content", "")).strip()
        if content:
            self.message_text.delete("1.0", "end")
            self.message_text.insert("1.0", f"Sobre tu mensaje: {content[:500]}\n\n")
        self.message_text.focus_set()

    def _mark_selected_messages_read(self):
        if "messages_tree" not in self.__dict__:
            return
        message_ids = [str(item) for item in self.messages_tree.selection()]
        marked = yarbis_bus.mark_messages_read(message_ids)
        self._append_activity("Mensajes Yarbis", f"Mensajes marcados como leidos: {marked}.")
        self._refresh_instance_panels(force=True)

    def _open_message_target_instance(self):
        target = self.message_target_var.get().strip()
        if not target:
            messagebox.showinfo("Yarbis", "Elige una instancia destino.", parent=self)
            return
        if target == yarbis_instance.current_instance_id():
            messagebox.showinfo("Yarbis", "Esa instancia ya esta abierta en esta ventana.", parent=self)
            return
        try:
            _launch_desktop_instance(target)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir la instancia: {exc}", parent=self)

    def _create_scrolled_text(self, parent, **text_options):
        frame = tk.Frame(parent, bd=0, highlightthickness=0)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(frame, **text_options)
        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=text.yview,
            style="Yarbis.Vertical.TScrollbar",
        )
        text.configure(yscrollcommand=scrollbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        text.frame = frame
        text.vbar = scrollbar
        return text

    def _build_action_group(self, parent, title: str, button_specs):
        group = ttk.LabelFrame(parent, text=title)
        group.pack(fill="x", pady=(0, 8))

        for spec in button_specs:
            button_options = {
                "command": spec["command"],
                "style": spec.get("style", "TButton"),
            }
            if "textvariable" in spec:
                button_options["textvariable"] = spec["textvariable"]
            else:
                button_options["text"] = spec["text"]
            self._pack_action_button(
                ttk.Button(group, **button_options),
                disable_when_busy=spec.get("disable_when_busy", True),
            )

    def _build_service_group(self, parent):
        group = ttk.LabelFrame(parent, text="Servicio")
        group.pack(fill="x", pady=(0, 8))

        self.service_toggle_button = ttk.Button(
            group,
            textvariable=self.service_button_text,
            command=self._toggle_service,
            style="Secondary.TButton",
        )
        self._pack_action_button(self.service_toggle_button)

        self.service_autostart_check = tk.Checkbutton(
            group,
            textvariable=self.service_autostart_text,
            variable=self.service_autostart_var,
            command=self._toggle_service_autostart,
            indicatoron=False,
            anchor="center",
            padx=10,
            pady=7,
            bd=0,
            relief="flat",
            highlightthickness=1,
        )
        self.service_autostart_check.pack(fill="x", padx=8, pady=(5, 8))
        self._action_buttons.append(self.service_autostart_check)

        self.service_pulse_button = ttk.Button(
            group,
            text="Configurar pulso",
            command=self._edit_service_pulse,
        )
        self._pack_action_button(self.service_pulse_button)

        self.mobile_ui_button = ttk.Button(
            group,
            text="UI móvil",
            command=self._edit_mobile_ui,
        )
        self._pack_action_button(self.mobile_ui_button)

        self.local_context_button = ttk.Button(
            group,
            text="Contexto local",
            command=self._edit_local_context,
        )
        self._pack_action_button(self.local_context_button)

        self.remove_service_button = ttk.Button(
            group,
            text="Quitar de SCM",
            command=self._remove_service,
            style="Danger.TButton",
        )
        self._pack_action_button(self.remove_service_button)

    def _pack_action_button(self, button, disable_when_busy: bool = True):
        button.pack(fill="x", padx=8, pady=3)
        if disable_when_busy:
            self._action_buttons.append(button)

    def _on_actions_content_configure(self, _event=None):
        self.actions_canvas.configure(scrollregion=self.actions_canvas.bbox("all"))

    def _on_actions_canvas_configure(self, event):
        self.actions_canvas.itemconfigure(self.actions_window, width=event.width)

    def _bind_action_mousewheel(self, _event=None):
        self.actions_canvas.bind_all("<MouseWheel>", self._on_action_mousewheel)

    def _unbind_action_mousewheel(self, _event=None):
        self.actions_canvas.unbind_all("<MouseWheel>")

    def _on_action_mousewheel(self, event):
        self.actions_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _apply_theme(self, theme_name: str):
        self.current_theme_name = theme_name if theme_name in THEMES else "dark"
        self.theme_palette = THEMES[self.current_theme_name]
        palette = self.theme_palette

        if not use_bootstrap_theme(self.style, self.current_theme_name) and not self._bootstrap_style_active:
            try:
                self.style.theme_use("clam")
            except tk.TclError:
                pass
        self.configure(bg=palette["bg"])
        if hasattr(self, "actions_canvas"):
            self.actions_canvas.configure(bg=palette["bg"])
        if hasattr(self, "actions_scrollbar"):
            style_scrollbar_widget(self.actions_scrollbar, palette)
        if hasattr(self, "home_canvas"):
            self.home_canvas.configure(bg=palette["bg"])
        if hasattr(self, "home_scrollbar"):
            style_scrollbar_widget(self.home_scrollbar, palette)

        configure_app_styles(self.style, palette)
        self.style.configure(
            "Hidden.TButton",
            background=palette["bg"],
            foreground=palette["bg"],
            bordercolor=palette["bg"],
            lightcolor=palette["bg"],
            darkcolor=palette["bg"],
            relief="flat",
            padding=(0, 0),
        )
        self.style.map(
            "Hidden.TButton",
            background=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
            foreground=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
            bordercolor=[
                ("active", palette["button_active"]),
                ("pressed", palette["button_active"]),
                ("disabled", palette["bg"]),
            ],
        )
        self.option_add("*TCombobox*Listbox*Background", palette["field_bg"])
        self.option_add("*TCombobox*Listbox*Foreground", palette["field_fg"])
        self.option_add("*TCombobox*Listbox*selectBackground", palette["select_bg"])
        self.option_add("*TCombobox*Listbox*selectForeground", palette["select_fg"])

        style_text_widget(self.summary_text, palette)
        for widget in getattr(self, "_last_result_widgets", []):
            style_text_widget(widget, palette)
        style_text_widget(self.activity_text, palette)
        style_text_widget(self.reply_text, palette)
        if "message_text" in self.__dict__:
            style_text_widget(self.message_text, palette)
        self._style_service_autostart_toggle()
        if self._current_view:
            self._show_view(self._current_view)

        self.theme_var.set("Oscuro" if self.current_theme_name == "dark" else "Claro")
        self.theme_button_text.set(
            "Usar modo claro" if self.current_theme_name == "dark" else "Usar modo oscuro"
        )

    def _style_service_autostart_toggle(self):
        if not hasattr(self, "service_autostart_check"):
            return

        palette = self.theme_palette
        enabled = bool(self.service_autostart_var.get())
        state = str(self.service_autostart_check.cget("state"))
        disabled = state == "disabled"

        if disabled:
            bg = palette["disabled_bg"]
            fg = palette["disabled_fg"]
            active_bg = palette["disabled_bg"]
        elif enabled:
            bg = palette["secondary"]
            fg = palette["secondary_fg"]
            active_bg = palette["secondary_hover"]
        else:
            bg = palette["button_bg"]
            fg = palette["fg"]
            active_bg = palette["button_active"]

        self.service_autostart_check.configure(
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=fg,
            selectcolor=bg,
            disabledforeground=palette["disabled_fg"],
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )

    def _set_text(self, widget, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    @staticmethod
    def _format_last_result_text(state: dict) -> str:
        result = str(state.get("last_result", "")).strip()
        if result:
            return result
        return "Aún no hay respuesta de Yarbis. Ejecuta un ciclo o responde una pregunta para verla aquí."

    def _refresh_last_result_widgets(self, state: dict):
        content = self._format_last_result_text(state)
        if content == self._last_result_text:
            return
        for widget in getattr(self, "_last_result_widgets", []):
            self._set_text(widget, content)
        self._last_result_text = content

    def _activity_should_follow_end(self) -> bool:
        try:
            return self.activity_text.yview()[1] >= 0.98
        except tk.TclError:
            return True

    def _refresh_activity_view(self, force_scroll: bool = False):
        signature = activity.activity_history_signature()
        if not force_scroll and signature == self._last_activity_signature:
            return

        content = activity.read_activity_history()
        if content == self._last_activity_text:
            self._last_activity_signature = signature
            return

        should_scroll = force_scroll or self._activity_should_follow_end()
        try:
            previous_view = self.activity_text.yview()[0]
        except tk.TclError:
            previous_view = 1.0

        self.activity_text.configure(state="normal")
        self.activity_text.delete("1.0", "end")
        self.activity_text.insert("1.0", content)
        if should_scroll:
            self.activity_text.see("end")
        else:
            self.activity_text.yview_moveto(previous_view)
        self.activity_text.configure(state="disabled")
        self._last_activity_text = content
        self._last_activity_signature = signature

    def _append_activity_fallback(self, title: str, content: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rendered = f"[{timestamp}] {title}\n{content.strip() or 'Sin salida adicional.'}\n\n"
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", rendered)
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")
        self._last_activity_text += rendered

    def _append_activity(self, title: str, content: str):
        try:
            activity.append_activity(title, content)
            self._refresh_activity_view(force_scroll=True)
        except Exception:  # pragma: no cover - respaldo visual si falla el disco
            self._append_activity_fallback(title, str(content))

    @staticmethod
    def _state_file_signature() -> tuple[str, int, int]:
        state_path = Path(memory_store.STATE_FILE)
        if not state_path.is_absolute():
            state_path = _WORKSPACE_ROOT / state_path
        try:
            stat = state_path.stat()
        except OSError:
            return (str(state_path), 0, 0)
        return (str(state_path), int(stat.st_size), int(stat.st_mtime_ns))

    def _load_state_for_view(self, force_reload: bool = True) -> dict:
        signature = self._state_file_signature()
        if (
            not force_reload
            and self._cached_state is not None
            and signature == self._last_state_file_signature
        ):
            return self._cached_state

        state = load_state()
        self._cached_state = state
        self._last_state_file_signature = signature
        return state

    @staticmethod
    def _thinking_status_text(label: str) -> str:
        safe_label = str(label).strip() or "Operación"
        return f"Estoy pensando: {safe_label}..."

    @staticmethod
    def _format_thinking_started_at(value: str) -> str:
        started_at = str(value).strip()
        if not started_at:
            return ""
        try:
            parsed = datetime.fromisoformat(started_at)
            return parsed.astimezone().strftime("%H:%M:%S")
        except ValueError:
            return started_at

    @staticmethod
    def _runtime_thinking_from_state(state: dict) -> tuple[bool, str, str]:
        thinking = state.get("runtime", {}).get("thinking", {})
        thinking_label = str(thinking.get("label", "")).strip() or "Operación"
        thinking_active = bool(thinking.get("active")) and bool(thinking_label)
        started_at = str(thinking.get("started_at", "")).strip()
        return thinking_active, thinking_label, started_at

    def _sync_runtime_thinking(self, state: dict | None = None) -> bool:
        if self._clear_abandoned_runtime_operation():
            state = load_state()
        state = state or load_state()
        thinking_active, thinking_label, started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            started_display = self._format_thinking_started_at(started_at)
            started_text = f" desde {started_display}" if started_display else ""
            self.thinking_var.set(f"SI - {thinking_label}{started_text}")
            self._set_busy(True, self._thinking_status_text(thinking_label), source="runtime")
            return True

        live_status = voice_conversation.desktop_status()
        live_state = str(live_status.get("state", "")).strip()
        if live_state and live_state != voice_conversation.STATE_IDLE:
            detail = str(live_status.get("detail", "")).strip()
            self.thinking_var.set(f"Voz en vivo - {detail or live_state}")
            return False

        self.thinking_var.set("No.")
        for source in ("runtime", "runtime_event"):
            if source in self._busy_sources:
                self._set_busy(False, source=source)
        return False

    def _clear_abandoned_runtime_operation(self) -> bool:
        try:
            return bool(clear_abandoned_runtime_operation())
        except Exception:
            return False

    def _show_thinking_blocked_message(self, label: str):
        messagebox.showinfo(
            "Yarbis",
            (
                f"{self._thinking_status_text(label)}\n\n"
                "La entrada queda bloqueada hasta que termine para no mezclar dos operaciones."
            ),
            parent=self,
        )

    def _status_refresh_due(
        self,
        force: bool = False,
        ensure_context_helper: bool = False,
        ensure_context_task: bool = False,
    ) -> bool:
        if force or ensure_context_helper or ensure_context_task:
            return True
        if self._cached_health_status is None or self._cached_readiness_status is None:
            return True
        return time.monotonic() - self._last_status_refresh_at >= (_STATUS_REFRESH_INTERVAL_MS / 1000)

    def _request_status_refresh(
        self,
        force: bool = False,
        ensure_context_helper: bool = False,
        ensure_context_task: bool = False,
    ) -> bool:
        if self._closing or not self._status_refresh_due(
            force=force,
            ensure_context_helper=ensure_context_helper,
            ensure_context_task=ensure_context_task,
        ):
            return False

        if self._status_refresh_in_flight:
            self._status_refresh_pending_force = self._status_refresh_pending_force or force
            self._status_refresh_pending_context_helper = (
                self._status_refresh_pending_context_helper or ensure_context_helper
            )
            self._status_refresh_pending_context_task = self._status_refresh_pending_context_task or ensure_context_task
            return False

        self._status_refresh_in_flight = True
        if self._cached_health_status is None:
            self.health_var.set("Consultando...")
            self.readiness_var.set("Consultando...")

        def worker():
            try:
                snapshot = self._build_status_snapshot(
                    force=force,
                    ensure_context_helper=ensure_context_helper,
                    ensure_context_task=ensure_context_task,
                )
                self._result_queue.put(("status_snapshot", "Estado", snapshot))
            except Exception as exc:  # pragma: no cover - respaldo visual
                self._result_queue.put(("status_error", "Estado", str(exc)))

        threading.Thread(
            target=worker,
            name="yarbis-desktop-status-refresh",
            daemon=True,
        ).start()
        return True

    def _build_status_snapshot(
        self,
        force: bool = False,
        ensure_context_helper: bool = False,
        ensure_context_task: bool = False,
    ) -> dict:
        health = health_status(force_service=force)
        readiness = readiness_status(force=force)
        helper_status = get_context_helper_status()

        try:
            state = load_state()
        except Exception:
            state = {}

        service_status = health.get("service", {}) if isinstance(health, dict) else {}
        service_running = bool(service_status.get("running")) if isinstance(service_status, dict) else False
        events = []

        self._ensure_telegram_polling_matches_service(service_running)

        if ensure_context_helper or ensure_context_task:
            for message in self._sync_context_helper_background(
                ensure_task=ensure_context_task,
                service_status=service_status if isinstance(service_status, dict) else None,
                local_context_settings=state.get("local_context", {}) if isinstance(state, dict) else {},
            ):
                events.append(("Contexto local", message))
            helper_status = get_context_helper_status()

        return {
            "health": health,
            "readiness": readiness,
            "context_helper": helper_status,
            "events": events,
        }

    def _sync_context_helper_background(
        self,
        ensure_task: bool = False,
        service_status: dict | None = None,
        local_context_settings: dict | None = None,
    ) -> list[str]:
        try:
            settings = local_context_settings if isinstance(local_context_settings, dict) else get_local_context_settings()
            service = service_status if isinstance(service_status, dict) else get_service_status()
        except Exception:
            return []

        if not (service.get("running") and local_context_enabled(settings)):
            return []

        try:
            messages = []
            if ensure_task:
                messages.append(ensure_context_task())
            if not get_context_helper_status().get("running"):
                messages.append(start_context_helper())
            return [message for message in messages if str(message).strip()]
        except Exception as exc:
            return [f"No pude iniciar el helper local: {exc}"]

    def _apply_status_snapshot(self, snapshot: dict):
        if not isinstance(snapshot, dict):
            return

        self._cached_health_status = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else None
        self._cached_readiness_status = (
            snapshot.get("readiness") if isinstance(snapshot.get("readiness"), dict) else None
        )
        self._cached_context_helper_status = (
            snapshot.get("context_helper") if isinstance(snapshot.get("context_helper"), dict) else None
        )
        self._last_status_refresh_at = time.monotonic()

        for event in snapshot.get("events", []):
            if not isinstance(event, (list, tuple)) or len(event) != 2:
                continue
            title, body = event
            if str(body).strip():
                self._append_activity(str(title), str(body))

        self.refresh_state_view(force_heavy=False, force_state_reload=False)

    def _finish_status_refresh(self):
        pending_force = self._status_refresh_pending_force
        pending_context_helper = self._status_refresh_pending_context_helper
        pending_context_task = self._status_refresh_pending_context_task
        self._status_refresh_in_flight = False
        self._status_refresh_pending_force = False
        self._status_refresh_pending_context_helper = False
        self._status_refresh_pending_context_task = False
        if pending_force or pending_context_helper or pending_context_task:
            self._request_status_refresh(
                force=pending_force,
                ensure_context_helper=pending_context_helper,
                ensure_context_task=pending_context_task,
            )

    def _cached_service_status(self) -> dict | None:
        health = self._cached_health_status
        if not isinstance(health, dict):
            return None
        service_status = health.get("service")
        return service_status if isinstance(service_status, dict) else None

    def _require_cached_service_status(self) -> dict | None:
        service_status = self._cached_service_status()
        if service_status is not None:
            return service_status

        self.status_var.set("Consultando servicio...")
        self._request_status_refresh(force=True)
        messagebox.showinfo(
            "Yarbis",
            "Estoy consultando el estado del servicio. Intenta de nuevo en un momento.",
            parent=self,
        )
        return None

    def _set_service_controls_state(self, service_status: dict | None):
        disabled = self._busy or service_status is None
        toggle_state = "disabled" if disabled else "normal"
        installed = bool(service_status and service_status.get("installed"))
        installed_state = "normal" if installed and not self._busy else "disabled"

        if hasattr(self, "service_toggle_button"):
            self.service_toggle_button.configure(state=toggle_state)
        if hasattr(self, "remove_service_button"):
            self.remove_service_button.configure(state=installed_state)
        if hasattr(self, "service_autostart_check"):
            self.service_autostart_check.configure(state=installed_state)
            self._style_service_autostart_toggle()

    def refresh_state_view(self, force_heavy: bool = True, force_state_reload: bool = True):
        state = self._load_state_for_view(force_reload=force_state_reload)
        self._request_status_refresh(force=force_heavy)
        self.goal_var.set(state["goal"])
        self.cycles_var.set(str(state["cycle_count"]))
        model_provider = state.get("model_provider", {})
        if not isinstance(model_provider, dict):
            model_provider = {}
        active_provider = str(model_provider.get("default", MODEL_PROVIDER_OLLAMA)).strip().lower()
        if active_provider not in {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}:
            active_provider = MODEL_PROVIDER_OLLAMA
        model_settings = model_provider.get(active_provider, state.get("ollama", {}))
        if not isinstance(model_settings, dict):
            model_settings = {}
        fallback_models = model_settings.get("fallback_models", [])
        fallback_text = f", +{len(fallback_models)} fallback(s)" if fallback_models else ""
        host_text = model_settings.get("host") or "local"
        provider_label = "OpenRouter" if active_provider == MODEL_PROVIDER_OPENROUTER else "Ollama"
        model_name = model_settings.get("model") or ("sin modelo" if active_provider == MODEL_PROVIDER_OPENROUTER else DEFAULT_OLLAMA_MODEL)
        self.ollama_var.set(
            f"{provider_label}: {model_name}{fallback_text} "
            f"@ {host_text} "
            f"({model_settings.get('timeout_seconds', DEFAULT_OLLAMA_TIMEOUT_SECONDS)}s)"
        )
        coding_settings = state.get("coding", {})
        coding_workspace = str(coding_settings.get("workspace_path", "")).strip()
        coding_pending = coding_settings.get("pending_proposal_ids", [])
        if coding_workspace:
            self.coding_var.set(
                f"{coding_workspace} "
                f"(modo={coding_settings.get('mode', 'propose_first')}, "
                f"propuestas={len(coding_pending)}, "
                f"validación={coding_settings.get('validation_command') or '-'})"
            )
        else:
            self.coding_var.set("Sin workspace de código.")

        proactive_settings = state["service"]["proactive"]
        pulse_status = "activo" if proactive_settings["enabled"] else "desactivado"
        pulse_model = str(proactive_settings.get("model", "")).strip()
        pulse_model_text = f", modelo {pulse_model}" if pulse_model else ", modelo principal"
        pulse_cycles_text = format_cycle_count(proactive_settings.get("cycles"))
        pulse_text = (
            f"Pulso {pulse_status}: {pulse_cycles_text} cada "
            f"{proactive_settings['interval_seconds']}s{pulse_model_text}."
        )
        local_context_settings = state.get("local_context", {})
        local_context_status = "activo" if local_context_enabled(local_context_settings) else "desactivado"
        health = self._cached_health_status
        readiness = self._cached_readiness_status
        helper_status = self._cached_context_helper_status
        service_status = self._cached_service_status()

        if health is None:
            self.health_var.set("Consultando...")
        else:
            self.health_var.set(format_health_status(health))
        if readiness is None:
            self.readiness_var.set("Consultando...")
        else:
            self.readiness_var.set(format_readiness_status(readiness))

        conversation_view = conversation_ux.build_conversation_view(
            state,
            service_status=service_status or {},
            voice_status=voice_conversation.desktop_status(),
            channel="desktop",
            limit=5,
        )
        conversation_vars = (
            ("conversation_headline_var", conversation_view.get("headline", "Yarbis está listo.")),
            ("conversation_detail_var", conversation_view.get("detail", "")),
            ("conversation_next_step_var", conversation_view.get("next_step", "")),
        )
        for variable_name, value in conversation_vars:
            variable = self.__dict__.get(variable_name)
            if hasattr(variable, "set"):
                variable.set(value)

        helper_text = (
            format_context_helper_status(helper_status)
            if helper_status is not None
            else "contexto consultando"
        )
        local_context_text = (
            f"Contexto local {local_context_status} "
            f"(modo={local_context_settings.get('mode', 'safe')}, {helper_text})."
        )
        mobile_status = public_mobile_ui_status(state.get("service", {}).get("mobile_ui", {}))
        if mobile_status["enabled"]:
            mobile_url = (
                mobile_status.get("secure_url")
                or mobile_status.get("tailscale_url")
                or mobile_status.get("local_url")
            )
            mobile_timeout = mobile_status.get("job_timeout_seconds")
            mobile_text = f"UI móvil activa ({mobile_url}, timeout={mobile_timeout}s)."
        else:
            mobile_text = "UI móvil desactivada."
        if service_status is None:
            self.service_var.set(f"Consultando servicio SCM. {pulse_text} {local_context_text} {mobile_text}")
            self.service_button_text.set("Consultando...")
            self.service_autostart_var.set(False)
            self.service_autostart_text.set("Iniciar con Windows: consultando")
            self._set_service_controls_state(None)
        elif not service_status["installed"]:
            self.service_var.set(f"No instalado en SCM. {pulse_text} {local_context_text} {mobile_text}")
            self.service_button_text.set("Instalar e iniciar")
            self.service_autostart_var.set(False)
            self.service_autostart_text.set("Iniciar con Windows: NO")
            self._set_service_controls_state(service_status)
        elif service_status["running"]:
            account_text = (
                f", cuenta={service_status['account_name']}"
                if service_status.get("account_name")
                else ""
            )
            self.service_var.set(
                f"Activo en SCM (PID {service_status['pid']}, "
                f"arranque={service_status['start_type']}{account_text}). "
                f"{pulse_text} {local_context_text} {mobile_text}"
            )
            self.service_button_text.set("Detener servicio")
            autostart_enabled = bool(service_status["autostart_enabled"])
            self.service_autostart_var.set(autostart_enabled)
            self.service_autostart_text.set(
                "Iniciar con Windows: SI" if autostart_enabled else "Iniciar con Windows: NO"
            )
            self._set_service_controls_state(service_status)
        else:
            account_text = (
                f", cuenta={service_status['account_name']}"
                if service_status.get("account_name")
                else ""
            )
            self.service_var.set(
                f"Instalado en SCM, detenido "
                f"(arranque={service_status['start_type']}{account_text}). "
                f"{pulse_text} {local_context_text} {mobile_text}"
            )
            self.service_button_text.set("Iniciar servicio")
            autostart_enabled = bool(service_status["autostart_enabled"])
            self.service_autostart_var.set(autostart_enabled)
            self.service_autostart_text.set(
                "Iniciar con Windows: SI" if autostart_enabled else "Iniciar con Windows: NO"
            )
            self._set_service_controls_state(service_status)

        self._sync_runtime_thinking(state)

        pending_question = state["awaiting_user_input"].get("question", "").strip()
        self._view_has_pending_question = has_pending_user_question(state)
        if self._view_has_pending_question:
            self.pending_var.set(pending_question)
            if not self._busy:
                self.status_var.set("Esperando respuesta del usuario.")
            self.send_button.configure(text="Responder y continuar")
        else:
            self.pending_var.set("Sin preguntas pendientes.")
            if not self._busy:
                self.status_var.set("Listo.")
            self.send_button.configure(text="Enviar y ejecutar")

        self._last_state_signature = (
            state.get("goal", ""),
            state.get("cycle_count", 0),
            self._view_has_pending_question,
            state.get("runtime", {}).get("thinking", {}).get("active", False),
            len(state.get("tasks", [])) if isinstance(state.get("tasks"), list) else 0,
            len(state.get("notes", [])) if isinstance(state.get("notes"), list) else 0,
        )
        summary_text = render_state_summary(state, include_last_result=False)
        if summary_text != self._last_summary_text:
            self._set_text(self.summary_text, summary_text)
            self._last_summary_text = summary_text
        self._refresh_last_result_widgets(state)
        self._refresh_activity_view()
        self._refresh_instance_panels()

    def _maybe_show_first_run(self):
        if self._first_run_checked:
            return

        state = load_state()
        if str(state.get("goal", "")).strip():
            self._first_run_checked = True
            return
        if self._busy:
            self.after(1000, self._maybe_show_first_run)
            return

        self._first_run_checked = True
        dialog = FirstRunDialog(self, initial_state=state)
        if dialog.result is None:
            self.status_var.set("Define un objetivo para empezar.")
            return

        self._apply_first_run_setup(dialog.result)

    def _apply_first_run_setup(self, settings: dict):
        activity_lines = []
        try:
            activity_lines.append(update_goal(settings["goal"]))
            provider = str(settings.get("provider", MODEL_PROVIDER_OLLAMA)).strip().lower()
            if provider == MODEL_PROVIDER_OPENROUTER:
                activity_lines.append(update_openrouter_settings(
                    settings["model"],
                    settings["timeout_seconds"],
                    host=settings.get("host", ""),
                    fallback_models=settings.get("fallback_models", ""),
                    api_key=settings.get("api_key", ""),
                    api_key_env_var=settings.get("api_key_env_var", ""),
                ))
            else:
                activity_lines.append(update_ollama_settings(
                    settings["model"],
                    settings["timeout_seconds"],
                    host=settings.get("host", ""),
                    fallback_models=settings.get("fallback_models", ""),
                    api_key=settings.get("api_key", ""),
                    api_key_env_var=settings.get("api_key_env_var", ""),
                ))
            if settings.get("name") or settings.get("role"):
                activity_lines.append(update_profile_text(
                    name=settings.get("name", ""),
                    role=settings.get("role", ""),
                ))
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            self.refresh_state_view()
            return

        self._append_activity("Primer uso", "\n\n".join(line for line in activity_lines if line))
        self.refresh_state_view()

        if settings.get("open_notifications"):
            self._edit_notifications()

        if settings.get("install_service"):
            self._toggle_service()
            return

        if settings.get("run_first_cycle"):
            self._start_background_job("Primer ciclo", _session_operation_subprocess, "run_cycle")

    def _sync_state_view(self):
        self.refresh_state_view(force_heavy=False, force_state_reload=False)
        self.after(_STATE_SYNC_INTERVAL_MS, self._sync_state_view)

    @staticmethod
    def _initial_runtime_events_position() -> int:
        try:
            return activity.EVENTS_FILE.stat().st_size
        except OSError:
            return 0

    def _sync_runtime_events(self):
        if self._closing:
            return

        self._poll_runtime_events()
        self.after(_EVENT_SYNC_INTERVAL_MS, self._sync_runtime_events)

    def _poll_runtime_events(self):
        if self._local_telegram_polling:
            self._runtime_events_position = self._initial_runtime_events_position()
            return

        event_path = activity.EVENTS_FILE
        try:
            event_size = event_path.stat().st_size
        except OSError:
            self._runtime_events_position = 0
            return

        if event_size < self._runtime_events_position:
            self._runtime_events_position = 0
        if event_size == self._runtime_events_position:
            return

        try:
            with open(event_path, "r", encoding="utf-8") as events_file:
                events_file.seek(self._runtime_events_position)
                lines = events_file.readlines()
                self._runtime_events_position = events_file.tell()
        except OSError:
            return

        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type", "")).strip().lower()
            if event_type in {"remote_job_started", "remote_job_finished", "remote_job_failed"}:
                self._handle_telegram_event(event)
                continue

            label = str(event.get("label", "")).strip() or "Operación"
            if label.lower() not in _SERVICE_RUNTIME_EVENT_LABELS:
                continue

            if event_type == "operation_started":
                status_text = str(event.get("status_text", "")).strip() or self._thinking_status_text(label)
                self._result_queue.put(("runtime_start", label, status_text))
            elif event_type == "operation_finished":
                self._result_queue.put(("runtime_success", label, ""))
            elif event_type == "operation_failed":
                error_text = str(event.get("error", "")).strip() or "Error desconocido."
                self._result_queue.put(("runtime_error", label, error_text))

    def _ensure_telegram_polling_matches_service(self, service_running: bool | None = None):
        if service_running is None:
            service_status = self._cached_service_status()
            if service_status is None:
                self._request_status_refresh(force=True)
                return
            service_running = bool(service_status.get("running"))

        if service_running:
            if self._local_telegram_polling:
                stop_telegram_polling()
                self._local_telegram_polling = False
            return

        if not self._local_telegram_polling:
            start_telegram_polling(event_callback=self._handle_telegram_event)
            self._local_telegram_polling = True

    def _process_yarbis_messages(self):
        if self._closing:
            return

        try:
            service_status = self._cached_service_status()
            service_running = bool(service_status.get("running")) if isinstance(service_status, dict) else False
            if not service_running and not self._yarbis_message_worker_running:
                self._yarbis_message_worker_running = True

                def worker():
                    try:
                        processed = yarbis_bus.process_pending_messages(limit=1)
                        self._result_queue.put(("yarbis_messages_done", "Mensajes Yarbis", processed))
                    except Exception as exc:
                        self._result_queue.put(("yarbis_messages_error", "Mensajes Yarbis", str(exc)))

                threading.Thread(
                    target=worker,
                    name="yarbis-desktop-message-processor",
                    daemon=True,
                ).start()
        finally:
            self.after(_YARBIS_MESSAGE_SYNC_MS, self._process_yarbis_messages)

    def _sync_context_helper_once(self, ensure_task: bool = False):
        try:
            local_context_settings = self._load_state_for_view(force_reload=False).get("local_context", {})
        except Exception:
            local_context_settings = {}
        if not local_context_enabled(local_context_settings):
            return

        service_status = self._cached_service_status()
        if service_status is not None and not service_status.get("running"):
            return

        self._request_status_refresh(
            force=False,
            ensure_context_helper=True,
            ensure_context_task=ensure_task,
        )

    def _ensure_context_helper(self):
        if self._closing:
            return

        self._sync_context_helper_once()
        self.after(_CONTEXT_HELPER_SYNC_MS, self._ensure_context_helper)

    def _set_busy(self, busy: bool, status_text: str = "", source: str = "local"):
        if busy:
            self._busy_sources.add(source)
        else:
            self._busy_sources.discard(source)

        self._busy = bool(self._busy_sources)
        state = "disabled" if self._busy else "normal"
        for button in self._action_buttons:
            button.configure(state=state)
        self._set_service_controls_state(self._cached_service_status())
        self._update_instance_action_states()
        self.reply_text.configure(state=state)
        if "message_text" in self.__dict__:
            self.message_text.configure(state=state)
        if status_text:
            self.status_var.set(status_text)
        elif not self._busy:
            self.status_var.set("Listo.")

    def _start_background_job(self, label: str, func, *args, **kwargs):
        state = self._load_state_for_view(force_reload=False)
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una acción en curso. Espera a que termine.")
            return

        self._set_busy(True, self._thinking_status_text(label))
        self._append_activity(f"{label} iniciado", "Operación en curso.")

        def worker():
            try:
                result = func(*args, **kwargs)
                self._result_queue.put(("success", label, result))
            except Exception as exc:  # pragma: no cover - respaldo visual
                self._result_queue.put(("error", label, str(exc)))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _poll_worker_queue(self):
        try:
            while True:
                kind, label, payload = self._result_queue.get_nowait()
                if kind == "status_snapshot":
                    try:
                        self._apply_status_snapshot(payload)
                    finally:
                        self._finish_status_refresh()
                elif kind == "status_error":
                    self.health_var.set("No pude consultar estado.")
                    self.readiness_var.set("No pude consultar preparacion.")
                    if not self._busy:
                        self.status_var.set(f"No pude refrescar estado: {payload}")
                    self._finish_status_refresh()
                elif kind == "success":
                    self._set_busy(False, source="local")
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                elif kind == "remote_start":
                    self._set_busy(True, str(payload), source="remote")
                    self._append_activity(f"{label} iniciado", str(payload))
                    self.refresh_state_view(force_heavy=False)
                elif kind == "runtime_start":
                    self._set_busy(True, str(payload), source="runtime_event")
                    self._append_activity(f"{label} iniciado", str(payload))
                elif kind == "runtime_success":
                    self._set_busy(False, source="runtime_event")
                    self.refresh_state_view(force_heavy=False)
                elif kind == "runtime_error":
                    self._set_busy(False, source="runtime_event")
                    self._append_activity(f"{label} (error)", str(payload))
                    self.refresh_state_view(force_heavy=False)
                elif kind == "remote_success":
                    self._set_busy(False, source="remote")
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                elif kind == "remote_error":
                    self._set_busy(False, source="remote")
                    self._append_activity(f"{label} (error)", str(payload))
                    self.refresh_state_view()
                    self.status_var.set("La acción terminó con error.")
                elif kind == "voice_transcript":
                    self._finish_voice_recording_ui()
                    transcript = str(payload).strip()
                    if transcript:
                        if self.reply_text.get("1.0", "end-1c").strip():
                            self.reply_text.insert("end", "\n" + transcript)
                        else:
                            self.reply_text.insert("1.0", transcript)
                        self.reply_text.focus_set()
                    self._append_activity(label, transcript or "Voz sin texto detectado.")
                    self.status_var.set("Dictado listo.")
                elif kind == "voice_error":
                    self._finish_voice_recording_ui()
                    self._append_activity(f"{label} (error)", str(payload))
                    self.status_var.set("No pude transcribir la voz.")
                elif kind == "live_voice_status":
                    detail = str(payload.get("detail", "") if isinstance(payload, dict) else payload).strip()
                    state_text = str(payload.get("state", "") if isinstance(payload, dict) else "").strip()
                    self.status_var.set(detail or "Voz en vivo activa.")
                    if state_text in {"thinking", "speaking", "error"}:
                        self._append_activity(label, detail or state_text)
                    self.refresh_state_view(force_heavy=False)
                elif kind == "live_voice_done":
                    self._live_voice_stop_event = None
                    self._append_activity(label, "Conversacion en vivo detenida.")
                    self.status_var.set("Voz en vivo detenida.")
                    self.refresh_state_view()
                elif kind == "live_voice_error":
                    self._live_voice_stop_event = None
                    self._append_activity(f"{label} (error)", str(payload))
                    self.status_var.set("No pude mantener la voz en vivo.")
                elif kind == "memory_import_ready":
                    self._set_busy(False, source="local")
                    source_path, summary = payload
                    self._continue_memory_import(str(source_path), str(summary))
                elif kind == "yarbis_messages_done":
                    self._yarbis_message_worker_running = False
                    if payload:
                        self._append_activity(label, f"Mensajes directos procesados: {payload}.")
                        self.refresh_state_view()
                elif kind == "yarbis_messages_error":
                    self._yarbis_message_worker_running = False
                    self._append_activity(f"{label} (error)", str(payload))
                elif kind == "event":
                    self._append_activity(label, str(payload))
                    self.refresh_state_view()
                else:
                    self._set_busy(False, source="local")
                    self._append_activity(f"{label} (error)", str(payload))
                    self.status_var.set("La acción terminó con error.")
        except queue.Empty:
            pass

        self.after(150, self._poll_worker_queue)

    def _run_cycle(self):
        if self._show_pending_user_question():
            return
        self._start_background_job("Ciclo", _session_operation_subprocess, "run_cycle")

    def _run_auto(self):
        if self._show_pending_user_question():
            return
        default_cycles = load_state()["autonomy"]["auto_cycles_default"]
        cycles_text = simpledialog.askstring(
            "Modo autónomo",
            "¿Cuántos ciclos quieres ejecutar? (vacío = hasta terminar)",
            initialvalue="" if default_cycles is None else str(default_cycles),
            parent=self,
        )
        if cycles_text is None:
            return
        cycles = normalize_cycle_count(cycles_text, default=0)
        if cycles == 0:
            messagebox.showerror(
                "Modo autónomo",
                "Indica un número positivo o deja el campo vacío para ejecutar hasta terminar.",
                parent=self,
            )
            return
        self._start_background_job("Modo autónomo", _session_operation_subprocess, "run_auto", cycles=cycles)

    def _stop_current_operation(self):
        result = request_stop_current_operation(source="desktop")
        self._append_activity("Detener", result)
        self.status_var.set(result)
        self.refresh_state_view()

    def _finish_voice_recording_ui(self):
        self._voice_recording = False
        self._voice_record_stop_event = None
        self._voice_record_thread = None
        if hasattr(self, "voice_button"):
            self.voice_button.configure(text="Dictar")

    def _toggle_voice_recording(self):
        if self._voice_recording:
            if self._voice_record_stop_event is not None:
                self._voice_record_stop_event.set()
            self.status_var.set("Transcribiendo voz...")
            if hasattr(self, "voice_button"):
                self.voice_button.configure(text="Dictar")
            return

        stop_event = threading.Event()
        self._voice_record_stop_event = stop_event
        self._voice_recording = True
        self.voice_button.configure(text="Detener dictado")
        self.status_var.set("Grabando voz...")

        def worker():
            audio_path = None
            try:
                audio_path = yarbis_voice.record_microphone_to_file(stop_event, settings=load_state())
                transcript = yarbis_voice.transcribe_audio_file(audio_path, settings=load_state())
                self._result_queue.put(("voice_transcript", "Dictado", transcript))
            except Exception as exc:
                self._result_queue.put(("voice_error", "Dictado", str(exc)))
            finally:
                yarbis_voice.cleanup_voice_file(audio_path)

        thread = threading.Thread(target=worker, daemon=True)
        self._voice_record_thread = thread
        thread.start()

    def _toggle_live_voice(self):
        if self._live_voice_thread and self._live_voice_thread.is_alive():
            if self._live_voice_stop_event is not None:
                self._live_voice_stop_event.set()
            self.status_var.set("Deteniendo voz en vivo...")
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        stop_event = threading.Event()
        self._live_voice_stop_event = stop_event
        self.status_var.set("Voz en vivo: di 'Yarbis' para hablar.")
        self._append_activity("Voz en vivo", "Conversacion en vivo iniciada.")

        def status_callback(snapshot: dict):
            self._result_queue.put(("live_voice_status", "Voz en vivo", snapshot))

        def worker():
            try:
                result = voice_conversation.run_desktop_live_conversation(
                    stop_event,
                    settings=load_state(),
                    status_callback=status_callback,
                )
                self._result_queue.put(("live_voice_done", "Voz en vivo", result))
            except Exception as exc:
                self._result_queue.put(("live_voice_error", "Voz en vivo", str(exc)))

        thread = threading.Thread(target=worker, daemon=True)
        self._live_voice_thread = thread
        thread.start()

    def _speak_last_result(self):
        text = str(load_state().get("last_result", "")).strip()
        if not text:
            messagebox.showinfo("Yarbis", "No hay ultimo resultado para leer.", parent=self)
            return

        def worker():
            try:
                yarbis_voice.speak_text(text, settings=load_state(), cancellable=True)
                self._result_queue.put(("event", "Voz", "Lectura finalizada."))
            except Exception as exc:
                self._result_queue.put(("voice_error", "Voz", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set("Leyendo ultimo resultado...")

    def _stop_speaking(self):
        stopped = yarbis_voice.stop_speaking()
        self._append_activity("Voz", "Lectura detenida." if stopped else "No habia lectura activa.")
        self.status_var.set("Voz detenida." if stopped else "Sin voz activa.")

    def _show_pending_user_question(self) -> bool:
        state = load_state()
        if not has_pending_user_question(state):
            return False

        pending_question = state["awaiting_user_input"].get("question", "").strip()
        self.refresh_state_view()
        self.reply_text.focus_set()
        messagebox.showinfo(
            "Yarbis",
            (
                "Yarbis necesita tu respuesta antes de continuar.\n"
                "Cuando respondas, retomará el modo autónomo.\n\n"
                f"{pending_question}"
            ).strip(),
            parent=self,
        )
        return True

    def _toggle_theme(self):
        next_theme = "light" if self.current_theme_name == "dark" else "dark"
        result = update_ui_theme(next_theme)
        self._apply_theme(next_theme)
        self._append_activity("Tema", result)
        self.status_var.set("Listo.")

    def _open_another_instance(self):
        try:
            _launch_instance_selector()
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir el selector de instancias: {exc}", parent=self)
            return
        self._append_activity("Instancias", "Selector de instancias abierto.")

    def _edit_ollama_settings(self):
        dialog = OllamaSettingsDialog(self, initial_settings=get_model_provider_settings())
        if dialog.result is None:
            return

        try:
            provider = dialog.result.pop("provider", MODEL_PROVIDER_OLLAMA)
            if provider == MODEL_PROVIDER_OPENROUTER:
                result = update_openrouter_settings(**dialog.result)
            elif provider == MODEL_PROVIDER_OPENAI_COMPAT:
                result = update_openai_compat_settings(**dialog.result)
            elif provider == MODEL_PROVIDER_PUTER:
                result = update_puter_settings(**dialog.result)
            else:
                result = update_ollama_settings(**dialog.result)
            provider_result = update_model_provider(provider)
            result = f"{provider_result}\n{result}"
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Modelo", result)
        self.refresh_state_view()

    def _edit_voice_settings(self, prefer_edge: bool = False):
        try:
            voices = yarbis_voice.list_tts_voices(load_state(), include_downloadable=True, language="all")
        except Exception as exc:
            voices = []
            self._append_activity("Voz", f"No pude listar voces del sistema: {exc}")

        dialog = VoiceSettingsDialog(
            self,
            initial_settings=load_state().get("voice", {}),
            voices=voices,
            prefer_edge=prefer_edge,
        )
        if dialog.result is None:
            return

        try:
            result = yarbis_voice.update_voice_settings_text(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Voz", result)
        self.refresh_state_view()

    def _choose_edge_voice(self):
        self._edit_voice_settings(prefer_edge=True)

    def _test_voice(self):
        def worker():
            try:
                yarbis_voice.speak_text("Hola, soy Yarbis probando esta voz local.", settings=load_state(), cancellable=True)
                self._result_queue.put(("event", "Voz", "Prueba de voz finalizada."))
            except Exception as exc:
                self._result_queue.put(("voice_error", "Voz", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set("Probando voz...")

    def _edit_notifications(self):
        dialog = NotificationsDialog(self, initial_settings=get_notification_settings())
        if dialog.result is None:
            return

        try:
            result = update_notification_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc))
            return

        self._append_activity("Notificaciones", result)
        self.refresh_state_view()
        if (
            dialog.result.get("telegram_enabled")
            and dialog.result.get("telegram_bot_token")
            and not dialog.result.get("telegram_chat_id")
        ):
            messagebox.showinfo(
                "Yarbis",
                (
                    "Telegram ya tiene bot token, pero falta vincular el chat.\n\n"
                    "Abre tu bot en Telegram y envía /start. Después usa "
                    "Probar notificación para confirmar el enlace."
                ),
                parent=self,
            )

    def _send_test_notification(self):
        self._start_background_job("Prueba de notificacion", send_test_notification)

    def _connect_social_account(self):
        dialog = SocialOAuthDialog(self)
        if dialog.result is None:
            return

        missing = [
            label
            for label, value in (
                ("Client/App ID", dialog.result.get("client_id")),
                ("Client/App Secret", dialog.result.get("client_secret")),
            )
            if not str(value or "").strip()
        ]
        if missing:
            messagebox.showwarning("Yarbis", "Faltan datos: " + ", ".join(missing), parent=self)
            return

        self._start_background_job(
            "Conectar red social",
            start_social_oauth_text,
            **dialog.result,
        )

    def _show_social_accounts(self):
        self._append_activity("Redes sociales", social_accounts_overview_text())
        self.refresh_state_view()

    def _show_social_publications(self):
        self._append_activity("Publicaciones sociales", list_social_publications_text(status="all", limit=15))
        self.refresh_state_view()

    def _copy_social_confirmation(self):
        publication_id = simpledialog.askstring(
            "Copiar confirmación",
            "Id de publicación pendiente:",
            parent=self,
        )
        if publication_id is None:
            return
        cleaned_id = publication_id.strip().lower()
        if not cleaned_id:
            messagebox.showinfo("Yarbis", "Indica un id de publicación pendiente.", parent=self)
            return

        state = load_state()
        matches = [
            publication
            for publication in state.get("social", {}).get("pending_publications", [])
            if publication.get("id", "").lower() == cleaned_id
            or publication.get("id", "").lower().startswith(cleaned_id)
        ]
        if len(matches) != 1:
            messagebox.showwarning(
                "Yarbis",
                "No encontré una publicación pendiente única con ese id.",
                parent=self,
            )
            return

        phrase = matches[0].get("confirmation_phrase", f"PUBLICAR {matches[0]['id']}")
        self.clipboard_clear()
        self.clipboard_append(phrase)
        self.update()
        self._append_activity("Redes sociales", f"Confirmacion copiada: {phrase}")

    def _open_assisted_social_post(self):
        publication_id = simpledialog.askstring(
            "Abrir asistido",
            "Id de publicación pendiente o draft:",
            parent=self,
        )
        if publication_id is None:
            return
        cleaned_id = publication_id.strip()
        if not cleaned_id:
            messagebox.showinfo("Yarbis", "Indica un id de publicación o draft.", parent=self)
            return
        self._start_background_job(
            "Facebook asistido",
            open_assisted_social_post_text,
            publication_id="" if cleaned_id.lower().startswith("draft") else cleaned_id,
            draft_id=cleaned_id if cleaned_id.lower().startswith("draft") else "",
        )

    @staticmethod
    def _install_and_start_service(start_auto: bool, account_name: str = "", password: str = "") -> str:
        install_result = install_service(
            start_auto=start_auto,
            account_name=account_name,
            password=password,
        )
        start_result = start_service()
        task_result = ensure_context_task()
        helper_result = start_context_helper()
        return f"{install_result}\n{start_result}\n{task_result}\n{helper_result}"

    @staticmethod
    def _start_service_with_context_helper() -> str:
        start_result = start_service()
        task_result = ensure_context_task()
        helper_result = start_context_helper()
        return f"{start_result}\n{task_result}\n{helper_result}"

    @staticmethod
    def _remove_service_with_context_helper() -> str:
        service_result = remove_service()
        helper_result = stop_context_helper()
        task_result = remove_context_task()
        return f"{service_result}\n{helper_result}\n{task_result}"

    def _edit_service_pulse(self):
        dialog = ServicePulseDialog(self, initial_settings=get_service_proactive_settings())
        if dialog.result is None:
            return

        try:
            result = update_service_proactive_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Pulso proactivo", result)
        self.refresh_state_view()

    def _edit_mobile_ui(self):
        settings = get_mobile_ui_settings()
        dialog = ServiceMobileUiDialog(
            self,
            initial_settings=settings,
            status=public_mobile_ui_status(settings),
        )
        if dialog.result is None:
            return

        try:
            result = update_mobile_ui_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        status = public_mobile_ui_status()
        url = (
            status.get("secure_url")
            or status.get("tailscale_url")
            or status.get("local_url")
        )
        if url:
            try:
                self.clipboard_clear()
                self.clipboard_append(url)
                self.update()
                result += f"\nURL copiada al portapapeles: {url}"
            except tk.TclError:
                pass
        self._append_activity("UI móvil", result)
        self.refresh_state_view()

    def _edit_local_context(self):
        dialog = LocalContextDialog(self, initial_settings=get_local_context_settings())
        if dialog.result is None:
            return

        try:
            result = update_local_context_settings(**dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc), parent=self)
            return

        self._append_activity("Contexto local", result)
        self._sync_context_helper_once(ensure_task=True)
        self.refresh_state_view()

    def _toggle_service(self):
        service_status = self._require_cached_service_status()
        if service_status is None:
            return
        if service_status["running"]:
            self._start_background_job("Servicio", stop_service)
            return

        if self._local_telegram_polling:
            stop_telegram_polling()
            self._local_telegram_polling = False

        if not service_status["installed"]:
            dialog = ServiceInstallDialog(
                self,
                initial_autostart=bool(self.service_autostart_var.get()),
            )
            if dialog.result is None:
                self.refresh_state_view()
                return
            account_name = dialog.result.get("account_name", "")
            password = dialog.result.get("password", "")
            if service_account_requires_password(account_name) and not password:
                messagebox.showwarning(
                    "Yarbis",
                    (
                        "La cuenta indicada necesita password para registrarse en SCM.\n\n"
                        "Indica password o deja cuenta y password vacíos para usar LocalSystem."
                    ),
                    parent=self,
                )
                self.refresh_state_view()
                return

            self._start_background_job(
                "Servicio",
                self._install_and_start_service,
                bool(dialog.result.get("start_auto")),
                account_name,
                password,
            )
            return

        self._start_background_job("Servicio", self._start_service_with_context_helper)

    def _toggle_service_autostart(self):
        service_status = self._require_cached_service_status()
        if service_status is None:
            return
        if not service_status["installed"]:
            messagebox.showinfo(
                "Yarbis",
                "Instala el servicio en SCM antes de cambiar su arranque con Windows.",
                parent=self,
            )
            self.refresh_state_view()
            return

        enabled = self.service_autostart_var.get()
        self.service_autostart_text.set(
            "Iniciar con Windows: SI" if enabled else "Iniciar con Windows: NO"
        )
        self._style_service_autostart_toggle()
        self._start_background_job("Servicio", set_autostart_enabled, enabled)

    def _remove_service(self):
        service_status = self._require_cached_service_status()
        if service_status is None:
            return
        if not service_status["installed"]:
            self._append_activity("Servicio", "El servicio de Yarbis no esta instalado en SCM.")
            self.refresh_state_view()
            return

        should_remove = messagebox.askyesno(
            "Quitar servicio",
            (
                "Esto detendra Yarbis si esta activo y quitara el registro del servicio en SCM.\n\n"
                "Quieres continuar?"
            ),
            parent=self,
        )
        if not should_remove:
            return

        self._start_background_job("Quitar servicio", self._remove_service_with_context_helper)

    def _update_yarbis(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una acción en curso. Espera a que termine.", parent=self)
            return

        service_status = self._require_cached_service_status()
        if service_status is None:
            return

        needs_admin = bool(service_status.get("installed"))
        admin_text = (
            "\n\nComo el servicio SCM esta instalado, Windows pedira permisos de administrador."
            if needs_admin
            else ""
        )
        should_update = messagebox.askyesno(
            "Actualizar Yarbis",
            (
                "Voy a abrir una ventana externa de PowerShell para actualizar Yarbis. "
                "Esta ventana se cerrará para que el actualizador pueda cambiar código y dependencias.\n\n"
                "Si hay cambios locales, el actualizador los guardará con git stash y los reaplicará después."
                f"{admin_text}\n\nQuieres continuar?"
            ),
            parent=self,
        )
        if not should_update:
            return

        try:
            _launch_update_process(needs_admin=needs_admin)
        except Exception as exc:
            messagebox.showwarning("Yarbis", f"No pude abrir el actualizador: {exc}", parent=self)
            return

        self.status_var.set("Actualizador iniciado en ventana externa.")
        self._append_activity(
            "Actualizacion",
            "Actualizador externo iniciado. Esta ventana se cerrará y Yarbis se reabrirá si termina bien.",
        )
        self._closing = True
        if self._voice_record_stop_event is not None:
            self._voice_record_stop_event.set()
        if self._live_voice_stop_event is not None:
            self._live_voice_stop_event.set()
        stop_telegram_polling()
        self.destroy()

    def _change_goal(self):
        dialog = MultilineTextDialog(
            self,
            title="Cambiar objetivo",
            label="Nuevo objetivo para Yarbis",
            initial_value=load_state()["goal"],
            height=6,
        )
        if dialog.result is None:
            return

        try:
            result = update_goal(dialog.result)
        except ValueError as exc:
            messagebox.showwarning("Yarbis", str(exc))
            return

        self._append_activity("Objetivo actualizado", result)
        self.refresh_state_view()

    def _manage_idea_projects(self):
        dialog = IdeaProjectsDialog(self, visual_callback=self._open_visual_workspace)
        if dialog.result:
            self._append_activity("Ideas/proyectos", dialog.result)
        self.refresh_state_view()

    def _open_visual_workspace(self):
        status = public_mobile_ui_status(load_state().get("service", {}).get("mobile_ui", {}))
        if not status.get("enabled"):
            messagebox.showinfo(
                "Yarbis",
                "Activa la UI movil en Servicio -> UI movil para abrir la mesa visual web.",
                parent=self,
            )
            return

        active_urls = status.get("active_urls") or []
        base_url = (
            status.get("secure_url")
            or (active_urls[0] if active_urls else "")
            or status.get("tailscale_url")
            or status.get("local_url")
        )
        if not base_url:
            messagebox.showwarning("Yarbis", "No encontre URL disponible para la UI movil.", parent=self)
            return

        url = base_url.rstrip("/") + "/#visual"
        webbrowser.open(url)
        self._append_activity("Visual web", f"Mesa visual abierta: {url}")

    def _choose_coding_workspace(self):
        state = load_state()
        initial_dir = str(state.get("coding", {}).get("workspace_path", "")).strip() or str(_WORKSPACE_ROOT)
        selected_path = filedialog.askdirectory(
            title="Selecciona el repositorio de código",
            initialdir=initial_dir,
            parent=self,
        )
        if not selected_path:
            return

        result = coding_set_workspace_text(selected_path)
        self._append_activity("Workspace de código", result)
        self.refresh_state_view()

    def _manage_coding_proposals(self):
        dialog = CodingProposalsDialog(
            self,
            apply_validate_callback=coding_apply_and_validate_text,
            apply_callback=coding_apply_proposal_text,
            check_callback=coding_check_proposal_text,
            detect_validation_callback=coding_detect_validation_command_text,
            discard_callback=coding_discard_proposal_text,
            detail_callback=coding_get_proposal_text,
            list_callback=coding_list_proposals_text,
            range_callback=coding_read_text_range_text,
            search_callback=coding_search_text_text,
            status_callback=coding_workflow_status_text,
            validation_plan_callback=coding_validation_plan_text,
            validate_callback=coding_run_validation_text,
        )
        if dialog.result:
            self._append_activity("Propuestas de coding", dialog.result)
        self.refresh_state_view()

    def _edit_profile(self):
        dialog = ProfileDialog(self, initial_profile=load_state()["profile"])
        if dialog.result is None:
            return

        result = update_profile_text(**dialog.result)
        self._append_activity("Perfil", result)
        self.refresh_state_view()

    def _save_note(self):
        dialog = NoteDialog(self, "Guardar nota")
        if dialog.result is None:
            return

        result = save_note_text(**dialog.result)
        self._append_activity("Nota", result)
        self.refresh_state_view()

    def _manage_notes(self):
        dialog = NotesDialog(self)
        if dialog.result:
            self._append_activity("Notas", dialog.result)
        self.refresh_state_view()

    def _memory_backups_dir(self) -> Path:
        return Path(__file__).resolve().parent / ".yarbis_memory_backups"

    def _edit_memory_protection(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        state = self._load_state_for_view(force_reload=False)
        status_text = (
            "Estado cargado desde cache. Usa Verificar respaldos para revisar todos "
            "los archivos sin trabar la ventana."
        )
        dialog = MemoryProtectionDialog(
            self,
            initial_settings=state.get("memory_protection", {}),
            status_text=status_text,
        )
        if dialog.result is None:
            return

        self._start_background_job(
            "Proteccion de memoria",
            update_memory_protection_settings_text,
            **dialog.result,
        )

    def _verify_memory_backups(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        self._start_background_job("Verificacion de memoria", verify_memory_backups_text)

    def _backup_memory(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        backups_dir = self._memory_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        target_path = filedialog.asksaveasfilename(
            parent=self,
            title="Guardar respaldo de memoria",
            initialdir=str(backups_dir),
            defaultextension=".json",
            filetypes=(
                ("Respaldos de memoria Yarbis", "*.json"),
                ("Todos los archivos", "*.*"),
            ),
        )
        if not target_path:
            return

        include_secrets = messagebox.askyesno(
            "Secretos en respaldo",
            (
                "Quieres incluir tokens persistidos de ntfy y Telegram?\n\n"
                "Elige No para crear un respaldo portable con secretos redactados."
            ),
            parent=self,
        )
        self._start_background_job(
            "Respaldo de memoria",
            create_memory_backup_text,
            path=target_path,
            include_secrets=include_secrets,
        )

    def _import_memory(self):
        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        backups_dir = self._memory_backups_dir()
        backups_dir.mkdir(parents=True, exist_ok=True)
        source_path = filedialog.askopenfilename(
            parent=self,
            title="Seleccionar respaldo de memoria",
            initialdir=str(backups_dir),
            filetypes=(
                ("Respaldos de memoria Yarbis", "*.json"),
                ("Todos los archivos", "*.*"),
            ),
        )
        if not source_path:
            return

        self._set_busy(True, self._thinking_status_text("Inspeccion de memoria"))
        self._append_activity("Trasplante de memoria iniciado", "Revisando respaldo antes de importar.")

        def worker():
            try:
                summary = inspect_memory_backup_text(source_path)
                self._result_queue.put(("memory_import_ready", "Trasplante de memoria", (source_path, summary)))
            except Exception as exc:
                self._result_queue.put(("error", "Trasplante de memoria", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _continue_memory_import(self, source_path: str, summary: str):
        if not summary.startswith("Respaldo de memoria."):
            messagebox.showwarning("Yarbis", summary, parent=self)
            return

        dialog = MemoryImportModeDialog(self, backup_summary=summary)
        if dialog.result is None:
            return

        mode = dialog.result
        if mode == "replace":
            should_import = messagebox.askyesno(
                "Confirmar trasplante",
                (
                    "Esto reemplazará la memoria actual después de crear un respaldo local previo. "
                    "Quieres continuar?"
                ),
                parent=self,
            )
            if not should_import:
                return

        self._start_background_job(
            "Trasplante de memoria",
            import_memory_backup_text,
            source_path,
            mode=mode,
        )

    def _create_task(self):
        dialog = TaskDialog(self, "Crear tarea")
        if dialog.result is None:
            return

        result = add_task_text(**dialog.result)
        self._append_activity("Tarea", result)
        self.refresh_state_view()

    def _send_reply(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        reply_text = self.reply_text.get("1.0", "end-1c").strip()
        if not reply_text:
            messagebox.showinfo("Yarbis", "Escribe una respuesta o contexto antes de enviarlo.")
            return

        displayed_pending = self._view_has_pending_question
        actual_pending = has_pending_user_question(state)
        if actual_pending != displayed_pending:
            self.refresh_state_view()
            if displayed_pending and not actual_pending:
                messagebox.showinfo(
                    "Yarbis",
                    (
                        "La pregunta pendiente ya se respondio desde otro canal.\n\n"
                        "Revise el estado actualizado. Si aun quieres mandar contexto "
                        "adicional, vuelve a presionar Enviar."
                    ),
                    parent=self,
                )
                return

        self.reply_text.delete("1.0", "end")
        self._start_background_job(
            "Respuesta",
            _session_operation_subprocess,
            "submit_user_reply",
            reply_text=reply_text,
        )

    def _clear_activity(self):
        activity.clear_activity_history()
        self._last_activity_text = ""
        self._set_text(self.activity_text, "")

    def _factory_reset(self):
        state = load_state()
        thinking_active, thinking_label, _started_at = self._runtime_thinking_from_state(state)
        if thinking_active:
            self._sync_runtime_thinking(state)
            self._show_thinking_blocked_message(thinking_label)
            return

        if self._busy:
            messagebox.showinfo("Yarbis", "Ya hay una accion en curso. Espera a que termine.", parent=self)
            return

        should_reset = messagebox.askyesno(
            "Reiniciar de fabrica",
            (
                "Esto borrara objetivo, perfil, notas, tareas, conversacion, Telegram, "
                "configuración local y actividad.\n\n"
                "No borra el código ni desinstala el servicio de Windows.\n\n"
                "Quieres continuar?"
            ),
            parent=self,
        )
        if not should_reset:
            return

        confirmation = simpledialog.askstring(
            "Confirmar reinicio",
            "Escribe REINICIAR para dejar Yarbis de fabrica.",
            parent=self,
        )
        if confirmation != "REINICIAR":
            self.status_var.set("Reinicio de fabrica cancelado.")
            return

        if self._local_telegram_polling:
            stop_telegram_polling()
            self._local_telegram_polling = False

        result = factory_reset_yarbis()
        self._last_activity_text = ""
        self._last_summary_text = ""
        self._last_result_text = ""
        self._view_has_pending_question = False
        self._first_run_checked = False
        self.reply_text.delete("1.0", "end")
        self._set_text(self.activity_text, "")
        for widget in getattr(self, "_last_result_widgets", []):
            self._set_text(widget, "")
        self._apply_theme(get_ui_theme())
        self.refresh_state_view()
        self.status_var.set("Yarbis reiniciado de fabrica.")
        messagebox.showinfo("Yarbis", result, parent=self)
        self.after(250, self._maybe_show_first_run)

    def _handle_telegram_event(self, message):
        if isinstance(message, dict):
            event_type = str(message.get("type", "")).strip().lower()
            label = str(message.get("label", "")).strip() or "Telegram"
            content = str(message.get("content", "")).strip()
            status_text = str(message.get("status_text", "")).strip()

            if event_type == "remote_job_started":
                self._result_queue.put(("remote_start", label, status_text or f"Ejecutando: {label}..."))
                return

            if event_type == "remote_job_finished":
                self._result_queue.put(("remote_success", label, content))
                return

            if event_type == "remote_job_failed":
                self._result_queue.put(("remote_error", label, content or "Error desconocido."))
                return

        rendered = str(message).strip()
        if not rendered:
            return
        if rendered.startswith("Telegram: procesado "):
            return

        self._result_queue.put(("event", "Telegram", rendered))

    def _shutdown_window(self):
        self._closing = True
        if self._voice_record_stop_event is not None:
            self._voice_record_stop_event.set()
        if self._live_voice_stop_event is not None:
            self._live_voice_stop_event.set()
        stop_telegram_polling()
        self.destroy()

    def _on_close(self):
        if self._busy:
            should_close = messagebox.askyesno(
                "Cerrar Yarbis",
                "Hay una acción en curso. ¿Quieres cerrar la ventana de todos modos?",
                parent=self,
            )
            if not should_close:
                return
        self._shutdown_window()


def main():
    if not _select_instance_before_launch():
        return

    if not _acquire_single_instance_lock():
        _show_already_running_message()
        return

    try:
        _write_desktop_pid()
        clear_activity_for_first_run_if_needed()
        startup_message = run_startup_self_analysis(force=False, background=True)
        app = YarbisDesktop()
        app._append_activity("Autoanalisis inicial", startup_message)
        app._append_activity(
            "Interfaz lista",
            (
                "Ya puedes usar Yarbis sin abrir terminal. "
                "Ejecuta un ciclo, deja un objetivo o responde desde esta ventana."
            ),
        )
        app._append_activity("Estado inicial", get_status_text())
        app.mainloop()
    finally:
        try:
            wait_for_memory_protection_maintenance(timeout_seconds=15)
        except Exception:
            pass
        _clear_desktop_pid()
        _release_single_instance_lock()


def _run_main_with_crash_report():
    try:
        main()
    except Exception:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        crash_log = _RUNTIME_DIR / "desktop_crash.log"
        crash_log.write_text(
            (
                f"Yarbis desktop crash at {datetime.now().isoformat(timespec='seconds')}\n\n"
                f"{traceback.format_exc()}"
            ),
            encoding="utf-8",
        )
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Yarbis no pudo abrir",
                f"Guardé el detalle del error en:\n{crash_log}",
                parent=root,
            )
            root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    _run_main_with_crash_report()
