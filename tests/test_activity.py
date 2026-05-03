import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import activity
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


if __name__ == "__main__":
    unittest.main()
