# Referencia de modulos

Yarbis son ~45 modulos Python (lista canonica en `py-modules` de
[pyproject.toml](../pyproject.toml)). Aqui se agrupan por capa. Los modulos hoja
(sin dependencias del agente) permiten que el core corra sin extras.

## Nucleo del agente

| Modulo | Rol |
|---|---|
| `agent.py` | El cerebro. `run_one_cycle` (compacta memoria, arma contexto, llama al modelo, despacha tools, persiste), `build_messages`, los clientes de modelo (Ollama/OpenRouter/OpenAI-compat/Puter), el registro `available_functions`/`tool_definitions` y la whitelist proactiva. |
| `session.py` | Orquestacion de operaciones: `submit_user_reply`, `run_cycle_with_output`, `run_auto_with_output`, el candado de operacion por instancia y los wrappers `*_text` que usan los canales. |
| `tools.py` | Implementacion de las 146 herramientas del agente (memoria, codigo, navegador, redes, dispositivo, malla, atajos, etc.). |
| `intent_text.py` | Deteccion de intencion en texto (afirmaciones, comandos) para interpretar respuestas del usuario. |
| `conversation_ux.py` | Formateo de la experiencia conversacional (timeline, notificaciones, texto compacto por canal). |

## Memoria

| Modulo | Rol |
|---|---|
| `memory.py` | El estado. `load_state`/`save_state`/`normalize_state`/`state_transaction`, todos los bloques de `state.json`, defaults, topes, migraciones, compactacion de historial y desalojo de notas. |
| `memory_recall.py` | Recuperacion por relevancia: BM25 en Python puro sobre notas + insights + ideas. |
| `memory_embeddings.py` | Backend de embeddings **opcional** (Ollama/OpenAI-compat via urllib) para rerank semantico; fallback lexico. |
| `memory_backup.py` | Respaldos rotativos: crear, verificar, restaurar, podar, exportar/importar paquetes. |
| `memory_transfer.py` | Trasplante de memoria entre instancias (import/export de estado). |
| `atomic_io.py` | I/O robusto: escritura atomica y lecturas tolerantes a BOM (`read_json_bom_safe`). |
| `secrets_redaction.py` | Redaccion de secretos (tokens, keys, Bearer) en logs y salidas. |

## Modelo de si mismo

| Modulo | Rol |
|---|---|
| `self_knowledge.py` | Autoconocimiento auto-derivado: identidad, catalogo de capacidades (del registro real de tools), mapa de codigo (AST) y entorno/hardware; mas los insights aprendidos. |
| `proactive_context.py` | Construccion del mensaje del pulso proactivo: hints de contexto local, seccion de experiencia y correcciones recientes del usuario. |

## Capa por sistema operativo

| Modulo | Rol |
|---|---|
| `device_profile.py` | Perfil del dispositivo: sondeo de comportamiento, clase de dispositivo y capacidades efectivas. Nunca escribe estado. |
| `notifications.py` | Notificaciones por-SO (win11toast/notify-send/osascript/termux) + ntfy + Telegram. |
| `credential_store.py` | Secretos por-SO: DPAPI (Windows), keyring (Linux/macOS), base64 (fallback). |
| `pc_context.py` | Senales de la PC: idle, ventana activa, bateria, memoria, disco — por-SO. |
| `pc_context_runtime.py` | Runtime del contexto (Windows-especifico) usado por el helper de bandeja. |
| `pc_context_tray.py` | Helper de bandeja que captura el snapshot en la sesion interactiva. |
| `pc_context_worker.py` | Worker que produce el snapshot de contexto local. |
| `power.py` | Apagado/reinicio/cancelar por-SO. |

## Servicio y arranque

| Modulo | Rol |
|---|---|
| `yarbis_service.py` | El loop del servicio: inbox de Telegram, pulso proactivo, mensajes entre instancias, sincronizacion de malla, recuperacion de respuestas pendientes. Entrypoint como proceso plano. |
| `service_manager.py` | Servicio de Windows (SCM) + host .NET; ademas `readiness_status`/`health_status`. |
| `native_service.py` | Servicio nativo fuera de Windows: systemd (`--user`), launchd, runit de Termux, o supervisor portable. |
| `yarbis_instance.py` | Identidad de instancia: `YARBIS_INSTANCE`, rutas, nombre de servicio, registro de instancias. |
| `yarbis_node.py` | Bootstrap sin dependencias: sondea el entorno y arranca el servicio en un dispositivo nuevo. |

## Coordinacion multi-instancia y malla

| Modulo | Rol |
|---|---|
| `yarbis_bus.py` | Bus por archivos entre instancias de la misma maquina: mensajes directos, respuestas, "responder por el usuario". |
| `mesh.py` | Malla entre maquinas: sync throttled, procesamiento de entrantes, delegacion por capacidad. |
| `mesh_relay.py` | Cliente del relay (solo urllib): enrolar, enviar, poll, roster, con firmas HMAC. |

## Canales y UI

| Modulo | Rol |
|---|---|
| `main.py` | REPL de terminal por comandos. |
| `yarbis_desktop.py` | App de escritorio Tkinter (solo Windows) con vistas y control del servicio. |
| `yarbis_tui.py` | TUI multiplataforma (Textual), abrible con doble clic. |
| `yarbis_mobile.py` | UI movil (servidor HTTP), PWA instalable, y la API de Atajos de iOS. |
| `telegram_inbox.py` | Lector/escritor de Telegram (long polling, comandos, notas de voz). |
| `telegram_format.py` | Formateo de mensajes para Telegram. |
| `ui_dialogs.py` / `ui_settings_dialogs.py` / `ui_theme.py` | Dialogos y tema de la app de escritorio (Tkinter). |

## Capacidades

| Modulo | Rol |
|---|---|
| `browser_automation.py` | Automatizacion de navegador real con Playwright; perfiles y arranque en sesion activa (Session 0). |
| `internet.py` | Busqueda web (DuckDuckGo HTML) y lectura de paginas bajo politica. |
| `integrations.py` | Calendario (`.ics`), correo (`mailto:`), abrir rutas/URLs con manejadores locales. |
| `voice.py` | Voz local: STT (faster-whisper), TTS (edge-tts/pyttsx3), grabacion. |
| `voice_conversation.py` | Conversacion de voz en vivo (wake phrase, turnos). |
| `social_publishing.py` | Publicacion en redes (Facebook Pages, Instagram, LinkedIn) + asistida. |
| `social_oauth.py` | OAuth local para conectar cuentas Meta/LinkedIn. |
| `activity.py` | Feed de actividad/eventos observables por las UIs. |

> Detalle de las funciones expuestas al agente: ver el
> [catalogo de herramientas](herramientas.md).
