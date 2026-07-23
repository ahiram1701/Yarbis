# Referencia de configuracion

Yarbis guarda su configuracion y su memoria en un archivo **`state.json` por
instancia**. Los valores se editan normalmente desde la app, la TUI, Telegram o
pidiendoselo a Yarbis por lenguaje natural (que usa las herramientas de
configuracion). Las **variables de entorno** sirven como override avanzado.

- Ubicacion: `state.json` en la raiz de la instancia (`default`) o en
  `.yarbis_instances/<id>/state.json`.
- Todo bloque ausente se completa con defaults al cargar (`normalize_state`), asi
  que un `state.json` viejo se migra solo.
- Los secretos (tokens, API keys) se guardan por `credential_store` cuando es
  posible y se redactan en los logs; nunca los pongas en texto plano si puedes
  evitarlo.

## Bloques de `state.json`

### Nucleo del agente
| Bloque | Proposito | Campos clave (default) |
|---|---|---|
| `state_schema_version` | Version del esquema para migraciones. | entero |
| `goal` | Objetivo principal que guia los ciclos. | texto |
| `messages` | Historial conversacional (se compacta al crecer). | lista de turnos |
| `conversation_summary` | Resumen rodante de los turnos ya compactados. | `text`, `updated_at`, `summarized_count:0` |
| `last_result` | Resultado del ultimo ciclo. | texto |
| `cycle_count` | Contador de ciclos ejecutados. | entero |
| `awaiting_user_input` | Si el agente esta pausado esperando una respuesta. | `pending:false`, `question`, `reason`, `fields[]` |
| `autonomy` | Limites de autonomia. | `max_steps_per_cycle:null` (sin limite), `auto_cycles_default:null` |
| `runtime` | Estado operativo en vivo. | `thinking{...}`, `stop_requested{...}` |

### Memoria y perfil
| Bloque | Proposito | Campos clave (default) |
|---|---|---|
| `profile` | Perfil del usuario. | `name`, `role`, `preferences[]`, `constraints[]`, `timezone` |
| `notes` | Notas persistentes ricas. | lista: `title`, `content`, `category`, `tags[]`, `importance:0` (0-3), `pinned:false`, fechas, `access_count` |
| `tasks` | Tareas con estado. | lista: `title`, `details`, `status` (pending/in_progress/blocked/done), `priority`, `result` |
| `current_plan` | Plan de pasos cortos. | lista de textos (max 12) |
| `idea_projects` | Proyectos de ideas (producto/negocio/vida). | lista con direcciones, riesgos, preguntas, pasos |
| `recall` | Motor de recuperacion de memoria. | `embeddings{enabled:false, provider, model, host}` (BM25 por defecto; embeddings opcional) |
| `memory_protection` | Respaldos y auto-recuperacion. | `enabled:true`, `backup_on_every_change:true`, `retention{max_auto_backups:50, keep_daily_days:14}`, `auto_restore:true`, `mirror_dir` |
| `self_knowledge` | Autoconocimiento (identidad, codigo, entorno) + insights aprendidos. | `summary`, `source_signature`, `insights[]` |

### Modelo y ejecucion
| Bloque | Proposito | Campos clave (default) |
|---|---|---|
| `model_provider` | Proveedor activo y config de cada uno. | `default:"ollama"` + subbloques `ollama`/`openrouter`/`openai_compat`/`puter`, cada uno con `model`, `fallback_models[]`, `host`, `api_key`, `api_key_env_var`, `timeout_seconds:900` |
| `ollama` | Config legada de Ollama (espejada en `model_provider.ollama`). | `model:"qwen3.5:2b"`, `host`, `timeout_seconds:900` |
| `service` | Servicio de fondo. | `proactive{enabled:true, interval_seconds:1800, cycles:null, start_delay_seconds:60, model}`, `mobile_ui{...}` |
| `communication` | Estilo de respuesta. | `tone:"warm_brief"`, `detail_level:"balanced"`, `proactivity:"moderate"` |

El subbloque `service.mobile_ui` incluye: `enabled:false`, `port:8830`,
`job_timeout_seconds:1800`, `https_enabled:true`, `pin_hash`/`pin_salt`,
`tailscale_serve_target`, y el token de Atajos `shortcut_token_hash`/`_salt`.

### Capacidades (apagadas por defecto salvo indicacion)
| Bloque | Proposito | Campos clave (default) |
|---|---|---|
| `computer_control` | Control de PC/navegador. **Off por defecto.** | `enabled:false`, `settings{browser_channel:"msedge", browser_profile_mode:"isolated", headed:true, confirm_sensitive:true, os_control:true}` |
| `internet` | Politica de internet. | `mode:"auto"`, `provider:"duckduckgo_html"`, `max_search_results:5`, `max_page_chars:12000`, `allowed_domains[]`, `blocked_domains[]` |
| `vision` | Vision por imagenes. | `model:"gemma3:12b-cloud"`, `max_image_dim:1280`, `timeout_seconds:120` |
| `voice` | Voz local (STT/TTS) y conversacion en vivo. | `enabled:true`, `language:"es"`, `stt_model:"base"`, `tts_provider:"edge"`, `edge_voice`, `live_conversation{...}` |
| `local_context` | Snapshot de contexto de la PC. | `enabled:true`, `mode:"safe"`, `sample_interval_seconds:30`, `include_window_title:false`, `include_process_name:true` |
| `evolution` | Autoevolucion (propone mejoras). **Off por defecto.** | `enabled:false`, `interval_hours:6`, `max_pending:2`, `dimensions[]` |
| `mcp` | Cliente de servidores MCP externos. **Off por defecto.** | `enabled:false`, `servers[]` |
| `mesh` | Malla de nodos. **Off por defecto.** | `enabled:false`, `network_name`, `relay_url`, `node_id`, `node_name`, `secret_ref` |

### Canales e integraciones
| Bloque | Proposito | Campos clave (default) |
|---|---|---|
| `notifications` | Canales de aviso. | `enabled:true`, `channels:["windows"]`, `ntfy{server, topic, token, ...}`, `telegram{api_base, bot_token, chat_id, poll_timeout_seconds:25}` |
| `social` | Redes sociales. | `settings{meta_graph_version, require_confirmation:true}`, `accounts[]`, `drafts[]`, `pending_publications[]`, `media_inbox[]` |
| `ui` | Preferencias de interfaz. | `theme:"dark"` |

## Variables de entorno

Los ajustes guardados en `state.json` se usan cuando **no** hay una variable de
entorno que los sobrescriba. Las variables son override avanzado (utiles para el
entorno del servicio o para automatizar).

### Instancia y servicio
| Variable | Proposito |
|---|---|
| `YARBIS_INSTANCE` | Id de la instancia (define que `state.json` se usa). |
| `YARBIS_SERVICE_NAME` | Nombre del servicio para esta instancia. |
| `YARBIS_PREFIXES` / `YARBIS_WAKE_ALIAS_GROUP` | Prefijos/alias de invocacion. |

### Modelo
| Variable | Proposito |
|---|---|
| `YARBIS_MODEL_PROVIDER` | Proveedor activo (`ollama`/`openrouter`/`openai_compat`/`puter`). |
| `YARBIS_MODEL` | Modelo principal. |
| `YARBIS_OLLAMA_HOST` / `_API_KEY` / `_API_KEY_ENV_VAR` / `_FALLBACK_MODELS` / `_TIMEOUT_SECONDS` | Config de Ollama. |
| `YARBIS_OPENROUTER_HOST` / `_API_KEY` / `_API_KEY_ENV_VAR` / `_FALLBACK_MODELS` / `_TIMEOUT_SECONDS` | Config de OpenRouter. |
| `YARBIS_OPENAI_COMPAT_HOST` / `_API_KEY_ENV_VAR` / `_FALLBACK_MODELS` / `_TIMEOUT_SECONDS` | Config del proveedor OpenAI-compatible. |
| `YARBIS_PUTER_HOST` / `_API_KEY_ENV_VAR` / `_FALLBACK_MODELS` / `_TIMEOUT_SECONDS` | Config de Puter (worker pasarela). |
| `YARBIS_EMPTY_RESPONSE_RETRIES`, `YARBIS_OPENROUTER_HTTP_RETRIES`, `YARBIS_OPENROUTER_RETRY_DELAY_SECONDS` | Reintentos. |

### Pulso proactivo (override de lo guardado)
| Variable | Proposito |
|---|---|
| `YARBIS_SERVICE_PROACTIVE` | `1` activa el pulso, `0` deja el servicio solo como inbox. |
| `YARBIS_SERVICE_PROACTIVE_INTERVAL_SECONDS` | Intervalo entre pulsos. |
| `YARBIS_SERVICE_PROACTIVE_CYCLES` | Ciclos por pulso (numero, o `none`/`unlimited`). |
| `YARBIS_SERVICE_PROACTIVE_START_DELAY_SECONDS` | Espera inicial tras arrancar. |
| `YARBIS_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS` | Tope de duracion por pulso. |
| `YARBIS_SERVICE_PROACTIVE_MODEL` | Modelo especifico para el pulso. |
| `YARBIS_PROACTIVE_SAFE_MODE` | `1` vuelve a la whitelist segura antigua de tools. |

### Notificaciones y Telegram
| Variable | Proposito |
|---|---|
| `YARBIS_NOTIFICATIONS` / `YARBIS_NOTIFICATION_CHANNELS` | Activar; canales (`windows,ntfy,telegram`). |
| `YARBIS_NTFY_SERVER` / `_TOPIC` / `_TOKEN` / `_PRIORITY` / `_TAGS` / `_TIMEOUT_SECONDS` | Config de ntfy. |
| `YARBIS_TELEGRAM_API_BASE` / `_BOT_TOKEN` / `_CHAT_ID` / `_TIMEOUT_SECONDS` / `_POLL_TIMEOUT_SECONDS` | Config de Telegram. |

### Autoevolucion y redes sociales
| Variable | Proposito |
|---|---|
| `YARBIS_EVOLUTION` / `_INTERVAL_HOURS` / `_MAX_RUNTIME_SECONDS` / `_SETTINGS_JSON` | Autoevolucion. |
| `YARBIS_FACEBOOK_ACCESS_TOKEN`, `YARBIS_INSTAGRAM_*`, `YARBIS_LINKEDIN_ACCESS_TOKEN` / `_CLIENT_SECRET`, `YARBIS_META_APP_SECRET` | Tokens/secretos sociales (mejor por OAuth/credential_store). |

> Los ejemplos de uso (PowerShell/bash) y las notas de troubleshooting de modelo
> estan en el [manual](manual.md).
