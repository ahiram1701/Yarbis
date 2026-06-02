import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yarbis_bus
import yarbis_instance


TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class YarbisBusTestCase(unittest.TestCase):
    def _instances_root(self) -> Path:
        root = TEST_RUNTIME_DIR / f"bus-{uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_send_message_to_inactive_instance_stays_queued(self):
        root = self._instances_root()
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                result = yarbis_bus.send_message("beta", "hola beta", wait_for_reply=True, timeout_seconds=1)

        self.assertEqual(result["status"], yarbis_bus.STATUS_QUEUED)
        self.assertEqual(result["status_text"], "queued")
        self.assertTrue((root / "_bus" / f"{result['id']}.json").exists())

    def test_process_pending_message_completes_and_writes_reply(self):
        root = self._instances_root()
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                sent = yarbis_bus.send_message("beta", "hola beta", wait_for_reply=False)

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "beta"}):
                processed = yarbis_bus.process_pending_messages(
                    runner=lambda message: f"respuesta a {message['from_instance']}"
                )

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                updated = yarbis_bus.wait_for_response(sent["id"], timeout_seconds=0)
                replies = yarbis_bus.list_messages(unread_only=True)

        self.assertEqual(processed, 1)
        self.assertEqual(updated["status"], yarbis_bus.STATUS_DONE)
        self.assertEqual(updated["response"], "respuesta a alpha")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["kind"], yarbis_bus.KIND_DIRECT_REPLY)


if __name__ == "__main__":
    unittest.main()
