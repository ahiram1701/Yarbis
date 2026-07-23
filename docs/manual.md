# Manual de uso de Yarbis

Guia practica, paso a paso, para instalar, configurar y usar Yarbis. Cada
seccion sigue el patron **que es -> como se hace -> como verificar**. Para el
detalle interno ver la [documentacion tecnica](README.md).

> Regla de oro: **le hablas a Yarbis en lenguaje natural** y el elige las
> herramientas. Casi nada de este manual requiere memorizar comandos; sirve para
> saber que puedes pedirle y como preparar el entorno.

## Indice
1. [Primeros pasos](#1-primeros-pasos)
2. [Uso diario](#2-uso-diario)
3. [Elegir el proveedor de modelo](#3-elegir-el-proveedor-de-modelo)
4. [Canales de acceso](#4-canales-de-acceso)
5. [Servicio de fondo (24/7)](#5-servicio-de-fondo-247)
6. [Memoria profesional](#6-memoria-profesional)
7. [Capacidades avanzadas](#7-capacidades-avanzadas)
8. [Correr en Linux, macOS y Android](#8-correr-en-linux-macos-y-android)
9. [Red de nodos (malla)](#9-red-de-nodos-malla)
10. [Solucion de problemas](#10-solucion-de-problemas)

---

## 1. Primeros pasos

### Instalar (Windows)
1. Requisitos: Windows 10/11, Python 3.11+ y (opcional) Ollama si quieres un
   modelo local.
2. Clona el repo y crea el entorno:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```
3. Arranca la app de escritorio o la TUI (ver [Canales](#4-canales-de-acceso)).

### Instalar (Linux/macOS/Android)
Ver [seccion 8](#8-correr-en-linux-macos-y-android). En resumen: con un proveedor
de modelo en la nube, el core corre **sin instalar dependencias**.

### Primer ciclo
1. Elige un proveedor de modelo (seccion 3). Por defecto, Ollama local con
   `qwen3.5:2b`.
2. Dile a Yarbis tu objetivo: *"Tu objetivo es ayudarme a organizar mi semana."*
3. Pidele que ejecute un ciclo (boton/comando **ciclo**) o varios en **modo
   autonomo** (**auto**).
4. Yarbis actua con herramientas y se detiene cuando necesita tu respuesta, no
   quedan tareas o el ciclo cerro sin trabajo util.

**Verificar:** pide *"dame un resumen de tu estado"* (usa `agent_overview`) y
veras objetivo, tareas, notas y configuracion.

---

## 2. Uso diario

Todo esto se lo pides en lenguaje natural; los nombres de tools son referencia.

- **Objetivo:** *"cambia tu objetivo a ..."* (`update_goal`).
- **Tareas:** *"agrega una tarea: llamar al banco"* (`add_task`); *"marca la
  tarea X como hecha"* (`update_task_status`, estados pending/in_progress/
  blocked/done).
- **Notas:** *"guarda una nota importante: el deploy corre en un VPS con
  systemd"* — puedes pedir que sea **importante** o **fijada** para que no se
  pierda (`save_note` con `importance`/`pinned`).
- **Plan:** *"arma un plan de 3 pasos para ..."* (`set_plan`).
- **Ideas:** *"crea un proyecto de idea para una app de notas"*
  (`create_idea_project`), con direcciones, riesgos, preguntas y proximos pasos.
- **Perfil:** *"recuerda que soy A. Hiram, mi zona horaria es ..."*
  (`update_profile`, `set_timezone`).
- **Ciclo vs autonomo:** un **ciclo** hace un paso controlado; el **modo
  autonomo** encadena varios. Puedes **detener** el pensamiento en curso desde la
  app/Telegram.

**Verificar:** *"lista mis tareas"* / *"que notas tienes de X"* (`list_tasks`,
`memory_search`).

---

## 3. Elegir el proveedor de modelo

Yarbis soporta cuatro proveedores. Cambialos desde `Modelo y timeout` en la app,
los comandos de Telegram (`/proveedor`, `/modelo`, `/timeout`, `/ollama`,
`/openrouter`) o pidiendoselo a Yarbis.

| Proveedor | Cuando usarlo | Notas |
|---|---|---|
| **Ollama** (local/cloud) | Tienes GPU o quieres privacidad total. | Requiere el paquete `ollama`. Cloud: `ollama signin` + modelos `*-cloud`. |
| **OpenRouter** | Acceso a muchos modelos con una API key. | Solo `urllib`, sin dependencias. |
| **OpenAI-compatible** | Groq, DeepSeek, Together, LM Studio local... | Define `host` + key. Sin dependencias. |
| **Puter** | Modelos gratis "user-pays", sin API key de pago. | Via un Worker pasarela (ver README de Puter). |

**Clave:** con cualquier proveedor **en la nube**, el core corre **sin instalar
dependencias de terceros** (ideal para Termux/Raspberry/VPS). El paquete `ollama`
solo hace falta con el proveedor Ollama.

Config por variables de entorno: ver [configuracion.md](configuracion.md#modelo).

**Verificar:** el *readiness* de la app valida API key y modelo. Si aparece un
timeout de Ollama, ver [seccion 10](#10-solucion-de-problemas).

---

## 4. Canales de acceso

### App de escritorio (Windows)
GUI con vistas de estado, chat, instancias, contexto, ajustes y control del
servicio. Doble clic en el lanzador `abrir_yarbis.cmd`.

### TUI (terminal, multiplataforma)
Interfaz de terminal rica, abrible con doble clic:
- Windows: `abrir_yarbis_tui.cmd`
- Linux/macOS: `./abrir_yarbis_tui.sh`

Pestañas: Chat, Estado, Instancias, Contexto, Ajustes, Actividad. Atajos:
Ctrl+R (ciclo), Ctrl+G (auto), Ctrl+S (detener), F5 (refrescar), Ctrl+Q (salir).

### Telegram
1. Crea un bot con @BotFather y copia el token.
2. Configuralo: en la app (`Servicio` -> notificaciones) o con
   `YARBIS_TELEGRAM_BOT_TOKEN` y `YARBIS_TELEGRAM_CHAT_ID`.
3. El **servicio de fondo** mantiene vivo el inbox. Escribele al bot: texto,
   comandos (`/status`, `/run`, `/auto`, `/proveedor`, ...) o **notas de voz**
   (las transcribe).

**Verificar:** `/status` responde con el estado del agente.

### Voz local
STT con faster-whisper, TTS con edge-tts (o pyttsx3). Config en el bloque
`voice`. Incluye **conversacion en vivo** con wake phrase ("Yarbis") en escritorio
y movil.

### UI movil como PWA (iPhone, iPad, Android)
1. Activa la UI movil: `Servicio` -> `UI movil`, define un **PIN**, un puerto
   (default 8830) y el timeout.
2. Para acceso fuera de casa y HTTPS (necesario para el microfono y la PWA), usa
   **Tailscale**: con MagicDNS + `HTTPS Certificates`, Yarbis configura
   `tailscale serve` y publica en `https://<tu-host>.ts.net/`.
3. En el telefono abre esa URL HTTPS y **"Anadir a pantalla de inicio"**
   (iOS/Safari) o **"Instalar aplicacion"** (Android/Chrome). Se abre a pantalla
   completa como app.

El service worker cachea solo el shell; nunca muestra estado viejo del agente.

### Atajos de iOS (Siri, Share Sheet)
Lo mas cercano a "Yarbis nativo" en iPhone. Ver [seccion 7](#atajos-de-ios).

---

## 5. Servicio de fondo (24/7)

El servicio mantiene a Yarbis vivo: inbox de Telegram, **pulso proactivo**
(avanza el objetivo solo, cada cierto intervalo), coordinacion entre instancias y
sincronizacion de malla.

- **Windows:** desde la app, `Servicio` -> `Instalar e iniciar` (crea el Windows
  Service en SCM; puede pedir administrador). `Iniciar con Windows` cambia el
  arranque a automatico.
- **Linux/macOS/Android u otro SO:** pidele a Yarbis *"instala tu servicio de
  fondo y que arranque solo"* (`install_background_service`,
  `start_background_service`, `set_background_service_autostart`). Elige el backend
  correcto (systemd `--user`, launchd, runit de Termux o supervisor portable).

Ajusta el pulso desde `Servicio` -> `Configurar pulso` o las variables
`YARBIS_SERVICE_PROACTIVE_*`. Con `YARBIS_SERVICE_PROACTIVE="0"` el servicio queda
solo como inbox remoto.

**Verificar:** *"como esta tu servicio de fondo"* (`background_service_status`).

---

## 6. Memoria profesional

Yarbis no solo guarda: **recuerda por relevancia** y **no olvida el hilo largo**.

- **Recuperar:** *"que sabes sobre el deploy de produccion"* — recupera los
  recuerdos pertinentes por relevancia (`memory_search`), no solo los recientes.
- **Notas ricas:** al guardar, marca `importance` (0-3), `tags` y `pinned`. Una
  nota importante o fijada **no la desaloja** una trivial.
- **Consolidar:** *"consolida tu memoria"* fusiona notas casi-duplicadas
  (`memory_consolidate`).
- **Auditar:** *"haz una auditoria de tu memoria"* (`memory_audit`) reporta
  integridad, respaldos, tamanos y metricas.
- **Compactacion automatica:** cuando el historial crece, Yarbis resume lo viejo
  y conserva lo reciente, sin perder el hilo y sin inflar `state.json`.

### Respaldo y trasplante
- Respaldos automaticos rotativos en `.yarbis_memory_backups/`
  (`create_memory_backup`, `list_memory_backups`, `verify_memory_backups`).
- Para mover la memoria a otra copia/maquina, usa las herramientas de
  import/export (`import_memory_backup`). **No** copies el `state.json` con
  PowerShell (`Out-File` agrega BOM); Yarbis tolera el BOM y se auto-sana, pero es
  mejor usar sus herramientas.

**Verificar:** `memory_audit` no debe reportar avisos criticos.

---

## 7. Capacidades avanzadas

### Navegador y control de PC (off por defecto)
Activa con *"activa el control de PC"* (`set_computer_control`). Luego Yarbis
puede navegar con su propio navegador (Playwright): `browser_open`,
`browser_observe`, `browser_act`. Las acciones sensibles (Publicar/Pagar/Enviar)
piden confirmacion si `confirm_sensitive` esta activo. Nunca se usa en pulsos
proactivos.

### Redes sociales
Prepara contenido y publica con confirmacion: conecta cuentas Meta/LinkedIn por
OAuth (`start_social_oauth`) y publica en Facebook Pages, Instagram profesional o
LinkedIn (`confirm_social_publication`). En perfil personal de Facebook solo
publicacion **asistida** (`open_assisted_social_post`): Yarbis abre Facebook y
copia el texto, no publica solo.

### Internet
Busqueda y lectura de paginas bajo una politica persistente (`web_search`,
`fetch_web_page`, `update_internet_settings`): modo, dominios permitidos/
bloqueados, limites.

### Vision
*"analiza esta imagen"* con un modelo multimodal (`analyze_image`). Config en
`vision_status`/`vision_set_model`.

### Modo coding
Yarbis trabaja sobre un repositorio local en modo `propose_first`: inspecciona
(`coding_read_text_file`, `coding_search_text`), **propone** cambios con diff
(`coding_propose_edits`) y **aplica solo tras tu aprobacion**
(`coding_apply_proposal`). Puede correr tests/checks del proyecto.

### Atajos de iOS
1. Pide *"crea un token para Atajos"* (`shortcuts_create_token`) — se muestra una
   vez.
2. En la app Atajos, usa **"Obtener contenido de URL"** hacia
   `https://<tu-host>.ts.net/api/shortcut/ask`, metodo POST, cabecera
   `Authorization: Bearer <token>`, cuerpo `{"text": ..., "esperar": true}`, y
   "Hablar" el campo `reply`.
3. Recetas: "Preguntale a Yarbis" (Siri), "Guardar en Yarbis" (Share Sheet),
   "Analizar con Yarbis" (foto), "Estado de Yarbis" (boton de accion).

Endpoints: `/api/shortcut/{ask,status,note,task,image}`. Requiere la UI movil
encendida y la URL HTTPS.

### Coordinacion entre instancias
Si corres varias instancias (`YARBIS_INSTANCE`), pueden mensajearse
(`send_yarbis_message`) y una puede **responder por ti** para desbloquear a otra
que espera (`list_pending_user_questions`, `answer_instance_for_user`).

### Servidores MCP y autoevolucion
- **MCP** (off por defecto): conecta servidores MCP externos y sus tools quedan
  disponibles como `mcp__<servidor>__<tool>` (`set_mcp_enabled`, `mcp_add_server`,
  `mcp_connect`).
- **Autoevolucion** (off por defecto): Yarbis propone mejoras a su objetivo/
  memoria y **espera tu aprobacion** (`evolution_*`).

---

## 8. Correr en Linux, macOS y Android

El core es Python y corre como **proceso plano** fuera de Windows. Con un
proveedor de modelo en la nube **no necesitas instalar nada**.

### Linux / macOS
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # win11toast se omite fuera de Windows
./run_yarbis_service.sh            # instancia default, primer plano
./run_yarbis_service.sh trader --bg  # otra instancia en segundo plano
./abrir_yarbis_tui.sh              # la TUI es la UI multiplataforma
```
Servicio gestionado: pidele a Yarbis que instale el servicio nativo (systemd
`--user` en Linux, launchd en macOS), o escribe la unit a mano (ejemplos en el
README).

### Android (Termux)
```bash
pkg install python git
git clone <tu-repo-yarbis> && cd yarbis
python yarbis_service.py           # o la TUI: python yarbis_tui.py
```
Recomendado: `pkg install termux-api` (notificaciones y bateria) y
`termux-services` (arranque con runit). Yarbis detecta Termux (`android-termux`) y
desactiva lo que no aplica (escritorio, navegador, voz).

**iPhone/iPad:** no corren el core (Apple no permite procesos Python de fondo);
usa la **PWA** y los **Atajos** como cliente.

### Adaptacion automatica
En cualquier equipo, Yarbis **sondea** que puede hacer (`probe_device`) y adapta
sus capacidades. *"que perfil de dispositivo tienes"* (`device_profile_overview`)
y *"que me sugieres para este equipo"* (`device_adaptation_suggestions`).

---

## 9. Red de nodos (malla)

Conecta instancias de Yarbis en **distintas maquinas tuyas** para que se
descubran y se repartan trabajo. No es un virus: cada nodo lo enrolas tu con el
secreto de la red, es revocable, y no hay auto-propagacion.

1. `mesh_create_network("mi-red")` — genera el secreto (se muestra una vez).
2. `mesh_deploy_help` — te da el codigo del relay (un Worker de Puter) con el
   secreto ya inyectado; desplegalo en tu cuenta Puter.
3. `mesh_configure_relay("https://<tu-worker>.puter.work")` — fija la URL.
4. `mesh_enroll` — enrola este nodo (se anuncia con sus capacidades).
5. Repite en otras maquinas con el **mismo secreto**.

Uso: `mesh_list_nodes` (roster), `mesh_send` (mensaje directo) y **`mesh_delegate`**
— un nodo sin pantalla delega "toma una captura" a un nodo con pantalla, que la
ejecuta y responde por la malla.

**Verificar:** `mesh_status` dice si el relay es alcanzable y `mesh_list_nodes`
muestra los nodos enrolados.

---

## 10. Solucion de problemas

| Sintoma | Causa probable | Que hacer |
|---|---|---|
| `No pude consultar Ollama: timed out` | El modelo tarda en cargar o el daemon no responde. | `ollama list`/`ollama ps`; calienta con `ollama run <modelo>`; sube `YARBIS_OLLAMA_TIMEOUT_SECONDS`; reinicia el servicio; o usa un modelo mas ligero. |
| Una instancia "queda como nueva" | `state.json` con BOM (trasplante por PowerShell) o test que escribio el estado real. | Yarbis tolera el BOM y se auto-sana; usa `import_memory_backup` en vez de copiar con PowerShell. Restaura desde `.yarbis_memory_backups/`. |
| El servicio "murio" (exit 1, sin traceback) | Falta de RAM con varias instancias corriendo subprocesos pesados a la vez. | Serializa las operaciones pesadas; reduce instancias activas; cierra procesos. |
| La PWA no se instala / sin microfono | No estas sobre HTTPS. | Usa la URL HTTPS de Tailscale (`tailscale serve`), no `http://localhost`. |
| El pulso proactivo no hace acciones | Solo usa tools proactive-safe; muchas capacidades estan off por defecto. | Activa la capacidad (control de PC, etc.) y da un objetivo claro. |
| Faltan modulos en Linux | `win11toast`/GUI no aplican fuera de Windows. | Es esperado; el readiness lo tiene en cuenta. Usa un proveedor de nube para cero dependencias. |
| No encuentra el modelo configurado | El modelo no esta descargado (Ollama) o mal escrito. | Descargalo, corrige el nombre, o define un `fallback_models`. |

Para diagnostico general: `memory_audit` (salud de memoria),
`background_service_status` (servicio), `probe_device` (entorno) y los logs en
`.yarbis_runtime/service.log`.
