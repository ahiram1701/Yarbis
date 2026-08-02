"""Rebind de instancia en caliente.

Lo critico: tras cambiar de instancia, TODO el proceso debe apuntar a la nueva y
el estado de la anterior debe quedar intacto. Escribir en el `state.json`
equivocado es el fallo catastrofico conocido del proyecto (causo reseteos de
memoria), asi que hay un test dedicado a esa regresion.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import activity
import instance_binding
import memory
import memory_backup
import service_manager
import session
import yarbis_instance


class RebindTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._original_root = yarbis_instance.INSTANCES_ROOT
        self._original_instance = yarbis_instance.current_instance_id()
        # Aisla las rutas del repo real: nada de estas pruebas toca una
        # instancia productiva.
        yarbis_instance.INSTANCES_ROOT = self.tmp / ".yarbis_instances"
        instance_binding.rebind("inst-a")

    def tearDown(self):
        yarbis_instance.INSTANCES_ROOT = self._original_root
        instance_binding.rebind(self._original_instance)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- la garantia principal -------------------------------------------
    def test_writing_after_rebind_leaves_the_other_instance_untouched(self):
        memory.save_state({"goal": "SOY A", "messages": []})
        a_path = Path(memory.STATE_FILE)
        a_bytes = a_path.read_bytes()

        instance_binding.rebind("inst-b")
        memory.save_state({"goal": "SOY B", "messages": []})

        self.assertEqual(memory.load_state()["goal"], "SOY B")
        self.assertNotEqual(Path(memory.STATE_FILE), a_path)
        # Regresion directa del fallo que causo los reseteos.
        self.assertEqual(a_path.read_bytes(), a_bytes)
        self.assertEqual(json.loads(a_path.read_text(encoding="utf-8-sig"))["goal"], "SOY A")

    def test_switching_back_reads_the_original_state(self):
        memory.save_state({"goal": "SOY A", "messages": []})
        instance_binding.rebind("inst-b")
        memory.save_state({"goal": "SOY B", "messages": []})
        instance_binding.rebind("inst-a")
        self.assertEqual(memory.load_state()["goal"], "SOY A")

    # --- cobertura de la tabla -------------------------------------------
    def test_all_bound_modules_point_at_the_new_instance(self):
        instance_binding.rebind("inst-b")
        for path in (
            memory.STATE_FILE,
            memory.STATE_LOCK_FILE,
            memory.MEMORY_BACKUPS_DIR,
            memory.MEMORY_PROTECTION_CONFIG_FILE,
            session.OPERATION_LOCK_FILE,
            activity.RUNTIME_DIR,
            memory_backup.BACKUPS_DIR,
            service_manager.RUNTIME_DIR,
            service_manager.PID_FILE,
            service_manager.LOG_FILE,
        ):
            self.assertIn("inst-b", str(path), f"no se re-apunto: {path}")

    def test_service_identity_follows_the_instance(self):
        instance_binding.rebind("inst-b")
        self.assertEqual(service_manager.INSTANCE_ID, "inst-b")
        self.assertIn("inst-b", service_manager.SERVICE_NAME)

    def test_backups_default_and_current_move_together(self):
        # memory._memory_backups_dir compara ambos para detectar un override;
        # si se desincronizan, los respaldos irian a la instancia equivocada.
        instance_binding.rebind("inst-b")
        self.assertEqual(memory.MEMORY_BACKUPS_DIR, memory.DEFAULT_MEMORY_BACKUPS_DIR)
        self.assertIn("inst-b", str(memory._memory_backups_dir()))

    # --- comportamiento del modulo ---------------------------------------
    def test_rebind_is_idempotent(self):
        first = instance_binding.rebind("inst-b")
        before = str(memory.STATE_FILE)
        second = instance_binding.rebind("inst-b")
        self.assertEqual(first, second)
        self.assertEqual(str(memory.STATE_FILE), before)

    def test_rebind_normalizes_the_id(self):
        self.assertEqual(instance_binding.rebind("  Inst-B  "), "inst-b")

    def test_ignores_modules_that_are_not_imported(self):
        import sys

        saved = sys.modules.pop("voice", None)
        try:
            instance_binding.rebind("inst-b")  # no debe fallar ni importar voice
            self.assertNotIn("voice", sys.modules)
        finally:
            if saved is not None:
                sys.modules["voice"] = saved

    def test_instance_scoped_caches_are_invalidated(self):
        import memory_recall

        memory_recall._CACHE["signature"] = "de-la-instancia-vieja"
        memory_recall._CACHE["index"] = object()
        service_manager._READINESS_CACHE["status"] = {"stale": True}

        instance_binding.rebind("inst-b")

        self.assertEqual(memory_recall._CACHE["signature"], "")
        self.assertIsNone(memory_recall._CACHE["index"])
        self.assertIsNone(service_manager._READINESS_CACHE["status"])
        # Las caches conservan su forma (otros modulos leen estas claves).
        self.assertIn("created_at", service_manager._READINESS_CACHE)

    def test_current_binding_reports_the_active_instance(self):
        instance_binding.rebind("inst-b")
        snapshot = instance_binding.current_binding()
        self.assertEqual(snapshot["instance"], "inst-b")
        self.assertIn("inst-b", snapshot["memory.STATE_FILE"])


if __name__ == "__main__":
    unittest.main()
