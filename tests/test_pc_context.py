import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pc_context
import proactive_context

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class PcContextTestCase(unittest.TestCase):
    def test_safe_mode_disables_window_titles(self):
        settings = pc_context.normalize_local_context_settings({
            "enabled": True,
            "mode": "safe",
            "include_window_title": True,
            "sample_interval_seconds": 1,
            "max_snapshot_age_seconds": 1,
        })

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["mode"], "safe")
        self.assertFalse(settings["include_window_title"])
        self.assertEqual(settings["sample_interval_seconds"], 5)
        self.assertEqual(settings["max_snapshot_age_seconds"], 15)

    def test_load_latest_snapshot_rejects_stale_snapshot(self):
        snapshot_path = TEST_RUNTIME_DIR / "pc_context_latest.json"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        snapshot_path.write_text(
            json.dumps({"captured_at": old_timestamp}),
            encoding="utf-8",
        )

        with patch.object(pc_context, "SNAPSHOT_FILE", snapshot_path):
            self.assertIsNone(pc_context.load_latest_snapshot(max_age_seconds=60))
            self.assertIsNotNone(pc_context.load_latest_snapshot(max_age_seconds=3600))

    def test_recent_workspace_files_prunes_ignored_directories(self):
        workspace = TEST_RUNTIME_DIR / "pc_context_workspace"
        normal_dir = workspace / "src"
        ignored_dir = workspace / ".venv"
        normal_dir.mkdir(parents=True, exist_ok=True)
        ignored_dir.mkdir(parents=True, exist_ok=True)
        normal_file = normal_dir / "recent.py"
        ignored_file = ignored_dir / "ignored.py"
        normal_file.write_text("print('visible')\n", encoding="utf-8")
        ignored_file.write_text("print('ignored')\n", encoding="utf-8")

        scanned_dirs = []
        real_scandir = os.scandir

        def tracking_scandir(path):
            scanned_dirs.append(Path(path).resolve())
            return real_scandir(path)

        with patch.object(pc_context, "WORKSPACE_ROOT", workspace):
            with patch.object(pc_context.os, "scandir", side_effect=tracking_scandir):
                files = pc_context._get_recent_workspace_files()

        self.assertIn("src/recent.py", [item["path"] for item in files])
        self.assertNotIn(".venv/ignored.py", [item["path"] for item in files])
        self.assertIn(workspace.resolve(), scanned_dirs)
        self.assertIn(normal_dir.resolve(), scanned_dirs)
        self.assertNotIn(ignored_dir.resolve(), scanned_dirs)

    def test_render_snapshot_summary_omits_private_details_when_absent(self):
        snapshot = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "user_presence": {"state": "active", "idle_seconds": 12},
            "foreground": {"available": True, "process_name": "Code.exe", "title": ""},
            "system_health": {
                "power": {"available": True, "ac_line_status": "plugged", "battery_percent": 88},
                "memory": {
                    "available": True,
                    "load_percent": 44,
                    "available_memory": "8.0 GB",
                    "total": "16.0 GB",
                },
                "disk": {
                    "available": True,
                    "free": "20.0 GB",
                    "total": "200.0 GB",
                    "free_percent": 10.0,
                },
            },
            "workspace": {
                "git": {"available": True, "change_count": 2, "changes": [" M agent.py"], "truncated": False},
                "recent_files": [{"path": "agent.py"}],
            },
            "limitations": ["Titulos de ventana desactivados por modo seguro."],
        }

        rendered = pc_context.render_snapshot_summary(snapshot)

        self.assertIn("proceso=Code.exe", rendered)
        self.assertNotIn("titulo=", rendered)
        self.assertIn("Git workspace: 2 cambio(s)", rendered)

    def test_proactive_tick_includes_fresh_snapshot_hints(self):
        snapshot = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "user_presence": {"state": "away", "idle_seconds": 901},
            "foreground": {"available": True, "process_name": "Code.exe", "title": ""},
            "system_health": {
                "power": {"available": True, "ac_line_status": "battery", "battery_percent": 15},
                "memory": {"available": True, "load_percent": 92},
                "disk": {"available": True, "free_percent": 8.5, "free": "8.0 GB", "total": "100.0 GB"},
            },
            "workspace": {
                "git": {"available": True, "change_count": 3, "changes": [" M memory.py"], "truncated": False},
            },
            "limitations": [],
        }
        state = {
            "local_context": {
                "enabled": True,
                "mode": "safe",
                "max_snapshot_age_seconds": 180,
            }
        }

        with patch.object(proactive_context, "load_latest_snapshot", return_value=snapshot):
            message = proactive_context.build_proactive_tick_message(state)

        self.assertIn("Pulso proactivo 24/7", message)
        self.assertIn("Contexto local observado", message)
        self.assertIn("Bateria baja", message)
        self.assertIn("Usuario ausente", message)
        self.assertIn("Workspace con 3 cambio(s)", message)
        self.assertIn("todas las herramientas disponibles", message)
        self.assertNotIn("No ejecutes tests completos", message)

    def test_proactive_tick_reports_disabled_context(self):
        message = proactive_context.build_proactive_tick_message({
            "local_context": {
                "enabled": False,
                "mode": "safe",
            }
        })

        self.assertIn("Desactivado por configuracion", message)

    def test_proactive_tick_reports_missing_context_when_enabled(self):
        state = {
            "local_context": {
                "enabled": True,
                "mode": "safe",
                "max_snapshot_age_seconds": 180,
            }
        }

        with patch.object(proactive_context, "load_latest_snapshot", return_value=None):
            message = proactive_context.build_proactive_tick_message(state)

        self.assertIn("No hay snapshot fresco", message)


if __name__ == "__main__":
    unittest.main()
