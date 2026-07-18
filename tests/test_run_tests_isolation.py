"""Regresion: correr los tests del proyecto NUNCA debe tocar el state.json real.

Bug 2026-07-13: la instancia `asistente` "quedaba como nueva" repetidamente.
Causa: `run_project_tests` lanzaba `unittest` heredando `YARBIS_INSTANCE`, asi
que los tests que escriben estado apuntaban al state.json REAL de la instancia
y borraban la memoria del usuario. El fix aisla el subproceso con una instancia
sandbox desechable.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import tools
import yarbis_instance


class GlobalTestIsolationTestCase(unittest.TestCase):
    def test_suite_runs_under_sandbox_instance(self):
        # tests/__init__.py fuerza un YARBIS_INSTANCE sandbox al importar el
        # paquete, para que NINGUN test (lanzado por run_project_tests,
        # run_system_command o manualmente) toque una instancia real.
        self.assertTrue(
            os.environ.get(yarbis_instance.ENV_INSTANCE, "").startswith("test-sandbox"),
            "La suite debe correr bajo una instancia sandbox, no una real.",
        )

    def test_current_instance_is_not_a_real_one(self):
        self.assertNotIn(
            yarbis_instance.current_instance_id(),
            {"asistente", "default", "trader", "dev", "worker", "contenidos", "mantenimiento", "ciber-seguridad"},
        )


class RunTestsIsolationTestCase(unittest.TestCase):
    def test_isolated_env_uses_disposable_instance(self):
        env, sandbox_id = tools._isolated_test_env()
        self.assertTrue(sandbox_id.startswith(tools._TEST_SANDBOX_PREFIX))
        self.assertEqual(env[yarbis_instance.ENV_INSTANCE], sandbox_id)
        self.assertNotIn(yarbis_instance.ENV_SERVICE_NAME, env)
        # Id valido para el esquema de instancias.
        self.assertEqual(yarbis_instance.normalize_instance_id(sandbox_id), sandbox_id)

    def test_cleanup_only_removes_sandbox_dirs(self):
        # No debe borrar instancias que no tengan el prefijo sandbox.
        fake = yarbis_instance.INSTANCES_ROOT / "no-soy-sandbox"
        fake.mkdir(parents=True, exist_ok=True)
        try:
            tools._cleanup_test_sandbox("no-soy-sandbox")
            self.assertTrue(fake.exists(), "no debio borrar una instancia sin prefijo sandbox")
        finally:
            fake.rmdir()

    def test_cleanup_removes_sandbox_dir(self):
        sandbox_id = f"{tools._TEST_SANDBOX_PREFIX}{uuid4().hex[:8]}"
        sandbox_dir = yarbis_instance.INSTANCES_ROOT / sandbox_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        (sandbox_dir / "state.json").write_text("{}", encoding="utf-8")
        tools._cleanup_test_sandbox(sandbox_id)
        self.assertFalse(sandbox_dir.exists())

    def test_run_project_tests_does_not_touch_real_instance_state(self):
        # Simula que Yarbis corre desde una instancia real con memoria del usuario.
        real_instance = f"real-{uuid4().hex[:8]}"
        with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: real_instance}):
            state_path = yarbis_instance.state_file(real_instance)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            seeded = memory.default_state()
            seeded["profile"]["name"] = "HIRAM_REAL"
            seeded["goal"] = "MEMORIA_REAL"
            seeded["messages"] = [{"role": "user", "content": f"m{i}"} for i in range(30)]
            # save_state honra STATE_FILE, que en este proceso apunta a la instancia real.
            with patch.object(memory, "STATE_FILE", state_path):
                memory.save_state(seeded)

            # Un test-objetivo minimo que SI escribe estado sin aislar (imita el bug).
            leaky = Path(tools.WORKSPACE_ROOT) / "tests" / f"_leaky_probe_{real_instance}.py"
            leaky.write_text(
                "import unittest, memory\n"
                "class T(unittest.TestCase):\n"
                "    def test_writes_state(self):\n"
                "        s = memory.default_state()\n"
                "        s['profile']['name'] = 'CONTAMINADO'\n"
                "        memory.save_state(s)\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            try:
                result = tools.run_project_tests(
                    test_target=f"tests/{leaky.name}", timeout_seconds=60
                )
                self.assertIn("Tests OK.", result)
                # El estado real de la instancia debe seguir intacto.
                with patch.object(memory, "STATE_FILE", state_path):
                    reloaded = memory.load_state()
                self.assertEqual(reloaded["profile"]["name"], "HIRAM_REAL")
                self.assertEqual(len(reloaded["messages"]), 30)
            finally:
                leaky.unlink(missing_ok=True)
                # limpiar la instancia real simulada
                import shutil

                shutil.rmtree(state_path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
