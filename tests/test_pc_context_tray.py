import unittest
from unittest.mock import Mock

import pc_context_tray


class PcContextTrayTestCase(unittest.TestCase):
    def test_capture_once_captures_only_when_service_running_and_context_enabled(self):
        collect_mock = Mock(return_value={"captured_at": "2026-05-06T12:00:00+00:00"})
        clear_mock = Mock()
        write_status_mock = Mock()

        status = pc_context_tray.capture_once(
            load_state_func=lambda: {
                "local_context": {
                    "enabled": True,
                    "mode": "safe",
                    "sample_interval_seconds": 30,
                    "max_snapshot_age_seconds": 180,
                }
            },
            get_service_status_func=lambda: {"running": True},
            collect_func=collect_mock,
            clear_func=clear_mock,
            write_status_func=write_status_mock,
        )

        self.assertEqual(status["state"], "capturando")
        collect_mock.assert_called_once()
        clear_mock.assert_not_called()
        write_status_mock.assert_called_once()

    def test_capture_once_pauses_and_clears_snapshot_when_service_is_stopped(self):
        collect_mock = Mock()
        clear_mock = Mock()

        status = pc_context_tray.capture_once(
            load_state_func=lambda: {
                "local_context": {
                    "enabled": True,
                    "mode": "safe",
                    "sample_interval_seconds": 30,
                }
            },
            get_service_status_func=lambda: {"running": False},
            collect_func=collect_mock,
            clear_func=clear_mock,
            write_status_func=Mock(),
        )

        self.assertEqual(status["state"], "pausado")
        collect_mock.assert_not_called()
        clear_mock.assert_called_once()

    def test_capture_once_disables_and_clears_snapshot_when_context_is_off(self):
        collect_mock = Mock()
        clear_mock = Mock()

        status = pc_context_tray.capture_once(
            load_state_func=lambda: {
                "local_context": {
                    "enabled": False,
                    "mode": "off",
                }
            },
            get_service_status_func=lambda: {"running": True},
            collect_func=collect_mock,
            clear_func=clear_mock,
            write_status_func=Mock(),
        )

        self.assertEqual(status["state"], "desactivado")
        collect_mock.assert_not_called()
        clear_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
