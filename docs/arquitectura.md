# Arquitectura de Yarbis

Yarbis es un **agente de un solo proceso** que corre en el equipo del usuario.
Un ciclo del agente consulta un modelo de lenguaje, ejecuta herramientas locales
y persiste todo en un archivo de estado. Alrededor de ese nucleo hay canales de
entrada/salida (Telegram, TUI, UI movil, Atajos), una capa de abstraccion por
sistema operativo, y una malla opcional entre nodos.

Principio rector: **el core no necesita dependencias de terceros**. Con un
proveedor de modelo en la nube, todo el agente funciona con solo la biblioteca
estandar de Python — lo que le permite correr en Termux, una Raspberry o un VPS
minimo. Las capacidades pesadas (voz, vision local, navegador, GUI) son
opcionales y se activan solo si el dispositivo puede.

## Vista general

```
                 Usuario / canales
   Telegram   TUI   UI movil (PWA)   Atajos iOS   app de escritorio
      |        |         |              |               |
      +--------+----+----+------+-------+-------+--------+
                    |           |               |
                    v           v               v
              +-----------------------------------------+
              |            session.py                    |  candado de operacion,
              |  submit_user_reply / run_*_with_output   |  serializa los ciclos
              +--------------------+---------------------+
                                   |
                                   v
              +-----------------------------------------+
              |               agent.py                   |
              |  run_one_cycle:                          |
              |   1. compacta historial (memoria)        |
              |   2. build_messages (contexto + recall)  |
              |   3. cliente de modelo (chat + tools)    |
              |   4. despacha tool_calls                 |
              |   5. persiste el resultado               |
              +----+-----------------+-------------------+
                   |                 |
                   v                 v
             +----------+     +---------------+
             | tools.py |     |  memory.py    |  state.json por instancia
             | 146 tools|<--->| load/save/    |  (normalize, respaldos,
             +----+-----+     | normalize     |   BOM-safe, atomic)
                  |           +---------------+
                  v
   filesystem, comandos, navegador (Playwright), internet,
   redes sociales, vision, voz, control de PC, MCP externo, malla
```

## El ciclo del agente

El corazon es `agent.run_one_cycle()` ([agent.py](../agent.py)). Cada ciclo:

1. **Compacta el historial si hace falta** (`maybe_compact_history`): si hay
   demasiados turnos, resume los viejos y poda los crudos (ver *Pipeline de
   memoria*). Es lo primero, para que el contexto siempre quepa.
2. **Arma el contexto** (`build_messages`): dos mensajes de sistema (el prompt
   base + un bloque de contexto con el resumen de estado, el resumen de
   conversacion, los **recuerdos recuperados por relevancia**, la linea de
   dispositivo, el autoconocimiento y las directivas aprendidas) seguidos de los
   ultimos ~20 turnos literales.
3. **Llama al modelo** con la lista de herramientas disponibles
   (`_current_tool_definitions`, que incluye las tools nativas y las de
   servidores MCP conectados).
4. **Despacha las tool_calls**: por cada llamada, ejecuta la funcion de
   `available_functions[nombre]` (o rutea a `mcp_client` si el nombre empieza con
   `mcp__`), agrega la salida al historial y sigue.
5. **Persiste** el resultado y, cuando corresponde, se detiene (pregunta al
   usuario, no quedan tareas, o el ciclo cerro sin trabajo util).

Un ciclo puede correr varios pasos (`max_steps_per_cycle`). El **modo autonomo**
encadena varios ciclos. El **pulso proactivo** del servicio agrega un mensaje de
contexto y corre ciclos con acceso completo a herramientas, sin intervencion.

### Serializacion

`session.py` mantiene un **candado de operacion** por instancia
(`session_operation_lock`, respaldado por un archivo con lock del SO). Solo una
operacion pesada corre a la vez por instancia; si llega otra mientras hay una en
curso, se rechaza con `SessionOperationBusy` y el llamador reintenta o encola.
Esto evita que Telegram, la UI y el pulso proactivo se pisen.

## Modelo de estado

Cada instancia tiene **un archivo `state.json`** (mas respaldos). Es la memoria
compartida: la interfaz, Telegram y el pulso proactivo leen y escriben el mismo
archivo.

- `memory.load_state()` lee y **normaliza** el estado; `save_state()` escribe de
  forma **atomica** (a un temporal + rename) para no corromper el archivo.
- `normalize_state()` es la funcion **pura** que valida y completa cada bloque
  con defaults, aplica topes y **migra** estados viejos (agrega bloques/campos
  nuevos). Se llama en cada carga, asi que el esquema siempre queda al dia.
- `state_transaction(label, mutator)` aplica un cambio bajo lock + respaldo:
  carga, muta, normaliza, guarda.
- **Lecturas tolerantes a BOM** (`atomic_io.read_json_bom_safe`): un `state.json`
  con BOM UTF-8 (tipico de trasplantes por PowerShell) no rompe la carga y se
  auto-sana. Ver la referencia de configuracion para los bloques.

Los respaldos rotativos viven en `.yarbis_memory_backups/` y los gestiona
`memory_backup.py` (crear, verificar, restaurar, podar, exportar/importar).

## Capa por sistema operativo

Todo lo dependiente del SO esta detras de un backend por-SO, con Windows
inalterado. La deteccion vive en `device_profile.py`, que **sondea
comportamiento** (¿hay pantalla? ¿se puede lanzar un proceso? ¿hay red?) en vez de
fiarse del nombre del SO — asi funciona incluso en sistemas desconocidos.

| Subsistema | Windows | Linux | macOS | Android (Termux) |
|---|---|---|---|---|
| Notificaciones | win11toast | notify-send | osascript | termux-notification |
| Credenciales | DPAPI | keyring/Secret Service | keyring/Keychain | base64 (fallback) |
| Bateria/energia | Win32 API | /sys/class/power_supply | pmset | termux-battery-status |
| Memoria | Win32 API | /proc/meminfo | sysctl+vm_stat | /proc/meminfo |
| Contexto activo | Win32 | xdotool/xprintidle | osascript/ioreg | (sin escritorio) |
| Apagado/reinicio | shutdown.exe | shutdown | shutdown | (segun permisos) |
| Servicio de fondo | SCM + host .NET | systemd (--user) | launchd | runit/proceso |

`device_profile` deriva una **clase de dispositivo** (`workstation`, `laptop`,
`server-headless`, `sbc`, `container`, `vm`, `android-termux`, `unknown`) y un
mapa de **capacidades** (`gui`, `desktop_control`, `visible_browser`, `browser`,
`audio_in`, `audio_out`, `local_llm`). Las herramientas consultan
`capability_allows(...)` antes de intentar algo imposible: un servidor sin
pantalla rechaza el control de escritorio con un motivo claro, y el navegador
degrada a modo headless en vez de fallar.

Punto clave: **la adaptacion nunca escribe el estado**. El `state.json` guarda la
*intencion* del usuario; el dispositivo decide que es posible *ahora*. Asi la
memoria se puede trasplantar entre maquinas y la adaptacion se recalcula sola.

## Servicio de fondo

Yarbis corre 24/7 como servicio, con un backend por-SO tras una fachada comun:

- **Windows**: un Windows Service real administrado por SCM
  (`service_manager.py`) que lanza un host .NET (`service_host/`), que a su vez
  arranca `yarbis_service.py` como proceso hijo.
- **Linux/macOS/Android/otros**: `native_service.py` genera y controla una unit
  de **systemd** (`--user`), un **launchd** LaunchAgent, un servicio **runit**
  de Termux, o —si el SO no tiene gestor conocido— un **supervisor portable**
  (proceso plano con relanzamiento).

Las herramientas `background_service_*` usan una fachada que despacha al backend
correcto. `yarbis_service.py` corre el **loop del servicio**: mantiene vivo el
inbox de Telegram, ejecuta el pulso proactivo, procesa mensajes entre instancias
y sincroniza la malla.

## Proveedores de modelo

`agent.py` abstrae el proveedor detras de una interfaz `.chat(...)` que devuelve
`SimpleNamespace(message=SimpleNamespace(content, tool_calls))`:

- **Ollama** (local o cloud) — via el paquete `ollama` (unica dependencia de
  terceros, y **opcional**).
- **OpenRouter**, **OpenAI-compatible** (Groq, DeepSeek, LM Studio, etc.) y
  **Puter** — clientes HTTP sobre `urllib` de la stdlib. Puter accede a
  `puter.ai.chat` via un Worker pasarela.

`_build_model_client(settings)` elige el cliente por proveedor;
`_resolve_model_runtime_settings` resuelve modelo/fallbacks/host/timeout desde el
estado o las variables de entorno. Como los clientes de nube usan solo `urllib`,
el core corre sin instalar nada.

## Pipeline de memoria

Mas alla del historial plano, la memoria es de nivel profesional (ver el
[catalogo de herramientas](herramientas.md) y la seccion de memoria del manual):

1. **Recuperacion por relevancia** (`memory_recall.py`): en cada turno, en vez de
   volcar las notas mas recientes, un buscador **BM25 en Python puro** recupera
   los recuerdos pertinentes (notas + aprendizajes + ideas) a la consulta (ultimo
   mensaje del usuario + objetivo). Opcionalmente, `memory_embeddings.py`
   reordena por similitud semantica si hay un proveedor de embeddings; sin el,
   cae al lexico.
2. **Compactacion del historial**: al superar el umbral, `maybe_compact_history`
   resume los turnos viejos (llamada al modelo sin herramientas, incremental) y
   los poda, conservando los recientes literales. Preserva el hilo largo y evita
   que `state.json` crezca sin limite.
3. **Notas ricas**: cada nota tiene importancia, tags, fijado (`pinned`), fechas
   y contador de accesos. El **desalojo es inteligente** (por importancia/fijado/
   recencia/accesos): una nota trivial no expulsa una importante.
4. **Auditoria** (`memory_audit`): integridad (BOM/esquema), respaldos, tamanos y
   metricas.

## Multi-instancia y malla

- **Multi-instancia (misma maquina)**: `yarbis_instance.py` distingue instancias
  por la variable `YARBIS_INSTANCE` (cada una con su `state.json`, su servicio y
  su nombre). `yarbis_bus.py` coordina instancias por **archivos** en un
  directorio compartido: mensajes directos, respuestas y "responder por el
  usuario" para desbloquear una instancia que espera.
- **Malla (entre maquinas)**: `mesh.py` + `mesh_relay.py` federan nodos de
  distintas maquinas del usuario a traves de un **relay** (un Worker de Puter,
  `yarbis_mesh_worker.js`, que hace de buzon + roster). Los nodos se enrolan con
  un secreto de red, se descubren y **delegan tareas por capacidad** (un nodo
  headless delega "toma una captura" a uno con pantalla). El loop del servicio
  sincroniza la malla con throttle. No hay auto-propagacion: cada nodo lo enrola
  el usuario.

## Canales

- **Terminal (`main.py`)**: REPL por comandos.
- **App de escritorio (`yarbis_desktop.py`, solo Windows)**: GUI Tkinter con
  vistas de estado, chat, instancias, contexto, ajustes y control del servicio.
- **TUI (`yarbis_tui.py`)**: interfaz de terminal rica (Textual), multiplataforma,
  abrible con doble clic.
- **Telegram (`telegram_inbox.py`, `telegram_format.py`)**: texto, notas de voz y
  comandos; lo mantiene vivo el servicio.
- **UI movil (`yarbis_mobile.py`)**: servidor HTTP local, responsive e instalable
  como **PWA**; se expone por HTTPS con Tailscale. Incluye la **API de Atajos**
  (token) para Siri/Share Sheet en iOS.
- **Contexto local (`pc_context*.py`)**: un helper de bandeja captura senales
  seguras de la PC (presencia/idle, proceso en primer plano, salud, cambios del
  workspace) para enriquecer el pulso proactivo.

## Seguridad transversal

- **Gate por capacidad potente**: control de PC, MCP, malla, autoevolucion y
  publicacion social estan **apagados por defecto** y requieren activacion
  explicita.
- **Secretos** via `credential_store.py` (nunca en claro en el estado) y
  **redaccion** en logs (`secrets_redaction.py`).
- **Pulso proactivo**: solo un subconjunto de tools de solo-lectura es
  "proactive-safe"; las acciones sensibles no se usan en pulsos autonomos.
- **Modo coding** por defecto `propose_first`: propone diffs y espera aprobacion.
- **Guarda de sintaxis** antes de escribir `.py` (evita romperse a si mismo).
