# Yarbis

Yarbis es un agente local para convertir un objetivo general en trabajo accionable usando Ollama u OpenRouter, memoria persistente, herramientas de filesystem/sistema, busqueda web controlada, navegador automatizable y notificaciones opcionales.

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
- guarda proyectos de ideas para explorar producto, negocio o vida personal con direcciones creativas, criterios, riesgos, preguntas y proximos pasos
- ejecuta un ciclo controlado o varios ciclos en modo autonomo
- se detiene cuando necesita una respuesta del usuario, cuando ya no quedan tareas abiertas o cuando el ciclo ya cerro sin seguimiento util
- puede buscar informacion publica con DuckDuckGo HTML y leer paginas web publicas bajo una politica persistente
- puede editar archivos dentro del workspace o rutas externas con checkpoint previo, vista previa y diff resumido
- puede leer/escribir rutas externas al workspace cuando indicas una ruta absoluta
- puede ejecutar comandos del sistema con timeout y salida acotada
- puede automatizar un navegador real con Playwright para navegar, hacer clicks, completar formularios, leer texto y tomar capturas
- puede crear eventos `.ics`, preparar correos `mailto:` y abrir rutas/URLs con manejadores locales
- puede preparar contenido para redes sociales, guardar drafts, conectar cuentas Meta/LinkedIn por OAuth local y publicar en Facebook Pages, Instagram profesional o LinkedIn con confirmacion explicita
- puede asistir publicaciones en perfil personal de Facebook copiando el texto y abriendo Facebook o Share Dialog, sin publicar automaticamente
- puede restaurar checkpoints y ejecutar tests del proyecto
- puede trabajar como agente de coding sobre un repositorio local activo en modo `propose_first`: inspecciona archivos, genera propuestas con diff y aplica cambios solo tras aprobacion
- ejecuta un autoanalisis de identidad, codigo fuente, sistema operativo y hardware al arrancar
- puede recibir y responder mensajes por Telegram cuando ese canal esta configurado, incluidas notas de voz
- puede ejecutarse como Windows Service y arrancar con Windows desde SCM
- puede observar senales locales seguras de la PC para enriquecer el pulso proactivo: presencia/idle, proceso en primer plano si esta permitido, salud del sistema y cambios recientes del workspace

## Requisitos

- Windows para la app de escritorio, notificaciones nativas y arranque con Windows
- Python 3.11 o superior
- .NET SDK 8 para compilar el host nativo del servicio SCM
- Ollama ejecutandose localmente, acceso directo a Ollama Cloud, u OpenRouter con API key
- un modelo disponible en el proveedor elegido; por defecto inicial se usa Ollama con `qwen3.5:2b`
- Microsoft Edge/Chrome o Chromium instalado para automatizacion con Playwright
- permisos de administrador para instalar, quitar o reconfigurar el servicio en SCM

Dependencias Python declaradas:

- `ollama==0.6.1`
- `win11toast==0.36.3`
- `playwright>=1.45,<2`
- `pystray>=0.19`
- `Pillow>=10`
- `faster-whisper>=1.1,<2`
- `pyttsx3>=2.99,<3`
- `sounddevice>=0.5,<1`
- `imageio-ffmpeg>=0.6,<1`
- `pykokoro>=0.6,<1`

## Instalacion

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Si no quieres usar Edge/Chrome instalado y prefieres el Chromium administrado por Playwright:

```powershell
python -m playwright install chromium
```

Tambien puedes preparar una copia nueva con:

```powershell
.\scripts\setup.ps1
```

El script crea `.venv` si hace falta, instala dependencias y revisa Ollama y .NET.

## Actualizacion

La forma recomendada de actualizar una instalacion existente es desde la app:

1. Abre Yarbis.
2. Usa `Mantenimiento` -> `Actualizar Yarbis`.
3. Confirma el cierre de la ventana actual.
4. Espera la ventana externa de PowerShell. Si el servicio SCM esta instalado, Windows pedira permisos de administrador.
5. Cuando el script termine bien, Yarbis se abre de nuevo automaticamente.

Tambien puedes actualizar manualmente desde PowerShell, en la carpeta del proyecto:

```powershell
.\scripts\update.ps1
```

Por defecto el actualizador usa `origin/main` y solo acepta fast-forward, para evitar merges inesperados:

```powershell
.\scripts\update.ps1 -Remote origin -Branch main
```

Si una copia no tiene remote `origin`, intenta usar `YARBIS_UPDATE_SOURCE` y despues el repo fuente `C:\DEV\Github\yarbis`. Si ese repo fuente tiene `origin`, usa su URL para traer cambios. Tambien puedes indicar una URL o ruta directa:

```powershell
.\scripts\update.ps1 -Remote https://github.com/ahiram1701/yarbis.git -Branch main
```

Si necesitas salir de una emergencia y ya validaste el cambio por otro camino, puedes omitir los checks:

```powershell
.\scripts\update.ps1 -SkipChecks
```

El boton de la app usa internamente:

```powershell
.\scripts\update.ps1 -RestartDesktop
```

Requisitos previos:

- Git disponible en `PATH` y acceso al remote configurado.
- Python disponible si `.venv` no existe todavia.
- `.venv` creado o permiso para que el actualizador lo cree.
- Permisos de administrador cuando el servicio SCM `Yarbis` esta instalado, porque debe detenerlo, reconfigurarlo y volverlo a iniciar.
- .NET SDK 8 si quieres validar o recompilar el host nativo del servicio.

Que conserva:

- `state.json`: objetivo, historial reciente, perfil, notas, tareas, Telegram, notificaciones, modelo y configuracion local.
- `.yarbis_runtime/`: credenciales, configuracion runtime, propuestas, logs, actividad, eventos y archivos operativos no versionados.
- `.yarbis_checkpoints/`: checkpoints de autoedicion.
- `.yarbis_memory_backups/`: paquetes portables de respaldo y trasplante de memoria.
- La configuracion del servicio SCM, incluida la cuenta existente; el actualizador solo refresca `binPath`, arranque automatico/manual y el host publicado.

Que puede cambiar:

- Codigo fuente versionado.
- Dependencias Python instaladas en `.venv`.
- Documentacion.
- Host nativo publicado en `.yarbis_runtime/service_host/`.

Si detecta cambios locales versionados o no versionados, el actualizador los guarda temporalmente con `git stash --include-untracked`, trae la actualizacion y luego intenta reaplicarlos con `git stash pop --index`. La memoria y configuracion local (`state.json`, `.yarbis_runtime/`, `.yarbis_checkpoints/`, `.yarbis_memory_backups/` y `tests_runtime/`) queda fuera del stash y se protege con un respaldo pre-update. No descarta trabajo local automaticamente. Si al reaplicar hay conflictos, deja el arbol de Git en estado conflictivo, conserva el stash y no reinicia el servicio hasta que resuelvas los conflictos.

Flujo interno del actualizador:

1. Valida que la carpeta sea un repo Git.
2. Resuelve y valida la fuente de actualizacion antes de tocar servicio o cambios locales.
3. Lee si el servicio SCM y el helper de contexto local estan activos.
4. Detiene el helper y el servicio si estaban corriendo, para congelar la memoria antes de tocar el codigo.
5. Copia `state.json` a `.yarbis_runtime/updates/state-AAAAMMDD-HHMMSS.json`, respalda configuracion runtime util y crea un respaldo portable de memoria.
6. Si hay cambios locales fuera de memoria/runtime, los guarda en un stash con nombre `yarbis-update-AAAAMMDD-HHMMSS`.
7. Mantiene `state.json` y carpetas runtime fuera del stash.
8. Ejecuta `git fetch` y `git merge --ff-only FETCH_HEAD`.
9. Instala dependencias con `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
10. Ejecuta `.\scripts\check.ps1`, salvo que uses `-SkipChecks`.
11. Reaplica el stash local con `git stash pop --index`.
12. Restaura configuracion local respaldada y verifica que `state.json` siga identico al respaldo pre-update; si falta o cambio durante la actualizacion, lo restaura automaticamente.
13. Si no hay conflictos, recompila/reconfigura el servicio SCM y reinicia lo que estaba activo.
14. Imprime un resumen con commit anterior, commit remoto, commit actual, checks, dependencias, cambios locales, servicio y respaldo de estado.

Recuperacion si algo falla:

- Lee el error completo en la ventana de PowerShell; normalmente indica si faltan permisos, Git, Python, .NET o si hay cambios locales.
- Si fallo por dependencias, ejecuta `.\scripts\setup.ps1` y luego repite `.\scripts\update.ps1`.
- Si fallo al reaplicar cambios locales, revisa `git status`, resuelve conflictos y despues inicia Yarbis o el servicio de nuevo.
- Para inspeccionar el respaldo temporal usa `git stash list`, `git stash show --stat 'stash@{N}'` y, si necesitas reaplicarlo manualmente, `git stash pop 'stash@{N}'`.
- Si el servicio quedo detenido tras una falla posterior al cambio de codigo, abre Yarbis como administrador y usa `Servicio` -> `Iniciar servicio`, o ejecuta de nuevo el update cuando el problema este corregido.
- Si `state.json` quedara danado, el actualizador intenta restaurarlo automaticamente desde `.yarbis_runtime/updates/`. Si necesitas hacerlo a mano, cierra Yarbis y restaura el respaldo mas reciente desde esa carpeta.

Ejemplos comunes:

```powershell
# Servicio detenido o no instalado
.\scripts\update.ps1

# Servicio instalado o activo: abre PowerShell como administrador
.\scripts\update.ps1

# Actualizar y reabrir la app al terminar
.\scripts\update.ps1 -RestartDesktop

# Actualizar desde otra rama remota
.\scripts\update.ps1 -Remote origin -Branch feature-x

# Saltar checks solo si ya sabes por que lo necesitas
.\scripts\update.ps1 -SkipChecks
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

- completar un primer uso guiado cuando aun no hay objetivo
- cambiar el objetivo
- cambiar proveedor, modelo y timeout
- ejecutar un ciclo
- ejecutar modo autonomo hasta terminar o indicando un numero explicito de ciclos
- responder preguntas pendientes
- enviar contexto libre al agente
- editar perfil
- crear, ver y eliminar notas
- crear tareas manuales
- revisar estado y actividad
- alternar tema claro/oscuro
- configurar y probar notificaciones
- conectar cuentas sociales y revisar publicaciones pendientes
- activar o desactivar el servicio de fondo
- configurar si el servicio se abre al iniciar Windows
- configurar el pulso proactivo del servicio
- configurar el contexto local persistente que alimenta el pulso proactivo
- configurar la UI movil por Tailscale con PIN local
- quitar el servicio de SCM
- actualizar Yarbis desde GitHub con un actualizador externo seguro

Si `state.json` aun no tiene objetivo, la app abre un asistente inicial. Define objetivo,
modelo local y contexto minimo, y puede ejecutar el primer ciclo al guardar. El panel
`Preparacion` muestra si falta algo para usar Yarbis ahora, por ejemplo objetivo, modelo
Ollama o dependencias.

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
- consume el snapshot de contexto local si la app de escritorio esta capturando senales recientes
- recupera al arrancar respuestas del usuario que hayan quedado guardadas pero sin ciclo completado
- evita que la app de escritorio o la terminal inicien un segundo lector de Telegram

El boton `Instalar e iniciar` compila el host con `dotnet publish` si hace falta, crea el servicio en SCM y despues lo inicia. `Detener servicio` manda la parada a SCM. `Quitar de SCM` detiene el servicio si hace falta y elimina su registro.

La casilla `Iniciar con Windows` cambia el tipo de arranque del servicio entre `auto` y `demand` usando SCM. Si el servicio aun no esta instalado, la casilla aparece deshabilitada; primero usa `Instalar e iniciar`.

Estas acciones suelen requerir ejecutar Yarbis como administrador. Al instalar desde la app puedes indicar la cuenta SCM del servicio (`DOMINIO\usuario` o `.\usuario`) y su password para que el proceso de fondo corra bajo esa identidad. Si dejas cuenta y password vacios, SCM usa `LocalSystem`.

La proactividad 24/7 del servicio esta activa por defecto. Tras una espera inicial, el servicio agrega un pulso de contexto al historial y ejecuta un ciclo autonomo con acceso completo a las herramientas del agente: archivos, comandos, tests/builds, internet, navegador, calendario, correo y aperturas del sistema. Si Yarbis necesita un dato que no puede obtener con herramientas, registra una pregunta pendiente y notifica por los canales configurados.

El contexto local de la PC se captura desde un helper de bandeja en la sesion interactiva del usuario, no desde SCM. Al instalar o iniciar el servicio desde la app, Yarbis crea/actualiza la tarea programada `YarbisLocalContext` para lanzar `.venv\Scripts\pythonw.exe pc_context_tray.py` al iniciar sesion. El helper sigue vivo aunque cierres la app de escritorio y solo captura cuando el servicio `Yarbis` esta activo y el contexto local esta habilitado. Si cierras sesion de Windows, el contexto interactivo se suspende hasta el siguiente inicio de sesion.

El contexto local tiene su propia configuracion. En modo seguro, Yarbis registra solo senales resumidas: presencia/idle, proceso en primer plano, salud de energia/memoria/disco y cambios recientes del workspace. Los titulos de ventana estan desactivados por defecto y solo se incluyen en modo detallado. Este modo controla el snapshot observado, no limita las herramientas disponibles para el pulso proactivo.

Si el servicio se detiene mientras procesa una respuesta del usuario, esa respuesta queda en el historial. En el siguiente arranque o pulso, Yarbis detecta si el ultimo mensaje del usuario no tiene respuesta del asistente y ejecuta primero ese ciclo pendiente antes de agregar trabajo proactivo nuevo.

Puedes ajustar el pulso desde la app con `Servicio` -> `Configurar pulso`. Los cambios se guardan en `state.json`; el servicio los lee en caliente para el intervalo y los ciclos. La espera inicial aplica al siguiente arranque del servicio. Tambien puedes ajustar las senales locales desde `Servicio` -> `Contexto local`; el observador se activa o detiene mientras la app de escritorio esta abierta.

La UI movil se configura desde `Servicio` -> `UI movil`. Al activarla debes definir un PIN local, un puerto por defecto `8787` y el timeout de operaciones moviles, por defecto 1800 segundos. El servicio la publica en `127.0.0.1` y, si Tailscale esta disponible, en la IP Tailscale de la PC, por ejemplo `http://100.99.240.111:8787`. Safari en iPhone puede entrar a esa URL cuando el telefono esta en la misma tailnet. Las sesiones usan cookie `HttpOnly`, CSRF en acciones POST y el PIN queda guardado como hash PBKDF2 en `state.json`.

El proveedor por defecto y su modelo tambien se pueden cambiar desde la app con `Modelo` -> `Modelo y timeout`. Ollama sigue siendo el default inicial; OpenRouter queda disponible con configuracion separada de modelo, host, fallbacks, API key env y timeout. Esos cambios se guardan en `state.json` y se aplican al siguiente ciclo, tanto en la app como en el servicio.

Cuando Yarbis aparece como `Pensando`, el boton `Detener pensando` solicita parar la operacion en curso. Si hay una llamada activa al proveedor de modelo, Yarbis cierra o reemplaza el cliente y el ciclo termina como detenido en cuanto la llamada libera el control.

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

## Ideas y proyectos

Yarbis puede tratar una idea como un proyecto vivo antes de convertirla en tareas. Para producto, negocio o proyectos personales, el flujo recomendado es:

1. Describe la idea aun si esta borrosa.
2. Yarbis abre varias direcciones creativas, supuestos, riesgos, preguntas abiertas y criterios de exito.
3. Cuando una direccion tenga sentido, Yarbis la guarda o actualiza como proyecto de idea.
4. Al decidir ejecutar, Yarbis activa el proyecto, copia sus proximos pasos al plan actual y crea tareas sin duplicar.

Desde la app usa `Objetivo y memoria de trabajo` -> `Ideas/proyectos`. En la UI movil, la vista `Contexto` muestra tarjetas de proyectos y permite crear, editar o activar. Por lenguaje natural puedes pedir cosas como: `Explora esta idea como proyecto`, `guarda estas alternativas`, `actualiza la direccion elegida` o `activa este proyecto`.

La UI movil tambien incluye la vista `Visual` para trabajar esos proyectos como mesa visual. Desde ahi puedes crear boards por proyecto con plantillas de canvas de idea, matriz de decision, roadmap/kanban y mapa mental; mover nodos, editar textos, ajustar zoom, auto-organizar y exportar HTML/SVG/JSON en `.yarbis_runtime/visual_boards/`. Desde escritorio, el dialogo `Ideas/proyectos` ofrece `Visual web` para abrir esa vista sin duplicar el editor en Tkinter.

## Creacion de contenido para redes sociales

Yarbis puede ayudarte a planear contenido, redactar piezas por plataforma, guardar drafts, preparar publicaciones y publicar cuando haya una cuenta conectada. La publicacion real siempre queda bloqueada hasta que confirmes con la frase exacta `PUBLICAR <id>`.

Flujo recomendado:

1. Define el objetivo de contenido, por ejemplo: `Quiero preparar contenido para redes sociales de mi servicio de automatizacion para negocios locales`.
2. Da contexto de marca: nicho, audiencia, oferta, tono, restricciones, enlaces, hashtags prohibidos o aprobados y frecuencia deseada.
3. Pide un calendario, brief o lote de piezas: `Crea 10 ideas para LinkedIn e Instagram con captions y CTA`.
4. Cuando una pieza te guste, pide: `Guarda esto como draft social para LinkedIn` o `Prepara esta pieza para publicar en Instagram`.
5. Revisa el preview que devuelve Yarbis. Si esta correcto, confirma exactamente con `PUBLICAR <id>`.

### Conectar cuentas

Desde la app de escritorio usa `Redes sociales` -> `Conectar cuenta`.

- `meta` conecta Facebook Pages, Instagram profesional y perfil personal de Facebook en modo asistido.
- `linkedin` conecta perfiles u organizaciones donde la app tenga permisos.
- Necesitas crear una app en Meta o LinkedIn y proporcionar `Client/App ID` y `Client/App Secret`.
- Si usas el callback local por defecto, Yarbis abre el navegador y espera la autorizacion. Si el proveedor no acepta loopback o prefieres completar manualmente, pega la URL final de callback en el campo `Callback pegado` y vuelve a aceptar.
- Los tokens se guardan en `.yarbis_runtime/credentials/`; `state.json` solo conserva referencias y metadatos.

Despues de conectar, usa `Redes sociales` -> `Ver cuentas` para revisar los destinos disponibles.

### Publicar con confirmacion

Puedes pedirlo en lenguaje natural:

```text
Prepara este copy para publicar en la pagina de Facebook conectada: ...
```

Yarbis creara una publicacion pendiente y devolvera un preview con una frase como:

```text
PUBLICAR pub-abc123
```

Solo cuando respondas exactamente esa frase, Yarbis intentara publicar por API. Si falta cuenta conectada, media compatible, permisos o token valido, la publicacion queda detenida y el error se registra sin exponer secretos.

### Facebook personal

Meta no permite publicar automaticamente en perfiles personales por Graph API. Para ese caso, Yarbis usa publicacion asistida:

- prepara el copy, hashtags, link y checklist
- copia el texto al portapapeles
- abre Facebook o el Share Dialog
- deja que tu hagas el click final de publicar

Puedes usar `Redes sociales` -> `Abrir asistido` o pedir: `Abre esta pieza en Facebook personal asistido`.

### Buenas practicas

- Usa Facebook Pages para publicacion automatizada de negocio.
- Usa Instagram profesional si quieres publicar por API; Instagram requiere media publica (`media_url`) para crear el contenedor.
- Para LinkedIn con imagen, usa una ruta local de archivo (`media_path`) para que Yarbis suba la imagen antes de publicar.
- Programa recordatorios con calendario o tareas; Yarbis no autopublica desde el servicio sin confirmacion exacta.

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
- `/stop`: detiene la operacion en curso; tambien funcionan `/detener`, `/parar` y `/cancelar_operacion`
- `/goal nuevo objetivo`: cambia el objetivo
- `/objetivo nuevo objetivo`: alias de `/goal`
- `/run`: ejecuta un ciclo
- `/auto`: ejecuta modo autonomo hasta terminar
- `/auto 3`: ejecuta el numero indicado de ciclos
- `/proveedor ollama`: usa Ollama como proveedor por defecto
- `/proveedor openrouter`: usa OpenRouter como proveedor por defecto
- `/modelo llama3.2:3b`: cambia el modelo de Ollama
- `/timeout 900`: cambia el timeout del proveedor activo en segundos; tambien acepta valores como `15m`
- `/ollama llama3.2:3b 900`: cambia modelo y timeout juntos
- `/ollama local gpt-oss:120b-cloud`: usa el daemon local de Ollama; puede mezclar modelos locales y cloud si hiciste `ollama signin`
- `/ollama cloud gpt-oss:120b`: usa `https://ollama.com` directo; requiere `OLLAMA_API_KEY`
- `/ollama host https://ollama.com`: cambia solo el host
- `/ollama fallback qwen3.5:2b, gpt-oss:120b-cloud`: configura modelos de respaldo en orden
- `/openrouter proveedor/modelo`: configura OpenRouter y lo usa como proveedor por defecto
- `/voz auto`: activa voz y respuestas habladas opcionales por Telegram
- `/voz on`: activa entrada de voz
- `/voz off`: desactiva entrada y respuestas de voz
- `/voz status`: muestra configuracion de voz
- `/voz voces`: lista voces locales disponibles para TTS
- `/voz catalogo [es|en|all]`: lista voces Kokoro disponibles
- `/voz proveedor kokoro|sistema`: cambia entre voces del sistema y Kokoro local
- `/voz usar NUMERO`: cambia la voz del sistema/Telegram por numero o ID
- `/voz velocidad 190`: cambia la velocidad de lectura local
- `/voz callar`: desactiva futuras respuestas habladas por Telegram
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
- `/confirmar_apagado CODIGO`: confirma un apagado solicitado
- `/confirmar_reinicio CODIGO`: confirma un reinicio solicitado
- `/cancelar_apagado`: cancela un apagado programado
- `/cancelar_reinicio`: cancela un reinicio programado

Tambien puedes decir `guarda una nota: Titulo | contenido | categoria`, `ver notas`, `detente`
o `borra la nota note-123` para gestionar notas con lenguaje natural. Para apagado o reinicio remoto,
puedes decir `apaga la pc`, `Yarbis, reinicia pc` o `reinicia pc en 5 minutos`
sin depender del modelo local. Por seguridad, las solicitudes de apagado y reinicio no ejecutan
la accion inmediatamente: Yarbis responde con un codigo y debes confirmar con
`/confirmar_apagado CODIGO` o `/confirmar_reinicio CODIGO` dentro de 10 minutos.
Los mensajes de texto sin `/` se procesan como respuesta
o contexto libre cuando no coinciden con una accion remota explicita.

## Voz local

Yarbis puede entender voz sin APIs pagadas ni subir audio a terceros. Usa `faster-whisper`
en CPU para transcribir, `pyttsx3` para leer respuestas con la voz del sistema y
`pykokoro` para voces neuronales locales. `imageio-ffmpeg` convierte audio
cuando Telegram necesita una nota de voz.

El primer uso de transcripcion puede descargar el modelo local `base`. La configuracion
por defecto queda en `state.json` bajo `voice`: idioma `es`, `stt_model=base`,
`stt_compute_type=int8`, maximo 120 segundos, proveedor TTS `system`, velocidad `175`,
voz Kokoro opcional, voz del navegador opcional y respuestas de Telegram en modo `auto`.
Kokoro descarga sus pesos locales la primera vez que se usa una voz. Las voces visibles incluyen
espanol (`ef_dora`, `em_alex`, `em_santa`), ingles, frances, portugues, italiano, japones y mandarin.

Superficies disponibles:

- Telegram entiende `voice` y `audio`; siempre responde con texto y, en modo `auto`,
  tambien envia nota de voz cuando la respuesta es corta. Usa `/voz catalogo es`,
  `/voz proveedor kokoro`, `/voz usar NUMERO`,
  `/voz velocidad NUMERO` y `/voz callar` para ajustar o silenciar.
- La UI movil permite grabar en el compositor y transcribe en Yarbis mediante
  `/api/voice/transcribe`; tambien puede leer resultados con `speechSynthesis` del navegador,
  elegir motor `Sistema/Kokoro`, probar voz, elegir voz/rate/pitch
  en `Config` -> `Voz` y detener habla activa con `Detener habla`. Para Kokoro en Safari/iPhone,
  la UI web pide audio WAV porque iOS no reproduce OGG/Opus de forma consistente.
  Si iPhone/Safari bloquea el microfono por HTTP o por origen no seguro, usa `Grabar archivo`:
  abre la captura/subida de audio del sistema y reutiliza la misma transcripcion local.
- La app de escritorio tiene `Dictar` en el compositor, `Voz` para elegir motor, voz del sistema
  o Kokoro, velocidad y modo Telegram, `Voces Kokoro`, `Probar voz`,
  `Leer ultimo resultado` y `Detener voz`.

Por seguridad, puedes pedir apagado o reinicio por voz, pero la confirmacion final
`/confirmar_apagado CODIGO` o `/confirmar_reinicio CODIGO` debe escribirse como texto.

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
$env:YARBIS_OLLAMA_FALLBACK_MODELS="gpt-oss:120b-cloud"
$env:YARBIS_OLLAMA_HOST=""
$env:YARBIS_OLLAMA_API_KEY="ollama_cloud_api_key"
$env:YARBIS_OLLAMA_API_KEY_ENV_VAR="OLLAMA_API_KEY"
$env:OLLAMA_API_KEY="ollama_cloud_api_key"
$env:YARBIS_OLLAMA_TIMEOUT_SECONDS="900"
$env:YARBIS_MODEL_PROVIDER="ollama"
$env:YARBIS_OPENROUTER_HOST="https://openrouter.ai/api/v1"
$env:YARBIS_OPENROUTER_API_KEY="openrouter_api_key"
$env:YARBIS_EMPTY_RESPONSE_RETRIES="1"
```

Si aparece `No pude consultar Ollama en este ciclo: timed out`, primero confirma que Ollama este vivo con `ollama list` u `ollama ps`. Si el modelo tarda en cargar, calientalo una vez con `ollama run qwen3.5:2b`, aumenta `YARBIS_OLLAMA_TIMEOUT_SECONDS`, reinicia la app o el servicio para que tome la variable, o cambia temporalmente a un modelo mas ligero con `YARBIS_MODEL="qwen3.5:0.8b"`.

Para usar cloud hay dos rutas:

- Mantener host local vacio, ejecutar `ollama signin` y usar modelos con sufijo cloud, por ejemplo `gpt-oss:120b-cloud`. Esta es la forma mas comoda para mezclar modelo local primario y fallback cloud desde el mismo daemon.
- Configurar `YARBIS_OLLAMA_HOST="https://ollama.com"` o poner ese host en la app, pegar la API key directa en Yarbis o definir `YARBIS_OLLAMA_API_KEY`/`OLLAMA_API_KEY`, y usar el nombre cloud directo, por ejemplo `gpt-oss:120b`.

Para usar OpenRouter, selecciona `openrouter` en `Modelo y timeout`, define un modelo en formato de proveedor/ruta y pega tu API key en `API key directa`. Tambien puedes usar `YARBIS_OPENROUTER_API_KEY` o la variable indicada en `api_key_env_var` (por defecto `OPENROUTER_API_KEY`). El readiness valida API key y modelo, y no ejecuta `ollama list` cuando OpenRouter es el proveedor activo.

Para uso diario, prefiere el boton `Modelo y timeout` de la app o los comandos de Telegram `/proveedor`, `/modelo`, `/timeout`, `/ollama` y `/openrouter`. Las variables `YARBIS_MODEL_PROVIDER`, `YARBIS_MODEL`, `YARBIS_OLLAMA_FALLBACK_MODELS`, `YARBIS_OLLAMA_HOST`, `YARBIS_OLLAMA_API_KEY`, `YARBIS_OLLAMA_API_KEY_ENV_VAR`, `YARBIS_OLLAMA_TIMEOUT_SECONDS`, `YARBIS_OPENROUTER_HOST`, `YARBIS_OPENROUTER_API_KEY` y `YARBIS_OPENROUTER_TIMEOUT_SECONDS` quedan como override avanzado y, si estan definidas, pueden tener prioridad sobre lo guardado en la interfaz o Telegram.

Servicio proactivo, como override avanzado de la configuracion guardada:

```powershell
$env:YARBIS_SERVICE_PROACTIVE="1"
$env:YARBIS_SERVICE_PROACTIVE_INTERVAL_SECONDS="1800"
$env:YARBIS_SERVICE_PROACTIVE_CYCLES="unlimited"
$env:YARBIS_SERVICE_PROACTIVE_START_DELAY_SECONDS="60"
$env:YARBIS_SERVICE_PROACTIVE_MAX_RUNTIME_SECONDS="60"
```

Usa `YARBIS_SERVICE_PROACTIVE_CYCLES` con un numero positivo para limitar ciclos por pulso, o vacio/`none`/`unlimited` para ejecutar hasta terminar. Usa `YARBIS_SERVICE_PROACTIVE="0"` para dejar el servicio solo como inbox remoto y desactivar los ciclos autonomos periodicos.
Si quieres volver temporalmente a la whitelist segura antigua, define `YARBIS_PROACTIVE_SAFE_MODE="1"` en el entorno del servicio.

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
- proyectos de ideas
- plan actual
- pregunta pendiente
- limites de autonomia
- configuracion del pulso proactivo del servicio
- tema de la interfaz
- politica de internet
- proteccion de memoria, respaldo automatico y espejo externo opcional
- resumen de autoconocimiento
- configuracion de notificaciones

El estado se normaliza antes de guardarse para mantener estructura compatible y valores validos. Yarbis no recorta destructivamente historial, notas, tareas ni resultados persistentes; cuando necesita enviar contexto al modelo, mostrar previews, resumir diffs o devolver salidas de tools, aplica limites sobre esa salida derivada.

`state.json`, `state.json.tmp` y `state.json.tmp-*` estan ignorados por git.

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

Internamente el agente usa `update_internet_settings`, `web_search`, `fetch_web_page` y, cuando hace falta una sesion real con clicks, formularios o capturas, `browser_automation`.

## Proteccion, respaldo y trasplante de memoria

Yarbis protege `state.json` con escrituras atomicas, verificacion JSON posterior y respaldos automaticos redactados en cada cambio. Si `state.json` falta o queda danado, intenta restaurar automaticamente el respaldo valido mas reciente; si no hay ninguno, preserva una copia del archivo danado en `.yarbis_runtime/memory_recovery/` y arranca con defaults seguros.

Los respaldos se guardan por defecto en `.yarbis_memory_backups/` y quedan fuera de git. Puedes configurar un espejo externo desde `Memoria` -> `Proteccion`, por ejemplo una carpeta de OneDrive, USB o red. La perdida fisica del disco solo queda cubierta si ese espejo vive fuera del workspace local.

Formato del paquete:

- `format`: identificador del respaldo de memoria de Yarbis
- `schema_version`: version del formato
- `id` y `created_at`: identificador y fecha UTC
- `options`: opciones usadas al crear el respaldo
- `redacted_paths`: campos sensibles omitidos
- `state`: estado normalizado listo para importar

Los respaldos automaticos siempre redactan `notifications.ntfy.token`, `notifications.telegram.bot_token` y `notifications.telegram.pending_power_confirmation.token`. Usa `include_secrets=True` solo en un respaldo manual si necesitas un clon completo y vas a proteger el archivo resultante.

Para trasplantar memoria hay dos modos:

- `replace`: crea primero un respaldo local del estado actual y luego sustituye `state.json`. Si el respaldo origen tenia secretos redactados, conserva los secretos actuales del destino.
- `merge`: crea primero un respaldo local y fusiona perfil, notas, tareas, proyectos de ideas, mensajes, plan y autoconocimiento sin reemplazar configuracion local como modelo/proveedor, internet, notificaciones, UI, servicio o contexto local.

Desde la app de escritorio usa `Memoria` -> `Proteccion`, `Respaldar memoria`, `Trasplantar memoria` o `Verificar respaldos`. Desde lenguaje natural, el agente usa `memory_protection_status`, `update_memory_protection_settings`, `verify_memory_backups`, `create_memory_backup`, `list_memory_backups`, `inspect_memory_backup` e `import_memory_backup`.

## Tools internas

Yarbis expone al modelo estas herramientas:

- `agent_overview`: resumen del estado actual
- `self_overview`: identidad, codigo fuente y entorno local
- `update_profile`: perfil personal
- `update_internet_settings`: politica web
- `request_user_input`: registra una pregunta pendiente
- `save_note` y `list_notes`: memoria persistente
- `create_idea_project`, `list_idea_projects`, `get_idea_project`, `update_idea_project` y `promote_idea_project_to_work`: proyectos de ideas para divergencia creativa, decision y conversion a plan/tareas
- `create_project_visual_board`, `list_project_visual_boards`, `get_project_visual_board`, `update_project_visual_board` y `export_project_visual_board`: boards visuales por proyecto con exportacion HTML/SVG/JSON
- `add_task`, `list_tasks` y `update_task_status`: backlog de trabajo
- `set_plan`: plan actual
- `memory_protection_status`, `update_memory_protection_settings` y `verify_memory_backups`: proteccion automatica de memoria
- `create_memory_backup`, `list_memory_backups`, `inspect_memory_backup` e `import_memory_backup`: respaldo y trasplante manual de memoria
- `coding_set_workspace`, `coding_workspace_overview`, `coding_list_files` y `coding_read_text_file`: contexto de un repositorio local activo
- `coding_propose_changes`, `coding_propose_text_file`, `coding_list_proposals`, `coding_get_proposal`, `coding_apply_proposal` y `coding_discard_proposal`: propuestas de cambios de codigo en modo `propose_first`
- `coding_git_status`, `coding_git_diff`, `coding_detect_validation_command`, `coding_update_validation_command` y `coding_run_validation`: estado Git, diff y validaciones dentro del repo activo
- `list_files` y `read_text_file`: lectura de rutas del workspace o del filesystem local
- `write_text_file`: escritura con checkpoint y diff
- `list_checkpoints` y `restore_checkpoint`: recuperacion de cambios
- `run_project_tests`: validacion con `unittest`
- `run_project_check`: validacion completa con `unittest` y build del host .NET
- `web_search` y `fetch_web_page`: acceso web publico bajo politica
- `browser_automation`: navegacion real con Playwright para abrir paginas, hacer clicks, llenar formularios, leer texto y tomar capturas
- `run_system_command`: comandos arbitrarios del sistema con timeout y salida acotada
- `create_calendar_event`: genera archivos `.ics` y puede abrirlos con la app de calendario predeterminada
- `compose_email`: abre o prepara borradores `mailto:` con el cliente de correo predeterminado
- `open_system_target`: abre rutas, URLs o URIs con el manejador predeterminado del sistema
- `social_accounts_overview`: resume cuentas sociales, drafts y publicaciones pendientes
- `start_social_oauth`: conecta cuentas Meta o LinkedIn mediante OAuth local o callback pegado
- `save_social_draft` y `list_social_drafts`: gestionan drafts de contenido social
- `prepare_social_publication`, `list_social_publications` y `confirm_social_publication`: preparan, revisan y publican piezas sociales con confirmacion exacta `PUBLICAR <id>`
- `open_assisted_social_post`: copia el copy y abre Facebook/Share Dialog para publicacion asistida en perfil personal

## Autoedicion segura

Las tools de archivos pueden trabajar dentro del workspace o con rutas absolutas externas. Las escrituras mantienen checkpoint previo en `.yarbis_checkpoints/`.

## Modo coding

Yarbis puede usar un repositorio local como workspace de codigo activo. Primero configura la carpeta con lenguaje natural o desde terminal con:

```powershell
python main.py
>>> coding workspace C:\ruta\al\repo
```

En este modo el flujo por defecto es `propose_first`: Yarbis no escribe directamente en archivos del repo activo con `write_text_file`; crea propuestas persistidas en `.yarbis_runtime/coding_proposals/` con contenido previo, contenido propuesto y diff. Las propuestas nuevas usan `schema_version=2` y pueden agrupar varios archivos como una unidad de trabajo: creacion, actualizacion o borrado de archivos de texto UTF-8.

Puedes revisar propuestas con `coding proposals`, abrir una con `coding get <id>`, aplicarla con `coding apply <id>` o descartarla con `coding discard <id>`. Al aplicar, Yarbis hace preflight de todos los archivos, bloquea la aplicacion si algo cambio desde la propuesta, crea checkpoints por archivo y limpia la propuesta pendiente. Las propuestas v1 de un archivo siguen siendo legibles y aplicables.

La validacion puede detectarse y guardarse por repo:

```powershell
>>> coding validation
>>> coding validation "python -m unittest discover -s tests"
>>> coding validate <id>
```

`coding validation` intenta detectar `scripts/check.ps1`, proyectos Python con `pyproject.toml` y `tests`, `package.json` o proyectos .NET. `coding validate [id]` usa el comando explicito, el guardado o el detectado, guarda el ultimo resultado en `state["coding"]["last_validation"]` y puede asociarlo a una propuesta.

El mismo flujo esta disponible en escritorio desde `Workspace de codigo` y `Propuestas`; en movil puedes elegir workspace, guardar comando de validacion, ver/aplicar/descartar/validar propuestas; por Telegram existen `/coding`, `/coding propuestas`, `/coding ver <id>`, `/coding aplicar <id>`, `/coding descartar <id>`, `/coding validar [id]` y `/coding workspace <ruta>`.

Limites de salida actuales:

- lectura de archivos: completa por defecto; opcionalmente acotada hasta 16,000 bytes con `max_bytes`
- escritura por operacion: 64,000 bytes
- vista previa de escritura: 600 caracteres
- diff resumido: 160 lineas
- salida de tests: 6,000 caracteres
- salida de comandos del sistema: 12,000 caracteres
- listado de archivos: 200 elementos

Antes de escribir, `write_text_file` crea un checkpoint en `.yarbis_checkpoints/`. Si un cambio sale mal, `restore_checkpoint` puede recuperar el estado previo.

Rutas del workspace protegidas contra escritura desde tools:

- `.git`
- `.venv`
- `__pycache__`
- `.yarbis_checkpoints`
- `.yarbis_memory_backups`
- `state.json`

Rutas omitidas del listado de archivos:

- `.git`
- `.venv`
- `__pycache__`
- `tests_runtime`
- `.yarbis_checkpoints`
- `.yarbis_memory_backups`

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
- proveedor/modelo configurado

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
- `.yarbis_runtime/pc_context_helper.pid`
- `.yarbis_runtime/pc_context_helper.stop`
- `.yarbis_runtime/pc_context_helper.status.json`
- `tests_runtime/`
- `__pycache__/`
- `.venv/`
- `*.log`

El servicio usa `.yarbis_runtime/` para PID y log. La marca `.yarbis_runtime/service.stop` queda como mecanismo de parada para ejecuciones manuales de `yarbis_service.py`; cuando corre bajo SCM, la parada llega por el control `STOP` del Service Control Manager. Los checkpoints de autoedicion viven en `.yarbis_checkpoints/`.

## Estructura principal

- `agent.py`: prompt, tool loop, ciclos autonomos y comunicacion con el proveedor de modelo
- `main.py`: interfaz de terminal
- `yarbis_desktop.py`: interfaz grafica Tkinter
- `ui_theme.py`, `ui_dialogs.py`, `ui_settings_dialogs.py`: tema y dialogos de la UI
- `yarbis_service.py`: loop Python de fondo ejecutado por el host del servicio
- `service_manager.py`: inicio, parada, estado, autostart y health del servicio
- `service_host/`: host nativo .NET que se registra ante SCM y controla el proceso Python
- `pc_context.py`, `pc_context_tray.py`, `pc_context_runtime.py`: captura local interactiva, helper de bandeja y tarea programada
- `memory.py`: estado persistente, defaults, normalizacion, transacciones y recuperacion
- `memory_backup.py`: nucleo portable de respaldos, redaccion, espejo, verificacion y retencion
- `memory_transfer.py`: importacion, fusion y trasplante de respaldos
- `tools.py`: herramientas internas del agente
- `internet.py`: busqueda y lectura web segura
- `notifications.py`: Windows, ntfy y Telegram
- `telegram_inbox.py`: polling y comandos remotos de Telegram
- `telegram_format.py`: formato compartido de respuestas Telegram
- `voice.py`: STT/TTS local y utilidades de audio
- `intent_text.py`: normalizacion compartida de intenciones
- `secrets_redaction.py`: redaccion de tokens en logs, eventos y errores
- `credential_store.py`: almacen local de credenciales cifradas para tokens sociales
- `social_oauth.py`: OAuth local y descubrimiento de cuentas Meta/LinkedIn
- `social_publishing.py`: adaptadores de publicacion Meta, Instagram y LinkedIn
- `activity.py`: actividad legible y eventos JSONL
- `power.py`: apagado, reinicio y cancelacion de acciones de energia de Windows
- `self_knowledge.py`: autoanalisis local
- `abrir_yarbis.vbs` y `abrir_yarbis.cmd`: lanzadores de escritorio
- `tests/`: suite de tests con `unittest`

## Observabilidad

Yarbis escribe actividad humana en `.yarbis_runtime/activity.log` y eventos estructurados en `.yarbis_runtime/events.jsonl`. Los eventos incluyen `operation_id` cuando aplican a ciclos, respuestas, pulsos proactivos o jobs remotos de Telegram, y pasan por redaccion de secretos antes de guardarse.

La funcion `health_status()` de `service_manager.py` reporta servicio, Telegram, pulso proactivo, UI movil, operacion activa y proveedor/modelo activo. La UI muestra un resumen de ese health en el panel principal.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Validacion completa antes de cerrar cambios:

```powershell
.\scripts\check.ps1
```

El script protege `state.json` y runtime local util antes de validar, ejecuta la suite Python con `unittest`, compila `service_host\YarbisServiceHost.csproj` con .NET 8 y restaura la memoria/configuracion local al terminar aunque haya fallos. Tambien puedes usar la tool interna `run_project_tests`, que ejecuta `unittest` dentro del workspace.
Para una validacion equivalente desde el agente, usa `run_project_check`.

Lint gradual:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\lint.ps1
```

Ruff esta configurado solo con reglas seguras iniciales.

## Limitaciones actuales

- La autonomia depende del modelo disponible en el proveedor configurado y de la calidad del objetivo inicial.
- Telegram procesa texto, notas de voz y archivos de audio compatibles; otros adjuntos se ignoran.
- La automatizacion de navegador requiere Playwright y un navegador Chromium/Edge disponible.
- Calendario y correo se integran con archivos `.ics`, `mailto:` y manejadores locales; no leen buzones ni calendarios cloud por OAuth.
- La publicacion social real requiere apps, permisos y tokens validos del proveedor. Perfil personal de Facebook solo se maneja con publicacion asistida; Yarbis no publica automaticamente en perfiles personales.
