# Yarbis

Yarbis es un **agente personal local**: convierte un objetivo general en trabajo
accionable, con memoria persistente y herramientas seguras, corriendo en tu
propia maquina. Habla con modelos LLM a traves de cuatro proveedores (Ollama,
OpenRouter, cualquier endpoint OpenAI-compatible, o Puter), y funciona en
**Windows, Linux, macOS y Android (Termux)**.

> **Documentacion completa en [`docs/`](docs/README.md)** — este README es la
> portada con instalacion rapida.

## Que puede hacer

- **Memoria profesional**: recuperacion por relevancia (BM25 sin dependencias +
  embeddings opcional), compactacion del historial (nunca pierde el hilo largo),
  notas ricas (importancia/tags/fijado) y auditoria.
- **Autonomia**: ciclos controlados o modo autonomo; un pulso proactivo 24/7 que
  avanza el objetivo solo cuando corre como servicio.
- **Multiplataforma y adaptable**: detecta el dispositivo (con pantalla o no, RAM,
  bateria, Termux) y ajusta sus capacidades; el core corre **sin dependencias de
  terceros** con un proveedor de modelo en la nube.
- **Canales**: app de escritorio (Windows), TUI multiplataforma, Telegram (texto
  y voz), UI movil instalable como **PWA**, y **Atajos de iOS** (Siri/Share Sheet).
- **Herramientas**: filesystem y comandos, busqueda web bajo politica, navegador
  automatizable (Playwright), control de PC, vision por imagenes, voz local,
  redes sociales, calendario/correo, y modo coding sobre un repo (propose-first).
- **Coordinacion**: multiples instancias que se coordinan en una maquina, y una
  **malla de nodos** entre tus maquinas (con relay en Puter) que delega trabajo
  por capacidad.
- **Extensible**: cliente **MCP** para conectar servidores externos; autoevolucion
  que propone mejoras y espera tu aprobacion.

Todas las capacidades potentes (control de PC, MCP, malla, autoevolucion,
publicacion social) estan **apagadas por defecto**.

## Documentacion

| Documento | Contenido |
|---|---|
| **[Manual de uso](docs/manual.md)** | Guia paso a paso: instalar, canales, servicio, memoria, red de nodos, troubleshooting. |
| **[Arquitectura](docs/arquitectura.md)** | Como funciona por dentro: ciclo del agente, estado, capa por-SO, malla, memoria. |
| **[Configuracion](docs/configuracion.md)** | Cada bloque de `state.json` y cada variable de entorno. |
| **[Modulos](docs/modulos.md)** | Los ~45 modulos Python y su rol. |
| **[Herramientas](docs/herramientas.md)** | Las 146 tools del agente por area. |

## Requisitos

- **Windows** para la app de escritorio, notificaciones nativas y el servicio SCM.
  Linux/macOS/Android usan la TUI y el servicio nativo (ver el manual).
- **Python 3.11+**.
- Un **proveedor de modelo**: Ollama (local/Cloud), OpenRouter, cualquier endpoint
  OpenAI-compatible (Groq/DeepSeek/LM Studio...) o Puter. Con un proveedor en la
  nube, el core no necesita dependencias de terceros.
- Opcional segun capacidades: **.NET SDK 8** (host del servicio SCM en Windows),
  un navegador Chromium/Edge (Playwright), microfono (voz).

## Instalacion rapida

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

O prepara una copia nueva con `.\scripts\setup.ps1` (crea `.venv`, instala
dependencias y revisa Ollama y .NET). Para una instalacion minima sin GUI/voz/
navegador (Termux, Raspberry, VPS), usa `requirements-min.txt`.

**Linux/macOS/Android:** ver la [seccion 8 del manual](docs/manual.md#8-correr-en-linux-macos-y-android).

## Primer arranque

Con doble clic (Windows), sin terminal:

- `abrir_yarbis.vbs` — app de escritorio sin consola.
- `abrir_yarbis.cmd` — app con ventana visible (para ver errores de arranque).
- `abrir_yarbis_admin.cmd` — app con permisos de administrador (para el servicio SCM).
- `abrir_yarbis_tui.cmd` — la **TUI**. En Linux/macOS: `./abrir_yarbis_tui.sh`.

Desde terminal: `.\.venv\Scripts\python.exe yarbis_desktop.py` (app) o
`python yarbis_tui.py [--instance <id>]` (TUI).

Si `state.json` aun no tiene objetivo, la app abre un asistente inicial: define
objetivo, modelo y contexto minimo, y puede correr el primer ciclo al guardar. El
panel `Preparacion` muestra si falta algo (objetivo, modelo, dependencias).

Luego, sigue el [manual de uso](docs/manual.md).

## Actualizacion

Recomendado, desde la app: `Mantenimiento` -> `Actualizar Yarbis`. Confirma el
cierre; espera la ventana externa de PowerShell (pedira administrador si el
servicio SCM esta instalado); al terminar bien, Yarbis se reabre.

Manual, en la carpeta del proyecto:

```powershell
.\scripts\update.ps1                       # usa origin/main, solo fast-forward
.\scripts\update.ps1 -RestartDesktop       # y reabre la app (lo que usa el boton)
.\scripts\update.ps1 -Remote origin -Branch main
.\scripts\update.ps1 -SkipChecks           # omite checks (solo en emergencia)
```

El actualizador **protege tu memoria y configuracion**: respalda `state.json`
antes de tocar codigo, mantiene fuera del stash `state.json`, `.yarbis_runtime/`,
`.yarbis_checkpoints/` y `.yarbis_memory_backups/`, hace `git merge --ff-only`,
reinstala dependencias, corre los checks y reinicia el servicio. Si algo falla,
lee el error en la ventana de PowerShell; si `state.json` se daña, se restaura
automaticamente desde `.yarbis_runtime/updates/`.

Requisitos del actualizador: Git en el `PATH`, Python (si no hay `.venv`), y
permisos de administrador cuando el servicio SCM `Yarbis` esta instalado.

## Estructura del repositorio

- Modulos Python del core, canales y herramientas (ver [docs/modulos.md](docs/modulos.md)).
- `service_host/` — host .NET del servicio SCM (Windows).
- `scripts/` — instalacion, actualizacion y checks (PowerShell).
- `docs/` — toda la documentacion.
- `tests/` — suite de pruebas (`python -m unittest discover -s tests`).
- Archivos runtime **no versionados**: `state.json`, `.yarbis_runtime/`,
  `.yarbis_checkpoints/`, `.yarbis_memory_backups/`, `.yarbis_instances/`.

## Limitaciones

- La autonomia depende del modelo del proveedor y de la calidad del objetivo.
- La app de escritorio (Tkinter) y el host de servicio .NET son solo Windows; en
  Linux/macOS/Android se usan la TUI y el servicio nativo.
- **iPhone/iPad no corren el core** (Apple no permite procesos Python de fondo);
  ahi el telefono es cliente via la PWA y los Atajos.
- El navegador requiere Playwright y un Chromium/Edge disponible; en Termux no hay
  binarios de navegador.
- Calendario y correo se integran por `.ics`/`mailto:`/manejadores locales; no
  leen buzones ni calendarios cloud.
- La publicacion social real requiere apps/permisos/tokens del proveedor; el
  perfil personal de Facebook solo se maneja con publicacion asistida.
- El despliegue del relay de la malla en Puter es asistido (el token MCP no tiene
  acceso de desarrollador).

## Licencia y desarrollo

Proyecto personal. Los tests se corren con `python -m unittest discover -s tests`
y el lint con `ruff check .`. Ver [docs/arquitectura.md](docs/arquitectura.md)
para entender el diseno antes de contribuir.
