"""Memoria profesional — auditoria de salud (Pilar 4).

Mockea solo las fronteras (BOM, lectura, respaldos); usa el normalize real.
"""

import unittest
from unittest.mock import patch

import atomic_io
import memory
import memory_backup
import tools


def _healthy_state():
    st = memory.default_state()
    st["notes"] = [
        {"id": "n1", "title": "A", "content": "x", "importance": 3, "pinned": True, "tags": ["t"], "access_count": 2},
        {"id": "n2", "title": "B", "content": "y"},
    ]
    return st


class MemoryAuditTestCase(unittest.TestCase):
    def _run(self, *, bom=False, parse_ok=True, verification=None):
        if verification is None:
            verification = {"valid_count": 3, "latest": {"created_at": memory_backup.utc_now_text()}}

        read_side = (lambda _p: {}) if parse_ok else _raise

        with patch.object(tools, "load_state", return_value=_healthy_state()), \
             patch.object(atomic_io, "file_has_bom", return_value=bom), \
             patch.object(atomic_io, "read_json_bom_safe", side_effect=read_side), \
             patch.object(memory_backup, "verify_backups", return_value=verification):
            return tools.memory_audit()

    def test_healthy_report(self):
        out = self._run()
        self.assertIn("Integridad: OK", out)
        self.assertIn("Sin problemas detectados", out)
        self.assertIn("fijadas=1", out)
        self.assertIn("importantes=1", out)
        self.assertIn("accedidas=1", out)

    def test_bom_is_flagged(self):
        out = self._run(bom=True)
        self.assertIn("REVISAR", out)
        self.assertIn("BOM", out)
        self.assertIn("Avisos", out)

    def test_broken_schema_is_flagged(self):
        out = self._run(parse_ok=False)
        self.assertIn("parse=falla", out)
        self.assertIn("no se pudo leer", out)

    def test_no_valid_backup_is_flagged(self):
        out = self._run(verification={"valid_count": 0, "latest": None})
        self.assertIn("sin respaldo valido", out.lower())

    def test_stale_backup_is_flagged(self):
        out = self._run(verification={"valid_count": 2, "latest": {"created_at": "2020-01-01T00:00:00+00:00"}})
        self.assertIn("respaldo fresco", out)


def _raise(_path):
    raise ValueError("json invalido")


if __name__ == "__main__":
    unittest.main()
