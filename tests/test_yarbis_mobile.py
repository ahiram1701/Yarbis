import http.client
import base64
import json
import socket
import subprocess
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import memory
import tools
import yarbis_instance
import yarbis_mobile
from ui_settings_dialogs import NotificationsDialog, ServiceMobileUiDialog, VoiceSettingsDialog

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class YarbisMobileTestCase(unittest.TestCase):
    def tearDown(self):
        yarbis_mobile.stop_mobile_ui_servers()
        yarbis_mobile._MOBILE_VALUE_CACHE.clear()

    def _service_status(self):
        return {
            "service_name": "Yarbis",
            "display_name": "Yarbis",
            "installed": True,
            "running": True,
            "state": "running",
            "pid": 123,
            "autostart_enabled": True,
            "start_type": "auto_start",
            "account_name": "LocalSystem",
            "log_file": "",
            "service_binary": "",
        }

    def test_pin_hash_and_session_cookie_round_trip(self):
        pin_hash, pin_salt = yarbis_mobile.hash_mobile_pin("1234")
        self.assertTrue(yarbis_mobile.verify_mobile_pin("1234", pin_hash, pin_salt))
        self.assertFalse(yarbis_mobile.verify_mobile_pin("9999", pin_hash, pin_salt))

        cookie, csrf = yarbis_mobile.create_session_cookie("secret")
        session = yarbis_mobile.verify_session_cookie(cookie, "secret")

        self.assertEqual(session["csrf"], csrf)
        self.assertIsNone(yarbis_mobile.verify_session_cookie(cookie, "other-secret"))

    def test_update_mobile_ui_settings_requires_pin_when_enabling(self):
        state_path = TEST_RUNTIME_DIR / "mobile_requires_pin_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="")

            result = yarbis_mobile.update_mobile_ui_settings(
                enabled=True,
                port=8787,
                pin="2468",
                job_timeout_seconds=3600,
            )
            state = memory.load_state()

        self.assertIn("UI movil actualizada", result)
        self.assertTrue(state["service"]["mobile_ui"]["enabled"])
        self.assertEqual(state["service"]["mobile_ui"]["port"], 8787)
        self.assertEqual(state["service"]["mobile_ui"]["job_timeout_seconds"], 3600)
        self.assertTrue(state["service"]["mobile_ui"]["https_enabled"])
        self.assertTrue(state["service"]["mobile_ui"]["pin_hash"])
        self.assertTrue(state["service"]["mobile_ui"]["session_secret"])
        self.assertIn("HTTPS iPhone: activo", result)

    def test_update_mobile_ui_settings_rejects_invalid_job_timeout(self):
        state_path = TEST_RUNTIME_DIR / "mobile_invalid_timeout_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with self.assertRaises(ValueError):
                yarbis_mobile.update_mobile_ui_settings(
                    enabled=True,
                    port=8787,
                    pin="2468",
                    job_timeout_seconds=10,
                )

    def test_http_api_requires_auth_login_and_csrf(self):
        state_path = TEST_RUNTIME_DIR / "mobile_http_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/state")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                body = json.dumps({"pin": "1357"})
                conn.request("POST", "/api/login", body=body, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")
                self.assertEqual(response.status, 200)
                self.assertTrue(login_payload["csrf"])
                self.assertIn(yarbis_mobile.MOBILE_COOKIE_NAME, cookie)

                conn.request("POST", "/api/action", body=json.dumps({"action": "stop_operation"}), headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                })
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                with patch.object(yarbis_mobile, "_execute_action", return_value={"result": "ok"}):
                    conn.request("POST", "/api/action", body=json.dumps({
                        "csrf": login_payload["csrf"],
                        "action": "stop_operation",
                    }), headers={
                        "Content-Type": "application/json",
                        "Cookie": cookie,
                        "X-CSRF-Token": login_payload["csrf"],
                    })
                    response = conn.getresponse()
                    action_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(action_payload["result"], "ok")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_instances_requires_auth_and_lists_ports(self):
        state_path = TEST_RUNTIME_DIR / "mobile_instances_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        instances_root = TEST_RUNTIME_DIR / f"mobile_instances_root-{socket.gethostname()}-{_free_port()}"

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(yarbis_instance, "INSTANCES_ROOT", instances_root):
                memory.save_state(memory.default_state())
                yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
                yarbis_instance.create_instance("alpha", display_name="Alpha")
                for instance_id, port in (("alpha", 9101),):
                    secondary_state = yarbis_instance.state_file(instance_id)
                    secondary_state.parent.mkdir(parents=True, exist_ok=True)
                    secondary_state.write_text(
                        json.dumps({"service": {"mobile_ui": {"port": port, "enabled": True}}}),
                        encoding="utf-8",
                    )

                server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = int(server.server_address[1])
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.request("GET", "/api/instances")
                    response = conn.getresponse()
                    response.read()
                    self.assertEqual(response.status, 401)

                    conn.request("POST", "/api/login", body=json.dumps({"pin": "1357"}), headers={"Content-Type": "application/json"})
                    response = conn.getresponse()
                    response.read()
                    cookie = response.getheader("Set-Cookie")

                    with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value=""):
                        with patch.object(yarbis_mobile, "current_tailscale_serve_targets", return_value=()):
                            conn.request("GET", "/api/instances", headers={"Cookie": cookie})
                            response = conn.getresponse()
                            payload = json.loads(response.read().decode("utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["current_instance"], "default")
        by_id = {item["id"]: item for item in payload["instances"]}
        self.assertIn("default", by_id)
        self.assertIn("alpha", by_id)
        self.assertTrue(by_id["default"]["current"])
        self.assertEqual(by_id["alpha"]["port"], 9101)
        self.assertFalse(by_id["alpha"]["active"])

    def test_load_state_cached_invalidates_on_mtime_change(self):
        state_path = TEST_RUNTIME_DIR / "mobile_state_cache.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            first = memory.default_state()
            first["goal"] = "objetivo corto"
            memory.save_state(first)
            self.assertEqual(yarbis_mobile._load_state_cached()["goal"], "objetivo corto")

            second = memory.default_state()
            second["goal"] = "objetivo largo y distinto para cambiar el tamaño del archivo"
            memory.save_state(second)
            self.assertEqual(
                yarbis_mobile._load_state_cached()["goal"],
                "objetivo largo y distinto para cambiar el tamaño del archivo",
            )

    def test_session_operation_script_waits_for_memory_maintenance(self):
        self.assertIn(
            "wait_for_memory_protection_maintenance",
            yarbis_mobile._MOBILE_SESSION_OPERATION_SCRIPT,
        )

    def test_http_api_state_view_query_uses_partial_state(self):
        state_path = TEST_RUNTIME_DIR / "mobile_http_view_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                response.read()
                cookie = response.getheader("Set-Cookie")

                with patch.object(yarbis_mobile, "_public_state", return_value={"view": "context"}) as state_mock:
                    conn.request("GET", "/api/state?view=context", headers={"Cookie": cookie})
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["state"], {"view": "context"})
                state_mock.assert_called_once_with("context")
            finally:
                server.shutdown()
                server.server_close()

    def test_context_state_and_actions_include_idea_projects(self):
        state_path = TEST_RUNTIME_DIR / "mobile_idea_projects_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        seeded_state = memory.normalize_state({
            "idea_projects": [{
                "id": "idea-demo",
                "title": "Idea demo",
                "kind": "mixto",
                "status": "exploring",
                "next_steps": ["Definir brief"],
            }]
        })

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(seeded_state)
            context_state = yarbis_mobile._public_context_state(memory.load_state())
            result = yarbis_mobile._execute_action("create_idea_project", {
                "title": "Nueva idea",
                "kind": "vida_proyecto",
                "next_steps": "Dar primer paso",
            })
            state = memory.load_state()

        self.assertEqual(context_state["idea_projects"][0]["id"], "idea-demo")
        self.assertIn("Proyecto de idea creado", result["result"])
        self.assertEqual(len(state["idea_projects"]), 2)
        self.assertEqual(state["idea_projects"][1]["kind"], "vida_proyecto")

    def test_visual_state_and_actions_manage_project_boards(self):
        state_path = TEST_RUNTIME_DIR / "mobile_visual_boards_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        visual_dir = TEST_RUNTIME_DIR / "mobile_visual_exports"

        seeded_state = memory.normalize_state({
            "idea_projects": [{
                "id": "idea-demo",
                "title": "Idea demo",
                "kind": "mixto",
                "status": "exploring",
                "summary": "Explorar una oferta",
                "next_steps": ["Definir brief"],
            }]
        })

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(tools, "VISUAL_BOARDS_DIR", visual_dir):
                memory.save_state(seeded_state)
                visual_state = yarbis_mobile._public_state("visual")
                create_result = yarbis_mobile._execute_action("create_visual_board", {
                    "project_id": "idea-demo",
                    "board_kind": "mind_map",
                })
                state = memory.load_state()
                board = state["idea_projects"][0]["visual_boards"][0]
                board["nodes"][0]["title"] = "Centro actualizado"
                update_result = yarbis_mobile._execute_action("update_visual_board", {
                    "project_id": "idea-demo",
                    "board_id": board["id"],
                    "board": board,
                })
                export_result = yarbis_mobile._execute_action("export_visual_board", {
                    "project_id": "idea-demo",
                    "board_id": board["id"],
                    "formats": "json,svg,html",
                })
                state = memory.load_state()

        self.assertEqual(visual_state["idea_projects"][0]["id"], "idea-demo")
        self.assertIn("Board visual creado", create_result["result"])
        self.assertIn("Board visual actualizado", update_result["result"])
        self.assertEqual(
            state["idea_projects"][0]["visual_boards"][0]["nodes"][0]["title"],
            "Centro actualizado",
        )
        self.assertIn("Board visual exportado", export_result["result"])
        export_paths = state["idea_projects"][0]["visual_boards"][0]["export_paths"]
        self.assertTrue(Path(export_paths["json"]).exists())
        self.assertTrue(Path(export_paths["svg"]).exists())
        self.assertTrue(Path(export_paths["html"]).exists())

    def test_http_api_voice_transcribe_requires_auth_and_csrf(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                body = json.dumps({
                    "audio_b64": base64.b64encode(b"webm").decode("ascii"),
                    "mime_type": "audio/webm",
                })
                conn.request("POST", "/api/voice/transcribe", body=body, headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")

                conn.request("POST", "/api/voice/transcribe", body=body, headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                })
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                with patch.object(
                    yarbis_mobile.yarbis_voice,
                    "transcribe_audio_bytes",
                    return_value="texto dictado",
                ) as transcribe_mock:
                    conn.request("POST", "/api/voice/transcribe", body=json.dumps({
                        "csrf": login_payload["csrf"],
                        "audio_b64": base64.b64encode(b"webm").decode("ascii"),
                        "mime_type": "audio/webm",
                    }), headers={
                        "Content-Type": "application/json",
                        "Cookie": cookie,
                        "X-CSRF-Token": login_payload["csrf"],
                    })
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["text"], "texto dictado")
                transcribe_mock.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_voice_voices_requires_auth_and_returns_settings(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_voices_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/voice/voices")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 401)

                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")

                with patch.object(
                    yarbis_mobile.yarbis_voice,
                    "list_tts_voices",
                    return_value=[{"index": 1, "id": "voice-1", "name": "Voz Uno"}],
                ) as voices_mock:
                    conn.request("GET", "/api/voice/voices", headers={"Cookie": cookie})
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["csrf"], login_payload["csrf"])
                self.assertEqual(payload["voices"][0]["id"], "voice-1")
                self.assertIn("browser_voice_name", payload["settings"])
                voices_mock.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_voice_live_session_requires_auth_and_processes_chunk(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_live_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/api/login", body=json.dumps({"pin": "1357"}), headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")
                csrf = login_payload["csrf"]

                conn.request("POST", "/api/voice/live/start", body=json.dumps({"csrf": csrf}), headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                    "X-CSRF-Token": csrf,
                })
                response = conn.getresponse()
                start_payload = json.loads(response.read().decode("utf-8"))
                session_id = start_payload["voice_session"]["id"]
                csrf = start_payload["csrf"]

                with patch.object(
                    yarbis_mobile.voice_conversation,
                    "transcribe_live_audio_bytes",
                    return_value="Yarbis toma nota",
                ):
                    with patch.object(
                        yarbis_mobile.voice_conversation,
                        "process_voice_turn",
                        return_value={
                            "state": "speaking",
                            "transcript": "toma nota",
                            "reply": "Yarbis:\nHecho.",
                            "spoken_text": "Hecho.",
                        },
                    ):
                        conn.request("POST", "/api/voice/live/chunk", body=json.dumps({
                            "csrf": csrf,
                            "session_id": session_id,
                            "audio_b64": base64.b64encode(b"audio").decode("ascii"),
                            "mime_type": "audio/webm",
                        }), headers={
                            "Content-Type": "application/json",
                            "Cookie": cookie,
                            "X-CSRF-Token": csrf,
                        })
                        response = conn.getresponse()
                        chunk_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(chunk_payload["voice_session"]["state"], "speaking")
                self.assertEqual(chunk_payload["voice_session"]["spoken_text"], "Hecho.")
            finally:
                server.shutdown()
                server.server_close()

    def test_http_api_voice_voices_catalog_flag_and_speak_endpoint(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_catalog_state.json"
        audio_path = TEST_RUNTIME_DIR / "mobile_speak.ogg"
        wav_path = TEST_RUNTIME_DIR / "mobile_speak.wav"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"ogg")
        wav_path.write_bytes(b"wav")

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=8787, pin="1357")
            server = yarbis_mobile._MobileHTTPServer(("127.0.0.1", 0), yarbis_mobile.MobileRequestHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = int(server.server_address[1])
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(
                    "POST",
                    "/api/login",
                    body=json.dumps({"pin": "1357"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                login_payload = json.loads(response.read().decode("utf-8"))
                cookie = response.getheader("Set-Cookie")

                with patch.object(
                    yarbis_mobile.yarbis_voice,
                    "list_tts_voices",
                    return_value=[{"index": 2, "id": "es-MX-JorgeNeural", "provider": "edge"}],
                ) as voices_mock:
                    conn.request("GET", "/api/voice/voices?catalog=1&refresh=1", headers={"Cookie": cookie})
                    response = conn.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["voices"][0]["provider"], "edge")
                self.assertTrue(voices_mock.call_args.kwargs["include_downloadable"])
                self.assertTrue(voices_mock.call_args.kwargs["refresh_catalog"])

                with patch.object(yarbis_mobile.yarbis_voice, "synthesize_speech_wav_file", return_value=wav_path):
                    with patch.object(yarbis_mobile.yarbis_voice, "cleanup_voice_file") as cleanup_mock:
                        conn.request("POST", "/api/voice/speak", body=json.dumps({
                            "csrf": login_payload["csrf"],
                            "text": "Hola",
                        }), headers={
                            "Content-Type": "application/json",
                            "Cookie": cookie,
                            "X-CSRF-Token": login_payload["csrf"],
                        })
                        response = conn.getresponse()
                        speak_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(base64.b64decode(speak_payload["audio_b64"]), b"wav")
                self.assertEqual(speak_payload["mime_type"], "audio/wav")
                cleanup_mock.assert_called_once_with(wav_path)

                with patch.object(yarbis_mobile.yarbis_voice, "synthesize_speech_file", return_value=audio_path):
                    with patch.object(yarbis_mobile.yarbis_voice, "cleanup_voice_file") as cleanup_mock:
                        conn.request("POST", "/api/voice/speak", body=json.dumps({
                            "csrf": login_payload["csrf"],
                            "text": "Hola",
                            "format": "ogg",
                        }), headers={
                            "Content-Type": "application/json",
                            "Cookie": cookie,
                            "X-CSRF-Token": login_payload["csrf"],
                        })
                        response = conn.getresponse()
                        speak_payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(base64.b64decode(speak_payload["audio_b64"]), b"ogg")
                self.assertEqual(speak_payload["mime_type"], "audio/ogg")
                cleanup_mock.assert_called_once_with(audio_path)

            finally:
                server.shutdown()
                server.server_close()

    def test_mobile_voice_settings_action_updates_state(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_settings_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = yarbis_mobile._execute_action("voice_settings", {
                "enabled": True,
                "tts_provider": "system",
                "tts_voice_id": "voice-1",
                "tts_rate": 190,
                "browser_voice_name": "Samantha",
                "browser_tts_rate": 1.2,
                "browser_tts_pitch": 0.8,
                "telegram_reply_mode": "always",
                "live_enabled": True,
                "live_wake_phrase": "Jarvis",
                "live_silence_ms": 1200,
                "live_max_turn_seconds": 30,
                "live_auto_speak": False,
                "live_barge_in": False,
            })
            state = memory.load_state()

        self.assertIn("Voz actualizada", result["result"])
        self.assertEqual(state["voice"]["tts_voice_id"], "voice-1")
        self.assertEqual(state["voice"]["tts_provider"], "system")
        self.assertEqual(state["voice"]["tts_rate"], 190)
        self.assertEqual(state["voice"]["browser_voice_name"], "Samantha")
        self.assertEqual(state["voice"]["browser_tts_rate"], 1.2)
        self.assertEqual(state["voice"]["browser_tts_pitch"], 0.8)
        self.assertEqual(state["voice"]["telegram_reply_mode"], "always")
        self.assertEqual(state["voice"]["live_conversation"]["wake_phrase"], "Jarvis")
        self.assertEqual(state["voice"]["live_conversation"]["silence_ms"], 1200)
        self.assertEqual(state["voice"]["live_conversation"]["max_turn_seconds"], 30)
        self.assertFalse(state["voice"]["live_conversation"]["auto_speak"])
        self.assertFalse(state["voice"]["live_conversation"]["barge_in"])

    def test_mobile_communication_settings_action_updates_state(self):
        state_path = TEST_RUNTIME_DIR / "mobile_communication_settings_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = yarbis_mobile._execute_action("communication_settings", {
                "tone": "directo",
                "detail_level": "breve",
                "proactivity": "alta",
            })
            state = memory.load_state()

        self.assertIn("Comunicacion actualizada", result["result"])
        self.assertEqual(state["communication"]["tone"], "direct")
        self.assertEqual(state["communication"]["detail_level"], "brief")
        self.assertEqual(state["communication"]["proactivity"], "high")

    def test_mobile_voice_settings_action_updates_edge_state(self):
        state_path = TEST_RUNTIME_DIR / "mobile_voice_edge_settings_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            result = yarbis_mobile._execute_action("voice_settings", {
                "enabled": True,
                "tts_provider": "edge",
                "edge_voice": "es-ES-AlvaroNeural",
                "edge_rate": 40,
                "edge_pitch": -10,
                "edge_volume": 25,
                "tts_rate": 200,
                "telegram_reply_mode": "auto",
            })
            state = memory.load_state()

        self.assertIn("Voz actualizada", result["result"])
        self.assertEqual(state["voice"]["tts_provider"], "edge")
        self.assertEqual(state["voice"]["edge_voice"], "es-ES-AlvaroNeural")
        self.assertEqual(state["voice"]["edge_rate"], 40)
        self.assertEqual(state["voice"]["edge_pitch"], -10)
        self.assertEqual(state["voice"]["edge_volume"], 25)

    def test_public_state_default_is_lightweight_and_loads_state_once(self):
        seeded_state = memory.default_state()
        seeded_state["messages"] = [
            {"role": "assistant", "content": "x" * 4000}
            for _ in range(100)
        ]
        seeded_state["last_result"] = "r" * (yarbis_mobile.MOBILE_LAST_RESULT_CHARS + 20)

        with patch.object(yarbis_mobile, "load_state", return_value=memory.normalize_state(seeded_state)) as load_mock:
            with patch.object(yarbis_mobile, "get_service_status", return_value=self._service_status()):
                with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                    payload = yarbis_mobile._public_state()

        self.assertEqual(load_mock.call_count, 1)
        self.assertIn("jobs", payload)
        self.assertIn("conversation", payload)
        self.assertIn("communication", payload)
        self.assertIn("communication", payload["conversation"])
        self.assertIn("voice", payload["conversation"])
        self.assertIn("health_text", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("activity_text", payload)
        self.assertNotIn("summary_text", payload)
        self.assertNotIn("memory_protection_status", payload)
        self.assertLess(len(payload["last_result"]), yarbis_mobile.MOBILE_LAST_RESULT_CHARS + 100)

    def test_public_state_views_are_lazy(self):
        seeded_state = memory.normalize_state({
            "goal": "demo movil",
            "profile": {"name": "Ana"},
            "notes": [{"id": "note-1", "title": "Nota", "content": "Contenido"}],
            "tasks": [{"id": "task-1", "title": "Tarea", "status": "pending"}],
            "coding": {"workspace_path": r"C:\DEV\demo", "pending_proposal_ids": ["prop-1"]},
        })

        with patch.object(yarbis_mobile, "load_state", return_value=seeded_state):
            with patch.object(yarbis_mobile, "get_service_status", return_value=self._service_status()):
                with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                    with patch.object(
                        yarbis_mobile,
                        "coding_list_proposals_text",
                        return_value="Propuestas pendientes:\n- prop-1",
                    ):
                        context_payload = yarbis_mobile._public_state("context")
                    settings_payload = yarbis_mobile._public_state("settings")
                    activity_payload = yarbis_mobile._public_state("activity")

        self.assertEqual(context_payload["profile"]["name"], "Ana")
        self.assertIn("prop-1", context_payload["coding"]["proposals_text"])
        self.assertIn("model_provider", settings_payload)
        self.assertIn("memory_protection_status", settings_payload)
        self.assertIn("summary_text", activity_payload)
        self.assertIn("activity_text", activity_payload)

    def test_mobile_activity_history_uses_small_lazy_limits(self):
        seeded_state = memory.default_state()
        event = {
            "timestamp": "2026-05-23T00:00:00+00:00",
            "type": "remote_job_finished",
            "label": "Respuesta",
            "content": "ok",
        }

        with patch.object(yarbis_mobile.activity, "activity_history_signature", return_value=((1, 1), (2, 2))):
            with patch.object(yarbis_mobile.activity, "read_activity_log", return_value="historial") as log_mock:
                with patch.object(yarbis_mobile.activity, "read_recent_events", return_value=[event]) as events_mock:
                    rendered = yarbis_mobile._mobile_activity_history(seeded_state)

        log_mock.assert_called_once_with(max_bytes=yarbis_mobile.MOBILE_ACTIVITY_MAX_BYTES)
        events_mock.assert_called_once_with(limit=yarbis_mobile.MOBILE_ACTIVITY_EVENT_LIMIT)
        self.assertIn("historial", rendered)
        self.assertIn("Respuesta remoto finalizado", rendered)

    def test_mobile_job_timeout_sends_notification(self):
        sent = []
        notified = threading.Event()

        def fake_notification(title, body):
            sent.append((title, body))
            notified.set()
            return True

        with patch.object(
            yarbis_mobile,
            "_session_operation_subprocess",
            side_effect=yarbis_mobile.MobileJobTimeoutError(120),
        ):
            with patch.object(yarbis_mobile, "send_notification", side_effect=fake_notification):
                job = yarbis_mobile._start_job("Respuesta", "submit_user_reply", {"reply_text": "hola"})
                self.assertTrue(notified.wait(timeout=2))

        stored = yarbis_mobile._get_job(job["id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("excedio el timeout movil", sent[0][1])
        self.assertIn("120 segundos", sent[0][1])

    def test_mobile_html_uses_lazy_views_and_slow_auto_refresh(self):
        html = yarbis_mobile._html_page()

        self.assertIn("refreshInFlight", html)
        self.assertIn("statePath(name)", html)
        self.assertIn("currentTab === \"activity\"", html)
        self.assertIn("mobileJobTimeout", html)
        self.assertIn("mobileHttpsEnabled", html)
        self.assertIn("}, 15000);", html)
        self.assertIn("type=\"file\" accept=\"audio/*\" capture", html)
        self.assertIn("Detener habla", html)
        self.assertIn("speechSynthesis.cancel", html)
        self.assertIn("/api/voice/speak", html)
        self.assertIn("/api/voice/live/start", html)
        self.assertIn("unlockMobileSpeechOutput", html)
        self.assertIn("mobileSpeechUnlocked", html)
        self.assertIn("No pude reproducir voz", html)
        self.assertIn("utterance.onerror", html)
        self.assertIn("await unlockMobileSpeechOutput()", html)
        self.assertIn("liveVoiceSpeechText", html)
        self.assertIn("spoken_turn_id", html)
        self.assertIn("LIVE_VOICE_POST_SPEECH_PAUSE_MS", html)
        self.assertIn("toggle-live-voice", html)
        self.assertIn("Conversacion en vivo", html)
        self.assertIn("preferredAudioRecorderOptions", html)
        self.assertIn("audioBlobFromChunks", html)
        self.assertIn("LIVE_VOICE_SEGMENT_MS", html)
        self.assertIn("startLiveVoiceSegment", html)
        self.assertNotIn("start(3000)", html)
        self.assertIn("Subir audio", html)
        self.assertNotIn("Usa Grabar archivo", html)
        self.assertNotIn("iPhone exige HTTPS", html)
        self.assertNotIn("Grabar archivo", html)
        self.assertIn("communicationTone", html)
        self.assertIn("save-communication", html)
        self.assertIn("preferredLocalSpeechFormat", html)
        self.assertIn("Refrescar voces", html)
        self.assertIn("modalBackdrop", html)
        self.assertNotIn("confirm(", html)
        self.assertNotIn("prompt(", html)
        self.assertIn("Voz neural (edge-tts)", html)
        self.assertIn("Usar seleccionada", html)
        self.assertIn("Probar voz", html)
        self.assertIn("edgeVoiceFilter", html)
        self.assertIn("voiceGroupEdge", html)
        self.assertIn("voiceGroupSystem", html)
        self.assertIn("edgeRate", html)
        self.assertIn("applyVoiceProviderVisibility", html)
        self.assertIn("Guardar validación", html)
        self.assertIn("coding_validate", html)
        self.assertIn("coding_check", html)
        self.assertIn("coding_apply_validate", html)
        self.assertIn("select-coding-proposal", html)
        self.assertIn("coding_search", html)
        self.assertIn("coding_read_range", html)
        self.assertIn("coding_validation_plan", html)
        self.assertIn("ntfyPriority", html)
        self.assertIn("ntfyTags", html)
        self.assertIn("guardado; vacío conserva", html)
        self.assertNotIn("Â", html)

    def test_mobile_coding_validation_actions_route_to_session_helpers(self):
        with patch.object(
            yarbis_mobile,
            "coding_update_validation_command_text",
            return_value="guardado",
        ) as update_mock:
            update_result = yarbis_mobile._execute_action("coding_validation", {"command": "pytest"})

        with patch.object(
            yarbis_mobile,
            "coding_run_validation_text",
            return_value="validado",
        ) as validate_mock:
            validate_result = yarbis_mobile._execute_action(
                "coding_validate",
                {"proposal_id": "proposal-1", "command": "pytest"},
            )

        self.assertEqual(update_result["result"], "guardado")
        self.assertEqual(validate_result["result"], "validado")
        update_mock.assert_called_once_with("pytest")
        validate_mock.assert_called_once_with(proposal_id="proposal-1", command="pytest")

    def test_mobile_coding_guided_actions_route_to_session_helpers(self):
        with patch.object(
            yarbis_mobile,
            "coding_workflow_status_text",
            return_value="status",
        ) as status_mock:
            status_result = yarbis_mobile._execute_action("coding_status", {})

        with patch.object(
            yarbis_mobile,
            "coding_check_proposal_text",
            return_value="check",
        ) as check_mock:
            check_result = yarbis_mobile._execute_action("coding_check", {"proposal_id": "proposal-1"})

        with patch.object(
            yarbis_mobile,
            "coding_apply_and_validate_text",
            return_value="aplicado validado",
        ) as apply_validate_mock:
            apply_validate_result = yarbis_mobile._execute_action(
                "coding_apply_validate",
                {"proposal_id": "proposal-1", "command": "pytest"},
            )

        with patch.object(
            yarbis_mobile,
            "coding_detect_validation_command_text",
            return_value="detectado",
        ) as detect_mock:
            detect_result = yarbis_mobile._execute_action("coding_detect_validation", {})

        with patch.object(
            yarbis_mobile,
            "coding_search_text_text",
            return_value="busqueda",
        ) as search_mock:
            search_result = yarbis_mobile._execute_action("coding_search", {"pattern": "foo", "path": ".", "glob": "*.py"})

        with patch.object(
            yarbis_mobile,
            "coding_read_text_range_text",
            return_value="rango",
        ) as range_mock:
            range_result = yarbis_mobile._execute_action(
                "coding_read_range",
                {"path": "app.py", "start_line": 2, "line_count": 5},
            )

        with patch.object(
            yarbis_mobile,
            "coding_validation_plan_text",
            return_value="plan",
        ) as plan_mock:
            plan_result = yarbis_mobile._execute_action("coding_validation_plan", {"proposal_id": "proposal-1"})

        self.assertEqual(status_result["result"], "status")
        self.assertEqual(check_result["result"], "check")
        self.assertEqual(apply_validate_result["result"], "aplicado validado")
        self.assertEqual(detect_result["result"], "detectado")
        self.assertEqual(search_result["result"], "busqueda")
        self.assertEqual(range_result["result"], "rango")
        self.assertEqual(plan_result["result"], "plan")
        status_mock.assert_called_once_with(include_diff=False)
        check_mock.assert_called_once_with("proposal-1")
        apply_validate_mock.assert_called_once_with(proposal_id="proposal-1", command="pytest")
        detect_mock.assert_called_once_with()
        search_mock.assert_called_once_with(pattern="foo", path=".", glob="*.py")
        range_mock.assert_called_once_with(path="app.py", start_line=2, line_count=5)
        plan_mock.assert_called_once_with("proposal-1")

    def test_detect_tailscale_dns_name_reads_magicdns(self):
        payload = {"Self": {"DNSName": "desktop-2p3ou07.tail82d7a7.ts.net."}}
        completed = subprocess.CompletedProcess(
            ["tailscale", "status", "--json"],
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch.object(yarbis_mobile, "_run_tailscale_command", return_value=completed) as run_mock:
            dns_name = yarbis_mobile.detect_tailscale_dns_name()

        self.assertEqual(dns_name, "desktop-2p3ou07.tail82d7a7.ts.net")
        run_mock.assert_called_once_with(["status", "--json"], timeout_seconds=2.0)

    def test_detect_tailscale_cert_domains_reads_status(self):
        payload = {
            "CertDomains": [
                "desktop-2p3ou07.tail82d7a7.ts.net.",
                "other.tail82d7a7.ts.net",
                "",
            ],
        }
        completed = subprocess.CompletedProcess(
            ["tailscale", "status", "--json"],
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch.object(yarbis_mobile, "_run_tailscale_command", return_value=completed) as run_mock:
            domains = yarbis_mobile.detect_tailscale_cert_domains()

        self.assertEqual(
            domains,
            ("desktop-2p3ou07.tail82d7a7.ts.net", "other.tail82d7a7.ts.net"),
        )
        run_mock.assert_called_once_with(["status", "--json"], timeout_seconds=2.0)

    def test_current_tailscale_serve_targets_extracts_proxy_targets(self):
        status = {
            "Web": {
                "desktop.tail.ts.net:443": {
                    "Handlers": {
                        "/": {"Proxy": "http://127.0.0.1:9304/"},
                    },
                },
                "other.tail.ts.net:443": "https://localhost:9443/",
            },
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_serve_status", return_value=status):
            targets = yarbis_mobile.current_tailscale_serve_targets()

        self.assertEqual(targets, ("http://127.0.0.1:9304", "https://localhost:9443"))

    def test_public_mobile_status_prioritizes_https_url(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8787,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "http://127.0.0.1:8787",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(
                        yarbis_mobile,
                        "_cached_tailscale_serve_status",
                        return_value={"Web": {"desktop.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}}}},
                    ):
                        with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8787"]):
                            status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["secure_url"], "https://desktop.tail.ts.net/")
        self.assertEqual(status["tailscale_https_url"], "https://desktop.tail.ts.net/")
        self.assertEqual(status["active_urls"][0], "https://desktop.tail.ts.net/")
        self.assertIn("http://127.0.0.1:8787", status["active_urls"])
        self.assertTrue(status["https_ready"])
        self.assertEqual(status["https_pending_reason"], "")
        self.assertTrue(status["tailscale_https_supported"])
        self.assertTrue(status["tailscale_serve_matches_target"])

    def test_public_mobile_status_does_not_advertise_inactive_https(self):
        settings = {
            "enabled": False,
            "pin_hash": "",
            "port": 8787,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8787"]):
                        status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["tailscale_https_url"], "https://desktop.tail.ts.net/")
        self.assertEqual(status["secure_url"], "")
        self.assertEqual(status["active_urls"], [])
        self.assertFalse(status["https_ready"])
        self.assertEqual(status["https_pending_reason"], "ui_disabled")

    def test_public_mobile_status_requires_tailscale_serve_target_for_https(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8787,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(yarbis_mobile, "_cached_tailscale_serve_status", return_value={}):
                        with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8787"]):
                            status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["tailscale_https_url"], "https://desktop.tail.ts.net/")
        self.assertEqual(status["secure_url"], "")
        self.assertEqual(status["active_urls"], ["http://127.0.0.1:8787"])
        self.assertFalse(status["https_ready"])
        self.assertEqual(status["https_pending_reason"], "serve_pending")

    def test_public_mobile_status_rejects_stale_serve_target(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8946,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "http://127.0.0.1:8946",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(
                        yarbis_mobile,
                        "_cached_tailscale_serve_status",
                        return_value={"Web": {"desktop.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9304"}}}}},
                    ):
                        with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8946"]):
                            status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["secure_url"], "")
        self.assertFalse(status["https_ready"])
        self.assertFalse(status["tailscale_serve_matches_target"])
        self.assertEqual(status["https_pending_reason"], "serve_pending")

    def test_public_mobile_status_flags_foreign_serve_target(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8787,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(
                        yarbis_mobile,
                        "_cached_tailscale_serve_status",
                        return_value={"Web": {"desktop.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8946"}}}}},
                    ):
                        with patch.object(yarbis_mobile, "_known_yarbis_mobile_serve_targets", return_value=set()):
                            with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8787"]):
                                status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertFalse(status["https_ready"])
        self.assertTrue(status["tailscale_serve_points_to_foreign_target"])
        self.assertEqual(status["https_pending_reason"], "serve_foreign_target")
        self.assertEqual(status["tailscale_expected_serve_target"], "http://127.0.0.1:8787")

    def test_claim_mobile_https_override_passes_through(self):
        captured = {}

        def fake_claim(override=False):
            captured["override"] = override
            return "ok"

        with patch.object(yarbis_mobile, "claim_mobile_https_for_current_instance", side_effect=fake_claim):
            yarbis_mobile._execute_action("mobile_https_claim", {"override": True})
        self.assertTrue(captured["override"])

    def test_live_voice_selftest_reports_wake_detection(self):
        state_path = TEST_RUNTIME_DIR / f"live_selftest_{id(self)}.json"
        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_mobile, "_decode_mobile_audio_payload", return_value=(b"audio", ".webm")):
                with patch.object(yarbis_mobile.voice_conversation, "transcribe_live_audio_bytes", return_value="Yarbis que hora es"):
                    ok = yarbis_mobile._selftest_mobile_live_voice({"audio_b64": "x", "mime_type": "audio/webm"})
                with patch.object(yarbis_mobile.voice_conversation, "transcribe_live_audio_bytes", return_value="que hora es"):
                    no_wake = yarbis_mobile._selftest_mobile_live_voice({"audio_b64": "x", "mime_type": "audio/webm"})
        self.assertTrue(ok["wake_detected"])
        self.assertIn("Yarbis que hora es", ok["transcript"])
        self.assertFalse(no_wake["wake_detected"])
        self.assertIn("activación", no_wake["message"].lower())

    def test_html_includes_live_voice_vad_and_selftest(self):
        html = yarbis_mobile._html_page()
        for needle in ("ensureLiveVoiceAnalyser", "selftest-live-voice", "liveContinuous", "selfTestLiveVoice"):
            self.assertIn(needle, html)

    def test_public_mobile_status_treats_known_other_instance_as_pending_not_error(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8946,
            "https_enabled": True,
            "https_last_error": "error viejo",
            "tailscale_serve_target": "http://127.0.0.1:8946",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(
                        yarbis_mobile,
                        "_cached_tailscale_serve_status",
                        return_value={"Web": {"desktop.tail.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9304"}}}}},
                    ):
                        with patch.object(
                            yarbis_mobile,
                            "_known_yarbis_mobile_serve_targets",
                            return_value={"http://127.0.0.1:9304", "http://127.0.0.1:8946"},
                        ):
                            status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["secure_url"], "")
        self.assertFalse(status["https_ready"])
        self.assertEqual(status["https_pending_reason"], "serve_pending")
        self.assertEqual(status["https_last_error"], "")
        self.assertEqual(status["stored_https_last_error"], "error viejo")
        self.assertEqual(status["tailscale_serve_current_targets"], ["http://127.0.0.1:9304"])

    def test_public_mobile_status_reports_missing_tailscale_https_certs(self):
        settings = {
            "enabled": True,
            "pin_hash": "hash",
            "port": 8787,
            "https_enabled": True,
            "https_last_error": "",
            "tailscale_serve_target": "",
        }

        with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value="100.99.240.111"):
            with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=()):
                    with patch.object(yarbis_mobile, "_active_mobile_urls", return_value=["http://127.0.0.1:8787"]):
                        status = yarbis_mobile.public_mobile_ui_status(settings)

        self.assertEqual(status["tailscale_https_url"], "https://desktop.tail.ts.net/")
        self.assertEqual(status["secure_url"], "")
        self.assertFalse(status["tailscale_https_supported"])
        self.assertEqual(status["https_pending_reason"], "tailscale_https_cert_missing")

    def test_ensure_mobile_ui_servers_configures_tailscale_serve_https(self):
        state_path = TEST_RUNTIME_DIR / "mobile_server_https_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        completed = subprocess.CompletedProcess(["tailscale"], 0, stdout="", stderr="")

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value=""):
                with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                    with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                        yarbis_mobile.update_mobile_ui_settings(enabled=True, port=port, pin="2468")
            with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                with patch.object(yarbis_mobile, "detect_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                    with patch.object(yarbis_mobile, "detect_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                        with patch.object(yarbis_mobile, "_tailscale_serve_status", return_value={}):
                            with patch.object(yarbis_mobile, "_run_tailscale_command", return_value=completed) as run_mock:
                                result = yarbis_mobile.ensure_mobile_ui_servers()
            state = memory.load_state()

        target = f"http://127.0.0.1:{port}"
        self.assertIn("HTTPS iPhone listo", result)
        self.assertEqual(state["service"]["mobile_ui"]["tailscale_serve_target"], target)
        run_mock.assert_called_once_with(["serve", "--bg", "--yes", target], timeout_seconds=10.0)

    def test_configure_tailscale_https_refuses_foreign_serve_status(self):
        foreign_status = {"Web": {"https://desktop.tail.ts.net/": "http://127.0.0.1:3000"}}

        with patch.object(yarbis_mobile, "detect_tailscale_dns_name", return_value="desktop.tail.ts.net"):
            with patch.object(yarbis_mobile, "detect_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                with patch.object(yarbis_mobile, "_tailscale_serve_status", return_value=foreign_status):
                    with patch.object(yarbis_mobile, "_known_yarbis_mobile_serve_targets", return_value=set()):
                        with patch.object(yarbis_mobile, "_run_tailscale_command") as run_mock:
                            with self.assertRaises(yarbis_mobile.MobileUiError) as raised:
                                yarbis_mobile._configure_tailscale_https(8787, {})

        self.assertIn("no parece ser de Yarbis", str(raised.exception))
        run_mock.assert_not_called()

    def test_configure_tailscale_https_can_move_between_yarbis_instances(self):
        status = {"Web": {"https://desktop.tail.ts.net/": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8946"}}}}}
        completed = subprocess.CompletedProcess(["tailscale"], 0, stdout="", stderr="")

        with patch.object(yarbis_mobile, "detect_tailscale_dns_name", return_value="desktop.tail.ts.net"):
            with patch.object(yarbis_mobile, "detect_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                with patch.object(yarbis_mobile, "_tailscale_serve_status", return_value=status):
                    with patch.object(
                        yarbis_mobile,
                        "_known_yarbis_mobile_serve_targets",
                        return_value={"http://127.0.0.1:8946"},
                    ):
                        with patch.object(yarbis_mobile, "_run_tailscale_command", return_value=completed) as run_mock:
                            result = yarbis_mobile._configure_tailscale_https(9304, {})

        self.assertEqual(result["target"], "http://127.0.0.1:9304")
        run_mock.assert_called_once_with(
            ["serve", "--bg", "--yes", "http://127.0.0.1:9304"],
            timeout_seconds=10.0,
        )

    def test_claim_mobile_https_for_current_instance_moves_serve_target(self):
        state_path = TEST_RUNTIME_DIR / "mobile_claim_https_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        target = f"http://127.0.0.1:{port}"
        completed = subprocess.CompletedProcess(["tailscale"], 0, stdout="", stderr="")
        serve_status = {"Web": {"desktop.tail.ts.net:443": {"Handlers": {"/": {"Proxy": target}}}}}

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=port, pin="2468")
            with patch.object(yarbis_mobile, "detect_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                with patch.object(yarbis_mobile, "detect_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                    with patch.object(yarbis_mobile, "_tailscale_serve_status", return_value={}):
                        with patch.object(yarbis_mobile, "_run_tailscale_command", return_value=completed) as run_mock:
                            with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value=""):
                                with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                                    with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                                        with patch.object(yarbis_mobile, "_cached_tailscale_serve_status", return_value=serve_status):
                                            result = yarbis_mobile.claim_mobile_https_for_current_instance()
            state = memory.load_state()

        self.assertIn("HTTPS movil ahora apunta a esta instancia", result)
        self.assertIn(target, result)
        self.assertEqual(state["service"]["mobile_ui"]["tailscale_serve_target"], target)
        run_mock.assert_called_once_with(["serve", "--bg", "--yes", target], timeout_seconds=10.0)

    def test_configure_tailscale_https_requires_cert_domains(self):
        with patch.object(yarbis_mobile, "detect_tailscale_dns_name", return_value="desktop.tail.ts.net"):
            with patch.object(yarbis_mobile, "detect_tailscale_cert_domains", return_value=()):
                with patch.object(yarbis_mobile, "_tailscale_serve_status") as status_mock:
                    with patch.object(yarbis_mobile, "_run_tailscale_command") as run_mock:
                        with self.assertRaises(yarbis_mobile.MobileUiError) as raised:
                            yarbis_mobile._configure_tailscale_https(8787, {})

        self.assertIn("HTTPS Certificates no esta habilitado", str(raised.exception))
        status_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_ensure_mobile_ui_servers_keeps_http_when_https_fails(self):
        state_path = TEST_RUNTIME_DIR / "mobile_server_https_error_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        port = _free_port()

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            with patch.object(yarbis_mobile, "_cached_tailscale_ipv4", return_value=""):
                with patch.object(yarbis_mobile, "_cached_tailscale_dns_name", return_value="desktop.tail.ts.net"):
                    with patch.object(yarbis_mobile, "_cached_tailscale_cert_domains", return_value=("desktop.tail.ts.net",)):
                        yarbis_mobile.update_mobile_ui_settings(enabled=True, port=port, pin="2468")
            with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                with patch.object(
                    yarbis_mobile,
                    "_configure_tailscale_https",
                    side_effect=yarbis_mobile.MobileUiError("serve ocupado"),
                ):
                    result = yarbis_mobile.ensure_mobile_ui_servers()
            state = memory.load_state()

        self.assertIn(f"http://127.0.0.1:{port}", result)
        self.assertIn("HTTPS iPhone no activo: serve ocupado", result)
        self.assertEqual(state["service"]["mobile_ui"]["https_last_error"], "serve ocupado")

    def test_mobile_settings_expose_https_claim_action(self):
        source = Path(yarbis_mobile.__file__).read_text(encoding="utf-8")

        self.assertIn('data-action="claim-mobile-https"', source)
        self.assertIn('action("mobile_https_claim"', source)
        self.assertIn('HTTPS disponible, pero apunta a otra instancia', source)

    def test_ensure_mobile_ui_servers_starts_localhost_when_tailscale_missing(self):
        state_path = TEST_RUNTIME_DIR / "mobile_server_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        port = _free_port()

        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            yarbis_mobile.update_mobile_ui_settings(enabled=True, port=port, pin="2468", https_enabled=False)
            with patch.object(yarbis_mobile, "detect_tailscale_ipv4", return_value=""):
                result = yarbis_mobile.ensure_mobile_ui_servers()
                status = yarbis_mobile.public_mobile_ui_status()
                last_bind_error = memory.load_state()["service"]["mobile_ui"]["last_bind_error"]

        self.assertIn("UI movil activa", result)
        self.assertEqual(status["active_urls"], [f"http://127.0.0.1:{port}"])
        self.assertIn("localhost", last_bind_error)

    def test_service_mobile_dialog_rejects_enabled_without_pin(self):
        dialog = object.__new__(ServiceMobileUiDialog)
        dialog.enabled_var = SimpleNamespace(get=lambda: True)
        dialog.port_var = SimpleNamespace(get=lambda: "8787")
        dialog.timeout_var = SimpleNamespace(get=lambda: "1800")
        dialog.pin_var = SimpleNamespace(get=lambda: "")
        dialog._configured = False

        with patch("ui_settings_dialogs.messagebox.showwarning") as warning_mock:
            self.assertFalse(ServiceMobileUiDialog.validate(dialog))

        warning_mock.assert_called_once()

    def test_voice_settings_dialog_apply_preserves_provider_choices(self):
        dialog = object.__new__(VoiceSettingsDialog)
        dialog.enabled_var = SimpleNamespace(get=lambda: True)
        dialog.provider_var = SimpleNamespace(get=lambda: "edge")
        dialog.voice_combo = SimpleNamespace(get=lambda: "Sistema Uno")
        dialog.edge_combo = SimpleNamespace(get=lambda: "Neural Uno")
        dialog.rate_var = SimpleNamespace(get=lambda: "195")
        dialog.edge_rate_var = SimpleNamespace(get=lambda: "40")
        dialog.edge_pitch_var = SimpleNamespace(get=lambda: "-10")
        dialog.edge_volume_var = SimpleNamespace(get=lambda: "25")
        dialog.telegram_mode_var = SimpleNamespace(get=lambda: "always")
        dialog._system_label_to_id = {"Sistema Uno": "system-voice"}
        dialog._edge_label_to_id = {"Neural Uno": "es-ES-AlvaroNeural"}

        VoiceSettingsDialog.apply(dialog)

        self.assertEqual(dialog.result["tts_provider"], "edge")
        self.assertEqual(dialog.result["tts_voice_id"], "system-voice")
        self.assertEqual(dialog.result["edge_voice"], "es-ES-AlvaroNeural")
        self.assertEqual(dialog.result["edge_rate"], "40")
        self.assertEqual(dialog.result["edge_pitch"], "-10")
        self.assertEqual(dialog.result["edge_volume"], "25")

    def test_voice_settings_dialog_requires_edge_voice_for_edge_provider(self):
        dialog = object.__new__(VoiceSettingsDialog)
        dialog.provider_var = SimpleNamespace(get=lambda: "edge")
        dialog.edge_combo = SimpleNamespace(get=lambda: "sin elegir")
        dialog.rate_var = SimpleNamespace(get=lambda: "175")
        dialog.edge_rate_var = SimpleNamespace(get=lambda: "0")
        dialog.edge_pitch_var = SimpleNamespace(get=lambda: "0")
        dialog.edge_volume_var = SimpleNamespace(get=lambda: "0")
        dialog._edge_label_to_id = {"sin elegir": ""}

        with patch("ui_settings_dialogs.messagebox.showwarning") as warning_mock:
            self.assertFalse(VoiceSettingsDialog.validate(dialog))

        warning_mock.assert_called_once()

    def test_notifications_dialog_apply_preserves_stored_tokens(self):
        dialog = object.__new__(NotificationsDialog)
        dialog.enabled_var = SimpleNamespace(get=lambda: True)
        dialog.windows_var = SimpleNamespace(get=lambda: True)
        dialog.ntfy_var = SimpleNamespace(get=lambda: True)
        dialog.telegram_var = SimpleNamespace(get=lambda: True)
        dialog.server_entry = SimpleNamespace(get=lambda: "https://ntfy.sh")
        dialog.topic_entry = SimpleNamespace(get=lambda: "yarbis")
        dialog.token_entry = SimpleNamespace(get=lambda: "")
        dialog.priority_combo = SimpleNamespace(get=lambda: "high")
        dialog.tags_entry = SimpleNamespace(get=lambda: "yarbis")
        dialog.telegram_bot_token_entry = SimpleNamespace(get=lambda: "")
        dialog.telegram_chat_id_entry = SimpleNamespace(get=lambda: "123")
        dialog._initial_ntfy_token = "ntfy-secret"
        dialog._initial_telegram_bot_token = "telegram-secret"

        NotificationsDialog.apply(dialog)

        self.assertEqual(dialog.result["ntfy_token"], "ntfy-secret")
        self.assertEqual(dialog.result["telegram_bot_token"], "telegram-secret")
        self.assertEqual(dialog.result["ntfy_priority"], "high")
        self.assertEqual(dialog.result["ntfy_tags"], "yarbis")


if __name__ == "__main__":
    unittest.main()
