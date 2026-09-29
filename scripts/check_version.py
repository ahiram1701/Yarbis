"""Valida que el tag de release coincide con la version de pyproject.toml.

Uso (lo llama .github/workflows/release.yml):
    python scripts/check_version.py v0.2.0

Sale con 0 si coinciden y con 1 (mensaje en stderr) si no.
"""

import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"]).strip()


def check(tag: str, version: str) -> str:
    """Devuelve "" si el tag corresponde a la version; si no, el motivo del rechazo."""
    if not tag.startswith("v"):
        return f"'{tag}' debe empezar con 'v' (ej. v{version})"
    base, sep, suffix = tag[1:].partition("-")
    # Pre-release: v0.2.0-rc1 publica la 0.2.0 marcada como prerelease.
    if sep and not suffix.isalnum():
        return f"sufijo de pre-release invalido en '{tag}' (usa algo como -rc1 o -beta2)"
    if base != version:
        return f"tag {base} != pyproject {version}; sube la version en pyproject.toml o corrige el tag"
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: check_version.py <tag>", file=sys.stderr)
        return 2
    error = check(argv[1].strip(), project_version())
    if error:
        print(f"Tag invalido: {error}", file=sys.stderr)
        return 1
    print(f"OK: {argv[1]} coincide con pyproject.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
