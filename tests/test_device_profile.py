"""Perfil de dispositivo, capacidades efectivas y gates de runtime.

Todo MOCKEADO: nunca se consultan /proc, /sys, pmset ni termux-api reales, y
ningun test escribe estado (el principio del feature es justamente no mutarlo).
"""

import subprocess
import unittest
from unittest.mock import patch

import device_profile


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class _ProfileEnv:
    """Arma un perfil determinista parcheando todas las senales del modulo."""

    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        defaults = {
            "system": "Linux",
            "termux": False,
            "container": False,
            "wsl": False,
            "virtualization": "",
            "sbc_model": "",
            "display": True,
            "total_memory": 16 * 1024**3,
            "power": {"available": False, "ac_line_status": "unknown", "battery_percent": None},
            "memory": {},
        }
        cfg = {**defaults, **self.overrides}
        self._patches = [
            patch.object(device_profile.platform, "system", return_value=cfg["system"]),
            patch.object(device_profile, "is_termux", return_value=cfg["termux"]),
            patch.object(device_profile, "is_container", return_value=cfg["container"]),
            patch.object(device_profile, "is_wsl", return_value=cfg["wsl"]),
            patch.object(device_profile, "detect_virtualization", return_value=cfg["virtualization"]),
            patch.object(device_profile, "sbc_model", return_value=cfg["sbc_model"]),
            patch.object(device_profile, "has_display", return_value=cfg["display"]),
            patch.object(device_profile, "_total_memory_bytes", return_value=cfg["total_memory"]),
            patch.object(device_profile, "_power_status", return_value=cfg["power"]),
            patch.object(device_profile, "_memory_status", return_value=cfg["memory"]),
            patch.object(device_profile, "_has_gpu", return_value=True),
        ]
        for p in self._patches:
            p.start()
        device_profile._CACHE["profile"] = None
        device_profile._CACHE["created_at"] = 0.0
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        device_profile._CACHE["profile"] = None
        device_profile._CACHE["created_at"] = 0.0
        return False


class DeviceClassTestCase(unittest.TestCase):
    def test_workstation_with_display_and_ram(self):
        with _ProfileEnv():
            profile = device_profile.get_profile(refresh=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_WORKSTATION)
        self.assertTrue(profile["capabilities"][device_profile.CAP_DESKTOP_CONTROL])
        self.assertTrue(profile["capabilities"][device_profile.CAP_LOCAL_LLM])

    def test_headless_server_has_no_desktop_or_audio(self):
        with _ProfileEnv(display=False):
            profile = device_profile.get_profile(refresh=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_SERVER_HEADLESS)
        caps = profile["capabilities"]
        self.assertFalse(caps[device_profile.CAP_GUI])
        self.assertFalse(caps[device_profile.CAP_DESKTOP_CONTROL])
        self.assertFalse(caps[device_profile.CAP_VISIBLE_BROWSER])
        self.assertFalse(caps[device_profile.CAP_AUDIO_IN])
        # El navegador headless si se puede usar en un servidor.
        self.assertTrue(caps[device_profile.CAP_BROWSER])

    def test_android_termux_profile(self):
        with _ProfileEnv(termux=True, display=False, total_memory=4 * 1024**3):
            profile = device_profile.get_profile(refresh=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_ANDROID_TERMUX)
        caps = profile["capabilities"]
        self.assertFalse(caps[device_profile.CAP_BROWSER])  # Playwright no tiene binarios
        self.assertFalse(caps[device_profile.CAP_AUDIO_IN])
        self.assertFalse(caps[device_profile.CAP_DESKTOP_CONTROL])
        self.assertFalse(caps[device_profile.CAP_LOCAL_LLM])

    def test_container_and_wsl_and_sbc(self):
        with _ProfileEnv(container=True, display=False):
            self.assertEqual(
                device_profile.get_profile(refresh=True)["device_class"],
                device_profile.CLASS_CONTAINER,
            )
        with _ProfileEnv(sbc_model="Raspberry Pi 4 Model B", display=False):
            self.assertEqual(
                device_profile.get_profile(refresh=True)["device_class"],
                device_profile.CLASS_SBC,
            )
        with _ProfileEnv(wsl=True):
            self.assertTrue(device_profile.get_profile(refresh=True)["wsl"])

    def test_laptop_on_battery_is_power_constrained(self):
        power = {"available": True, "ac_line_status": "battery", "battery_percent": 55}
        with _ProfileEnv(power=power):
            profile = device_profile.get_profile(refresh=True)
        self.assertEqual(profile["device_class"], device_profile.CLASS_LAPTOP)
        self.assertTrue(profile["power_constrained"])

    def test_low_memory_device(self):
        with _ProfileEnv(total_memory=1 * 1024**3):
            profile = device_profile.get_profile(refresh=True)
        self.assertTrue(profile["low_memory"])
        self.assertFalse(profile["capabilities"][device_profile.CAP_LOCAL_LLM])

    def test_capability_allows_gives_a_reason_when_denied(self):
        with _ProfileEnv(display=False):
            allowed, reason = device_profile.capability_allows(device_profile.CAP_DESKTOP_CONTROL)
        self.assertFalse(allowed)
        self.assertIn("escritorio", reason)
        self.assertIn(device_profile.CLASS_SERVER_HEADLESS, reason)

    def test_summary_omits_local_llm_as_a_hard_limit(self):
        # local_llm es consultivo: no debe leerse como "no puedo hacerlo".
        with _ProfileEnv(total_memory=1 * 1024**3):
            summary = device_profile.render_profile_summary()
        self.assertNotIn("modelo local", summary)


class EnvironmentDetectionTestCase(unittest.TestCase):
    def test_termux_detected_from_prefix(self):
        with patch.dict("os.environ", {"PREFIX": "/data/data/com.termux/files/usr"}, clear=False):
            self.assertTrue(device_profile.is_termux())

    def test_linux_display_detection(self):
        with patch.object(device_profile.platform, "system", return_value="Linux"), \
             patch.object(device_profile, "is_termux", return_value=False):
            with patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False):
                self.assertTrue(device_profile.has_display())
            with patch.dict("os.environ", {}, clear=True):
                self.assertFalse(device_profile.has_display())


class PcContextParsersTestCase(unittest.TestCase):
    """Parsers por-SO de bateria y memoria (pc_context)."""

    def test_linux_memory_from_proc_meminfo(self):
        import pc_context

        meminfo = "MemTotal:       8000000 kB\nMemAvailable:   2000000 kB\nSwapTotal: 0 kB\n"
        with patch.object(pc_context.Path, "read_text", return_value=meminfo):
            result = pc_context._linux_memory_status({
                "available": False, "load_percent": None, "total": "", "available_memory": "",
            })
        self.assertTrue(result["available"])
        self.assertEqual(result["load_percent"], 75)  # 6 de 8 GB usados

    def test_macos_memory_from_sysctl_and_vm_stat(self):
        import pc_context

        def fake_capture(args, timeout=3):
            if args[:2] == ["sysctl", "-n"]:
                return str(8 * 1024**3)
            return "Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free:  100000.\nPages inactive: 50000.\n"

        with patch.object(pc_context, "_run_capture", side_effect=fake_capture):
            result = pc_context._macos_memory_status({
                "available": False, "load_percent": None, "total": "", "available_memory": "",
            })
        self.assertTrue(result["available"])
        self.assertIn("GB", result["total"])

    def test_termux_battery_status_parsed(self):
        import pc_context

        payload = '{"percentage": 42, "status": "DISCHARGING", "plugged": "UNPLUGGED"}'
        with patch.object(pc_context.shutil, "which", return_value="/bin/termux-battery-status"), \
             patch.object(pc_context, "_run_capture", return_value=payload):
            result = pc_context._termux_power_status({
                "available": False, "ac_line_status": "unknown",
                "battery_percent": None, "battery_life_seconds": None,
            })
        self.assertTrue(result["available"])
        self.assertEqual(result["battery_percent"], 42)
        self.assertEqual(result["ac_line_status"], "battery")

    def test_macos_battery_from_pmset(self):
        import pc_context

        out = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=123)\t87%; discharging; 3:21 remaining"
        with patch.object(pc_context.shutil, "which", return_value="/usr/bin/pmset"), \
             patch.object(pc_context, "_run_capture", return_value=out):
            result = pc_context._macos_power_status({
                "available": False, "ac_line_status": "unknown",
                "battery_percent": None, "battery_life_seconds": None,
            })
        self.assertEqual(result["ac_line_status"], "battery")
        self.assertEqual(result["battery_percent"], 87)


class RuntimeGatesTestCase(unittest.TestCase):
    """Los gates rechazan con motivo, y NUNCA escriben estado."""

    def test_computer_control_refuses_without_desktop(self):
        import computer_control

        with patch.object(device_profile, "capability_allows", return_value=(False, "sin pantalla (perfil: server-headless)")):
            with self.assertRaises(computer_control.ComputerControlError) as ctx:
                computer_control._pyautogui()
        self.assertIn("sin pantalla", str(ctx.exception))

    def test_voice_refuses_without_audio(self):
        import voice

        with patch.object(device_profile, "capability_allows", return_value=(False, "sin audio (perfil: android-termux)")):
            with self.assertRaises(voice.VoiceError) as ctx:
                voice._require_device_audio("audio_in")
        self.assertIn("sin audio", str(ctx.exception))

    def test_voice_gate_is_transparent_when_allowed(self):
        import voice

        with patch.object(device_profile, "capability_allows", return_value=(True, "")):
            self.assertIsNone(voice._require_device_audio("audio_out"))

    def test_browser_degrades_to_headless_instead_of_failing(self):
        import tools

        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return "navegacion ok"

        def fake_allows(name):
            if name == device_profile.CAP_VISIBLE_BROWSER:
                return False, "no hay pantalla (perfil: server-headless)"
            return True, ""

        with patch.object(device_profile, "capability_allows", side_effect=fake_allows), \
             patch.object(tools, "run_browser_automation", side_effect=fake_run):
            result = tools.browser_automation(start_url="https://example.com", headless=False)

        self.assertTrue(captured["headless"])  # degradado, no fallo
        self.assertIn("navegacion ok", result)
        self.assertIn("sin ventana", result)

    def test_browser_refused_entirely_on_termux(self):
        import tools

        def fake_allows(name):
            if name == device_profile.CAP_BROWSER:
                return False, "no hay binarios de navegador (perfil: android-termux)"
            return True, ""

        with patch.object(device_profile, "capability_allows", side_effect=fake_allows):
            result = tools.browser_automation(start_url="https://example.com")
        self.assertIn("No puedo usar el navegador", result)


class DeviceToolsTestCase(unittest.TestCase):
    def test_overview_reports_class_and_capabilities(self):
        import tools

        with _ProfileEnv(display=False):
            out = tools.device_profile_overview()
        self.assertIn(device_profile.CLASS_SERVER_HEADLESS, out)
        self.assertIn("No disponibles aqui", out)

    def test_suggestions_never_apply_changes(self):
        import tools

        state = {
            "model_provider": {"provider": "ollama", "ollama": {"host": "http://localhost:11434"}},
            "service": {"proactive": {"interval_minutes": 30}},
            "autonomy": {"max_steps_per_cycle": 12},
        }
        power = {"available": True, "ac_line_status": "battery", "battery_percent": 30}
        with _ProfileEnv(total_memory=1 * 1024**3, power=power), \
             patch.object(tools, "load_state", return_value=state), \
             patch.object(tools, "state_transaction") as transaction:
            out = tools.device_adaptation_suggestions()

        transaction.assert_not_called()  # jamas escribe estado
        self.assertIn("nube", out)       # sugiere proveedor cloud con poca RAM
        self.assertIn("bateria", out)

    def test_suggestions_quiet_when_configuration_fits(self):
        import tools

        state = {"model_provider": {"provider": "puter"}, "service": {}, "autonomy": {}}
        with _ProfileEnv(), patch.object(tools, "load_state", return_value=state):
            out = tools.device_adaptation_suggestions()
        self.assertIn("no tengo sugerencias", out)


class CoreWithoutThirdPartyDepsTestCase(unittest.TestCase):
    def test_agent_imports_and_uses_cloud_provider_without_ollama(self):
        """El core debe correr con solo la stdlib usando un proveedor de nube."""
        code = (
            "import sys; sys.modules['ollama'] = None\n"
            "import agent\n"
            "assert agent.OLLAMA_AVAILABLE is False\n"
            "assert agent.client is None\n"
            "c = agent._build_model_client({'provider': 'openai_compat',"
            " 'host': 'https://example.com/v1', 'timeout_seconds': 30,"
            " 'api_key': 'x', 'api_key_env_var': ''})\n"
            "assert type(c).__name__ == 'OpenRouterClient'\n"
            "try:\n"
            "    agent.YarbisOllamaClient(host='http://localhost:11434')\n"
            "    raise SystemExit('deberia haber fallado')\n"
            "except RuntimeError as exc:\n"
            "    assert 'ollama' in str(exc).lower()\n"
            "print('ok')\n"
        )
        completed = subprocess.run(
            [__import__("sys").executable, "-c", code],
            capture_output=True, text=True, timeout=180,
            cwd=str(__import__("pathlib").Path(device_profile.__file__).resolve().parent),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
