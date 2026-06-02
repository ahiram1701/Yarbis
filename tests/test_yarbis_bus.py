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

    def test_ui_message_listing_does_not_mark_replies_read(self):
        root = self._instances_root()
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                sent = yarbis_bus.send_message("beta", "hola beta", wait_for_reply=False)

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "beta"}):
                yarbis_bus.process_pending_messages(runner=lambda _message: "respuesta beta")

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                messages = yarbis_bus.list_instance_messages()
                counts = yarbis_bus.message_counts()
                reply = [item for item in messages if item["kind"] == yarbis_bus.KIND_DIRECT_REPLY][0]
                still_unread = yarbis_bus.wait_for_response(sent["id"], timeout_seconds=0)

        self.assertEqual(reply["status"], yarbis_bus.STATUS_QUEUED)
        self.assertEqual(counts["unread_replies"], 1)
        self.assertEqual(still_unread["status"], yarbis_bus.STATUS_DONE)

    def test_mark_messages_read_only_updates_selected_replies_for_receiver(self):
        root = self._instances_root()
        with patch.object(yarbis_instance, "INSTANCES_ROOT", root):
            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                sent = yarbis_bus.send_message("beta", "hola beta", wait_for_reply=False)

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "beta"}):
                yarbis_bus.process_pending_messages(runner=lambda _message: "respuesta beta")

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "alpha"}):
                reply = [
                    item for item in yarbis_bus.list_instance_messages()
                    if item["kind"] == yarbis_bus.KIND_DIRECT_REPLY
                ][0]
                marked = yarbis_bus.mark_messages_read([reply["id"], sent["id"]])
                counts = yarbis_bus.message_counts()
                messages = yarbis_bus.list_instance_messages()

            with patch.dict(os.environ, {yarbis_instance.ENV_INSTANCE: "beta"}):
                marked_from_wrong_instance = yarbis_bus.mark_messages_read([reply["id"]])

        self.assertEqual(marked, 1)
        self.assertEqual(marked_from_wrong_instance, 0)
        self.assertEqual(counts["unread_replies"], 0)
        self.assertEqual(
            [item for item in messages if item["id"] == reply["id"]][0]["status"],
            yarbis_bus.STATUS_READ,
        )


if __name__ == "__main__":
    unittest.main()
