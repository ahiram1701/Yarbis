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

    def test_rename_instance_preserves_id_and_paths(self):
        root = TEST_RUNTIME_DIR / f"instances-rename-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            yarbis_instance.create_instance("worker")
            renamed = yarbis_instance.rename_instance("worker", "Trabajo fuerte")
            listed = yarbis_instance.list_instances()

        self.assertEqual(renamed["id"], "worker")
        self.assertEqual(renamed["display_name"], "Trabajo fuerte")
        self.assertEqual(renamed["state_file"], str(root / "worker" / "state.json"))
        self.assertEqual(
            [item for item in listed if item["id"] == "worker"][0]["display_name"],
            "Trabajo fuerte",
        )

    def test_archive_instance_moves_root_and_registry_entry(self):
        root = TEST_RUNTIME_DIR / f"instances-archive-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            yarbis_instance.create_instance("worker", display_name="Worker")
            state_path = yarbis_instance.state_file("worker")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("{}", encoding="utf-8")

            archived = yarbis_instance.archive_instance("worker")
            listed = yarbis_instance.list_instances()
            archives = yarbis_instance.list_archived_instances()

        self.assertEqual(archived["id"], "worker")
        self.assertFalse((root / "worker").exists())
        self.assertTrue(Path(archived["archive_dir"]).exists())
        self.assertEqual([item["id"] for item in listed], ["default"])
        self.assertEqual(archives[0]["id"], "worker")

    def test_archive_rejects_default_current_and_active_instances(self):
        root = TEST_RUNTIME_DIR / f"instances-archive-guards-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            yarbis_instance.create_instance("worker")
            with self.assertRaises(yarbis_instance.YarbisInstanceError):
                yarbis_instance.archive_instance("default")

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "worker"}):
                with self.assertRaises(yarbis_instance.YarbisInstanceError):
                    yarbis_instance.archive_instance("worker")

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "default"}):
                pid_file = yarbis_instance.runtime_dir("worker") / "desktop.pid"
                pid_file.parent.mkdir(parents=True, exist_ok=True)
                pid_file.write_text(str(os.getpid()), encoding="utf-8")
                with self.assertRaises(yarbis_instance.YarbisInstanceError):
                    yarbis_instance.archive_instance("worker")

    def test_restore_archived_instance_rejects_existing_id(self):
        root = TEST_RUNTIME_DIR / f"instances-restore-{uuid4().hex[:8]}"
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            yarbis_instance.create_instance("worker")
            archived = yarbis_instance.archive_instance("worker")
            yarbis_instance.create_instance("worker")

            with self.assertRaises(yarbis_instance.YarbisInstanceError):
                yarbis_instance.restore_archived_instance(archived["archive_id"])


if __name__ == "__main__":
    unittest.main()
