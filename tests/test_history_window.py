"""Ventana de historial coherente para APIs tipo OpenAI.

Bug real detectado con Puter: el historial se enviaba con un slice ciego
(`messages[-20:]`), y cuando el corte caia en medio de una secuencia de
herramientas quedaban mensajes `role="tool"` sin el `assistant` que los anuncio.
El proveedor respondia:
    400 messages with role 'tool' must be a response to a preceeding message
    with 'tool_calls'
Sobre el historial real de una instancia, el 52% de las ventanas posibles eran
invalidas. Ollama lo toleraba; Puter/OpenRouter/openai_compat no.
"""

import unittest

import agent


def _assistant(text="", call_ids=()):
    message = {"role": "assistant", "content": text}
    if call_ids:
        message["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": "t", "arguments": "{}"}}
            for cid in call_ids
        ]
    return message


def _tool(call_id, text="resultado"):
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _valid_for_openai(window) -> tuple[bool, str]:
    """Reglas que aplica OpenAI (y por tanto Puter/OpenRouter)."""
    announced = set()
    for message in window:
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if call.get("id"):
                    announced.add(call["id"])
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id and call_id not in announced:
                return False, f"tool huerfano: {call_id}"
    answered = {
        str(m.get("tool_call_id") or "") for m in window if m.get("role") == "tool"
    }
    for message in window:
        for call in (message.get("tool_calls") or []) if message.get("role") == "assistant" else []:
            if call.get("id") and call["id"] not in answered:
                return False, f"tool_call sin respuesta: {call['id']}"
    return True, ""


class CoherentWindowTestCase(unittest.TestCase):
    def test_drops_orphan_tool_message(self):
        # La ventana empieza justo en el `tool`: su assistant quedo fuera.
        history = [_assistant(call_ids=["c1"]), _tool("c1"), {"role": "user", "content": "hola"}]
        window = agent._coherent_message_window(history, limit=2)
        self.assertTrue(_valid_for_openai(window)[0])
        self.assertNotIn("tool", [m["role"] for m in window])

    def test_keeps_complete_pairs(self):
        history = [
            {"role": "user", "content": "haz algo"},
            _assistant(call_ids=["c1"]),
            _tool("c1"),
            _assistant("listo"),
        ]
        window = agent._coherent_message_window(history, limit=10)
        self.assertEqual(len(window), 4)
        self.assertTrue(_valid_for_openai(window)[0])

    def test_trims_tool_call_without_response(self):
        # El ciclo murio tras anunciar la tool: nadie respondio a c9.
        history = [{"role": "user", "content": "x"}, _assistant("pensando", ["c9"])]
        window = agent._coherent_message_window(history, limit=10)
        self.assertTrue(_valid_for_openai(window)[0])
        self.assertNotIn("tool_calls", window[-1])
        self.assertEqual(window[-1]["content"], "pensando")

    def test_drops_empty_assistant_left_without_calls(self):
        # assistant sin texto cuyo unico tool_call quedo sin respuesta: sobra.
        history = [{"role": "user", "content": "x"}, _assistant("", ["c9"])]
        window = agent._coherent_message_window(history, limit=10)
        self.assertEqual([m["role"] for m in window], ["user"])

    def test_tool_without_id_is_preserved(self):
        # Sin id no se puede emparejar, pero el conversor lo degrada a `user`.
        history = [{"role": "tool", "tool_name": "x", "content": "salida"}]
        window = agent._coherent_message_window(history, limit=10)
        self.assertEqual(len(window), 1)

    def test_respects_the_limit(self):
        history = [{"role": "user", "content": str(i)} for i in range(50)]
        self.assertEqual(len(agent._coherent_message_window(history, limit=20)), 20)

    def test_every_cut_of_a_long_tool_heavy_history_is_valid(self):
        """La propiedad que importa: NINGUN corte puede producir una ventana
        invalida (antes lo era la mitad de las veces)."""
        history = []
        for i in range(60):
            history.append({"role": "user", "content": f"pulso {i}"})
            history.append(_assistant(call_ids=[f"c{i}"]))
            history.append(_tool(f"c{i}"))
            history.append(_assistant(f"respuesta {i}"))

        invalid_raw = 0
        for cut in range(20, len(history)):
            if not _valid_for_openai(history[:cut][-20:])[0]:
                invalid_raw += 1
            ok, reason = _valid_for_openai(agent._coherent_message_window(history[:cut]))
            self.assertTrue(ok, f"corte {cut}: {reason}")
        self.assertGreater(invalid_raw, 0, "el historial de prueba deberia romper el slice ciego")

    def test_build_messages_uses_the_coherent_window(self):
        import memory

        state = memory.default_state()
        state["messages"] = [_assistant(call_ids=["c1"]), _tool("c1")] * 15
        sent = agent.build_messages(state)
        historial = [m for m in sent if m.get("role") in {"assistant", "tool", "user"}]
        self.assertTrue(_valid_for_openai(historial)[0])



class ProactiveTelegramNoiseTestCase(unittest.TestCase):
    """Un pulso que falla no debe spamear el telefono cada 30 minutos."""

    def setUp(self):
        import yarbis_service

        self.svc = yarbis_service
        self.svc._LAST_PROACTIVE_FAILURE["text"] = ""

    def test_repeated_failure_is_announced_only_once(self):
        from unittest.mock import patch

        fallo = "No pude consultar Puter en este ciclo: HTTP 500"
        with patch.object(self.svc, "_send_telegram_operation_update", return_value=True) as send:
            self.assertTrue(self.svc._send_proactive_telegram_update(fallo))
            self.assertFalse(self.svc._send_proactive_telegram_update(fallo))
            self.assertFalse(self.svc._send_proactive_telegram_update(fallo))
        self.assertEqual(send.call_count, 1)

    def test_a_different_failure_is_announced(self):
        from unittest.mock import patch

        with patch.object(self.svc, "_send_telegram_operation_update", return_value=True) as send:
            self.svc._send_proactive_telegram_update("No pude consultar Puter en este ciclo: A")
            self.svc._send_proactive_telegram_update("No pude consultar Puter en este ciclo: B")
        self.assertEqual(send.call_count, 2)

    def test_useful_output_always_reaches_telegram(self):
        from unittest.mock import patch

        with patch.object(self.svc, "_send_telegram_operation_update", return_value=True) as send:
            self.svc._send_proactive_telegram_update("Avance: cree la tarea X")
            self.svc._send_proactive_telegram_update("Avance: cree la tarea X")
        self.assertEqual(send.call_count, 2)

    def test_recovering_rearms_the_failure_notice(self):
        from unittest.mock import patch

        fallo = "No pude consultar Puter en este ciclo: HTTP 500"
        with patch.object(self.svc, "_send_telegram_operation_update", return_value=True) as send:
            self.svc._send_proactive_telegram_update(fallo)      # avisa
            self.svc._send_proactive_telegram_update("Avance real")  # se recupero
            self.svc._send_proactive_telegram_update(fallo)      # vuelve a avisar
        self.assertEqual(send.call_count, 3)


if __name__ == "__main__":
    unittest.main()
