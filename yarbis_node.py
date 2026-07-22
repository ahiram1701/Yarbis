#!/usr/bin/env python3
"""Bootstrap de un nodo Yarbis: traelo a la vida en cualquier dispositivo.

Diseñado para correr con **solo la biblioteca estandar**, en cualquier SO donde
haya un Python 3 — incluido uno que no reconozcamos. No instala nada por su
cuenta ni se copia a otras maquinas: lo corre una persona, aqui, a proposito.

Que hace:
  1. Sondea el entorno por comportamiento (archivos, procesos, red, servicio).
  2. Reporta que puede y que no puede hacer este dispositivo.
  3. Si falta el paquete `ollama`, recuerda que un proveedor en la nube
     (openai_compat / puter / openrouter) deja correr el core sin dependencias.
  4. Con --start, arranca el servicio con el gestor que corresponda (SCM en
     Windows; systemd/launchd/termux/portable segun el dispositivo).

Uso:
    python yarbis_node.py                 # solo sondea y reporta
    python yarbis_node.py --instance foo  # fija la instancia (YARBIS_INSTANCE)
    python yarbis_node.py --start         # ademas instala/arranca el servicio
"""

import argparse
import os
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent


def _bootstrap_instance(instance_id: str) -> None:
    """Fija YARBIS_INSTANCE ANTES de importar memory (que lee el env al importar)."""
    cleaned = str(instance_id or "").strip()
    if cleaned:
        os.environ["YARBIS_INSTANCE"] = cleaned


def _report_probe() -> dict:
    import device_profile

    profile = device_profile.get_profile(refresh=True)
    probes = device_profile.probe_capabilities()
    print(device_profile.render_profile_summary(profile))
    print(device_profile.render_probe_summary(probes))
    print(f"- SO reconocido: {'si' if profile.get('known_system') else 'no (modo adaptable)'}")

    try:
        import agent

        if not getattr(agent, "OLLAMA_AVAILABLE", True):
            print(
                "- El paquete `ollama` no esta instalado. Usa un proveedor en la "
                "nube (openai_compat / puter / openrouter): el core corre solo con "
                "la stdlib."
            )
    except Exception as exc:  # pragma: no cover - entorno muy limitado
        print(f"- Aviso al cargar el agente: {exc}")
    return {"profile": profile, "probes": probes}


def _start_service() -> None:
    if os.name == "nt":
        import service_manager

        print(service_manager.start_service())
        return
    import native_service

    print(f"Gestor de servicio: {native_service.service_manager_kind()}")
    print(native_service.start_service())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap de un nodo Yarbis.")
    parser.add_argument("--instance", default="", help="Id de instancia (YARBIS_INSTANCE).")
    parser.add_argument("--start", action="store_true", help="Instala/arranca el servicio.")
    args = parser.parse_args(argv)

    _bootstrap_instance(args.instance)
    sys.path.insert(0, str(WORKSPACE_ROOT))

    print(f"Yarbis node en {WORKSPACE_ROOT} (Python {sys.version.split()[0]})")
    _report_probe()

    if args.start:
        try:
            _start_service()
        except Exception as exc:
            print(f"No pude arrancar el servicio: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
