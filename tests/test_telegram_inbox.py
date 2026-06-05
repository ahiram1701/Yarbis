import unittest
from pathlib import Path
from unittest.mock import patch

import memory
import telegram_inbox

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class TelegramInboxTestCase(unittest.TestCase):
    def test_dispatch_coding_commands(self):
        with patch.object(
            telegram_inbox,
            "coding_list_proposals_text",
            return_value="propuestas",
        ) as proposals_mock:
            with patch.object(
                telegram_inbox,
                "coding_get_proposal_text",
                return_value="detalle",
            ) as get_mock:
                with patch.object(
                    telegram_inbox,
                    "coding_run_validation_text",
                    return_value="validado",
                ) as validation_mock:
                    with patch.object(
                        telegram_inbox,
                        "coding_workflow_status_text",
                        return_value="status",
                    ) as status_mock:
                        with patch.object(
                            telegram_inbox,
                            "coding_check_proposal_text",
                            return_value="check",
                        ) as check_mock:
                            with patch.object(
                                telegram_inbox,
                                "coding_apply_and_validate_text",
                                return_value="aplicado validado",
                            ) as apply_validate_mock:
                                with patch.object(
                                    telegram_inbox,
                                    "coding_search_text_text",
                                    return_value="busqueda",
                                ) as search_mock:
                                    with patch.object(
                                        telegram_inbox,
                                        "coding_read_text_range_text",
                                        return_value="rango",
                                    ) as range_mock:
                                        with patch.object(
                                            telegram_inbox,
                                            "coding_validation_plan_text",
                                            return_value="plan",
                                        ) as plan_mock:
                                            status_result = telegram_inbox._dispatch_command("/coding status", chat_id="123")
                                            proposals_result = telegram_inbox._dispatch_command(
                                                "/coding propuestas",
                                                chat_id="123",
                                            )
                                            get_result = telegram_inbox._dispatch_command(
                                                "/coding ver proposal-1",
                                                chat_id="123",
                                            )
                                            check_result = telegram_inbox._dispatch_command(
                                                "/coding revisar proposal-1",
                                                chat_id="123",
                                            )
                                            apply_validate_result = telegram_inbox._dispatch_command(
                                                "/coding aplicar_validar proposal-1",
                                                chat_id="123",
                                            )
                                            search_result = telegram_inbox._dispatch_command(
                                                "/coding buscar foo",
                                                chat_id="123",
                                            )
                                            range_result = telegram_inbox._dispatch_command(
                                                "/coding rango app.py 2 5",
                                                chat_id="123",
                                            )
                                            plan_result = telegram_inbox._dispatch_command(
                                                "/coding plan_validacion proposal-1",
                                                chat_id="123",
                                            )
                                            validation_result = telegram_inbox._dispatch_command(
                                                "/coding validar proposal-1",
                                                chat_id="123",
                                            )

        self.assertEqual(status_result, "status")
        self.assertEqual(proposals_result, "propuestas")
        self.assertEqual(get_result, "detalle")
        self.assertEqual(check_result, "check")
        self.assertEqual(apply_validate_result, "aplicado validado")
        self.assertEqual(search_result, "busqueda")
        self.assertEqual(range_result, "rango")
        self.assertEqual(plan_result, "plan")
        self.assertEqual(validation_result, "validado")
        status_mock.assert_called_once_with()
        proposals_mock.assert_called_once_with(status="pending", limit=20)
        get_mock.assert_called_once_with("proposal-1")
        check_mock.assert_called_once_with("proposal-1")
        apply_validate_mock.assert_called_once_with("proposal-1")
        search_mock.assert_called_once_with("foo")
        range_mock.assert_called_once_with("app.py", start_line="2", line_count="5")
        plan_mock.assert_called_once_with(proposal_id="proposal-1")
        validation_mock.assert_called_once_with(proposal_id="proposal-1")

    def test_process_telegram_update_binds_first_private_chat_and_runs_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_bind_state.json"
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

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/run",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "run_cycle_with_output", return_value="Ciclo hecho.") as run_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "123")
        run_mock.assert_called_once_with(emit_notifications=False)
        send_mock.assert_called_once()
        self.assertIn("vinculado", send_mock.call_args.args[0].lower())
        self.assertIn("Ciclo hecho.", send_mock.call_args.args[0])
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_free_text_to_submit_user_reply(self):
        state_path = TEST_RUNTIME_DIR / "telegram_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 2,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Trabajemos el nicho fitness",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "submit_user_reply",
                return_value="Respuesta procesada.",
            ) as reply_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        reply_mock.assert_called_once_with(
            "Trabajemos el nicho fitness",
            emit_notifications=False,
            blocking=False,
        )
        send_mock.assert_called_once()
        sent_text = send_mock.call_args.args[0]
        self.assertIn("Yarbis | Respuesta", sent_text)
        self.assertIn("Estado: Continuidad:", sent_text)
        self.assertIn("Resultado:", sent_text)
        self.assertIn("Respuesta procesada.", sent_text)
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")

    def test_process_telegram_update_transcribes_voice_and_routes_as_text(self):
        state_path = TEST_RUNTIME_DIR / "telegram_voice_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
            "voice": {
                "enabled": True,
                "telegram_reply_mode": "off",
            },
        })

        update = {
            "update_id": 200,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "voice": {"file_id": "voice-file", "duration": 3, "file_size": 100},
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "download_telegram_file", return_value=b"ogg") as download_mock:
                with patch.object(
                    telegram_inbox.yarbis_voice,
                    "transcribe_audio_bytes",
                    return_value="Trabajemos por voz",
                ) as transcribe_mock:
                    with patch.object(
                        telegram_inbox,
                        "submit_user_reply",
                        return_value="Respuesta procesada.",
                    ) as reply_mock:
                        with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                            telegram_inbox.process_telegram_update(update)

        download_mock.assert_called_once_with(
            "voice-file",
            max_bytes=telegram_inbox.yarbis_voice.MAX_VOICE_AUDIO_BYTES,
        )
        transcribe_mock.assert_called_once()
        reply_mock.assert_called_once_with("Trabajemos por voz", emit_notifications=False, blocking=False)
        self.assertIn("Respuesta procesada.", send_mock.call_args.args[0])

    def test_process_telegram_update_sends_optional_voice_reply_in_always_mode(self):
        state_path = TEST_RUNTIME_DIR / "telegram_voice_reply_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path = TEST_RUNTIME_DIR / "reply.ogg"
        audio_path.write_bytes(b"ogg")

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
            "voice": {
                "enabled": True,
                "telegram_reply_mode": "always",
            },
        })
        update = {
            "update_id": 201,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "voice": {"file_id": "voice-file", "duration": 3, "file_size": 100},
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "download_telegram_file", return_value=b"ogg"):
                with patch.object(telegram_inbox.yarbis_voice, "transcribe_audio_bytes", return_value="Hola"):
                    with patch.object(telegram_inbox, "submit_user_reply", return_value="Listo."):
                        with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                            with patch.object(
                                telegram_inbox.yarbis_voice,
                                "synthesize_speech_file",
                                return_value=audio_path,
                            ) as synth_mock:
                                with patch.object(telegram_inbox, "send_telegram_voice", return_value=True) as voice_mock:
                                    with patch.object(telegram_inbox.yarbis_voice, "cleanup_voice_file"):
                                        telegram_inbox.process_telegram_update(update)

        synth_mock.assert_called_once()
        voice_mock.assert_called_once_with(audio_path, chat_id="123", caption="Yarbis")

    def test_process_telegram_update_blocks_power_confirmation_from_voice(self):
        state_path = TEST_RUNTIME_DIR / "telegram_voice_confirm_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "pending_power_confirmation": {
                        "action": "shutdown",
                        "delay_seconds": 60,
                        "token": "ABC123",
                        "chat_id": "123",
                        "requested_at": "2026-05-25T00:00:00+00:00",
                    },
                },
            },
            "voice": {
                "enabled": True,
                "telegram_reply_mode": "off",
            },
        })
        update = {
            "update_id": 202,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "voice": {"file_id": "voice-file", "duration": 3, "file_size": 100},
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "download_telegram_file", return_value=b"ogg"):
                with patch.object(
                    telegram_inbox.yarbis_voice,
                    "transcribe_audio_bytes",
                    return_value="/confirmar_apagado ABC123",
                ):
                    with patch.object(telegram_inbox, "request_system_shutdown") as shutdown_mock:
                        with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                            telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_not_called()
        self.assertIn("Por seguridad", send_mock.call_args.args[0])

    def test_telegram_voice_commands_list_select_speed_and_mute(self):
        state_path = TEST_RUNTIME_DIR / "telegram_voice_commands_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        updates = [
            {"update_id": 203, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz voces"}},
            {"update_id": 204, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz usar 1"}},
            {"update_id": 205, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz velocidad 190"}},
            {"update_id": 206, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz callar"}},
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox.yarbis_voice,
                "list_tts_voices",
                return_value=[{"index": 1, "id": "voice-1", "name": "Voz Uno", "languages": ["es-MX"]}],
            ):
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    for update in updates:
                        telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        sent_text = "\n".join(call.args[0] for call in send_mock.call_args_list)
        self.assertIn("Voces disponibles", sent_text)
        self.assertEqual(state["voice"]["tts_voice_id"], "voice-1")
        self.assertEqual(state["voice"]["tts_rate"], 190)
        self.assertEqual(state["voice"]["telegram_reply_mode"], "off")

    def test_telegram_communication_command_updates_settings(self):
        state_path = TEST_RUNTIME_DIR / "telegram_communication_commands_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        updates = [
            {"update_id": 223, "message": {"chat": {"id": 123, "type": "private"}, "text": "/comunicacion status"}},
            {"update_id": 224, "message": {"chat": {"id": 123, "type": "private"}, "text": "/comunicacion tono humano"}},
            {"update_id": 225, "message": {"chat": {"id": 123, "type": "private"}, "text": "/comunicacion detalle breve"}},
            {"update_id": 226, "message": {"chat": {"id": 123, "type": "private"}, "text": "/comunicacion proactividad alta"}},
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                for update in updates:
                    telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        sent_text = "\n".join(call.args[0] for call in send_mock.call_args_list)
        self.assertIn("Comunicacion:", sent_text)
        self.assertEqual(state["communication"]["tone"], "human")
        self.assertEqual(state["communication"]["detail_level"], "brief")
        self.assertEqual(state["communication"]["proactivity"], "high")

    def test_telegram_voice_kokoro_catalog_provider_and_select(self):
        state_path = TEST_RUNTIME_DIR / "telegram_voice_kokoro_commands_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        updates = [
            {"update_id": 213, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz catalogo es"}},
            {"update_id": 214, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz proveedor kokoro"}},
            {"update_id": 215, "message": {"chat": {"id": 123, "type": "private"}, "text": "/voz usar em_alex"}},
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox.yarbis_voice, "kokoro_catalog_text", return_value="Catalogo Kokoro"):
                with patch.object(
                    telegram_inbox.yarbis_voice,
                    "find_tts_voice",
                    return_value={
                        "provider": "kokoro",
                        "id": "em_alex",
                        "name": "Alex",
                        "installed": True,
                    },
                ):
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        for update in updates:
                            telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        sent_text = "\n".join(call.args[0] for call in send_mock.call_args_list)
        self.assertIn("Catalogo Kokoro", sent_text)
        self.assertEqual(state["voice"]["tts_provider"], "kokoro")
        self.assertEqual(state["voice"]["kokoro_voice_id"], "em_alex")

    def test_process_telegram_update_stops_current_operation(self):
        state_path = TEST_RUNTIME_DIR / "telegram_stop_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        update = {
            "update_id": 23,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/stop",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_stop_current_operation",
                return_value="Solicitud de parada enviada para Ciclo.",
            ) as stop_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        stop_mock.assert_called_once_with(source="telegram")
        self.assertIn("Solicitud de parada enviada", send_mock.call_args.args[0])

    def test_process_telegram_update_changes_ollama_model_and_timeout(self):
        state_path = TEST_RUNTIME_DIR / "telegram_ollama_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        update = {
            "update_id": 24,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/ollama llama3.2:3b 15m",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        self.assertEqual(state["ollama"]["model"], "llama3.2:3b")
        self.assertEqual(state["ollama"]["timeout_seconds"], 900)
        sent_text = send_mock.call_args.args[0]
        self.assertIn("Configuracion de Ollama actualizada", sent_text)
        self.assertIn("llama3.2:3b", sent_text)

    def test_process_telegram_update_changes_timeout_only(self):
        state_path = TEST_RUNTIME_DIR / "telegram_timeout_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "ollama": {
                "model": "llama3.2:3b",
                "timeout_seconds": 300,
            },
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        update = {
            "update_id": 25,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/timeout 1200",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        self.assertEqual(state["ollama"]["model"], "llama3.2:3b")
        self.assertEqual(state["ollama"]["timeout_seconds"], 1200)

    def test_process_telegram_update_changes_ollama_cloud_and_fallbacks(self):
        state_path = TEST_RUNTIME_DIR / "telegram_ollama_cloud_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "ollama": {
                "model": "qwen3.5:2b",
                "timeout_seconds": 300,
            },
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        cloud_update = {
            "update_id": 26,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/ollama cloud gpt-oss:120b",
            },
        }
        fallback_update = {
            "update_id": 27,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/ollama fallback qwen3.5:2b, gpt-oss:120b-cloud",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                telegram_inbox.process_telegram_update(cloud_update)
                telegram_inbox.process_telegram_update(fallback_update)
            state = memory.load_state()

        self.assertEqual(state["ollama"]["model"], "gpt-oss:120b")
        self.assertEqual(state["ollama"]["host"], "https://ollama.com")
        self.assertEqual(
            state["ollama"]["fallback_models"],
            ["qwen3.5:2b", "gpt-oss:120b-cloud"],
        )

    def test_process_telegram_update_changes_default_provider(self):
        state_path = TEST_RUNTIME_DIR / "telegram_provider_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        update = {
            "update_id": 28,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/proveedor openrouter",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
            state = memory.load_state()

        self.assertEqual(state["model_provider"]["default"], "openrouter")
        self.assertIn("Proveedor por defecto actualizado", send_mock.call_args.args[0])

    def test_process_telegram_update_configures_openrouter_and_timeout(self):
        state_path = TEST_RUNTIME_DIR / "telegram_openrouter_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })
        openrouter_update = {
            "update_id": 29,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/openrouter openai/gpt-demo 15m",
            },
        }
        timeout_update = {
            "update_id": 30,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/timeout 1200",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                telegram_inbox.process_telegram_update(openrouter_update)
                telegram_inbox.process_telegram_update(timeout_update)
            state = memory.load_state()

        self.assertEqual(state["model_provider"]["default"], "openrouter")
        self.assertEqual(state["model_provider"]["openrouter"]["model"], "openai/gpt-demo")
        self.assertEqual(state["model_provider"]["openrouter"]["timeout_seconds"], 1200)

    def test_activity_text_keeps_full_telegram_message(self):
        long_text = "mensaje largo " * 80

        activity_text = telegram_inbox._incoming_activity_text(long_text)
        processed_text = telegram_inbox._trim_for_activity(long_text)

        self.assertIn("mensaje largo", activity_text)
        self.assertIn(long_text.strip(), processed_text)
        self.assertNotIn("...", processed_text)

    def test_activity_text_redacts_secret_like_telegram_token(self):
        activity_text = telegram_inbox._incoming_activity_text(
            "revisa https://api.telegram.org/bot123456:abc/sendMessage"
        )

        self.assertNotIn("123456:abc", activity_text)
        self.assertIn("[redacted]", activity_text)

    def test_process_telegram_update_defers_free_text_when_session_is_busy(self):
        state_path = TEST_RUNTIME_DIR / "telegram_busy_reply_state.json"
        queue_path = TEST_RUNTIME_DIR / f"telegram_busy_reply_queue_{id(self)}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que nicho quieres trabajar?",
                "reason": "Falta contexto.",
            },
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 22,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Trabajemos fitness",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(telegram_inbox, "DEFERRED_TELEGRAM_REPLIES_FILE", queue_path):
                memory.save_state(seeded_state)
                with patch.object(
                    telegram_inbox,
                    "submit_user_reply",
                    side_effect=telegram_inbox.SessionOperationBusy("ocupado"),
                ) as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        reply_mock.assert_called_once_with(
            "Trabajemos fitness",
            emit_notifications=False,
            blocking=False,
        )
        send_mock.assert_called_once()
        self.assertIn("Recibi tu respuesta", send_mock.call_args.args[0])
        self.assertTrue(queue_path.exists())

    def test_process_deferred_telegram_replies_sends_queued_answer(self):
        state_path = TEST_RUNTIME_DIR / "telegram_deferred_reply_state.json"
        queue_path = TEST_RUNTIME_DIR / f"telegram_deferred_reply_queue_{id(self)}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(telegram_inbox, "DEFERRED_TELEGRAM_REPLIES_FILE", queue_path):
                memory.save_state(memory.default_state())
                telegram_inbox._enqueue_deferred_telegram_reply("Trabajemos fitness", "123")
                with patch.object(
                    telegram_inbox,
                    "submit_user_reply",
                    return_value="Respuesta guardada.",
                ) as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        processed = telegram_inbox.process_deferred_telegram_replies()

        self.assertEqual(processed, 1)
        reply_mock.assert_called_once_with(
            "Trabajemos fitness",
            emit_notifications=False,
            blocking=False,
        )
        send_mock.assert_called_once()
        sent_text = send_mock.call_args.args[0]
        self.assertIn("Yarbis | Respuesta diferida", sent_text)
        self.assertIn("Estado: Continuidad:", sent_text)
        self.assertIn("Respuesta guardada.", sent_text)
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")
        self.assertEqual(telegram_inbox._load_deferred_telegram_replies(), [])

    def test_format_telegram_operation_reply_extracts_final_cycle_output(self):
        state = memory.normalize_state({
            "cycle_count": 7,
            "tasks": [
                {
                    "id": "task-1",
                    "title": "Seguir hilo",
                    "status": "pending",
                    "priority": "media",
                }
            ],
        })
        raw_output = (
            "=== CICLO 7 ===\n"
            "--- Paso 1 ---\n"
            "Decidi usar herramientas.\n"
            "> Ejecutando tool: list_tasks\n"
            "> Argumentos: {}\n"
            "\nYarbis:\n"
            "Avance listo para Telegram.\n\n"
            "...[truncado 120 caracteres]"
        )

        rendered = telegram_inbox.format_telegram_operation_reply(
            "Ciclo",
            raw_output,
            state=state,
        )

        self.assertIn("Yarbis | Ciclo", rendered)
        self.assertIn("Estado: Continuidad: 7 ciclo(s) | 1 tarea(s) abierta(s)", rendered)
        self.assertIn("Resultado:", rendered)
        self.assertIn("Avance listo para Telegram.", rendered)
        self.assertNotIn("truncado", rendered.lower())
        self.assertIn("/run o /auto", rendered)

    def test_pending_question_takes_precedence_over_natural_note_reply(self):
        state_path = TEST_RUNTIME_DIR / "telegram_pending_beats_note_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "awaiting_user_input": {
                "pending": True,
                "question": "Que contexto debo recordar?",
                "reason": "Falta contexto.",
            },
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 23,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "guarda una nota: Rutina | Revisar pendientes | personal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "submit_user_reply",
                return_value="Respuesta procesada.",
            ) as reply_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                    telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        reply_mock.assert_called_once_with(
            "guarda una nota: Rutina | Revisar pendientes | personal",
            emit_notifications=False,
            blocking=False,
        )
        self.assertEqual(state["notes"], [])

    def test_process_telegram_update_routes_goal_command_to_update_goal(self):
        state_path = TEST_RUNTIME_DIR / "telegram_goal_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo anterior",
            "messages": [{"role": "assistant", "content": "avance previo"}],
            "tasks": [{"id": "task-1", "title": "Vieja tarea", "status": "pending"}],
            "current_plan": ["Paso viejo"],
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/objetivo Preparar el lanzamiento del producto",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["goal"], "Preparar el lanzamiento del producto")
        self.assertEqual(state["tasks"], [])
        self.assertEqual(state["current_plan"], [])
        self.assertIn("Objetivo actualizado", send_mock.call_args.args[0])
        self.assertEqual(send_mock.call_args.kwargs["chat_id"], "123")
        self.assertIn("Telegram: procesado", summary)

    def test_goal_command_without_text_returns_usage(self):
        state_path = TEST_RUNTIME_DIR / "telegram_goal_usage_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "goal": "Objetivo anterior",
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 4,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/goal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["goal"], "Objetivo anterior")
        send_mock.assert_called_once_with("Uso: /goal nuevo objetivo", chat_id="123")

    def test_process_telegram_update_routes_note_create_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_create_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 41,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota crear Rutina | Revisar pendientes cada manana | personal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(len(state["notes"]), 1)
        self.assertEqual(state["notes"][0]["title"], "Rutina")
        self.assertEqual(state["notes"][0]["category"], "personal")
        send_mock.assert_called_once()
        self.assertIn("Nota guardada", send_mock.call_args.args[0])
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_does_not_repeat_same_update_id(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_duplicate_update_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 44,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota crear Rutina | Revisar pendientes cada manana | personal",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                first_summary = telegram_inbox.process_telegram_update(update)
                second_summary = telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertIn("Telegram: procesado", first_summary)
        self.assertEqual(second_summary, "")
        self.assertEqual(len(state["notes"]), 1)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 44)
        send_mock.assert_called_once()

    def test_process_telegram_update_routes_natural_note_without_model_reply(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_note_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 42,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "guarda una nota: Idea | Probar notas por Telegram | trabajo",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        reply_mock.assert_not_called()
        self.assertEqual(state["notes"][0]["title"], "Idea")
        self.assertIn("Nota guardada", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_note_delete_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_note_delete_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notes": [
                {
                    "id": "note-123",
                    "title": "Rutina",
                    "content": "Revisar pendientes",
                    "category": "personal",
                }
            ],
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 43,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/nota borrar note-123",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                telegram_inbox.process_telegram_update(update)
                state = memory.load_state()

        self.assertEqual(state["notes"], [])
        self.assertIn("Nota eliminada", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_shutdown_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_shutdown_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 5,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/apagar 5m",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        shutdown_mock.assert_not_called()
        pending = state["notifications"]["telegram"]["pending_power_confirmation"]
        self.assertEqual(pending["action"], "shutdown")
        self.assertEqual(pending["delay_seconds"], 300)
        self.assertEqual(pending["chat_id"], "123")
        self.assertIn("Confirmacion requerida", send_mock.call_args.args[0])
        self.assertIn("/confirmar_apagado", send_mock.call_args.args[0])
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_restart_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_restart_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 6,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/reiniciar 3m",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_restart",
                return_value="Reinicio programado.",
            ) as restart_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        restart_mock.assert_not_called()
        pending = state["notifications"]["telegram"]["pending_power_confirmation"]
        self.assertEqual(pending["action"], "restart")
        self.assertEqual(pending["delay_seconds"], 180)
        self.assertIn("Confirmacion requerida", send_mock.call_args.args[0])
        self.assertIn("/confirmar_reinicio", send_mock.call_args.args[0])
        self.assertIn("Telegram: procesado", summary)

    def test_process_telegram_update_routes_natural_shutdown_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_shutdown_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 6,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Yarbis, apaga la pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_not_called()
        reply_mock.assert_not_called()
        self.assertIn("Confirmacion requerida", send_mock.call_args.args[0])
        self.assertIn("/confirmar_apagado", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_natural_restart_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_natural_restart_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Yarbis, reinicia pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_restart",
                return_value="Reinicio programado.",
            ) as restart_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        restart_mock.assert_not_called()
        reply_mock.assert_not_called()
        self.assertIn("Confirmacion requerida", send_mock.call_args.args[0])
        self.assertIn("/confirmar_reinicio", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_short_natural_shutdown_phrase(self):
        state_path = TEST_RUNTIME_DIR / "telegram_short_natural_shutdown_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "apaga pc",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "request_system_shutdown",
                return_value="Apagado programado.",
            ) as shutdown_mock:
                with patch.object(telegram_inbox, "submit_user_reply") as reply_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        shutdown_mock.assert_not_called()
        reply_mock.assert_not_called()
        self.assertIn("Confirmacion requerida", send_mock.call_args.args[0])
        self.assertIn("/confirmar_apagado", send_mock.call_args.args[0])

    def test_process_telegram_update_confirms_pending_shutdown(self):
        state_path = TEST_RUNTIME_DIR / "telegram_confirm_shutdown_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "pending_power_confirmation": {
                        "action": "shutdown",
                        "delay_seconds": 300,
                        "token": "ABC123",
                        "chat_id": "123",
                        "requested_at": "2026-05-05T10:00:00+00:00",
                    },
                },
            },
        })

        update = {
            "update_id": 70,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/confirmar_apagado ABC123",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "_power_confirmation_expired", return_value=False):
                with patch.object(
                    telegram_inbox,
                    "request_system_shutdown",
                    return_value="Apagado programado.",
                ) as shutdown_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)
                        state = memory.load_state()

        shutdown_mock.assert_called_once_with(delay_seconds=300)
        self.assertEqual(state["notifications"]["telegram"]["pending_power_confirmation"]["action"], "")
        self.assertIn("Apagado programado.", send_mock.call_args.args[0])

    def test_process_telegram_update_confirms_pending_restart(self):
        state_path = TEST_RUNTIME_DIR / "telegram_confirm_restart_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "pending_power_confirmation": {
                        "action": "restart",
                        "delay_seconds": 180,
                        "token": "XYZ789",
                        "chat_id": "123",
                        "requested_at": "2026-05-05T10:00:00+00:00",
                    },
                },
            },
        })

        update = {
            "update_id": 71,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/confirmar_reinicio XYZ789",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "_power_confirmation_expired", return_value=False):
                with patch.object(
                    telegram_inbox,
                    "request_system_restart",
                    return_value="Reinicio programado.",
                ) as restart_mock:
                    with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                        telegram_inbox.process_telegram_update(update)

        restart_mock.assert_called_once_with(delay_seconds=180)
        self.assertIn("Reinicio programado.", send_mock.call_args.args[0])

    def test_process_telegram_update_routes_shutdown_cancel_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_cancel_shutdown_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 7,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/cancelar_apagado",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "cancel_system_shutdown",
                return_value="Apagado cancelado.",
            ) as cancel_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        cancel_mock.assert_called_once_with(action_label="apagado")
        send_mock.assert_called_once_with("Apagado cancelado.", chat_id="123")

    def test_process_telegram_update_routes_restart_cancel_command(self):
        state_path = TEST_RUNTIME_DIR / "telegram_cancel_restart_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 8,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/cancelar_reinicio",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "cancel_system_shutdown",
                return_value="Reinicio cancelado.",
            ) as cancel_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    telegram_inbox.process_telegram_update(update)

        cancel_mock.assert_called_once_with(action_label="reinicio")
        send_mock.assert_called_once_with("Reinicio cancelado.", chat_id="123")

    def test_shutdown_command_only_binds_first_private_chat(self):
        state_path = TEST_RUNTIME_DIR / "telegram_shutdown_bind_only_state.json"
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

        update = {
            "update_id": 8,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "/apagar",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "request_system_shutdown") as shutdown_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True) as send_mock:
                    summary = telegram_inbox.process_telegram_update(update)
                    state = memory.load_state()

        self.assertEqual(state["notifications"]["telegram"]["chat_id"], "123")
        shutdown_mock.assert_not_called()
        self.assertIn("vuelve a enviar", send_mock.call_args.args[0])
        self.assertIn("Telegram: vinculo chat", summary)

    def test_process_telegram_update_emits_activity_before_finishing_processing(self):
        state_path = TEST_RUNTIME_DIR / "telegram_activity_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                },
            },
        })

        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 123, "type": "private"},
                "text": "Mensaje remoto",
            },
        }

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(telegram_inbox, "_emit_event") as emit_mock:
                with patch.object(telegram_inbox, "send_telegram_message", return_value=True):
                    def fake_submit_user_reply(*args, **kwargs):
                        emitted_messages = [call.args[0] for call in emit_mock.call_args_list]
                        self.assertTrue(
                            any(
                                isinstance(item, str) and "Telegram: recibido" in item
                                for item in emitted_messages
                            ),
                        )
                        self.assertTrue(
                            any(
                                isinstance(item, dict)
                                and item.get("type") == "remote_job_started"
                                and item.get("label") == "Respuesta"
                                for item in emitted_messages
                            ),
                        )
                        return "Respuesta procesada."

                    with patch.object(
                        telegram_inbox,
                        "submit_user_reply",
                        side_effect=fake_submit_user_reply,
                        ):
                            summary = telegram_inbox.process_telegram_update(update)

        self.assertIn("Telegram: procesado", summary)
        emitted_messages = [call.args[0] for call in emit_mock.call_args_list]
        self.assertTrue(
            any(
                isinstance(item, dict)
                and item.get("type") == "remote_job_finished"
                and item.get("label") == "Respuesta"
                and item.get("content") == "Respuesta procesada."
                for item in emitted_messages
            ),
        )

    def test_poll_updates_once_persists_last_update_id(self):
        state_path = TEST_RUNTIME_DIR / "telegram_poll_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "last_update_id": 4,
                },
            },
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "telegram_api_request",
                return_value={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 5,
                            "message": {
                                "chat": {"id": 123, "type": "private"},
                                "text": "Hola",
                            },
                        }
                    ],
                },
            ) as api_mock:
                with patch.object(telegram_inbox, "process_telegram_update", return_value="ok"):
                    result = telegram_inbox._poll_updates_once()
                    state = memory.load_state()

        self.assertTrue(result)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 5)
        self.assertEqual(api_mock.call_args.args[0], "getUpdates")

    def test_poll_updates_once_advances_after_processing_error(self):
        state_path = TEST_RUNTIME_DIR / "telegram_poll_error_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "notifications": {
                "enabled": True,
                "channels": ["telegram"],
                "telegram": {
                    "bot_token": "bot-123",
                    "chat_id": "123",
                    "last_update_id": 8,
                },
            },
        })

        updates = [
            {
                "update_id": 9,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "text": "/cancelar_apagado",
                },
            },
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "text": "/status",
                },
            },
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            with patch.object(
                telegram_inbox,
                "telegram_api_request",
                return_value={"ok": True, "result": updates},
            ):
                with patch.object(
                    telegram_inbox,
                    "process_telegram_update",
                    side_effect=[RuntimeError("fallo cancelando"), "ok"],
                ) as process_mock:
                    with patch.object(telegram_inbox, "_emit_event") as emit_mock:
                        result = telegram_inbox._poll_updates_once()
                        state = memory.load_state()

        self.assertTrue(result)
        self.assertEqual(process_mock.call_count, 2)
        self.assertEqual(state["notifications"]["telegram"]["last_update_id"], 10)
        emitted = [call.args[0] for call in emit_mock.call_args_list]
        self.assertTrue(any("fallo cancelando" in str(item) for item in emitted))
        self.assertIn("ok", emitted)
