# Yarbis

Yarbis es un agente local para convertir un objetivo general en trabajo accionable usando Ollama, memoria persistente y herramientas seguras de filesystem.

En esta version ya funciona mas como un agente personal local:

- Mantiene un perfil personal con preferencias y restricciones.
- Guarda notas persistentes para no perder contexto entre ciclos.
- Lleva una cola de tareas con estados (`pending`, `in_progress`, `blocked`, `done`).
- Puede sostener un plan actual de varios pasos.
- Tiene un modo autonomo que ejecuta varios ciclos y puede detenerse cuando ya no quedan tareas abiertas.
- Puede buscar informacion publica y reciente en internet cuando hace falta para destrabar una tarea.
- Puede editar archivos del workspace con un flujo mas seguro: checkpoint previo, diff de cambios y restauracion.
- Puede validar cambios de codigo ejecutando los tests del proyecto.

## Requisitos

- Python 3.11 o superior
- Ollama ejecutandose localmente
- Un modelo disponible con el nombre configurado en `agent.py`

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Uso rapido sin terminal

Si ya tienes `.venv` y dependencias listas, ahora puedes abrir Yarbis con doble clic:

- `abrir_yarbis.vbs`: lanza la app de escritorio sin mostrar consola
- `abrir_yarbis.cmd`: alternativa visible por si quieres revisar errores de arranque

La interfaz de escritorio te deja:

- cambiar objetivo
- ejecutar un ciclo o varios en modo autonomo
- alternar entre modo oscuro y claro, recordando tu preferencia
- responder preguntas pendientes
- editar perfil
- guardar notas y tareas
- revisar el estado sin tocar la terminal
- recibir notificaciones nativas de Windows y, opcionalmente, avisos en iPhone via ntfy o Telegram cuando Yarbis termina el modo autonomo o necesita una respuesta tuya

## Uso por terminal

```bash
python main.py
```

Comandos disponibles:

- `goal`: actualiza el objetivo actual
- `run`: ejecuta un ciclo del agente
- `auto`: ejecuta varios ciclos seguidos
- `status`: muestra objetivo, perfil, plan, tareas y notas recientes
- `profile`: actualiza nombre, contexto, preferencias y restricciones
- `note`: guarda una nota rapida persistente
- `task`: agrega una tarea manual al backlog
- `reply`: envia una respuesta libre al agente; si habia una pregunta pendiente, reanuda el modo autonomo, y si no, ejecuta un ciclo con esa informacion
- `exit`: termina la sesion

Si Yarbis detecta que le falta un dato importante, ahora debe pedirlo en vez de inventarlo. Cuando eso pase:

- el modo `auto` se detiene para no seguir asumiendo cosas
- la pregunta queda registrada en el estado
- puedes responder con `reply` o escribiendo la respuesta directamente si hay una pregunta pendiente

Al cambiar `goal`, Yarbis reinicia el contexto operativo de ese objetivo:

- limpia mensajes conversacionales previos
- vacia tareas y plan actual
- conserva perfil y notas persistentes

## Como usarlo como agente personal local

Un flujo util suele ser:

1. Define tu objetivo con `goal`.
2. Carga tu contexto personal con `profile`.
3. Si hace falta, agrega notas o tareas manuales con `note` y `task`.
4. Ejecuta `run` para un paso controlado o `auto` para dejarlo avanzar solo varios ciclos.

Si el agente te hace una pregunta para destrabar el trabajo, respondela y Yarbis retomara automaticamente el modo autonomo desde ahi.

Ejemplos de cosas que puedes guardar en `profile`:

- Preferencias: "respuestas breves, soluciones locales, prioridad a Python"
- Restricciones: "no usar nube, no borrar archivos, no tocar .git sin pedirlo"

El agente tambien puede mantener ese contexto por si mismo durante la ejecucion usando sus propias tools internas (`add_task`, `update_task_status`, `save_note`, `set_plan`, etc.).

## Busqueda web bajo demanda

Yarbis ahora puede:

- buscar informacion publica y reciente con `web_search`
- leer paginas puntuales con `fetch_web_page`
- decidir por si mismo cuando conviene buscar afuera antes de pedirte un dato que en realidad es publico

La politica de internet queda persistida en el estado y por defecto usa:

- modo `auto`
- proveedor `duckduckgo_html`
- lectura solo de URLs `http` o `https`
- bloqueo de `localhost`, IPs privadas y dominios restringidos por politica

Si quieres cambiar esa politica, puedes pedirselo en lenguaje natural, por ejemplo:

- "desactiva internet por ahora"
- "limita internet a docs.python.org"
- "bloquea wikipedia.org en las busquedas"

## Autoedicion segura del proyecto

Yarbis ya puede trabajar sobre archivos del propio workspace, incluido su codigo fuente, pero ahora lo hace con mas control:

- Antes de escribir un archivo crea un checkpoint automatico en `.yarbis_checkpoints/`.
- Cada escritura devuelve una vista previa y un diff resumido del cambio.
- Si modifica codigo o tests, ahora esta instruido para ejecutar validaciones con los tests del proyecto.
- Si un cambio sale mal, puede listar checkpoints y restaurar un estado anterior del archivo.

Rutas protegidas actualmente:

- `.git`
- `.venv`
- `__pycache__`
- `.yarbis_checkpoints`
- `state.json`

Esto no convierte a Yarbis en un sistema infalible, pero si reduce bastante el riesgo de que una autoedicion deje el proyecto en peor estado sin una forma simple de volver atras.

## Ajustes utiles para PCs lentas

Puedes cambiar modelo y timeout sin editar el codigo:

```powershell
$env:YARBIS_MODEL="qwen3.5:2b"
$env:YARBIS_OLLAMA_TIMEOUT_SECONDS="900"
.venv\Scripts\python.exe yarbis_desktop.py
```

Si quieres recibir notificaciones en tu iPhone con ntfy:

1. Instala `ntfy` desde la App Store.
2. Suscribete a un topic dificil de adivinar, por ejemplo `yarbis-tu-topic-secreto`.
3. Abre Yarbis.
4. Pulsa `Notificaciones`.
5. Marca `Activar notificaciones` e `iPhone via ntfy`.
6. Escribe el mismo topic en `Topic`.
7. Guarda y pulsa `Probar notificacion`.

Yarbis recordara esta configuracion en `state.json`.

Si prefieres configurarlo desde PowerShell:

```powershell
$env:YARBIS_NOTIFICATION_CHANNELS="windows,ntfy"
$env:YARBIS_NTFY_TOPIC="yarbis-tu-topic-secreto"
.venv\Scripts\python.exe yarbis_desktop.py
```

Si solo quieres enviar al iPhone y no a Windows:

```powershell
$env:YARBIS_NOTIFICATION_CHANNELS="ntfy"
$env:YARBIS_NTFY_TOPIC="yarbis-tu-topic-secreto"
.venv\Scripts\python.exe yarbis_desktop.py
```

Opcionales utiles:

```powershell
$env:YARBIS_NTFY_SERVER="https://ntfy.sh"
$env:YARBIS_NTFY_PRIORITY="high"
$env:YARBIS_NTFY_TAGS="yarbis"
```

Usa un topic privado y largo. En `ntfy.sh`, si no configuras autenticacion, el topic funciona como secreto.

Si prefieres usar Telegram para recibir y responder desde tu iPhone:

1. Crea un bot con `@BotFather` y copia el token.
2. Abre Yarbis.
3. Pulsa `Notificaciones`.
4. Marca `Activar notificaciones` y `Telegram`.
5. Pega el `Bot token de Telegram`.
6. Guarda.
7. Abre el chat con tu bot en Telegram y envia `/start`.

Si dejas `Chat ID` vacio, Yarbis vinculara automaticamente el primer chat privado que escriba al bot. Despues podras:

- responder con texto libre cuando Yarbis te haga una pregunta
- usar `/status` para ver el estado actual
- usar `/run` para ejecutar un ciclo
- usar `/auto` o `/auto 3` para lanzar el modo autonomo

Si prefieres configurarlo desde PowerShell:

```powershell
$env:YARBIS_NOTIFICATION_CHANNELS="telegram"
$env:YARBIS_TELEGRAM_BOT_TOKEN="123456:tu-token"
.venv\Scripts\python.exe yarbis_desktop.py
```

Opcionales utiles:

```powershell
$env:YARBIS_TELEGRAM_CHAT_ID="123456789"
$env:YARBIS_TELEGRAM_TIMEOUT_SECONDS="10"
$env:YARBIS_TELEGRAM_POLL_TIMEOUT_SECONDS="25"
```

Si prefieres desactivar todas las notificaciones:

```powershell
$env:YARBIS_NOTIFICATIONS="0"
.venv\Scripts\python.exe yarbis_desktop.py
```

Si tu equipo va justo de CPU o RAM, suele ayudar mucho subir el timeout y evitar dejar `auto` corriendo demasiados ciclos seguidos.

## Endurecimiento incluido

- Las tools solo pueden leer y escribir dentro del workspace.
- Las lecturas y escrituras tienen limites de tamano para evitar inflar el contexto y el estado.
- Las escrituras crean checkpoints previos y muestran un diff resumido.
- Algunas rutas sensibles no pueden modificarse desde las tools (`.git`, `.venv`, `state.json`, etc.).
- Las consultas web solo aceptan URLs publicas `http/https` y bloquean `localhost` e IPs privadas o locales.
- El agente puede restaurar checkpoints y ejecutar tests del proyecto despues de tocar codigo.
- El estado se normaliza y recorta antes de persistirse.
- Los errores de Ollama ya no tumban la aplicacion completa.

## Limitaciones actuales

- Sigue siendo un agente local de escritorio o terminal, no un daemon del sistema operativo.
- No integra aun calendario, correo ni comandos del sistema fuera del workspace.
- La parte web sigue siendo limitada: busca y lee paginas publicas, pero no hace navegacion completa ni automatizacion del navegador.
- Su autonomia depende del modelo disponible en Ollama y de la calidad del objetivo inicial.

## Tests

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
