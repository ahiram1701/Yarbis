# Yarbis

Yarbis es un agente local sencillo para avanzar objetivos paso a paso usando Ollama y un conjunto pequeno de herramientas de filesystem.

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

## Uso

```bash
python main.py
```

Comandos disponibles:

- `goal`: actualiza el objetivo actual
- `run`: ejecuta un ciclo del agente
- `auto`: ejecuta varios ciclos seguidos
- `exit`: termina la sesion

## Endurecimiento incluido

- Las tools solo pueden leer y escribir dentro del workspace.
- Las lecturas y escrituras tienen limites de tamano para evitar inflar `state.json`.
- El estado se normaliza y recorta antes de persistirse.
- Los errores de Ollama ya no tumban la aplicacion completa.

## Tests

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
