# Yarbis

Yarbis es un agente local para convertir un objetivo general en trabajo accionable usando Ollama, memoria persistente, herramientas seguras de workspace, busqueda web controlada y notificaciones opcionales.

Funciona en tres modos:

- app de escritorio con Tkinter (`yarbis_desktop.py`)
- terminal interactiva (`main.py`)
- Windows Service administrado por SCM mediante el host nativo de `service_host/`

## Estado actual

Yarbis ya funciona como agente personal local:

- mantiene un perfil personal con nombre, contexto, preferencias y restricciones
- guarda notas persistentes para conservar contexto entre ciclos
- mantiene tareas con estados `pending`, `in_progress`, `blocked` y `done`
- sostiene un plan actual de pasos cortos
- ejecuta un ciclo controlado o varios ciclos en modo autonomo
- se detiene cuando necesita una respuesta del usuario, cuando ya no quedan tareas abiertas o cuando el ciclo ya cerro sin seguimiento util
- puede buscar informacion publica con DuckDuckGo HTML y leer paginas web publicas bajo una politica persistente
- puede editar archivos dentro del workspace con checkpoint previo, vista previa y diff resumido
- puede restaurar checkpoints y ejecutar tests del proyecto
- ejecuta un autoanalisis de identidad, codigo fuente, sistema operativo y hardware al arrancar
- puede recibir y responder mensajes por Telegram cuando ese canal esta configurado
- puede ejecutarse como Windows Service y arrancar con Windows desde SCM

## Requisitos

- Windows para la app de escritorio, notificaciones nativas y arranque con Windows
- Python 3.11 o superior
- .NET SDK 8 para compilar el host nativo del servicio SCM
- Ollama ejecutandose localmente
- un modelo disponible en Ollama; por defecto se usa `qwen3.5:2b`
- permisos de administrador para instalar, quitar o reconfigurar el servicio en SCM

Dependencias Python declaradas:

- `ollama==0.6.1`
- `win11toast==0.36.3`

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Uso rapido sin terminal

Si ya tienes `.venv` y dependencias listas, puedes abrir Yarbis con doble clic:

- `abrir_yarbis.vbs`: abre la app de escritorio sin mostrar consola
- `abrir_yarbis.cmd`: abre la app con una ventana visible por si necesitas revisar errores de arranque
- `abrir_yarbis_admin.cmd`: pide permisos de administrador y abre la app para instalar o reconfigurar el servicio SCM

Tambien puedes iniciarlo desde PowerShell:

```powershell
.\.venv\Scripts\python.exe yarbis_desktop.py
```

La interfaz de escritorio permite:

- cambiar el objetivo
- cambiar el modelo de Ollama y el timeout
- ejecutar un ciclo
- ejecutar modo autonomo indicando de 1 a 20 ciclos
- responder preguntas pendientes
- enviar contexto libre al agente
- editar perfil
- crear, ver y eliminar notas
- crear tareas manuales
- revisar estado y actividad
- alternar tema claro/oscuro
- configurar y probar notificaciones
- activar o desactivar el servicio de fondo
- configurar si el servicio se abre al iniciar Windows
- configurar el pulso proactivo del servicio
- quitar el servicio de SCM

## Servicio de fondo

El grupo `Servicio` de la interfaz grafica permite instalar y controlar Yarbis como Windows Service real administrado por SCM.

Cuando esta activo:

- SCM lanza el host nativo `.yarbis_runtime\service_host\YarbisServiceHost.exe`
- el host nativo inicia `.venv\Scripts\python.exe yarbis_service.py` como proceso hijo
- escribe su PID en `.yarbis_runtime/service.pid`
- registra actividad y errores en `.yarbis_runtime/service.log`
- publica actividad visible para la interfaz en `.yarbis_runtime/activity.log`
- mantiene activo el inbox de Telegram si Telegram esta configurado
- ejecuta un pulso proactivo periodico para avanzar el objetivo actual sin que tengas que abrir la app
- recupera al arrancar respuestas del usuario que hayan quedado guardadas pero sin ciclo completado
- evita que la app de escritorio o la terminal inicien un segundo lector de Telegram

El boton `Instalar e iniciar` compila el host con `dotnet publish` si hace falta, crea el servicio en SCM y despues lo inicia. `Detener servicio` manda la parada a SCM. `Quitar de SCM` detiene el servicio si hace falta y elimina su registro.

La casilla `Iniciar con Windows` cambia el tipo de arranque del servicio entre `auto` y `demand` usando SCM. Si el servicio aun no esta instalado, la casilla aparece deshabilitada; primero usa `Instalar e iniciar`.

Estas acciones suelen requerir ejecutar Yarbis como administrador. Por defecto, un servicio creado con `sc.exe create` corre bajo la cuenta `LocalSystem`; si necesitas otro usuario, ajusta la pestana `Iniciar sesion` desde `services.msc`.

La proactividad 24/7 del servicio esta activa por defecto. Tras una espera inicial, el servicio agrega un pulso de contexto al historial y ejecuta un ciclo autonomo breve. Si Yarbis necesita una decision, permiso o dato privado, registra una pregunta pendiente y notifica por los canales configurados.

Si el servicio se detiene mientras procesa una respuesta del usuario, esa respuesta queda en el historial. En el siguiente arranque o pulso, Yarbis detecta si el ultimo mensaje del usuario no tiene respuesta del asistente y ejecuta primero ese ciclo pendiente antes de agregar trabajo proactivo nuevo.

Puedes ajustar el pulso desde la app con `Servicio` -> `Configurar pulso`. Los cambios se guardan en `state.json`; el servicio los lee en caliente para el intervalo y los ciclos. La espera inicial aplica al siguiente arranque del servicio.

El modelo de Ollama y su timeout tambien se pueden cambiar desde la app con `Modelo` -> `Modelo y timeout`. Esos cambios se guardan en `state.json` y se aplican al siguiente ciclo, tanto en la app como en el servicio.

## Uso por terminal

```powershell
.\.venv\Scripts\python.exe main.py
```

Comandos disponibles:

- `goal`: actualiza el objetivo actual
- `run`: ejecuta un ciclo del agente
- `auto`: ejecuta varios ciclos seguidos
- `status`: muestra objetivo, perfil, plan, tareas, notas, internet, autoconocimiento y notificaciones
- `self`: refresca y muestra lo que Yarbis sabe de si mismo, su codigo fuente, el sistema operativo y el hardware local
- `profile`: actualiza nombre, contexto, preferencias y restricciones
- `note`: menu interactivo para crear, listar, ver o borrar notas
- `notes`: lista notas persistentes; tambien puedes usar `nota crear Titulo | contenido | categoria`, `nota ver ID` o `nota borrar ID`
- `task`: agrega una tarea manual al backlog
- `reply`: envia una respuesta libre; si habia una pregunta pendiente, reanuda el modo autonomo
- `exit`: termina la sesion

Si escribes texto libre mientras hay una pregunta pendiente, Yarbis lo toma como respuesta. Si no hay pregunta pendiente, usa `reply` para mandar contexto libre.

## Flujo recomendado

1. Define un objetivo claro con `goal` o desde la interfaz. El objetivo inicial viene vacio por defecto.
2. Carga contexto personal con `profile`.
3. Guarda notas o tareas cuando haya informacion estable.
4. Ejecuta `run` para un paso controlado o `auto` para dejar avanzar varios ciclos.
5. Si Yarbis pregunta algo, responde desde la app, la terminal o Telegram.

Al cambiar `goal`, Yarbis reinicia el contexto operativo del objetivo:

- limpia mensajes previos
- vacia tareas y plan actual
- borra el ultimo resultado
- limpia preguntas pendientes
- conserva perfil, notas, ajustes, notificaciones e internet

## Telegram

Telegram funciona como canal de notificaciones y tambien como inbox remoto.
Cuando Telegram esta activado y vinculado, las respuestas generadas desde la app o la terminal
tambien se copian al chat para que puedas seguir el hilo remoto sin abrir Yarbis.

Para configurarlo desde la interfaz:

1. Crea un bot con `@BotFather`.
2. Copia el token.
3. Abre Yarbis.
4. Pulsa `Notificaciones`.
5. Marca `Activar notificaciones` y `Telegram`.
6. Pega el `Bot token de Telegram`.
7. Guarda.
8. Abre el chat con el bot y envia `/start`.

Si `Chat ID` queda vacio, Yarbis vincula automaticamente el primer chat privado que escriba al bot.
Por seguridad, si ese primer mensaje es un comando de apagado o reinicio, solo vincula el chat; debes
reenviar el comando una vez vinculado.

Comandos disponibles por Telegram:

- `/start` o `/help`: muestra ayuda
- `/status`: muestra el estado actual
- `/goal nuevo objetivo`: cambia el objetivo
- `/objetivo nuevo objetivo`: alias de `/goal`
- `/run`: ejecuta un ciclo
- `/auto`: ejecuta modo autonomo con los ciclos por defecto
- `/auto 3`: ejecuta el numero indicado de ciclos
- `/notas`: lista notas persistentes
- `/notas personal`: lista notas de una categoria
- `/nota crear Titulo | contenido | categoria`: guarda una nota
- `/nota ID`: muestra una nota completa
- `/nota borrar ID`: elimina una nota
- `/apagar`: programa el apagado de esta PC en 60 segundos
- `/apagar ahora`: apaga esta PC inmediatamente
- `/apagar 5m`: programa el apagado en 5 minutos
- `/reiniciar`: programa el reinicio de esta PC en 60 segundos
- `/reiniciar ahora`: reinicia esta PC inmediatamente
- `/reiniciar 5m`: programa el reinicio en 5 minutos
- `/cancelar_apagado`: cancela un apagado programado
- `/cancelar_reinicio`: cancela un reinicio programado

Tambien puedes decir `guarda una nota: Titulo | contenido | categoria`, `ver notas`
o `borra la nota note-123` para gestionar notas con lenguaje natural. Para apagado o reinicio remoto,
puedes decir `apaga la pc`, `Yarbis, reinicia pc` o `reinicia pc en 5 minutos`
sin depender del modelo local. Los mensajes de texto sin `/` se procesan como respuesta
o contexto libre cuando no coinciden con una accion remota explicita. Por ahora Telegram
solo procesa texto.

## Notificaciones

Canales soportados:

- Windows, usando `win11toast`
- ntfy
- Telegram

Desde la interfaz, el boton `Notificaciones` permite:

- activar o desactivar notificaciones
- elegir canales
- configurar servidor, topic, token, prioridad y tags de ntfy
- configurar token y chat ID de Telegram
- enviar una prueba con `Probar notificacion`

Ejemplo con ntfy:

```powershell
$env:YARBIS_NOTIFICATION_CHANNELS="windows,ntfy"
$env:YARBIS_NTFY_TOPIC="yarbis-tu-topic-secreto"
.\.venv\Scripts\python.exe yarbis_desktop.py
```

Ejemplo con Telegram:

```powershell
$env:YARBIS_NOTIFICATION_CHANNELS="telegram"
$env:YARBIS_TELEGRAM_BOT_TOKEN="123456:tu-token"
.\.venv\Scripts\python.exe yarbis_desktop.py
```

Para desactivar todas las notificaciones:

```powershell
$env:YARBIS_NOTIFICATIONS="0"
.\.venv\Scripts\python.exe yarbis_desktop.py
```

## Variables de entorno

Modelo y ejecucion:

```powershell
$env:YARBIS_MODEL="qwen3.5:2b"
$env:YARBIS_OLLAMA_TIMEOUT_SECONDS="900"
$env:YARBIS_EMPTY_RESPONSE_RETRIES="1"
```

Si aparece `No pude consultar Ollama en este ciclo: timed out`, primero confirma que Ollama este vivo con `ollama list` u `ollama ps`. Si el modelo tarda en cargar, calientalo una vez con `ollama run qwen3.5:2b`, aumenta `YARBIS_OLLAMA_TIMEOUT_SECONDS`, reinicia la app o el servicio para que tome la variable, o cambia temporalmente a un modelo mas ligero con `YARBIS_MODEL="qwen3.5:0.8b"`.

Para uso diario, prefiere el boton `Modelo y timeout` de la app. Las variables `YARBIS_MODEL` y `YARBIS_OLLAMA_TIMEOUT_SECONDS` quedan como override avanzado y, si estan definidas, pueden tener prioridad sobre lo guardado en la interfaz.

Servicio proactivo, como override avanzado de la configuracion guardada:

```powershell
$env:YARBIS_SERVICE_PROACTIVE="1"
$env:YARBIS_SERVICE_PROACTIVE_INTERVAL_SECONDS="1800"
$env:YARBIS_SERVICE_PROACTIVE_CYCLES="1"
$env:YARBIS_SERVICE_PROACTIVE_START_DELAY_SECONDS="60"
```

Usa `YARBIS_SERVICE_PROACTIVE="0"` para dejar el servicio solo como inbox remoto y desactivar los ciclos autonomos periodicos.

Notificaciones:

```powershell
$env:YARBIS_NOTIFICATIONS="1"
$env:YARBIS_NOTIFICATION_CHANNELS="windows,ntfy,telegram"
$env:YARBIS_NTFY_SERVER="https://ntfy.sh"
$env:YARBIS_NTFY_TOPIC="yarbis-tu-topic-secreto"
$env:YARBIS_NTFY_TOKEN=""
$env:YARBIS_NTFY_PRIORITY="high"
$env:YARBIS_NTFY_TAGS="yarbis"
$env:YARBIS_NTFY_TIMEOUT_SECONDS="10"
$env:YARBIS_TELEGRAM_API_BASE="https://api.telegram.org"
$env:YARBIS_TELEGRAM_BOT_TOKEN="123456:tu-token"
$env:YARBIS_TELEGRAM_CHAT_ID="123456789"
$env:YARBIS_TELEGRAM_TIMEOUT_SECONDS="10"
$env:YARBIS_TELEGRAM_POLL_TIMEOUT_SECONDS="25"
```

Los ajustes guardados en `state.json` se usan cuando no hay una variable de entorno que los sobrescriba.

## Copias de trabajo

El proyecto original vive en la carpeta donde esta este README. Si usas una copia para pruebas o como instancia activa, sincroniza los cambios de codigo desde el original antes de arrancar el servicio o la app. Cada copia mantiene su propio `state.json`, `.yarbis_runtime/`, configuracion de Telegram y log del servicio.

## Memoria y configuracion

El archivo `state.json` guarda:

- objetivo actual
- historial conversacional reciente
- ultimo resultado
- conteo de ciclos
- perfil
- notas
- tareas
- plan actual
- pregunta pendiente
- limites de autonomia
- configuracion del pulso proactivo del servicio
- tema de la interfaz
- politica de internet
- resumen de autoconocimiento
- configuracion de notificaciones

El estado se normaliza antes de guardarse para mantener estructura compatible y valores validos. Yarbis no recorta destructivamente historial, notas, tareas ni resultados persistentes; cuando necesita enviar contexto al modelo, mostrar previews, resumir diffs o devolver salidas de tools, aplica limites sobre esa salida derivada.

`state.json` y `state.json.tmp` estan ignorados por git.

## Busqueda web

La politica de internet queda persistida en `state.json`.

Valores actuales:

- modo: `auto` u `off`
- proveedor: `duckduckgo_html`
- resultados por busqueda: 1 a 10, por defecto 5
- caracteres por pagina: 1,000 a 30,000, por defecto 12,000
- timeout por request: 3 a 60 segundos, por defecto 10
- dominios permitidos opcionales
- dominios bloqueados opcionales

La lectura web solo permite URLs publicas `http` y `https`. Bloquea `localhost`, dominios `.local`, IPs privadas, loopback, link-local, multicast, reservadas o no especificadas.

Puedes pedir cambios en lenguaje natural, por ejemplo:

- "desactiva internet por ahora"
- "limita internet a docs.python.org"
- "bloquea wikipedia.org en las busquedas"

Internamente el agente usa `update_internet_settings`, `web_search` y `fetch_web_page`.

## Tools internas

Yarbis expone al modelo estas herramientas:

- `agent_overview`: resumen del estado actual
- `self_overview`: identidad, codigo fuente y entorno local
- `update_profile`: perfil personal
- `update_internet_settings`: politica web
- `request_user_input`: registra una pregunta pendiente
- `save_note` y `list_notes`: memoria persistente
- `add_task`, `list_tasks` y `update_task_status`: backlog de trabajo
- `set_plan`: plan actual
- `list_files` y `read_text_file`: lectura dentro del workspace
- `write_text_file`: escritura con checkpoint y diff
- `list_checkpoints` y `restore_checkpoint`: recuperacion de cambios
- `run_project_tests`: validacion con `unittest`
- `web_search` y `fetch_web_page`: acceso web publico bajo politica

## Autoedicion segura

Las tools de archivos trabajan solo dentro del workspace.

Limites de salida actuales:

- lectura de archivos: completa por defecto; opcionalmente acotada hasta 16,000 bytes con `max_bytes`
- escritura por operacion: 64,000 bytes
- vista previa de escritura: 600 caracteres
- diff resumido: 160 lineas
- salida de tests: 6,000 caracteres
- listado de archivos: 200 elementos

Antes de escribir, `write_text_file` crea un checkpoint en `.yarbis_checkpoints/`. Si un cambio sale mal, `restore_checkpoint` puede recuperar el estado previo.

Rutas protegidas contra escritura desde tools:

- `.git`
- `.venv`
- `__pycache__`
- `.yarbis_checkpoints`
- `state.json`

Rutas omitidas del listado de archivos:

- `.git`
- `.venv`
- `__pycache__`
- `tests_runtime`
- `.yarbis_checkpoints`

## Autoconocimiento local

Al arrancar desde terminal, app de escritorio o servicio, Yarbis ejecuta un autoanalisis y guarda el resumen en `state.json`.

Ese resumen incluye:

- identidad del proyecto tomada del README
- inventario compacto de archivos fuente
- rama y commit de Git, si estan disponibles
- sistema operativo
- host y modelo de equipo cuando Windows lo reporta
- CPU, RAM, disco del workspace y GPU cuando Windows lo reporta
- version de Python, ejecutable, PID y cwd
- modelo Ollama configurado

Durante una sesion, puedes pedir:

- `self`
- "hazte un autoanalisis"
- "refresca tu autoanalisis"
- "que sabes de ti"

## Archivos runtime y git

Archivos no versionados:

- `state.json`
- `state.json.tmp`
- `.yarbis_checkpoints/`
- `.yarbis_runtime/`
- `tests_runtime/`
- `__pycache__/`
- `.venv/`
- `*.log`

El servicio usa `.yarbis_runtime/` para PID y log. La marca `.yarbis_runtime/service.stop` queda como mecanismo de parada para ejecuciones manuales de `yarbis_service.py`; cuando corre bajo SCM, la parada llega por el control `STOP` del Service Control Manager. Los checkpoints de autoedicion viven en `.yarbis_checkpoints/`.

## Estructura principal

- `agent.py`: prompt, tool loop, ciclos autonomos y comunicacion con Ollama
- `main.py`: interfaz de terminal
- `yarbis_desktop.py`: interfaz grafica Tkinter
- `ui_theme.py`, `ui_dialogs.py`, `ui_settings_dialogs.py`: tema y dialogos de la UI
- `yarbis_service.py`: loop Python de fondo ejecutado por el host del servicio
- `service_manager.py`: inicio, parada, estado, autostart y health del servicio
- `service_host/`: host nativo .NET que se registra ante SCM y controla el proceso Python
- `memory.py`: estado persistente, defaults, normalizacion y transacciones
- `tools.py`: herramientas internas del agente
- `internet.py`: busqueda y lectura web segura
- `notifications.py`: Windows, ntfy y Telegram
- `telegram_inbox.py`: polling y comandos remotos de Telegram
- `telegram_format.py`: formato compartido de respuestas Telegram
- `intent_text.py`: normalizacion compartida de intenciones
- `secrets_redaction.py`: redaccion de tokens en logs, eventos y errores
- `activity.py`: actividad legible y eventos JSONL
- `power.py`: apagado, reinicio y cancelacion de acciones de energia de Windows
- `self_knowledge.py`: autoanalisis local
- `abrir_yarbis.vbs` y `abrir_yarbis.cmd`: lanzadores de escritorio
- `tests/`: suite de tests con `unittest`

## Observabilidad

Yarbis escribe actividad humana en `.yarbis_runtime/activity.log` y eventos estructurados en `.yarbis_runtime/events.jsonl`. Los eventos incluyen `operation_id` cuando aplican a ciclos, respuestas, pulsos proactivos o jobs remotos de Telegram, y pasan por redaccion de secretos antes de guardarse.

La funcion `health_status()` de `service_manager.py` reporta servicio, Telegram, pulso proactivo, operacion activa y modelo Ollama. La UI muestra un resumen de ese health en el panel principal.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Validacion completa antes de cerrar cambios:

```powershell
.\scripts\check.ps1
```

El script ejecuta la suite Python con `unittest` y compila `service_host\YarbisServiceHost.csproj` con .NET 8. Tambien puedes usar la tool interna `run_project_tests`, que ejecuta `unittest` dentro del workspace.

Lint gradual:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\lint.ps1
```

Ruff esta configurado solo con reglas seguras iniciales.

## Limitaciones actuales

- El servicio usa SCM, pero por defecto corre como `LocalSystem`; algunos recursos de usuario, como notificaciones interactivas de Windows, pueden no comportarse igual que en la app de escritorio.
- La autonomia depende del modelo local disponible en Ollama y de la calidad del objetivo inicial.
- La web solo cubre busqueda y lectura de paginas publicas; no hace navegacion completa ni automatizacion de navegador.
- Telegram procesa mensajes de texto, no adjuntos.
- Las tools no ejecutan comandos arbitrarios del sistema; solo leen/escriben dentro del workspace y ejecutan tests Python del proyecto.
- No integra aun calendario, correo, filesystem externo al workspace ni acciones del sistema fuera del alcance documentado.
