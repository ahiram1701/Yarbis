"""Re-apunta el proceso a otra instancia de Yarbis, en caliente.

Varios modulos calculan sus rutas **al importarse** desde `YARBIS_INSTANCE`
(`memory.STATE_FILE`, `session.OPERATION_LOCK_FILE`, `activity.RUNTIME_DIR`, …).
Por eso, historicamente, cambiar de instancia exigia relanzar el proceso: es lo
que hace la app de escritorio. La TUI necesita cambiar sin reiniciar.

Diseno: UNA tabla declarativa `_BINDINGS` con (modulo, atributo, factory). Nada
de reasignaciones dispersas por el codigo: si un modulo gana una ruta derivada de
la instancia, se agrega aqui y queda cubierto por los tests.

Riesgo que gobierna este modulo: escribir en el `state.json` equivocado es el
fallo catastrofico conocido del proyecto (causo reseteos de memoria). Por eso
`rebind` reasigna TODO de una vez, invalida las caches con ambito de instancia y
esta cubierto por un test que verifica que el estado de la instancia anterior
queda intacto byte a byte.

No fuerza imports: solo toca modulos YA importados (via `sys.modules`), para que
un nodo headless no cargue GUI/voz por rebindear.
"""

import sys

import yarbis_instance

# (modulo, atributo, factory(instance_id) -> valor)
_BINDINGS: tuple[tuple[str, str, object], ...] = (
    # Memoria: lo mas critico. DEFAULT_* y su gemelo se mueven juntos porque
    # memory._memory_backups_dir compara ambos para detectar un override.
    ("memory", "STATE_FILE", yarbis_instance.state_file),
    ("memory", "STATE_LOCK_FILE", yarbis_instance.state_lock_file),
    ("memory", "DEFAULT_MEMORY_BACKUPS_DIR", yarbis_instance.memory_backups_dir),
    ("memory", "MEMORY_BACKUPS_DIR", yarbis_instance.memory_backups_dir),
    (
        "memory",
        "DEFAULT_MEMORY_PROTECTION_CONFIG_FILE",
        lambda i: yarbis_instance.runtime_dir(i) / "memory_protection.json",
    ),
    (
        "memory",
        "MEMORY_PROTECTION_CONFIG_FILE",
        lambda i: yarbis_instance.runtime_dir(i) / "memory_protection.json",
    ),
    # Candado de operacion: si no se mueve, la TUI se serializaria contra la
    # instancia equivocada.
    ("session", "OPERATION_LOCK_FILE", yarbis_instance.operation_lock_file),
    ("activity", "RUNTIME_DIR", yarbis_instance.runtime_dir),
    ("tools", "RUNTIME_DIR", yarbis_instance.runtime_dir),
    ("memory_backup", "BACKUPS_DIR", yarbis_instance.memory_backups_dir),
    ("credential_store", "CREDENTIALS_DIR", lambda i: yarbis_instance.runtime_dir(i) / "credentials"),
    ("computer_control", "DESKTOP_RUNTIME_DIR", lambda i: yarbis_instance.runtime_dir(i) / "desktop"),
    ("voice", "VOICE_RUNTIME_DIR", lambda i: yarbis_instance.runtime_dir(i) / "voice"),
    # Servicio: identidad + rutas runtime derivadas.
    ("service_manager", "INSTANCE_ID", lambda i: i),
    ("service_manager", "RUNTIME_DIR", yarbis_instance.runtime_dir),
    ("service_manager", "PID_FILE", lambda i: yarbis_instance.runtime_dir(i) / "service.pid"),
    ("service_manager", "STOP_FILE", lambda i: yarbis_instance.runtime_dir(i) / "service.stop"),
    ("service_manager", "LOG_FILE", lambda i: yarbis_instance.runtime_dir(i) / "service.log"),
    ("service_manager", "OPERATION_LOCK_FILE", yarbis_instance.operation_lock_file),
    ("service_manager", "SERVICE_HOST_OUTPUT_DIR", lambda i: yarbis_instance.runtime_dir(i) / "service_host"),
    (
        "service_manager",
        "SERVICE_HOST_EXE",
        lambda i: yarbis_instance.runtime_dir(i) / "service_host" / "YarbisServiceHost.exe",
    ),
    ("service_manager", "SERVICE_NAME", yarbis_instance.service_name),
    ("service_manager", "SERVICE_DISPLAY_NAME", yarbis_instance.service_display_name),
    ("service_manager", "SERVICE_DESCRIPTION", yarbis_instance.service_description),
)

# Caches con ambito de instancia: si no se invalidan, la UI mostraria datos de
# la instancia anterior. (device_profile NO: describe el equipo, no la instancia.)
_CACHE_RESETS: tuple[tuple[str, str, object], ...] = (
    ("memory_recall", "_CACHE", lambda: {"signature": "", "index": None, "built_at": 0.0}),
    (
        "self_knowledge",
        "_SELF_KNOWLEDGE_CACHE",
        lambda: {"created_at": 0.0, "text": "", "source_signature": ""},
    ),
    ("service_manager", "_READINESS_CACHE", lambda: {"created_at": 0.0, "status": None}),
    (
        "service_manager",
        "_SERVICE_STATUS_CACHE",
        lambda: {"created_at": 0.0, "status": None, "instance_id": None},
    ),
)


def _loaded(module_name: str):
    """El modulo si ya esta importado; None si no (no lo importa a proposito)."""
    return sys.modules.get(module_name)


def _reset_caches() -> None:
    for module_name, attr, factory in _CACHE_RESETS:
        module = _loaded(module_name)
        if module is None or not hasattr(module, attr):
            continue
        current = getattr(module, attr)
        fresh = factory()
        # Mutar en sitio cuando es un dict: otros modulos pueden tener una
        # referencia al mismo objeto.
        if isinstance(current, dict) and isinstance(fresh, dict):
            current.clear()
            current.update(fresh)
        else:
            setattr(module, attr, fresh)


def rebind(instance_id: object) -> str:
    """Apunta el proceso a `instance_id`. Devuelve el id normalizado aplicado.

    Idempotente: rebindear a la instancia actual es seguro. Solo toca modulos ya
    importados.
    """
    normalized = yarbis_instance.configure_instance(instance_id)

    for module_name, attr, factory in _BINDINGS:
        module = _loaded(module_name)
        if module is None or not hasattr(module, attr):
            continue
        setattr(module, attr, factory(normalized))

    _reset_caches()
    return normalized


def current_binding() -> dict:
    """Diagnostico: a que instancia apunta cada modulo cargado ahora mismo."""
    snapshot = {"instance": yarbis_instance.current_instance_id()}
    for module_name, attr, _factory in _BINDINGS:
        module = _loaded(module_name)
        if module is None or not hasattr(module, attr):
            continue
        snapshot[f"{module_name}.{attr}"] = str(getattr(module, attr))
    return snapshot
