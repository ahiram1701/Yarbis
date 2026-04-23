import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import memory
import notifications

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class NotificationsTestCase(unittest.TestCase):
    def test_send_notification_returns_false_when_provider_is_missing(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATION_CHANNELS": "windows,ntfy",
                "YARBIS_NTFY_TOPIC": "",
            },
            clear=False,
        ):
            with patch.object(notifications, "_get_notify_provider", return_value=None):
                self.assertFalse(notifications.send_notification("Hola", "Mundo"))

    def test_send_notification_respects_disable_env_var(self):
        mock_notify = Mock()

        with patch.dict(os.environ, {"YARBIS_NOTIFICATIONS": "0"}, clear=False):
            with patch.object(notifications, "_get_notify_provider", return_value=mock_notify):
                result = notifications.send_notification("Hola", "Mundo")

        self.assertFalse(result)
        mock_notify.assert_not_called()

    def test_notify_user_input_required_uses_win11toast_when_available(self):
        mock_notify = Mock()

        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "windows",
            },
            clear=False,
        ):
            with patch.object(notifications, "_get_notify_provider", return_value=mock_notify):
                result = notifications.notify_user_input_required(
                    "Que nicho quieres trabajar?",
                    "Falta ese dato para continuar.",
                )

        self.assertTrue(result)
        args = mock_notify.call_args.args
        kwargs = mock_notify.call_args.kwargs

        self.assertEqual(
            args,
            (
                "Yarbis necesita tu respuesta",
                "Que nicho quieres trabajar?\nFalta ese dato para continuar.",
            ),
        )
        self.assertEqual(kwargs["group"], "yarbis")
        self.assertTrue(kwargs["tag"].startswith("yarbis-"))

    def test_send_notification_posts_to_ntfy_when_topic_is_configured(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "ntfy",
                "YARBIS_NTFY_SERVER": "https://ntfy.example/",
                "YARBIS_NTFY_TOPIC": "yarbis-secret",
                "YARBIS_NTFY_TOKEN": "token-123",
                "YARBIS_NTFY_PRIORITY": "high",
                "YARBIS_NTFY_TAGS": "warning, yarbis",
                "YARBIS_NTFY_TIMEOUT_SECONDS": "3",
            },
            clear=False,
        ):
            with patch.object(notifications, "_post_ntfy_payload", return_value=None) as post_mock:
                result = notifications.send_notification("Titulo", "Cuerpo")

        self.assertTrue(result)
        post_mock.assert_called_once_with(
            "https://ntfy.example",
            {
                "topic": "yarbis-secret",
                "title": "Titulo",
                "message": "Cuerpo",
                "priority": "high",
                "tags": ["warning", "yarbis"],
            },
            token="token-123",
            timeout=3,
        )

    def test_send_notification_ignores_ntfy_without_topic(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "ntfy",
                "YARBIS_NTFY_TOPIC": "",
            },
            clear=False,
        ):
            with patch.object(notifications, "_post_ntfy_payload", return_value=None) as post_mock:
                result = notifications.send_notification("Titulo", "Cuerpo")

        self.assertFalse(result)
        post_mock.assert_not_called()

    def test_send_notification_uses_persisted_ntfy_settings(self):
        state_path = TEST_RUNTIME_DIR / "notifications_persisted_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["ntfy"],
                "ntfy": {
                    "server": "https://ntfy.persisted",
                    "topic": "persisted-topic",
                    "token": "",
                    "priority": "low",
                    "tags": "yarbis",
                    "timeout_seconds": 7,
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(notifications, "_post_ntfy_payload", return_value=None) as post_mock:
                    result = notifications.send_notification("Titulo", "Cuerpo")

        self.assertTrue(result)
        post_mock.assert_called_once_with(
            "https://ntfy.persisted",
            {
                "topic": "persisted-topic",
                "title": "Titulo",
                "message": "Cuerpo",
                "priority": "low",
                "tags": ["yarbis"],
            },
            token="",
            timeout=7,
        )
