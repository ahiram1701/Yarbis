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
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
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
import instance_binding
import yarbis_bus
import yarbis_instance
from atomic_io import read_json_bom_safe
from memory import (
    MODEL_PROVIDER_OLLAMA,
    MODEL_PROVIDER_OPENROUTER,
    MODEL_PROVIDER_OPENAI_COMPAT,
    MODEL_PROVIDER_PUTER,
    load_state,
    render_state_summary,
)
from session import (
    get_service_proactive_settings,
    run_auto_with_output,
    run_cycle_with_output,
    request_stop_current_operation,
    submit_user_reply,
    update_goal,
    update_ollama_settings,
    update_openrouter_settings,
    update_openai_compat_settings,
    update_puter_settings,
    update_service_proactive_settings,
)
from tools import (
    add_task,
    agent_overview,
    answer_instance_for_user,
    background_service_status,
    install_background_service,
    remove_background_service,
    save_note,
    send_yarbis_message,
    set_background_service_autostart,
    set_computer_control,
    set_mcp_enabled,
    start_background_service,
    stop_background_service,
)

try:
    from service_manager import health_status
except Exception:  # pragma: no cover - service_manager puede requerir extras
    health_status = None


def _instance_is_running(instance_id: str) -> bool:
    """Si la instancia esta viva, combinando dos senales.

    `yarbis_bus.instance_is_active` mira los PID files, pero en Windows el
    servicio corre como LocalSystem y un proceso de usuario NO puede abrir ese
    proceso: siempre daria "detenida". Por eso se consulta tambien al gestor de
    servicios (leer el estado no requiere elevacion), que es lo que ya hace la UI
    movil. La senal del bus sigue valiendo para procesos propios (app/TUI) y para
    Linux/macOS, donde los PID si son visibles.
    """
    try:
        if yarbis_bus.instance_is_active(instance_id):
            return True
    except Exception:
        pass
    try:
        import service_manager

        return bool(service_manager.get_service_status(instance_id=instance_id)["running"])
    except Exception:
        return False


def _instance_rows() -> list[dict]:
    """Resumen de TODAS las instancias, leido sin cambiarse a ninguna.

    Lee cada `state.json` de forma tolerante a BOM (mismo patron que
    `list_pending_user_questions`), asi el panel puede mostrar objetivo y
    pregunta pendiente de instancias que ni siquiera estan corriendo.
    """
    rows = []
    for item in yarbis_instance.list_instances():
        instance_id = str(item.get("id", "")).strip()
        if not instance_id:
            continue
        goal, waiting, last = "", False, ""
        try:
            state = read_json_bom_safe(yarbis_instance.state_file(instance_id))
            if isinstance(state, dict):
                goal = str(state.get("goal", "")).strip()
                last = str(state.get("last_result", "")).strip()
                pending = state.get("awaiting_user_input", {})
                waiting = bool(isinstance(pending, dict) and pending.get("pending"))
        except Exception:
            pass
        active = _instance_is_running(instance_id)
        rows.append({
            "id": instance_id,
            "active": active,
            "waiting": waiting,
            "goal": goal,
            "last_result": last,
        })
    rows.sort(key=lambda row: row["id"])
    return rows


def _pending_question(state: dict) -> str:
    pending = state.get("awaiting_user_input", {}) if isinstance(state, dict) else {}
    if not isinstance(pending, dict) or not pending.get("pending"):
        return ""
    return str(pending.get("question", "")).strip() or "Yarbis espera tu respuesta."


def _short(text: str, limit: int = 48) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned[: limit - 1] + "…" if len(cleaned) > limit else cleaned


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
    try:
        import device_profile

        lines.append(device_profile.render_profile_summary())
    except Exception:
        pass
    for key, value in data.items():
        if isinstance(value, dict):
            inner = ", ".join(f"{k}={v}" for k, v in value.items())
            lines.append(f"{key}: {inner}")
        else:
            lines.append(f"{key}: {value}")
    # En Linux/macOS el servicio vive en systemd/launchd, no en SCM: agregarlo.
    if os.name != "nt":
        try:
            import native_service

            st = native_service.get_service_status()
            if st["installed"]:
                estado = "activo" if st["running"] else "detenido"
                pid = f" PID {st['pid']}" if st.get("pid") else ""
                auto = "si" if st["autostart_enabled"] else "no"
                lines.append(f"servicio_nativo ({st['manager']}): {estado}{pid}, autostart={auto}")
            else:
                lines.append(f"servicio_nativo ({st['manager']}): no instalado")
        except Exception:
            pass
    return "\n".join(lines)


class YarbisTUI(App):
    TITLE = "Yarbis"
    CSS = """
    Screen { layout: vertical; }
    #chat_log, #actividad { height: 1fr; border: round $primary; }
    #estado, #contexto, #ajustes, #instancias { height: 1fr; border: round $primary; padding: 1; }
    #chat_input { dock: bottom; }
    #pending_banner { dock: top; height: auto; padding: 0 1; background: $warning 20%; }
    #pending_banner.hidden { display: none; }
    #inst_table { height: 1fr; border: round $primary; }
    .subhead { padding: 1 1 0 1; text-style: bold; }
    .row { height: auto; }
    .field { width: 1fr; }
    Input { margin: 0 1 0 0; }
    Button { margin: 0 1 0 0; }
    """
    BINDINGS = [
        ("ctrl+r", "run_cycle", "Ciclo"),
        ("ctrl+g", "run_auto", "Auto"),
        ("ctrl+s", "stop", "Detener"),
        # OJO: en una terminal Ctrl+I ES Tab (Textual: KEY_ALIASES {'tab':
        # ['ctrl+i']}), asi que ese atajo lo consumia la navegacion de foco y
        # nunca disparaba. F2 no colisiona con ningun codigo ASCII de control.
        ("f2", "switch_instance", "Instancia"),
        Binding("ctrl+t", "switch_instance", "Instancia", show=False),
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
                yield Static("", id="pending_banner", classes="hidden")
                yield RichLog(id="chat_log", wrap=True, markup=True, highlight=False)
                yield Input(placeholder="Escribe tu mensaje y Enter…  (Ctrl+R ciclo, Ctrl+G auto, F2 instancia)", id="chat_input")
            with TabPane("Estado", id="tab-estado"):
                with VerticalScroll():
                    yield Static("Cargando…", id="estado")
            with TabPane("Instancias", id="tab-instancias"):
                with Horizontal(classes="row"):
                    yield Select([], prompt="cambiar a instancia…", id="inst_select", classes="field")
                    yield Button("Cambiar a esta", id="btn_switch", variant="primary")
                yield DataTable(id="inst_table", cursor_type="row")
                with Horizontal(classes="row"):
                    yield Input(placeholder="respuesta / mensaje para la fila seleccionada", id="inst_msg", classes="field")
                with Horizontal(classes="row"):
                    yield Button("Responder por mí", id="btn_unblock", variant="primary")
                    yield Button("Enviar mensaje", id="btn_send_msg")
                    yield Button("Difundir a las que esperan", id="btn_broadcast")
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
                    yield Input(placeholder="api key / secreto (vacío = conservar)", id="set_key", classes="field", password=True)
                    yield Button("Guardar proveedor", id="btn_provider", variant="primary")
                with Horizontal(classes="row"):
                    yield Button("Control PC on", id="btn_cc_on")
                    yield Button("Control PC off", id="btn_cc_off")
                    yield Button("MCP on", id="btn_mcp_on")
                    yield Button("MCP off", id="btn_mcp_off")
                yield Static("— Servicio de fondo —", classes="subhead")
                yield Static("Cargando…", id="servicio")
                with Horizontal(classes="row"):
                    yield Button("Instalar", id="btn_svc_install")
                    yield Button("Iniciar", id="btn_svc_start", variant="primary")
                    yield Button("Detener", id="btn_svc_stop")
                    yield Button("Quitar", id="btn_svc_remove", variant="error")
                with Horizontal(classes="row"):
                    yield Button("Arranque automático: sí", id="btn_svc_auto_on")
                    yield Button("Arranque automático: no", id="btn_svc_auto_off")
                yield Static("— Pulso proactivo —", classes="subhead")
                with Horizontal(classes="row"):
                    yield Select(
                        [("activo", "1"), ("desactivado", "0")],
                        prompt="pulso",
                        id="pulse_enabled",
                        classes="field",
                    )
                    yield Input(placeholder="intervalo (seg)", id="pulse_interval", classes="field")
                    yield Input(placeholder="ciclos (vacío = hasta terminar)", id="pulse_cycles", classes="field")
                with Horizontal(classes="row"):
                    yield Input(placeholder="espera inicial (seg)", id="pulse_delay", classes="field")
                    yield Input(placeholder="modelo del pulso (opcional)", id="pulse_model", classes="field")
                    yield Button("Guardar pulso", id="btn_pulse", variant="primary")
            with TabPane("Actividad", id="tab-actividad"):
                yield RichLog(id="actividad", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._chat = self.query_one("#chat_log", RichLog)
        table = self.query_one("#inst_table", DataTable)
        table.add_columns("Instancia", "Estado", "Espera", "Objetivo")
        self._chat.write("[b]Yarbis TUI[/b] — instancia activa: "
                          f"[b]{self.instance_id}[/b]. F2 para cambiar de instancia.")
        self._update_subtitle()
        self.action_refresh()
        self.set_interval(6.0, self._refresh_light)

    # ---------- refresco ----------
    def _widget(self, widget_id: str, widget_type=Static):
        """El widget, o None si aun no esta montado o la app se esta cerrando.

        El refresco periodico (cada 6s) puede dispararse mientras los widgets ya
        se desmontaron: sin esto, `query_one` lanza NoMatches y tumba la TUI.
        """
        try:
            return self.query_one(f"#{widget_id}", widget_type)
        except Exception:
            return None

    def _refresh_light(self) -> None:
        # Refresco periodico barato: estado + actividad (no toca el LLM).
        self._refresh_estado()
        self._refresh_actividad()
        self._refresh_pending_banner()

    def action_refresh(self) -> None:
        self._refresh_estado()
        self._refresh_instancias()
        self._refresh_contexto()
        self._refresh_ajustes()
        self._refresh_servicio()
        self._refresh_pulso()
        self._refresh_actividad()
        self._refresh_pending_banner()

    def _update_subtitle(self, label: str = "") -> None:
        estado = f" — {label}…" if label else ""
        self.sub_title = f"instancia: {self.instance_id}{estado}"

    def _refresh_pending_banner(self) -> None:
        """Si la instancia activa espera respuesta, hacerlo evidente."""
        try:
            question = _pending_question(load_state())
        except Exception:
            question = ""
        banner = self._widget("pending_banner")
        if banner is None:
            return
        if question:
            banner.update(f"[b yellow]Yarbis espera tu respuesta:[/b yellow] {question}")
            banner.remove_class("hidden")
        else:
            banner.update("")
            banner.add_class("hidden")

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
        panel = self._widget("estado")
        if panel is not None:
            panel.update(text)

    def _refresh_instancias(self) -> None:
        try:
            rows = _instance_rows()
        except Exception as exc:
            self._chat.write(f"[red](no pude listar instancias: {exc})[/red]")
            return

        self._rows = rows
        table = self._widget("inst_table", DataTable)
        if table is None:
            return
        previous = table.cursor_row
        table.clear()
        for row in rows:
            marca = " (actual)" if row["id"] == self.instance_id else ""
            table.add_row(
                f"{row['id']}{marca}",
                "activa" if row["active"] else "detenida",
                "sí" if row["waiting"] else "",
                _short(row["goal"]),
                key=row["id"],
            )
        if rows and previous is not None and previous < len(rows):
            table.move_cursor(row=previous)

        selector = self._widget("inst_select", Select)
        if selector is None:
            return
        selector.set_options([(row["id"], row["id"]) for row in rows])

    def _selected_instance(self) -> str:
        """Id de la instancia de la fila seleccionada en la tabla."""
        rows = getattr(self, "_rows", [])
        table = self.query_one("#inst_table", DataTable)
        index = table.cursor_row
        if rows and index is not None and 0 <= index < len(rows):
            return rows[index]["id"]
        return ""

    def _refresh_servicio(self) -> None:
        panel = self._widget("servicio")
        if panel is not None:
            panel.update(self._safe(background_service_status))

    def _refresh_pulso(self) -> None:
        try:
            pulse = get_service_proactive_settings()
        except Exception:
            return
        cycles = pulse.get("cycles")
        campos = {
            "pulse_interval": str(pulse.get("interval_seconds", "") or ""),
            "pulse_cycles": "" if cycles in (None, "") else str(cycles),
            "pulse_delay": str(pulse.get("start_delay_seconds", "") or ""),
            "pulse_model": str(pulse.get("model", "") or ""),
        }
        for widget_id, value in campos.items():
            campo = self._widget(widget_id, Input)
            if campo is not None:
                campo.value = value
        selector = self._widget("pulse_enabled", Select)
        if selector is not None:
            selector.value = "1" if pulse.get("enabled") else "0"

    def _refresh_contexto(self) -> None:
        panel = self._widget("contexto")
        if panel is not None:
            panel.update(self._safe(agent_overview))

    def _refresh_ajustes(self) -> None:
        active = ""
        try:
            mp = load_state().get("model_provider", {})
            active = mp.get("default", "?")
            block = mp.get(active, {}) if isinstance(mp, dict) else {}
            text = (
                f"Proveedor activo: {active}\n"
                f"Modelo: {block.get('model', '') or '-'}\n"
                f"Host: {block.get('host', '') or '-'}\n"
                f"Fallbacks: {', '.join(block.get('fallback_models', []) or []) or '-'}\n"
                f"API key guardada: {'si' if str(block.get('api_key', '')).strip() else 'no'}\n\n"
                "Cambia lo que necesites abajo y pulsa Guardar proveedor."
            )
        except Exception as exc:
            text = f"(error: {exc})"
        panel = self._widget("ajustes")
        if panel is not None:
            panel.update(text)
        # Precargar el formulario con la config ACTUAL: si se deja vacio el
        # modelo, guardar falla ("el modelo no puede quedar vacio"), asi que un
        # formulario en blanco obligaba a reescribirlo todo para tocar una cosa.
        if active and active in _PROVIDER_UPDATERS:
            selector = self._widget("set_provider", Select)
            if selector is not None and selector.value in (None, Select.BLANK):
                selector.value = active
            self._load_provider_fields(active)

    def _load_provider_fields(self, provider: str) -> None:
        """Vuelca modelo y host del proveedor indicado en el formulario."""
        try:
            block = load_state().get("model_provider", {}).get(provider, {})
        except Exception:
            return
        if not isinstance(block, dict):
            return
        campo_model = self._widget("set_model", Input)
        if campo_model is not None:
            campo_model.value = str(block.get("model", "") or "")
        campo_host = self._widget("set_host", Input)
        if campo_host is not None:
            campo_host.value = str(block.get("host", "") or "")

    def _refresh_actividad(self) -> None:
        log = self._widget("actividad", RichLog)
        if log is None:
            return
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
        self._update_subtitle(label if busy else "")
        # Feedback real: la entrada y las acciones se deshabilitan mientras corre.
        for widget_id, widget_type in (("chat_input", Input), ("btn_switch", Button)):
            try:
                self.query_one(f"#{widget_id}", widget_type).disabled = busy
            except Exception:
                pass

    # ---------- instancias ----------
    def action_switch_instance(self) -> None:
        """F2 (o Ctrl+T): ir al selector de instancia."""
        self.query_one(TabbedContent).active = "tab-instancias"
        self._refresh_instancias()
        self.query_one("#inst_select", Select).focus()

    def switch_to_instance(self, instance_id: str) -> bool:
        """Cambia la instancia activa EN CALIENTE (sin reiniciar la TUI)."""
        target = str(instance_id or "").strip()
        if not target or target == self.instance_id:
            return False
        if self.busy:
            self._chat.write(
                "[yellow]No puedo cambiar de instancia con una operación en curso. "
                "Espera o usa Ctrl+S para detenerla.[/yellow]"
            )
            return False
        try:
            self.instance_id = instance_binding.rebind(target)
        except Exception as exc:
            self._chat.write(f"[red]No pude cambiar de instancia: {exc}[/red]")
            return False

        self._chat.write(f"[b]── ahora en: {self.instance_id} ──[/b]")
        self._update_subtitle()
        self.action_refresh()
        return True

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
        elif bid == "btn_switch":
            selected = self.query_one("#inst_select", Select).value
            target = str(selected) if selected not in (None, Select.BLANK) else self._selected_instance()
            self.switch_to_instance(target)
        elif bid == "btn_unblock":
            target, answer = self._selected_instance(), val("inst_msg")
            if not target:
                self._chat.write("[yellow]Elige una instancia en la tabla.[/yellow]")
            elif not answer:
                self._chat.write("[yellow]Escribe la respuesta que darás por el usuario.[/yellow]")
            else:
                self._chat.write(self._safe(answer_instance_for_user, target, answer))
                self._refresh_instancias()
        elif bid == "btn_send_msg":
            target, msg = self._selected_instance(), val("inst_msg")
            if target and msg:
                self._chat.write(self._safe(send_yarbis_message, target, msg))
            else:
                self._chat.write("[yellow]Elige una instancia y escribe el mensaje.[/yellow]")
        elif bid == "btn_broadcast":
            answer = val("inst_msg")
            waiting = [row["id"] for row in getattr(self, "_rows", []) if row["waiting"]]
            if not answer:
                self._chat.write("[yellow]Escribe la respuesta a difundir.[/yellow]")
            elif not waiting:
                self._chat.write("[yellow]Ninguna instancia está esperando respuesta.[/yellow]")
            else:
                for target in waiting:
                    self._chat.write(f"[b]{target}:[/b] " + self._safe(answer_instance_for_user, target, answer))
                self._refresh_instancias()
        elif bid == "btn_svc_install":
            self._chat.write(self._safe(install_background_service))
            self._refresh_servicio()
        elif bid == "btn_svc_start":
            self._chat.write(self._safe(start_background_service))
            self._refresh_servicio()
        elif bid == "btn_svc_stop":
            self._chat.write(self._safe(stop_background_service))
            self._refresh_servicio()
        elif bid == "btn_svc_remove":
            self._chat.write(self._safe(remove_background_service))
            self._refresh_servicio()
        elif bid == "btn_svc_auto_on":
            self._chat.write(self._safe(set_background_service_autostart, True))
            self._refresh_servicio()
        elif bid == "btn_svc_auto_off":
            self._chat.write(self._safe(set_background_service_autostart, False))
            self._refresh_servicio()
        elif bid == "btn_pulse":
            self._save_pulse()
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

    def on_select_changed(self, event: Select.Changed) -> None:
        value = event.value
        if event.select.id == "inst_select":
            # Elegir instancia la cambia al instante.
            if value not in (None, Select.BLANK):
                self.switch_to_instance(str(value))
            return
        if event.select.id == "set_provider":
            # Al elegir proveedor, mostrar SU configuracion actual.
            if value not in (None, Select.BLANK):
                self._load_provider_fields(str(value))

    def _save_pulse(self) -> None:
        def val(widget_id: str) -> str:
            return self.query_one(f"#{widget_id}", Input).value.strip()

        enabled_raw = self.query_one("#pulse_enabled", Select).value
        try:
            current = get_service_proactive_settings()
        except Exception:
            current = {}
        enabled = (
            bool(current.get("enabled"))
            if enabled_raw in (None, Select.BLANK)
            else str(enabled_raw) == "1"
        )
        interval = val("pulse_interval") or current.get("interval_seconds", 1800)
        delay = val("pulse_delay") or current.get("start_delay_seconds", 60)
        cycles = val("pulse_cycles")  # vacio = hasta terminar

        self._chat.write(self._safe(
            update_service_proactive_settings,
            enabled,
            interval,
            cycles or None,
            delay,
            val("pulse_model"),
        ))
        self._refresh_pulso()
        self._refresh_estado()

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
