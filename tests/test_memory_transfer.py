import json
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import memory
import memory_transfer

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class MemoryTransferTestCase(unittest.TestCase):
    def _paths(self, name: str):
        base = TEST_RUNTIME_DIR / "memory_transfer" / f"{name}-{uuid4().hex[:8]}"
        base.mkdir(parents=True, exist_ok=True)
        return base / "state.json", base / ".yarbis_memory_backups"

    def _seeded_state(self, label: str = "Origen") -> dict:
        return memory.normalize_state({
            "goal": f"Objetivo {label}",
            "messages": [{"role": "user", "content": f"hola {label}"}],
            "profile": {
                "name": label,
                "role": "tester",
                "preferences": ["local"],
                "constraints": ["sin nube"],
            },
            "notes": [{
                "id": f"note-{label.casefold()}",
                "title": f"Dato {label}",
                "content": "memoria estable",
                "category": "general",
            }],
            "tasks": [{
                "id": f"task-{label.casefold()}",
                "title": f"Tarea {label}",
                "status": "pending",
                "priority": "media",
            }],
            "current_plan": [f"Plan {label}"],
            "runtime": {
                "thinking": {
                    "active": True,
                    "label": "Operacion vieja",
                    "source": "pid:123",
                    "started_at": "2026-05-16T00:00:00+00:00",
                    "operation_id": "op-vieja",
                }
            },
            "self_knowledge": {
                "last_analyzed_at": "2026-05-16T00:00:00+00:00",
                "summary": f"Autoconocimiento {label}",
            },
            "notifications": {
                "enabled": True,
                "channels": ["ntfy", "telegram"],
                "ntfy": {
                    "topic": "topic",
                    "token": f"ntfy-{label}",
                },
                "telegram": {
                    "bot_token": f"123456:{label}",
                    "chat_id": "42",
                    "pending_power_confirmation": {
                        "action": "shutdown",
                        "token": f"pending-{label}",
                    },
                },
            },
            "model_provider": {
                "default": "openrouter",
                "openrouter": {
                    "model": "openai/gpt-demo",
                    "api_key": f"openrouter-{label}",
                },
            },
        })

    def test_create_backup_redacts_secrets_by_default(self):
        state_path, backups_dir = self._paths("redacts")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                memory.save_state(self._seeded_state())
                result = memory_transfer.create_backup()

        package = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

        self.assertEqual(package["format"], memory_transfer.BACKUP_FORMAT)
        self.assertEqual(package["schema_version"], memory_transfer.SCHEMA_VERSION)
        self.assertFalse(package["options"]["include_secrets"])
        self.assertEqual(
            set(package["redacted_paths"]),
            set(memory_transfer.SECRET_PATHS),
        )
        self.assertEqual(package["state"]["notifications"]["ntfy"]["token"], memory_transfer.REDACTED_VALUE)
        self.assertEqual(package["state"]["notifications"]["telegram"]["bot_token"], memory_transfer.REDACTED_VALUE)
        self.assertEqual(
            package["state"]["notifications"]["telegram"]["pending_power_confirmation"]["token"],
            memory_transfer.REDACTED_VALUE,
        )
        self.assertEqual(
            package["state"]["model_provider"]["openrouter"]["api_key"],
            memory_transfer.REDACTED_VALUE,
        )

    def test_create_backup_can_include_secrets(self):
        state_path, backups_dir = self._paths("include-secrets")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                memory.save_state(self._seeded_state("Secreto"))
                result = memory_transfer.create_backup(include_secrets=True)

        package = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

        self.assertTrue(package["options"]["include_secrets"])
        self.assertEqual(package["redacted_paths"], [])
        self.assertEqual(package["state"]["notifications"]["ntfy"]["token"], "ntfy-Secreto")
        self.assertEqual(package["state"]["notifications"]["telegram"]["bot_token"], "123456:Secreto")
        self.assertEqual(package["state"]["model_provider"]["openrouter"]["api_key"], "openrouter-Secreto")

    def test_replace_import_preserves_destination_secrets_for_redacted_backup(self):
        state_path, backups_dir = self._paths("replace")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                memory.save_state(self._seeded_state("Origen"))
                backup = memory_transfer.create_backup()

                destination = self._seeded_state("Destino")
                destination["notifications"]["ntfy"]["token"] = "ntfy-destino"
                destination["notifications"]["telegram"]["bot_token"] = "123456:destino"
                destination["notifications"]["telegram"]["pending_power_confirmation"]["token"] = "pending-destino"
                memory.save_state(destination)

                result = memory_transfer.import_backup(backup["path"], mode="replace")
                state = memory.load_state()
                saved_backups = memory_transfer.list_backups(limit=10)

        self.assertEqual(result["mode"], "replace")
        self.assertEqual(state["goal"], "Objetivo Origen")
        self.assertFalse(state["runtime"]["thinking"]["active"])
        self.assertEqual(state["notifications"]["ntfy"]["token"], "ntfy-destino")
        self.assertEqual(state["notifications"]["telegram"]["bot_token"], "123456:destino")
        self.assertEqual(
            state["notifications"]["telegram"]["pending_power_confirmation"]["token"],
            "pending-destino",
        )
        self.assertTrue(any(item["id"] == result["safety_backup"]["id"] for item in saved_backups))

    def test_merge_import_combines_memories_and_preserves_local_configuration(self):
        state_path, backups_dir = self._paths("merge")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                memory.save_state(self._seeded_state("Origen"))
                backup = memory_transfer.create_backup(include_secrets=True)

                destination = self._seeded_state("Destino")
                destination["ollama"]["model"] = "modelo-local"
                destination["internet"]["mode"] = "off"
                memory.save_state(destination)

                memory_transfer.import_backup(backup["path"], mode="merge")
                state = memory.load_state()

        self.assertEqual(state["goal"], "Objetivo Destino")
        self.assertEqual(state["ollama"]["model"], "modelo-local")
        self.assertEqual(state["internet"]["mode"], "off")
        self.assertEqual(state["profile"]["name"], "Origen")
        self.assertEqual(state["profile"]["preferences"], ["local"])
        self.assertEqual(
            [note["id"] for note in state["notes"]],
            ["note-destino", "note-origen"],
        )
        self.assertEqual(
            [task["id"] for task in state["tasks"]],
            ["task-destino", "task-origen"],
        )
        self.assertEqual(
            [message["content"] for message in state["messages"]],
            ["hola Destino", "hola Origen"],
        )
        self.assertFalse(state["runtime"]["thinking"]["active"])

    def test_invalid_import_does_not_modify_state(self):
        state_path, backups_dir = self._paths("invalid")
        invalid_path = backups_dir / "broken.json"
        backups_dir.mkdir(parents=True, exist_ok=True)
        invalid_path.write_text("{", encoding="utf-8")

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(memory_transfer, "BACKUPS_DIR", backups_dir):
                memory.save_state(self._seeded_state("Destino"))
                before = memory.load_state()
                with self.assertRaises(memory_transfer.MemoryTransferError):
                    memory_transfer.import_backup(str(invalid_path), mode="replace")
                after = memory.load_state()

        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
