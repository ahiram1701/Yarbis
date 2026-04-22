# Yarbis

Yarbis es un agente local para convertir un objetivo general en trabajo accionable usando Ollama, memoria persistente y herramientas seguras de filesystem.

En esta version ya funciona mas como un agente personal local:

- Mantiene un perfil personal con preferencias y restricciones.
- Guarda notas persistentes para no perder contexto entre ciclos.
- Lleva una cola de tareas con estados (`pending`, `in_progress`, `blocked`, `done`).
- Puede sostener un plan actual de varios pasos.
- Tiene un modo autonomo que ejecuta varios ciclos y puede detenerse cuando ya no quedan tareas abiertas.

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
- `reply`: envia una respuesta libre al agente y ejecuta un ciclo con esa informacion
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

Si el agente te hace una pregunta para destrabar el trabajo, respondela y Yarbis retomara el flujo desde ahi en el siguiente ciclo.

Ejemplos de cosas que puedes guardar en `profile`:

- Preferencias: "respuestas breves, soluciones locales, prioridad a Python"
- Restricciones: "no usar nube, no borrar archivos, no tocar .git sin pedirlo"

El agente tambien puede mantener ese contexto por si mismo durante la ejecucion usando sus propias tools internas (`add_task`, `update_task_status`, `save_note`, `set_plan`, etc.).

## Ajustes utiles para PCs lentas

Puedes cambiar modelo y timeout sin editar el codigo:

```powershell
$env:YARBIS_MODEL="qwen3.5:2b"
$env:YARBIS_OLLAMA_TIMEOUT_SECONDS="900"
.venv\Scripts\python.exe yarbis_desktop.py
```

Si tu equipo va justo de CPU o RAM, suele ayudar mucho subir el timeout y evitar dejar `auto` corriendo demasiados ciclos seguidos.

## Endurecimiento incluido

- Las tools solo pueden leer y escribir dentro del workspace.
- Las lecturas y escrituras tienen limites de tamano para evitar inflar `state.json`.
- El estado se normaliza y recorta antes de persistirse.
- Los errores de Ollama ya no tumban la aplicacion completa.

## Limitaciones actuales

- Sigue siendo un agente local de escritorio o terminal, no un daemon del sistema operativo.
- No integra aun calendario, correo, navegador ni comandos del sistema fuera del workspace.
- Su autonomia depende del modelo disponible en Ollama y de la calidad del objetivo inicial.

## Tests

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
