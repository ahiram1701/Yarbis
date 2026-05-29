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

    def test_send_notification_posts_to_telegram_when_configured(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "bot-123",
                "YARBIS_TELEGRAM_CHAT_ID": "456",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "telegram_api_request",
                return_value={"ok": True, "result": {}},
            ) as telegram_mock:
                result = notifications.send_notification("Titulo", "Cuerpo")

        self.assertTrue(result)
        telegram_mock.assert_called_once()
        self.assertEqual(telegram_mock.call_args.args[0], "sendMessage")
        self.assertEqual(
            telegram_mock.call_args.args[1],
            {
                "chat_id": "456",
                "text": "Titulo\n\nCuerpo",
                "disable_web_page_preview": True,
            },
        )

    def test_send_telegram_message_labels_long_chunks(self):
        long_text = "a" * (notifications.MAX_TELEGRAM_MESSAGE_CHARS + 120)

        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "bot-123",
                "YARBIS_TELEGRAM_CHAT_ID": "456",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "telegram_api_request",
                return_value={"ok": True, "result": {}},
            ) as telegram_mock:
                result = notifications.send_telegram_message(long_text)

        self.assertTrue(result)
        self.assertEqual(telegram_mock.call_count, 2)
        sent_texts = [
            call.args[1]["text"]
            for call in telegram_mock.call_args_list
        ]
        self.assertTrue(sent_texts[0].startswith("Yarbis (parte 1/2)"))
        self.assertTrue(sent_texts[1].startswith("Yarbis (parte 2/2)"))
        self.assertLessEqual(len(sent_texts[0]), notifications.TELEGRAM_API_MESSAGE_LIMIT)
        self.assertLessEqual(len(sent_texts[1]), notifications.TELEGRAM_API_MESSAGE_LIMIT)

    def test_send_telegram_chat_action_posts_typing_indicator(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "123456:abc",
                "YARBIS_TELEGRAM_CHAT_ID": "456",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "telegram_api_request",
                return_value={"ok": True, "result": {}},
            ) as telegram_mock:
                result = notifications.send_telegram_chat_action()

        self.assertTrue(result)
        telegram_mock.assert_called_once_with(
            "sendChatAction",
            {
                "chat_id": "456",
                "action": "typing",
            },
            settings=None,
        )

    def test_send_telegram_voice_posts_multipart(self):
        audio_path = TEST_RUNTIME_DIR / "voice.ogg"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"ogg-data")

        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "123456:abc",
                "YARBIS_TELEGRAM_CHAT_ID": "456",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "_post_multipart_file",
                return_value={"ok": True, "result": {}},
            ) as post_mock:
                result = notifications.send_telegram_voice(audio_path, caption="Yarbis")

        self.assertTrue(result)
        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[1], {"chat_id": "456", "caption": "Yarbis"})
        self.assertEqual(post_mock.call_args.args[2], "voice")
        self.assertEqual(post_mock.call_args.args[3], audio_path)
        self.assertEqual(post_mock.call_args.kwargs["content_type"], "audio/ogg")

    def test_telegram_api_request_redacts_token_from_errors(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "123456:abc",
                "YARBIS_NTFY_TOKEN": "token-123",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "_post_json",
                side_effect=RuntimeError(
                    "fallo en https://api.telegram.org/bot123456:abc/sendMessage "
                    "con Authorization: Bearer token-123"
                ),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    notifications.telegram_api_request("sendMessage", {"chat_id": "456"})

        error_text = str(ctx.exception)
        self.assertNotIn("123456:abc", error_text)
        self.assertNotIn("token-123", error_text)
        self.assertIn("[redacted]", error_text)

    def test_user_input_required_telegram_message_mentions_continuity(self):
        with patch.dict(
            os.environ,
            {
                "YARBIS_NOTIFICATIONS": "1",
                "YARBIS_NOTIFICATION_CHANNELS": "telegram",
                "YARBIS_TELEGRAM_BOT_TOKEN": "bot-123",
                "YARBIS_TELEGRAM_CHAT_ID": "456",
            },
            clear=False,
        ):
            with patch.object(
                notifications,
                "telegram_api_request",
                return_value={"ok": True, "result": {}},
            ) as telegram_mock:
                result = notifications.notify_user_input_required(
                    "Que prioridad quieres darme?",
                    "Necesito decidir el siguiente paso.",
                )

        self.assertTrue(result)
        sent_text = telegram_mock.call_args.args[1]["text"]
        self.assertIn("Que prioridad quieres darme?", sent_text)
        self.assertIn("app de escritorio", sent_text)
        self.assertIn("retomará los ciclos", sent_text)

    def test_user_input_required_telegram_falls_back_to_pending_state(self):
        state_path = TEST_RUNTIME_DIR / "notifications_telegram_pending_fallback_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que tono quieres usar?",
                "reason": "Necesito ese criterio para continuar.",
            },
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "456",
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(
                    notifications,
                    "telegram_api_request",
                    return_value={"ok": True, "result": {}},
                ) as telegram_mock:
                    result = notifications.notify_user_input_required("", "")

        self.assertTrue(result)
        sent_text = telegram_mock.call_args.args[1]["text"]
        self.assertIn("Yarbis necesita tu respuesta", sent_text)
        self.assertIn("Que tono quieres usar?", sent_text)
        self.assertIn("Necesito ese criterio para continuar.", sent_text)
        self.assertIn("app de escritorio", sent_text)

    def test_try_link_telegram_chat_persists_latest_private_chat(self):
        state_path = TEST_RUNTIME_DIR / "notifications_telegram_link_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "",
                },
            },
        })

        response = {
            "ok": True,
            "result": [
                {
                    "update_id": 10,
                    "message": {
                        "chat": {"id": -100, "type": "group"},
                        "text": "/start",
                    },
                },
                {
                    "update_id": 11,
                    "message": {
                        "chat": {"id": 456, "type": "private"},
                        "text": "/start",
                    },
                },
            ],
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(
                    notifications,
                    "telegram_api_request",
                    return_value=response,
                ) as telegram_mock:
                    linked = notifications.try_link_telegram_chat()
                    state = memory.load_state()

        self.assertTrue(linked)
        telegram_mock.assert_called_once()
        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "456")
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 11)
