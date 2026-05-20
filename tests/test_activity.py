import unittest
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import activity
import secrets_redaction
import yarbis_service

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class ActivityTestCase(unittest.TestCase):
    def _unique_path(self, *parts: str) -> Path:
        return TEST_RUNTIME_DIR / "activity" / uuid.uuid4().hex / Path(*parts)

    def test_append_read_and_clear_activity_log(self):
        log_path = self._unique_path("activity.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(activity, "ACTIVITY_LOG_FILE", log_path):
            rendered = activity.append_activity(
                "Ciclo",
                "Linea uno\r\nLinea dos",
                timestamp="2026-05-02 14:30:00",
            )
            self.assertEqual(rendered, activity.read_activity_log())
            self.assertIn("[2026-05-02 14:30:00] Ciclo", rendered)
            self.assertIn("Linea uno\nLinea dos", rendered)

            activity.clear_activity_log()
            self.assertEqual(activity.read_activity_log(), "")

    def test_service_log_also_records_public_activity(self):
        base_dir = self._unique_path("service").parent
        base_dir.mkdir(parents=True, exist_ok=True)
        service_log = base_dir / "service.log"
        activity_log = base_dir / "activity.log"

        with patch.object(yarbis_service, "LOG_FILE", service_log):
            with patch.object(activity, "ACTIVITY_LOG_FILE", activity_log):
                yarbis_service._log("Pulso proactivo iniciado.\nDetalle visible.")

        self.assertIn("Pulso proactivo iniciado.", service_log.read_text(encoding="utf-8"))
        public_activity = activity_log.read_text(encoding="utf-8")
        self.assertIn("Servicio", public_activity)
        self.assertIn("Pulso proactivo iniciado.\nDetalle visible.", public_activity)

    def test_service_log_and_activity_redact_known_secret_tokens(self):
        base_dir = self._unique_path("service_redaction").parent
        base_dir.mkdir(parents=True, exist_ok=True)
        service_log = base_dir / "service.log"
        activity_log = base_dir / "activity.log"

        with patch.dict(
            yarbis_service.os.environ,
            {
                "YARBIS_TELEGRAM_BOT_TOKEN": "123456:abc",
                "YARBIS_NTFY_TOKEN": "token-123",
            },
            clear=False,
        ):
            with patch.object(yarbis_service, "LOG_FILE", service_log):
                with patch.object(activity, "ACTIVITY_LOG_FILE", activity_log):
                    yarbis_service._log(
                        "Fallo en https://api.telegram.org/bot123456:abc/sendMessage "
                        "con Authorization: Bearer token-123"
                    )

        service_text = service_log.read_text(encoding="utf-8")
        activity_text = activity_log.read_text(encoding="utf-8")
        self.assertNotIn("123456:abc", service_text)
        self.assertNotIn("token-123", service_text)
        self.assertNotIn("123456:abc", activity_text)
        self.assertNotIn("token-123", activity_text)
        self.assertIn("[redacted]", service_text)

    def test_emit_event_writes_jsonl_and_redacts_secrets(self):
        events_path = self._unique_path("events.jsonl")
        events_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.dict(
            os.environ,
            {
                "YARBIS_TELEGRAM_BOT_TOKEN": "123456:abc",
                "YARBIS_NTFY_TOKEN": "token-123",
            },
            clear=False,
        ):
            with patch.object(activity, "EVENTS_FILE", events_path):
                event = activity.emit_event(
                    "telegram_error",
                    operation_id="op-1",
                    content="https://api.telegram.org/bot123456:abc/sendMessage Bearer token-123",
                )

        stored = json.loads(events_path.read_text(encoding="utf-8").strip())
        self.assertEqual(event["type"], "telegram_error")
        self.assertEqual(stored["operation_id"], "op-1")
        self.assertNotIn("123456:abc", stored["content"])
        self.assertNotIn("token-123", stored["content"])
        self.assertIn("[redacted]", stored["content"])

    def test_read_activity_history_includes_recent_events(self):
        log_path = self._unique_path("activity_history.log")
        events_path = log_path.with_name("events.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(activity, "ACTIVITY_LOG_FILE", log_path):
            with patch.object(activity, "EVENTS_FILE", events_path):
                activity.append_activity("Manual", "Entrada visible.", timestamp="2026-05-05 10:00:00")
                activity.emit_event(
                    "remote_job_finished",
                    label="Telegram",
                    content="Respuesta enviada.",
                )
                rendered = activity.read_activity_history()

        self.assertIn("Manual", rendered)
        self.assertIn("Eventos recientes", rendered)
        self.assertIn("Telegram remoto finalizado", rendered)
        self.assertIn("Respuesta enviada.", rendered)

    def test_render_recent_events_reuses_redactor_state_load(self):
        events_path = self._unique_path("events_redactor.jsonl")
        events_path.parent.mkdir(parents=True, exist_ok=True)
        secret = "ntfy-secret-value"
        events_path.write_text(
            "\n".join(
                json.dumps({
                    "timestamp": "2026-05-05T10:00:00+00:00",
                    "type": "remote_job_finished",
                    "label": "Telegram",
                    "content": f"Respuesta {secret} {index}",
                })
                for index in range(10)
            )
            + "\n",
            encoding="utf-8",
        )
        state = {
            "notifications": {
                "ntfy": {"token": secret},
            }
        }

        with patch.object(activity, "EVENTS_FILE", events_path):
            with patch.object(secrets_redaction, "_load_current_state", return_value=state) as load_mock:
                rendered = activity.render_recent_events(limit=10)

        self.assertEqual(load_mock.call_count, 1)
        self.assertNotIn(secret, rendered)
        self.assertIn("[redacted]", rendered)

    def test_emit_event_trims_large_events_file(self):
        events_path = self._unique_path("events_trim.jsonl")
        events_path.parent.mkdir(parents=True, exist_ok=True)
        old_lines = [
            json.dumps({"timestamp": "2026-05-05T10:00:00+00:00", "type": "old", "content": "x" * 80})
            for _ in range(20)
        ]
        events_path.write_text("\n".join(old_lines) + "\n", encoding="utf-8")

        with patch.object(activity, "EVENTS_FILE", events_path):
            with patch.object(activity, "MAX_EVENTS_FILE_BYTES", 700):
                with patch.object(activity, "KEEP_EVENTS_FILE_BYTES", 350):
                    activity.emit_event("new", content="fresh")

        rendered = events_path.read_text(encoding="utf-8")
        self.assertLessEqual(events_path.stat().st_size, 700)
        self.assertIn('"type": "new"', rendered)

    def test_read_activity_history_can_limit_activity_log_bytes(self):
        log_path = self._unique_path("activity_history_tail.log")
        events_path = log_path.with_name("events.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("primera linea\n" + ("x" * 200) + "\nultima linea\n", encoding="utf-8")

        with patch.object(activity, "ACTIVITY_LOG_FILE", log_path):
            with patch.object(activity, "EVENTS_FILE", events_path):
                rendered = activity.read_activity_history(activity_max_bytes=64)

        self.assertIn("historial anterior omitido", rendered)
        self.assertIn("ultima linea", rendered)
        self.assertNotIn("primera linea", rendered)

    def test_activity_history_signature_changes_with_log_file(self):
        log_path = self._unique_path("activity_signature.log")
        events_path = log_path.with_name("events.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(activity, "ACTIVITY_LOG_FILE", log_path):
            with patch.object(activity, "EVENTS_FILE", events_path):
                before = activity.activity_history_signature()
                activity.append_activity("Demo", "Contenido")
                after = activity.activity_history_signature()

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
