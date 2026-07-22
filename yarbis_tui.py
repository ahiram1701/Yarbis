"""TUI completa de Yarbis (Textual).

Interfaz de terminal rica, multiplataforma y abrible con doble clic (ver
abrir_yarbis_tui.cmd / abrir_yarbis_tui.sh). Reutiliza las MISMAS funciones que
la app de escritorio y la UI movil: no tiene logica de negocio propia, solo
orquesta llamadas a session/tools/memory/service_manager/activity en hilos worker
para no congelar el render.

Uso:
    python yarbis_tui.py [--instance <id>]

La TUI se adjunta a UNA instancia (por defecto la que indique YARBIS_INSTANCE o
`default`); las demas se ven y coordinan por el bus desde la pestana Instancias.
"""

import os
import sys

# IMPORTANTE: fijar la instancia ANTES de importar memory (que calcula STATE_FILE
# al importar). Se hace desde --instance o se respeta YARBIS_INSTANCE existente.
def _preselect_instance() -> None:
    argv = sys.argv[1:]
    for index, arg in enumerate(argv):
        value = ""
        if arg == "--instance" and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith("--instance="):
            value = arg.split("=", 1)[1]
        if value.strip():
            os.environ["YARBIS_INSTANCE"] = value.strip()
            return


_preselect_instance()

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

import activity
import yarbis_instance
from memory import (
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    MODEL_PROVIDER_OPENAI_COMPAT,
    MODEL_PROVIDER_PUTER,
    load_state,
    render_state_summary,
)
from session import (
    run_auto_with_output,
    run_cycle_with_output,
    request_stop_current_operation,
    submit_user_reply,
    update_goal,
    update_ollama_settings,
    update_openrouter_settings,
    update_openai_compat_settings,
    update_puter_settings,
)
from tools import (
    add_task,
    agent_overview,
    answer_instance_for_user,
    list_pending_user_questions,
    list_yarbis_instances,
    save_note,
    send_yarbis_message,
    set_computer_control,
    set_mcp_enabled,
)

try:
    from service_manager import health_status
except Exception:  # pragma: no cover - service_manager puede requerir extras
    health_status = None


_PROVIDER_UPDATERS = {
    MODEL_PROVIDER_OLLAMA: update_ollama_settings,
    MODEL_PROVIDER_OPENROUTER: update_openrouter_settings,
    MODEL_PROVIDER_OPENAI_COMPAT: update_openai_compat_settings,
    MODEL_PROVIDER_PUTER: update_puter_settings,
}


def _render_health() -> str:
    if health_status is None:
        return "(health no disponible)"
    try:
        data = health_status(force_service=False)
    except Exception as exc:
        return f"(no pude leer health: {exc})"
    if not isinstance(data, dict):
        return str(data)
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            inner = ", ".join(f"{k}={v}" for k, v in value.items())
            lines.append(f"{key}: {inner}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


class YarbisTUI(App):
    TITLE = "Yarbis"
    CSS = """
    Screen { layout: vertical; }
    #chat_log, #actividad { height: 1fr; border: round $primary; }
    #estado, #contexto, #ajustes, #instancias { height: 1fr; border: round $primary; padding: 1; }
    #chat_input { dock: bottom; }
    .row { height: auto; }
    .field { width: 1fr; }
    Input { margin: 0 1 0 0; }
    Button { margin: 0 1 0 0; }
    """
    BINDINGS = [
        ("ctrl+r", "run_cycle", "Ciclo"),
        ("ctrl+g", "run_auto", "Auto"),
        ("ctrl+s", "stop", "Detener"),
        ("f5", "refresh", "Refrescar"),
        ("ctrl+q", "quit", "Salir"),
    ]

    def __init__(self):
        super().__init__()
        self.instance_id = yarbis_instance.current_instance_id()
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-chat"):
            with TabPane("Chat", id="tab-chat"):
                yield RichLog(id="chat_log", wrap=True, markup=True, highlight=False)
                yield Input(placeholder="Escribe tu mensaje y Enter…  (Ctrl+R ciclo, Ctrl+G auto, Ctrl+S detener)", id="chat_input")
            with TabPane("Estado", id="tab-estado"):
                with VerticalScroll():
                    yield Static("Cargando…", id="estado")
            with TabPane("Instancias", id="tab-instancias"):
                with VerticalScroll():
                    yield Static("Cargando…", id="instancias")
                with Horizontal(classes="row"):
                    yield Input(placeholder="instancia destino", id="inst_target", classes="field")
                    yield Input(placeholder="respuesta / mensaje", id="inst_msg", classes="field")
                with Horizontal(classes="row"):
                    yield Button("Desbloquear (responder por mí)", id="btn_unblock", variant="primary")
                    yield Button("Enviar mensaje", id="btn_send_msg")
                    yield Button("Refrescar", id="btn_refresh_inst")
            with TabPane("Contexto", id="tab-contexto"):
                with VerticalScroll():
                    yield Static("Cargando…", id="contexto")
                with Horizontal(classes="row"):
                    yield Input(placeholder="nuevo objetivo", id="ctx_goal", classes="field")
                    yield Button("Cambiar objetivo", id="btn_goal")
                with Horizontal(classes="row"):
                    yield Input(placeholder="título de tarea", id="ctx_task", classes="field")
                    yield Button("Crear tarea", id="btn_task")
                with Horizontal(classes="row"):
                    yield Input(placeholder="título de nota", id="ctx_note_title", classes="field")
                    yield Input(placeholder="contenido", id="ctx_note_body", classes="field")
                    yield Button("Guardar nota", id="btn_note")
            with TabPane("Ajustes", id="tab-ajustes"):
                with VerticalScroll():
                    yield Static("Cargando…", id="ajustes")
                with Horizontal(classes="row"):
                    yield Select(
                        [(p, p) for p in _PROVIDER_UPDATERS],
                        prompt="proveedor",
                        id="set_provider",
                        classes="field",
                    )
                    yield Input(placeholder="modelo", id="set_model", classes="field")
                with Horizontal(classes="row"):
                    yield Input(placeholder="host / base_url / worker_url", id="set_host", classes="field")
                    yield Input(placeholder="api key / secreto", id="set_key", classes="field", password=True)
                    yield Button("Guardar proveedor", id="btn_provider", variant="primary")
                with Horizontal(classes="row"):
                    yield Button("Control PC on", id="btn_cc_on")
                    yield Button("Control PC off", id="btn_cc_off")
                    yield Button("MCP on", id="btn_mcp_on")
                    yield Button("MCP off", id="btn_mcp_off")
            with TabPane("Actividad", id="tab-actividad"):
                yield RichLog(id="actividad", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"instancia: {self.instance_id}"
        self._chat = self.query_one("#chat_log", RichLog)
        self._chat.write("[b]Yarbis TUI[/b] — conectado a la instancia "
                          f"[b]{self.instance_id}[/b]. Escribe abajo para hablar con Yarbis.")
        self.action_refresh()
        self.set_interval(6.0, self._refresh_light)

    # ---------- refresco ----------
    def _refresh_light(self) -> None:
        # Refresco periodico barato: estado + actividad (no toca el LLM).
        self._refresh_estado()
        self._refresh_actividad()

    def action_refresh(self) -> None:
        self._refresh_estado()
        self._refresh_instancias()
        self._refresh_contexto()
        self._refresh_ajustes()
        self._refresh_actividad()

    def _safe(self, fn, *args) -> str:
        try:
            return str(fn(*args))
        except Exception as exc:
            return f"(error: {exc})"

    def _refresh_estado(self) -> None:
        try:
            summary = render_state_summary(load_state(), include_last_result=False)
        except Exception as exc:
            summary = f"(no pude leer el estado: {exc})"
        text = f"{summary}\n\n--- Health ---\n{_render_health()}"
        self.query_one("#estado", Static).update(text)

    def _refresh_instancias(self) -> None:
        text = self._safe(list_yarbis_instances) + "\n\n--- Esperando tu respuesta ---\n" + self._safe(list_pending_user_questions)
        self.query_one("#instancias", Static).update(text)

    def _refresh_contexto(self) -> None:
        self.query_one("#contexto", Static).update(self._safe(agent_overview))

    def _refresh_ajustes(self) -> None:
        try:
            mp = load_state().get("model_provider", {})
            active = mp.get("default", "?")
            block = mp.get(active, {}) if isinstance(mp, dict) else {}
            text = (
                f"Proveedor activo: {active}\n"
                f"Modelo: {block.get('model', '') or '-'}\n"
                f"Host: {block.get('host', '') or '-'}\n"
                f"Fallbacks: {', '.join(block.get('fallback_models', []) or []) or '-'}\n\n"
                "Proveedores disponibles: ollama, openrouter, openai_compat, puter.\n"
                "Elige uno abajo, pon modelo (y host/key si aplica) y Guardar."
            )
        except Exception as exc:
            text = f"(error: {exc})"
        self.query_one("#ajustes", Static).update(text)

    def _refresh_actividad(self) -> None:
        log = self.query_one("#actividad", RichLog)
        log.clear()
        try:
            for event in activity.read_recent_events(limit=60):
                ts = str(event.get("timestamp", ""))[:19].replace("T", " ")
                title = str(event.get("title", ""))
                content = str(event.get("content", "")).replace("\n", " ")[:200]
                log.write(f"{ts}  {title}: {content}")
        except Exception as exc:
            log.write(f"(no pude leer la actividad: {exc})")

    # ---------- chat / ciclos ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat_input":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if self.busy:
            self._chat.write("[yellow]Ocupado: espera a que termine la operación en curso.[/yellow]")
            return
        self._chat.write(f"[b cyan]Tú:[/b cyan] {text}")
        self._run_reply(text)

    @work(thread=True, exclusive=True, group="op")
    def _run_reply(self, text: str) -> None:
        self.call_from_thread(self._set_busy, True, "respondiendo")
        try:
            out = submit_user_reply(text, emit_notifications=False)
        except Exception as exc:
            out = f"(error: {exc})"
        self.call_from_thread(self._chat.write, f"[b green]Yarbis:[/b green] {out}")
        self.call_from_thread(self._set_busy, False)
        self.call_from_thread(self._refresh_estado)

    @work(thread=True, exclusive=True, group="op")
    def _run_op(self, label: str, fn) -> None:
        self.call_from_thread(self._set_busy, True, label)
        try:
            out = str(fn())
        except Exception as exc:
            out = f"(error: {exc})"
        self.call_from_thread(self._chat.write, f"[b green]Yarbis ({label}):[/b green] {out}")
        self.call_from_thread(self._set_busy, False)
        self.call_from_thread(self._refresh_estado)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.busy = busy
        self.sub_title = f"instancia: {self.instance_id}" + (f" — {label}…" if busy else "")

    def action_run_cycle(self) -> None:
        if self.busy:
            return
        self._run_op("ciclo", lambda: run_cycle_with_output(emit_notifications=False, mirror_telegram=False))

    def action_run_auto(self) -> None:
        if self.busy:
            return
        self._run_op("auto", lambda: run_auto_with_output(emit_notifications=False, mirror_telegram=False))

    def action_stop(self) -> None:
        self._chat.write("[yellow]" + self._safe(lambda: request_stop_current_operation("tui")) + "[/yellow]")

    # ---------- botones ----------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        def val(widget_id: str) -> str:
            return self.query_one(f"#{widget_id}", Input).value.strip()

        if bid == "btn_refresh_inst":
            self._refresh_instancias()
        elif bid == "btn_unblock":
            target, answer = val("inst_target"), val("inst_msg")
            if target and answer:
                self._chat.write(self._safe(answer_instance_for_user, target, answer))
                self._refresh_instancias()
        elif bid == "btn_send_msg":
            target, msg = val("inst_target"), val("inst_msg")
            if target and msg:
                self._chat.write(self._safe(send_yarbis_message, target, msg))
        elif bid == "btn_goal":
            goal = val("ctx_goal")
            if goal:
                self._chat.write(self._safe(update_goal, goal))
                self._refresh_contexto()
        elif bid == "btn_task":
            title = val("ctx_task")
            if title:
                self._chat.write(self._safe(add_task, title))
                self._refresh_contexto()
        elif bid == "btn_note":
            title, body = val("ctx_note_title"), val("ctx_note_body")
            if title and body:
                self._chat.write(self._safe(save_note, title, body))
        elif bid == "btn_provider":
            self._save_provider()
        elif bid == "btn_cc_on":
            self._chat.write(self._safe(set_computer_control, True))
        elif bid == "btn_cc_off":
            self._chat.write(self._safe(set_computer_control, False))
        elif bid == "btn_mcp_on":
            self._chat.write(self._safe(set_mcp_enabled, True))
        elif bid == "btn_mcp_off":
            self._chat.write(self._safe(set_mcp_enabled, False))

    def _save_provider(self) -> None:
        provider = self.query_one("#set_provider", Select).value
        model = self.query_one("#set_model", Input).value.strip()
        host = self.query_one("#set_host", Input).value.strip()
        key = self.query_one("#set_key", Input).value.strip()
        if provider in (Select.BLANK, None):
            self._chat.write("[yellow]Elige un proveedor.[/yellow]")
            return
        updater = _PROVIDER_UPDATERS.get(provider)
        if updater is None:
            self._chat.write("[yellow]Proveedor invalido.[/yellow]")
            return
        try:
            current = load_state().get("model_provider", {}).get(provider, {})
            timeout = current.get("timeout_seconds", 900) if isinstance(current, dict) else 900
            out = updater(
                model=model,
                timeout_seconds=timeout,
                host=host or None,
                api_key=key or None,
            )
        except Exception as exc:
            out = f"(error: {exc})"
        self._chat.write(f"[b green]Proveedor:[/b green] {out}")
        self._refresh_ajustes()
        self._refresh_estado()


def main() -> None:
    YarbisTUI().run()


if __name__ == "__main__":
    main()
