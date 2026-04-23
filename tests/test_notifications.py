import os
import unittest
from unittest.mock import Mock, patch

import notifications


class NotificationsTestCase(unittest.TestCase):
    def test_send_notification_returns_false_when_provider_is_missing(self):
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

        with patch.dict(os.environ, {"YARBIS_NOTIFICATIONS": "1"}, clear=False):
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
