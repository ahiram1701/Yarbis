# Catalogo de herramientas

El agente actua mediante **146 herramientas** (`agent.available_functions`).
Aqui estan agrupadas por area. Las marcadas **·** son *proactive-safe*: solo esas
(76) puede usar el pulso proactivo autonomo; el resto requiere una accion
originada por el usuario. Muchas capacidades potentes estan ademas apagadas por
defecto (control de PC, MCP, malla, autoevolucion, publicacion social).

No hace falta memorizar nombres: le pides las cosas a Yarbis en lenguaje natural
y el elige la herramienta. Este catalogo es una referencia de lo que puede hacer.

## Memoria, tareas y contexto
- **·** `agent_overview` — resumen del estado (objetivo, tareas, notas, config).
- **·** `update_goal` — fija el objetivo principal.
- **·** `set_plan` — fija el plan de pasos cortos.
- **·** `add_task` / **·** `list_tasks` / **·** `update_task_status` — tareas y su estado.
- **·** `save_note` — guarda una nota (con `tags`, `importance`, `pinned`).
- **·** `list_notes` / **·** `get_note` / **·** `delete_note` — consultar/eliminar notas.
- **·** `memory_search` — recuerda por **relevancia** (no por recencia).
- `memory_consolidate` — fusiona notas casi-duplicadas.
- **·** `memory_audit` — salud de la memoria (integridad, respaldos, tamanos).
- **·** `update_profile` — perfil del usuario (nombre, rol, preferencias, restricciones).
- **·** `set_timezone` — zona horaria.
- **·** `request_user_input` — hace una pregunta al usuario y pausa el ciclo.
- **·** `create_idea_project` / **·** `list_idea_projects` / **·** `get_idea_project` / **·** `update_idea_project` / **·** `promote_idea_project_to_work` — proyectos de ideas.
- **·** `create_project_visual_board` / **·** `get_project_visual_board` / **·** `list_project_visual_boards` / **·** `update_project_visual_board` / `export_project_visual_board` — tableros visuales de proyectos.
- **·** `create_memory_backup` / `import_memory_backup` / **·** `list_memory_backups` / **·** `inspect_memory_backup` / **·** `verify_memory_backups` — respaldos de memoria.
- **·** `memory_protection_status` / `update_memory_protection_settings` — proteccion de memoria.

## Autoconocimiento
- **·** `self_overview` — identidad, capacidades, mapa de codigo, entorno y hardware.
- **·** `record_self_insight` / **·** `list_self_insights` / `remove_self_insight` — self-model aprendido (fortalezas/limites/estrategias/lecciones).

## Codigo y desarrollo
- **·** `coding_workspace_overview` / **·** `coding_workflow_status` / **·** `coding_set_workspace` — workspace de codigo activo.
- **·** `coding_list_files` / **·** `coding_read_text_file` / **·** `coding_read_text_range` / **·** `coding_search_text` — inspeccionar el repo.
- **·** `coding_propose_changes` / **·** `coding_propose_edits` / **·** `coding_propose_text_file` — proponer cambios (diff, requiere aprobacion).
- **·** `coding_get_proposal` / **·** `coding_list_proposals` / **·** `coding_check_proposal` — revisar propuestas.
- `coding_apply_proposal` / `coding_apply_and_validate` / `coding_discard_proposal` — aplicar/descartar. *(accion)*
- **·** `coding_git_status` / **·** `coding_git_diff` — estado del repo.
- **·** `coding_detect_validation_command` / **·** `coding_validation_plan` / `coding_run_validation` / `coding_update_validation_command` — validacion (tests/build).
- **·** `list_files` / **·** `coding_read_text_file` (lectura general); `read_text_file` / `write_text_file` — archivos dentro o fuera del workspace (con checkpoint). *(escribe)*
- `run_system_command` — comando del sistema con timeout y salida acotada.
- `run_project_tests` / `run_project_check` — suite y checks del proyecto.
- `list_checkpoints` / `restore_checkpoint` — checkpoints de edicion.
- **·** `list_self_code_changes` / `mark_self_code_changes_versioned` — bitacora de autoedicion.

## Navegador y control de PC (capacidad potente, off por defecto)
- `set_computer_control` — activa/desactiva el control de PC/navegador.
- `browser_open` — abre el navegador propio (Playwright) en una URL.
- `browser_observe` — lista los elementos clicables (por `ref`).
- `browser_act` — clic/escritura por `ref` o texto (con confirmacion sensible).
- `browser_automation` — automatizacion por script de acciones.
- `desktop_look` / `desktop_screen_size` — mira la pantalla / tamano.
- `desktop_click` / `desktop_move` / `desktop_type` / `desktop_press` — mouse/teclado por coordenadas.
- `open_system_target` — abre una ruta/URL con el manejador del SO.

## Dispositivo (adaptacion)
- **·** `device_profile_overview` — clase de dispositivo, hardware y capacidades.
- **·** `probe_device` — sondeo por comportamiento (archivos/procesos/red/servicio).
- **·** `device_adaptation_suggestions` — sugiere ajustes segun el equipo (sin aplicarlos).

## Servicio de fondo (multiplataforma)
- **·** `background_service_status` — estado del servicio (SCM/systemd/launchd/termux/portable).
- `install_background_service` / `start_background_service` / `stop_background_service` / `remove_background_service` / `set_background_service_autostart` — gestionar el servicio.

## Internet
- `web_search` — busqueda web (DuckDuckGo HTML) bajo politica.
- `fetch_web_page` — lee una pagina publica.
- `update_internet_settings` — cambia la politica de internet.

## Vision y voz
- **·** `analyze_image` — analiza una imagen (modelo multimodal).
- **·** `vision_status` / `vision_set_model` — config de vision.
- La voz (STT/TTS y conversacion en vivo) se configura desde la app/UI y el bloque `voice`.

## Redes sociales
- **·** `social_accounts_overview` — cuentas conectadas y su estado.
- `start_social_oauth` — conectar una cuenta Meta/LinkedIn por OAuth local.
- **·** `prepare_social_publication` / **·** `save_social_draft` / **·** `list_social_drafts` / **·** `list_social_publications` — preparar/guardar/listar.
- `confirm_social_publication` — publicar con confirmacion explicita.
- `open_assisted_social_post` — publicacion asistida (perfil personal de Facebook).
- `set_social_confirmation` — exigir o no confirmacion.
- **·** `list_recent_media` — media del inbox para adjuntar.

## Integraciones (calendario/correo)
- `create_calendar_event` — crea un evento `.ics`.
- `compose_email` — prepara un correo `mailto:`.

## Instancias (misma maquina)
- **·** `list_yarbis_instances` — instancias registradas.
- **·** `send_yarbis_message` / **·** `read_yarbis_messages` — mensajes entre instancias.
- **·** `list_pending_user_questions` — instancias que esperan tu respuesta.
- `answer_instance_for_user` — responde por ti para desbloquear otra instancia.

## Malla de nodos (entre maquinas, off por defecto)
- `mesh_create_network` — crea la red (genera el secreto).
- `mesh_deploy_help` — codigo del relay de Puter con el secreto inyectado.
- `mesh_configure_relay` — fija la URL del relay.
- `mesh_enroll` — enrola este nodo y activa la sincronizacion.
- **·** `mesh_status` / **·** `mesh_list_nodes` — estado y roster.
- `mesh_send` — mensaje a otro nodo.
- `mesh_delegate` — delega una tarea a un nodo con la capacidad requerida.
- `mesh_leave` — saca el nodo de la malla.

## Atajos de iOS
- `shortcuts_create_token` — genera el token para Siri/Share Sheet (se muestra una vez).
- **·** `shortcuts_status` — si estan configurados y con que URL.
- `shortcuts_revoke_token` — revoca el token.

## Servidores MCP externos (off por defecto)
- `set_mcp_enabled` — activa la capacidad MCP.
- `mcp_add_server` / `mcp_remove_server` / `mcp_connect` — administrar servidores.
- **·** `mcp_list_servers` / **·** `mcp_list_tools` — inventario.
- `mcp_refresh_tools` — redescubrir herramientas.
- `mcp_call_tool` — llamar una tool MCP manualmente (debug).
- Al conectar, las tools externas aparecen como `mcp__<servidor>__<tool>`.

## Autoevolucion (off por defecto)
- `evolution_set_enabled` / `evolution_set_interval` — activar y frecuencia.
- **·** `evolution_status` / **·** `evolution_list_pending` / **·** `evolution_list_directives` / **·** `evolution_list_suggestions` — revisar.
- **·** `evolution_propose_goal` / **·** `evolution_propose_memory` / **·** `evolution_propose_directive` — proponer mejoras.
- `evolution_apply_suggestion` / `evolution_apply_directive` / `evolution_discard_suggestion` / `evolution_discard_directive` — aplicar/descartar (aprobacion manual).
