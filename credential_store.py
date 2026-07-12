import base64
import ctypes
import ctypes.wintypes
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yarbis_instance
from atomic_io import read_json_bom_safe

WORKSPACE_ROOT = Path(__file__).resolve().parent
CREDENTIALS_DIR = yarbis_instance.runtime_dir() / "credentials"


class CredentialStoreError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_ref(kind: str) -> str:
    cleaned_kind = "".join(char for char in str(kind).lower() if char.isalnum() or char in {"-", "_"}) or "secret"
    return f"cred-{cleaned_kind}-{uuid4().hex[:12]}"


def _credential_path(ref: str) -> Path:
    cleaned_ref = str(ref).strip()
    if not cleaned_ref or any(char in cleaned_ref for char in "\\/:*?\"<>|"):
        raise CredentialStoreError("Referencia de credencial invalida.")
    return CREDENTIALS_DIR / f"{cleaned_ref}.json"


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(raw: bytes) -> bytes:
    if not _dpapi_available():
        raise CredentialStoreError("DPAPI no esta disponible en esta plataforma.")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = DataBlob(len(raw), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DataBlob()

    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialStoreError("No pude cifrar la credencial con DPAPI.")

    try:
        protected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return protected


def _dpapi_unprotect(protected: bytes) -> bytes:
    if not _dpapi_available():
        raise CredentialStoreError("DPAPI no esta disponible en esta plataforma.")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(protected)
    in_blob = DataBlob(len(protected), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DataBlob()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialStoreError("No pude descifrar la credencial con DPAPI.")

    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
    return raw


def _protect_secret(secret: str) -> tuple[str, str]:
    raw = str(secret).encode("utf-8")
    if _dpapi_available():
        return "dpapi", base64.b64encode(_dpapi_protect(raw)).decode("ascii")
    return "base64", base64.b64encode(raw).decode("ascii")


def _unprotect_secret(method: str, payload: str) -> str:
    protected = base64.b64decode(str(payload).encode("ascii"))
    if method == "dpapi":
        return _dpapi_unprotect(protected).decode("utf-8")
    if method == "base64":
        return protected.decode("utf-8")
    raise CredentialStoreError(f"Metodo de credencial no soportado: {method}")


def save_secret(secret: str, kind: str = "social", metadata: dict | None = None) -> str:
    cleaned_secret = str(secret).strip()
    if not cleaned_secret:
        raise CredentialStoreError("La credencial no puede quedar vacia.")

    ref = _new_ref(kind)
    method, payload = _protect_secret(cleaned_secret)
    record = {
        "id": ref,
        "kind": str(kind).strip() or "social",
        "method": method,
        "payload": payload,
        "created_at": _utc_now(),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    target = _credential_path(ref)
    target.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")
    return ref


def load_secret(ref: str) -> str:
    path = _credential_path(ref)
    try:
        record = read_json_bom_safe(path)
    except FileNotFoundError as exc:
        raise CredentialStoreError(f"No encontre la credencial: {ref}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialStoreError(f"No pude leer la credencial: {exc}") from exc

    if record.get("id") != str(ref).strip():
        raise CredentialStoreError("La credencial no coincide con su referencia.")
    return _unprotect_secret(str(record.get("method", "")), str(record.get("payload", "")))


def delete_secret(ref: str) -> bool:
    path = _credential_path(ref)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
