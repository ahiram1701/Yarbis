import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yarbis_instance


TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class YarbisInstanceTestCase(unittest.TestCase):
    def test_default_instance_preserves_legacy_paths(self):
        with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "default"}):
            self.assertEqual(yarbis_instance.current_instance_id(), "default")
            self.assertEqual(yarbis_instance.state_file(), Path("state.json"))
            self.assertEqual(yarbis_instance.runtime_dir(), yarbis_instance.WORKSPACE_ROOT / ".yarbis_runtime")
            self.assertEqual(yarbis_instance.memory_backups_dir(), yarbis_instance.WORKSPACE_ROOT / ".yarbis_memory_backups")
            self.assertEqual(yarbis_instance.service_name(), "Yarbis")
            self.assertEqual(yarbis_instance.default_mobile_ui_port(), 8787)

    def test_secondary_instance_uses_isolated_paths_and_names(self):
        root = TEST_RUNTIME_DIR / f"instances-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "worker"}):
                self.assertEqual(yarbis_instance.current_instance_id(), "worker")
                self.assertEqual(yarbis_instance.state_file(), root / "worker" / "state.json")
                self.assertEqual(yarbis_instance.runtime_dir(), root / "worker" / ".yarbis_runtime")
                self.assertEqual(yarbis_instance.memory_backups_dir(), root / "worker" / ".yarbis_memory_backups")
                self.assertEqual(yarbis_instance.service_name(), "Yarbis-worker")
                self.assertNotEqual(yarbis_instance.default_mobile_ui_port(), 8787)

    def test_registry_lists_default_and_created_instance(self):
        root = TEST_RUNTIME_DIR / f"instances-registry-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            created = yarbis_instance.create_instance("worker")
            listed = yarbis_instance.list_instances()

        self.assertEqual(created["id"], "worker")
        self.assertEqual([item["id"] for item in listed], ["default", "worker"])


if __name__ == "__main__":
    unittest.main()
