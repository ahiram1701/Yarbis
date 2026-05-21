import json
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import memory_transfer

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class MemoryTestCase(unittest.TestCase):
    def _memory_protection_paths(self, name: str):
        base = TEST_RUNTIME_DIR / "memory_protection" / f"{name}-{uuid4().hex[:8]}"
        base.mkdir(parents=True, exist_ok=True)
        return base, base / "state.json", base / "state.lock"

    def test_load_state_returns_defaults_for_invalid_json(self):
        state_path = TEST_RUNTIME_DIR / "memory_invalid_state.json"
        lock_path = TEST_RUNTIME_DIR / "memory_invalid_state.lock"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                state = memory.load_state()

        self.assertEqual(state["goal"], memory.DEFAULT_GOAL)
        self.assertEqual(state["messages"], [])
        self.assertEqual(state["notes"], [])
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertEqual(state["profile"]["preferences"], [])
        self.assertEqual(
            state["autonomy"]["max_steps_per_cycle"],
            memory.DEFAULT_MAX_STEPS_PER_CYCLE,
        )
        self.assertEqual(state["ollama"]["timeout_seconds"], 900)
        self.assertEqual(state["coding"]["workspace_path"], "")
        self.assertEqual(state["coding"]["mode"], memory.DEFAULT_CODING_MODE)
        self.assertEqual(state["coding"]["pending_proposal_ids"], [])

    def test_legacy_default_goal_normalizes_to_empty(self):
        normalized = memory.normalize_state({
            "goal": "Ayudar al usuario de forma autonoma con tareas locales.",
        })

        self.assertEqual(normalized["goal"], "")

    def test_normalize_state_preserves_coding_settings(self):
        normalized = memory.normalize_state({
            "coding": {
                "workspace_path": "C:/repo/demo",
                "mode": "propose_first",
                "pending_proposal_ids": ["proposal-1", "proposal-1", "", "proposal-2"],
            }
        })

        self.assertEqual(normalized["coding"]["workspace_path"], "C:/repo/demo")
        self.assertEqual(normalized["coding"]["mode"], "propose_first")
        self.assertEqual(normalized["coding"]["pending_proposal_ids"], ["proposal-1", "proposal-2"])

    def test_normalize_state_defaults_invalid_coding_mode(self):
        normalized = memory.normalize_state({
            "coding": {
                "workspace_path": "C:/repo/demo",
                "mode": "direct_write",
            }
        })

        self.assertEqual(normalized["coding"]["mode"], memory.DEFAULT_CODING_MODE)

    def test_save_state_keeps_messages_complete(self):
        state_path = TEST_RUNTIME_DIR / "memory_complete_messages_state.json"
        lock_path = TEST_RUNTIME_DIR / "memory_complete_messages_state.lock"
        long_message = "x" * (memory.MAX_MESSAGE_CHARS + 10)
        oversized_state = {
            "goal": "demo",
            "messages": [
                {"role": "assistant", "content": long_message}
                for _ in range(memory.MAX_MESSAGES + 5)
            ],
            "last_result": "ok",
            "cycle_count": 3,
        }

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(oversized_state)
            stored_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(len(stored_state["messages"]), memory.MAX_MESSAGES + 5)
        self.assertEqual(stored_state["messages"][-1]["content"], long_message)
        self.assertNotIn("[truncado", stored_state["messages"][-1]["content"])

    def test_save_state_keeps_last_result_complete(self):
        state_path = TEST_RUNTIME_DIR / "memory_last_result_state.json"
        lock_path = TEST_RUNTIME_DIR / "memory_last_result_state.lock"
        long_result = "respuesta larga " * 500

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state({
                    "goal": "demo",
                    "last_result": long_result,
                })
            stored_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(stored_state["last_result"], long_result)
        self.assertNotIn("[truncado", stored_state["last_result"])

    def test_state_transaction_preserves_independent_updates_from_stale_snapshots(self):
        state_path = TEST_RUNTIME_DIR / "memory_transaction_state.json"
        lock_path = TEST_RUNTIME_DIR / "memory_transaction_state.lock"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(memory.default_state())
                stale_state = memory.load_state()

                memory.state_transaction(
                    "add_note",
                    lambda state: state["notes"].append({
                        "id": "note-1",
                        "title": "Contexto",
                        "content": "Dato estable",
                        "category": "general",
                    }),
                )

                def add_task_from_stale_task_view(state):
                    state["tasks"] = stale_state["tasks"] + [{
                        "id": "task-1",
                        "title": "Validar transaccion",
                        "status": "pending",
                        "priority": "media",
                    }]

                memory.state_transaction("add_task", add_task_from_stale_task_view)
                state = memory.load_state()

        self.assertEqual([note["id"] for note in state["notes"]], ["note-1"])
        self.assertEqual([task["id"] for task in state["tasks"]], ["task-1"])

    def test_state_transaction_can_skip_auto_backup_for_volatile_writes(self):
        base, state_path, lock_path = self._memory_protection_paths("transaction-backup-skip")
        backups_dir = base / ".yarbis_memory_backups"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(memory.default_state())
                initial_count = len(list(backups_dir.glob("*.json")))

                def mark_thinking(state):
                    state["runtime"]["thinking"]["active"] = True
                    state["runtime"]["thinking"]["label"] = "Ciclo"

                memory.state_transaction("runtime_thinking_start", mark_thinking, create_backup=False)
                after_volatile_count = len(list(backups_dir.glob("*.json")))

                memory.state_transaction(
                    "save_note",
                    lambda state: state["notes"].append({
                        "id": "note-1",
                        "title": "Dato estable",
                        "content": "Debe respaldarse.",
                        "category": "general",
                    }),
                )
                after_durable_count = len(list(backups_dir.glob("*.json")))

        self.assertEqual(after_volatile_count, initial_count)
        self.assertGreater(after_durable_count, after_volatile_count)

    def test_state_transaction_reports_busy_lock_clearly(self):
        state_path = TEST_RUNTIME_DIR / "memory_busy_lock_state.json"
        lock_path = TEST_RUNTIME_DIR / "memory_busy_lock_state.lock"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                with patch.object(memory, "_lock_state_handle", return_value=False):
                    with self.assertRaises(TimeoutError) as ctx:
                        memory.state_transaction("busy-test", lambda state: None)

        self.assertIn("No pude tomar el lock de estado", str(ctx.exception))
        self.assertIn("busy-test", str(ctx.exception))

    def test_save_state_creates_redacted_auto_backup_and_mirror(self):
        base, state_path, lock_path = self._memory_protection_paths("auto-mirror")
        mirror_dir = base / "mirror"

        seeded_state = memory.normalize_state({
            "goal": "memoria protegida",
            "notifications": {
                "enabled": True,
                "channels": ["ntfy", "telegram"],
                "ntfy": {"topic": "topic", "token": "ntfy-secret"},
                "telegram": {
                    "bot_token": "123456:secret",
                    "chat_id": "42",
                    "pending_power_confirmation": {
                        "action": "shutdown",
                        "token": "pending-secret",
                    },
                },
            },
            "ollama": {
                "model": "gpt-oss:120b",
                "host": "https://ollama.com",
                "api_key": "ollama-secret",
            },
            "model_provider": {
                "default": "openrouter",
                "ollama": {
                    "model": "gpt-oss:120b",
                    "host": "https://ollama.com",
                    "api_key": "ollama-secret",
                },
                "openrouter": {
                    "model": "openai/gpt-demo",
                    "api_key": "openrouter-secret",
                },
            },
            "memory_protection": {
                "mirror_dir": str(mirror_dir),
                "retention": {
                    "max_auto_backups": 10,
                    "keep_daily_days": 0,
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                with patch.object(memory, "MEMORY_PROTECTION_MAINTENANCE_ASYNC", False):
                    memory.save_state(seeded_state)

        stored = json.loads(state_path.read_text(encoding="utf-8"))
        local_backups = list((base / ".yarbis_memory_backups").glob("*.json"))
        mirror_backups = list(mirror_dir.glob("*.json"))

        self.assertTrue(stored["memory_protection"]["last_backup_at"])
        self.assertEqual(stored["memory_protection"]["last_error"], "")
        self.assertEqual(len(local_backups), 1)
        self.assertEqual(len(mirror_backups), 1)

        package = json.loads(local_backups[0].read_text(encoding="utf-8"))
        self.assertEqual(package["options"]["reason"], "auto_state_change")
        self.assertFalse(package["options"]["include_secrets"])
        self.assertEqual(package["state"]["notifications"]["ntfy"]["token"], "[redacted]")
        self.assertEqual(package["state"]["notifications"]["telegram"]["bot_token"], "[redacted]")
        self.assertEqual(
            package["state"]["notifications"]["telegram"]["pending_power_confirmation"]["token"],
            "[redacted]",
        )
        self.assertEqual(
            package["state"]["model_provider"]["ollama"]["api_key"],
            "[redacted]",
        )
        self.assertEqual(
            package["state"]["ollama"]["api_key"],
            "[redacted]",
        )
        self.assertEqual(
            package["state"]["model_provider"]["openrouter"]["api_key"],
            "[redacted]",
        )

    def test_auto_backup_mirror_does_not_hold_state_lock(self):
        base, state_path, lock_path = self._memory_protection_paths("async-mirror-lock")
        mirror_dir = base / "mirror"
        mirror_started = threading.Event()
        release_mirror = threading.Event()

        seeded_state = memory.normalize_state({
            "goal": "memoria sin bloqueo",
            "memory_protection": {
                "mirror_dir": str(mirror_dir),
            },
        })

        def slow_mirror(backup_path, mirror_dir):
            mirror_started.set()
            release_mirror.wait(timeout=2)
            return str(Path(mirror_dir) / Path(backup_path).name)

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                self.assertTrue(memory.wait_for_memory_protection_maintenance(timeout_seconds=2))
                with patch.object(memory.memory_backup, "mirror_backup", side_effect=slow_mirror):
                    memory.save_state(seeded_state)
                    self.assertTrue(mirror_started.wait(timeout=2))
                    state = memory.load_state()
                    release_mirror.set()
                    self.assertTrue(memory.wait_for_memory_protection_maintenance(timeout_seconds=2))

        self.assertEqual(state["goal"], "memoria sin bloqueo")

    def test_load_state_restores_corrupt_state_from_local_backup(self):
        base, state_path, lock_path = self._memory_protection_paths("restore-local")
        seeded_state = memory.normalize_state({"goal": "restaurar local"})

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(seeded_state)
                state_path.write_text("{", encoding="utf-8")
                restored = memory.load_state()

        recovery_files = list((base / ".yarbis_runtime" / "memory_recovery").glob("*.json"))
        self.assertEqual(restored["goal"], "restaurar local")
        self.assertTrue(restored["memory_protection"]["last_recovery_at"])
        self.assertIn("Estado restaurado", restored["memory_protection"]["last_error"])
        self.assertEqual(len(recovery_files), 1)

    def test_load_state_restores_missing_state_from_mirror_when_local_backup_is_gone(self):
        base, state_path, lock_path = self._memory_protection_paths("restore-mirror")
        mirror_dir = base / "mirror"
        seeded_state = memory.normalize_state({
            "goal": "restaurar espejo",
            "memory_protection": {
                "mirror_dir": str(mirror_dir),
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                with patch.object(memory, "MEMORY_PROTECTION_MAINTENANCE_ASYNC", False):
                    memory.save_state(seeded_state)
                shutil.rmtree(base / ".yarbis_memory_backups")
                state_path.unlink()
                restored = memory.load_state()

        self.assertEqual(restored["goal"], "restaurar espejo")
        self.assertTrue(restored["memory_protection"]["last_recovery_at"])
        self.assertTrue(state_path.exists())

    def test_load_state_without_valid_backup_preserves_corrupt_file_and_defaults(self):
        base, state_path, lock_path = self._memory_protection_paths("no-backup")
        state_path.write_text("{", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                state = memory.load_state()

        recovery_files = list((base / ".yarbis_runtime" / "memory_recovery").glob("*.json"))
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["goal"], memory.DEFAULT_GOAL)
        self.assertEqual(stored["goal"], memory.DEFAULT_GOAL)
        self.assertEqual(len(recovery_files), 1)
        self.assertIn("No pude leer", state["memory_protection"]["last_error"])

    def test_load_state_does_not_rewrite_unchanged_memory_protection_config(self):
        _base, state_path, lock_path = self._memory_protection_paths("config-cache")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                memory.save_state(memory.default_state())
                with patch.object(
                    memory.memory_backup,
                    "write_json_atomic",
                    side_effect=AssertionError("no debe escribir config sin cambios"),
                ):
                    state = memory.load_state()

        self.assertEqual(state["goal"], memory.DEFAULT_GOAL)

    def test_auto_backup_pruning_preserves_manual_backups(self):
        base, state_path, lock_path = self._memory_protection_paths("prune")
        backups_dir = base / ".yarbis_memory_backups"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory, "STATE_LOCK_FILE", lock_path):
                with patch.object(memory, "MEMORY_PROTECTION_MAINTENANCE_ASYNC", False):
                    with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                        memory.save_state(memory.normalize_state({
                            "goal": "version 0",
                            "memory_protection": {
                                "retention": {
                                    "max_auto_backups": 2,
                                    "keep_daily_days": 0,
                                },
                            },
                        }))
                        manual_backup = memory_transfer.create_backup()
                        for index in range(1, 6):
                            memory.save_state(memory.normalize_state({
                                "goal": f"version {index}",
                                "memory_protection": {
                                    "retention": {
                                        "max_auto_backups": 2,
                                        "keep_daily_days": 0,
                                    },
                                },
                            }))

        packages = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in backups_dir.glob("*.json")
        ]
        auto_packages = [
            package
            for package in packages
            if package["options"]["reason"] == "auto_state_change"
        ]
        manual_ids = [
            package["id"]
            for package in packages
            if package["options"]["reason"] == "manual"
        ]

        self.assertLessEqual(len(auto_packages), 2)
        self.assertIn(manual_backup["id"], manual_ids)

    def test_render_state_summary_can_omit_last_result_for_internal_context(self):
        summary = memory.render_state_summary(
            {
                "last_result": "respuesta larga " * 500,
            },
            include_last_result=False,
        )

        self.assertNotIn("Ultimo resultado:", summary)

    def test_normalize_state_sanitizes_profile_notes_and_tasks(self):
        normalized = memory.normalize_state({
            "profile": {
                "name": "A" * 200,
                "preferences": "rapido, rapido, local",
                "constraints": ["sin nube", "", "sin destruir archivos"],
            },
            "notes": [
                {"title": "", "content": ""},
                {"title": "Contexto", "content": "x" * (memory.MAX_NOTE_CONTENT_CHARS + 50)},
            ],
            "tasks": [
                {"title": "Preparar backlog", "status": "desconocido", "priority": "urgente"},
            ],
            "current_plan": "Definir objetivo\nCrear tareas\nEjecutar",
        })

        self.assertEqual(normalized["profile"]["preferences"], ["rapido", "local"])
        self.assertEqual(normalized["profile"]["constraints"], ["sin nube", "sin destruir archivos"])
        self.assertEqual(len(normalized["notes"]), 1)
        self.assertEqual(normalized["notes"][0]["content"], "x" * (memory.MAX_NOTE_CONTENT_CHARS + 50))
        self.assertEqual(normalized["tasks"][0]["status"], "pending")
        self.assertEqual(normalized["tasks"][0]["priority"], "media")
        self.assertEqual(len(normalized["current_plan"]), 3)

    def test_normalize_state_keeps_pending_user_question_only_when_valid(self):
        normalized = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "x" * (memory.MAX_AWAITING_INPUT_QUESTION_CHARS + 20),
                "reason": "Falta definir el nicho principal",
                "fields": "nicho, audiencia, audiencia",
            }
        })

        pending = normalized["awaiting_user_input"]

        self.assertTrue(pending["pending"])
        self.assertEqual(pending["question"], "x" * (memory.MAX_AWAITING_INPUT_QUESTION_CHARS + 20))
        self.assertEqual(pending["fields"], ["nicho", "audiencia"])

    def test_normalize_state_uses_supported_ui_theme(self):
        normalized = memory.normalize_state({
            "ui": {"theme": "light"}
        })
        fallback = memory.normalize_state({
            "ui": {"theme": "neon"}
        })

        self.assertEqual(normalized["ui"]["theme"], "light")
        self.assertEqual(fallback["ui"]["theme"], "dark")

    def test_normalize_state_sanitizes_ollama_settings(self):
        normalized = memory.normalize_state({
            "ollama": {
                "model": "",
                "timeout_seconds": 999_999,
            },
        })
        custom = memory.normalize_state({
            "ollama": {
                "model": "llama3.2:3b",
                "fallback_models": "gpt-oss:120b-cloud, qwen3.5:0.8b",
                "host": "https://ollama.com/api",
                "api_key": "ollama-secret",
                "api_key_env_var": "OLLAMA_API_KEY",
                "timeout_seconds": 900,
            },
        })

        self.assertEqual(normalized["ollama"]["model"], memory.DEFAULT_OLLAMA_MODEL)
        self.assertEqual(
            normalized["ollama"]["timeout_seconds"],
            memory.MAX_OLLAMA_TIMEOUT_SECONDS,
        )
        self.assertEqual(custom["ollama"]["model"], "llama3.2:3b")
        self.assertEqual(
            custom["ollama"]["fallback_models"],
            ["gpt-oss:120b-cloud", "qwen3.5:0.8b"],
        )
        self.assertEqual(custom["ollama"]["host"], "https://ollama.com")
        self.assertEqual(custom["ollama"]["api_key"], "ollama-secret")
        self.assertEqual(custom["ollama"]["api_key_env_var"], "OLLAMA_API_KEY")
        self.assertEqual(custom["ollama"]["timeout_seconds"], 900)

    def test_normalize_state_migrates_legacy_ollama_to_model_provider(self):
        normalized = memory.normalize_state({
            "ollama": {
                "model": "llama3.2:3b",
                "fallback_models": ["qwen3.5:0.8b"],
                "host": "https://ollama.com/api",
                "api_key": "ollama-secret",
                "timeout_seconds": 900,
            },
        })

        self.assertEqual(normalized["model_provider"]["default"], "ollama")
        self.assertEqual(normalized["model_provider"]["ollama"]["model"], "llama3.2:3b")
        self.assertEqual(normalized["model_provider"]["ollama"]["host"], "https://ollama.com")
        self.assertEqual(normalized["model_provider"]["ollama"]["api_key"], "ollama-secret")
        self.assertEqual(normalized["ollama"], normalized["model_provider"]["ollama"])

    def test_normalize_state_sanitizes_openrouter_settings(self):
        normalized = memory.normalize_state({
            "model_provider": {
                "default": "openrouter",
                "openrouter": {
                    "model": "openai/gpt-demo",
                    "fallback_models": "anthropic/claude-demo, openai/gpt-demo",
                    "host": "https://openrouter.ai/api/v1/chat/completions",
                    "api_key": "openrouter-secret",
                    "api_key_env_var": "OPENROUTER_API_KEY",
                    "timeout_seconds": 1200,
                },
            },
        })

        self.assertEqual(normalized["model_provider"]["default"], "openrouter")
        self.assertEqual(normalized["model_provider"]["openrouter"]["model"], "openai/gpt-demo")
        self.assertEqual(
            normalized["model_provider"]["openrouter"]["fallback_models"],
            ["anthropic/claude-demo"],
        )
        self.assertEqual(
            normalized["model_provider"]["openrouter"]["host"],
            "https://openrouter.ai/api/v1",
        )
        self.assertEqual(
            normalized["model_provider"]["openrouter"]["api_key_env_var"],
            "OPENROUTER_API_KEY",
        )
        self.assertEqual(
            normalized["model_provider"]["openrouter"]["api_key"],
            "openrouter-secret",
        )

    def test_normalize_state_sanitizes_runtime_thinking_state(self):
        normalized = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Ciclo" * 40,
                    "source": "pid:1234",
                    "started_at": "2026-05-01T12:00:00+00:00",
                },
            },
        })
        inactive = memory.normalize_state({
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "",
                },
            },
        })

        thinking = normalized["runtime"]["thinking"]
        self.assertTrue(thinking["active"])
        self.assertEqual(thinking["label"], "Ciclo" * 40)
        self.assertEqual(thinking["source"], "pid:1234")
        self.assertFalse(inactive["runtime"]["thinking"]["active"])

    def test_normalize_state_sanitizes_service_proactive_settings(self):
        normalized = memory.normalize_state({
            "service": {
                "proactive": {
                    "enabled": "false",
                    "interval_seconds": 10,
                    "cycles": 99,
                    "start_delay_seconds": -20,
                    "model": "qwen3.5:0.8b",
                }
            }
        })

        proactive = normalized["service"]["proactive"]

        self.assertFalse(proactive["enabled"])
        self.assertEqual(proactive["interval_seconds"], 60)
        self.assertEqual(proactive["cycles"], 5)
        self.assertEqual(proactive["start_delay_seconds"], 0)
        self.assertEqual(proactive["model"], "qwen3.5:0.8b")

    def test_normalize_state_sanitizes_local_context_settings(self):
        normalized = memory.normalize_state({
            "local_context": {
                "enabled": True,
                "mode": "DETAILED",
                "sample_interval_seconds": 1,
                "max_snapshot_age_seconds": 999_999,
                "include_window_title": True,
                "include_process_name": False,
                "include_workspace_changes": False,
                "include_system_health": True,
            }
        })
        off_state = memory.normalize_state({
            "local_context": {
                "enabled": True,
                "mode": "off",
                "include_window_title": True,
            }
        })

        settings = normalized["local_context"]
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["mode"], "detailed")
        self.assertEqual(settings["sample_interval_seconds"], 5)
        self.assertEqual(settings["max_snapshot_age_seconds"], 24 * 60 * 60)
        self.assertTrue(settings["include_window_title"])
        self.assertFalse(settings["include_process_name"])
        self.assertFalse(settings["include_workspace_changes"])
        self.assertTrue(settings["include_system_health"])
        self.assertFalse(off_state["local_context"]["enabled"])
        self.assertFalse(off_state["local_context"]["include_window_title"])

    def test_normalize_state_sanitizes_internet_settings(self):
        normalized = memory.normalize_state({
            "internet": {
                "mode": "AUTO",
                "provider": "desconocido",
                "max_search_results": 99,
                "max_page_chars": 999_999,
                "request_timeout_seconds": 1,
                "allowed_domains": "example.com, https://docs.python.org/, example.com",
                "blocked_domains": ["localhost", "https://news.ycombinator.com/"],
            }
        })

        settings = normalized["internet"]

        self.assertEqual(settings["mode"], "auto")
        self.assertEqual(settings["provider"], memory.DEFAULT_SEARCH_PROVIDER)
        self.assertEqual(settings["max_search_results"], 10)
        self.assertEqual(settings["max_page_chars"], 30_000)
        self.assertEqual(settings["request_timeout_seconds"], 3)
        self.assertEqual(settings["allowed_domains"], ["example.com", "docs.python.org"])
        self.assertEqual(settings["blocked_domains"], ["localhost", "news.ycombinator.com"])

    def test_normalize_state_sanitizes_notification_settings(self):
        normalized = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": "windows, ntfy, telegram, desconocido, ntfy",
                "ntfy": {
                    "server": "",
                    "topic": "/yarbis-secret/",
                    "token": "token-123",
                    "priority": "urgent",
                    "tags": "yarbis,warning",
                    "timeout_seconds": 120,
                },
                "telegram": {
                    "api_base": "",
                    "bot_token": "bot-123",
                    "chat_id": " 456 ",
                    "timeout_seconds": 120,
                    "poll_timeout_seconds": 0,
                    "last_update_id": -4,
                },
            },
        })

        settings = normalized["notifications"]

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["channels"], ["windows", "ntfy", "telegram"])
        self.assertEqual(settings["ntfy"]["server"], memory.DEFAULT_NTFY_SERVER)
        self.assertEqual(settings["ntfy"]["topic"], "yarbis-secret")
        self.assertEqual(settings["ntfy"]["priority"], "urgent")
        self.assertEqual(settings["ntfy"]["timeout_seconds"], 60)
        self.assertEqual(settings["telegram"]["api_base"], memory.DEFAULT_TELEGRAM_API_BASE)
        self.assertEqual(settings["telegram"]["bot_token"], "bot-123")
        self.assertEqual(settings["telegram"]["chat_id"], "456")
        self.assertEqual(settings["telegram"]["timeout_seconds"], 60)
        self.assertEqual(settings["telegram"]["poll_timeout_seconds"], 1)
        self.assertEqual(settings["telegram"]["last_update_id"], 0)

    def test_render_state_summary_highlights_unlinked_telegram(self):
        summary = memory.render_state_summary({
            "internet": {
                "mode": "auto",
            },
            "notifications": {
                "enabled": True,
                "channels": ["windows", "telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "",
                },
            },
        })

        self.assertIn("Internet: modo=auto", summary)
        self.assertIn("canales=windows, telegram", summary)
        self.assertIn("Telegram: pendiente de vincular", summary)

    def test_render_state_summary_shows_self_knowledge_timestamp(self):
        summary = memory.render_state_summary({
            "self_knowledge": {
                "last_analyzed_at": "2026-04-29T12:00:00+00:00",
                "summary": "Identidad: Yarbis",
            },
        })

        self.assertIn("Autoconocimiento: actualizado en 2026-04-29T12:00:00+00:00.", summary)

    def test_render_state_summary_separates_assistant_and_user_identity(self):
        summary = memory.render_state_summary({
            "profile": {
                "name": "Ahiram",
            },
        })

        self.assertIn("Identidad: asistente=Yarbis; usuario=Ahiram", summary)
