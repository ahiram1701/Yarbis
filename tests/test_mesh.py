"""Fase 3 de ubicuidad: la malla integrada (delegacion por capacidad + sync).

Mockea el relay (mesh_relay) y el ciclo del agente para no tocar la red ni correr
el modelo. Verifica el enrutamiento por capacidad y el procesamiento entrante ->
respuesta al nodo origen, que es el pago de la funcionalidad.
"""

import unittest
from unittest.mock import patch

import mesh


def _state(**mesh_over):
    base = {
        "enabled": True,
        "network_name": "red",
        "relay_url": "https://relay.example/mesh",
        "node_id": "nodeA",
        "node_name": "Servidor",
        "secret_ref": "ref-1",
        "last_sync_at": "",
    }
    base.update(mesh_over)
    return {"mesh": base, "messages": []}


class MeshIdentityTestCase(unittest.TestCase):
    def test_is_active_requires_relay_and_secret(self):
        self.assertTrue(mesh.is_active(_state()))
        self.assertFalse(mesh.is_active(_state(enabled=False)))
        self.assertFalse(mesh.is_active(_state(relay_url="")))
        self.assertFalse(mesh.is_active(_state(secret_ref="")))

    def test_node_identity(self):
        with patch.object(mesh, "load_state", return_value=_state()):
            identity = mesh.node_identity()
        self.assertEqual(identity["node_id"], "nodeA")
        self.assertEqual(identity["node_name"], "Servidor")


class MeshDelegateTestCase(unittest.TestCase):
    def test_delegate_picks_a_node_with_the_capability(self):
        roster = [
            {"node_id": "nodeA", "capabilities": {"visible_browser": False}},
            {"node_id": "laptop", "capabilities": {"visible_browser": True, "device_class": "laptop"}},
        ]
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_load_secret", return_value="s"), \
             patch.object(mesh.mesh_relay, "roster", return_value=roster), \
             patch.object(mesh.mesh_relay, "send", return_value={"id": "x"}) as send:
            target = mesh.delegate("visible_browser", "abre esta pagina")

        self.assertEqual(target, "laptop")
        # Se envio un sobre al nodo elegido, con la tarea.
        envelope = send.call_args[0][2]
        self.assertEqual(envelope["to_node"], "laptop")
        self.assertEqual(envelope["kind"], mesh.KIND_MESH_TASK)
        self.assertIn("abre esta pagina", envelope["content"])

    def test_delegate_fails_when_no_node_has_capability(self):
        roster = [{"node_id": "nodeA", "capabilities": {}}]
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_load_secret", return_value="s"), \
             patch.object(mesh.mesh_relay, "roster", return_value=roster):
            with self.assertRaises(mesh.MeshError):
                mesh.delegate("visible_browser", "algo")

    def test_send_to_node_rejects_self(self):
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_load_secret", return_value="s"):
            with self.assertRaises(mesh.MeshError):
                mesh.send_to_node("nodeA", "hola")


class MeshInboundTestCase(unittest.TestCase):
    def test_task_runs_and_replies_to_origin(self):
        envelope = mesh.mesh_relay.make_envelope("laptop", "nodeA", mesh.KIND_MESH_TASK, "que hora es")
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_load_secret", return_value="s"), \
             patch.object(mesh, "_run_mesh_task", return_value="son las 3") as run, \
             patch.object(mesh.mesh_relay, "send", return_value={"id": "r"}) as send:
            mesh._handle_inbound(envelope)

        run.assert_called_once()
        # Se devolvio una respuesta (mesh_reply) al nodo origen 'laptop'.
        reply = send.call_args[0][2]
        self.assertEqual(reply["to_node"], "laptop")
        self.assertEqual(reply["kind"], mesh.KIND_MESH_REPLY)
        self.assertEqual(reply["content"], "son las 3")

    def test_reply_is_recorded_not_reprocessed(self):
        envelope = mesh.mesh_relay.make_envelope("laptop", "nodeA", mesh.KIND_MESH_REPLY, "listo")
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_run_mesh_task") as run, \
             patch.object(mesh, "_record_reply") as record:
            mesh._handle_inbound(envelope)
        run.assert_not_called()
        record.assert_called_once()

    def test_busy_requeues_instead_of_dropping(self):
        from session import SessionOperationBusy

        envelope = mesh.mesh_relay.make_envelope("laptop", "nodeA", mesh.KIND_MESH_TASK, "tarea")
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_run_mesh_task", side_effect=SessionOperationBusy("ocupado")), \
             patch.object(mesh, "_requeue_to_self") as requeue:
            mesh._handle_inbound(envelope)
        requeue.assert_called_once()


class MeshSyncTestCase(unittest.TestCase):
    def test_sync_polls_and_processes(self):
        envelopes = [
            mesh.mesh_relay.make_envelope("laptop", "nodeA", mesh.KIND_MESH_REPLY, "ok1"),
            mesh.mesh_relay.make_envelope("laptop", "nodeA", mesh.KIND_MESH_REPLY, "ok2"),
        ]
        mesh._ENROLLED_THIS_PROCESS["done"] = True
        with patch.object(mesh, "load_state", return_value=_state()), \
             patch.object(mesh, "_load_secret", return_value="s"), \
             patch.object(mesh.mesh_relay, "poll", return_value=envelopes), \
             patch.object(mesh, "_handle_inbound") as handle, \
             patch.object(mesh, "state_transaction"):
            processed = mesh.sync_now()
        self.assertEqual(processed, 2)
        self.assertEqual(handle.call_count, 2)

    def test_maybe_sync_throttles(self):
        mesh._LAST_SYNC["at"] = 0.0
        with patch.object(mesh.time, "monotonic", return_value=1000.0), \
             patch.object(mesh, "is_active", return_value=True), \
             patch.object(mesh, "sync_now", return_value=0) as sync:
            mesh.maybe_sync()          # primera: corre
            mesh.maybe_sync()          # inmediata: throttled, no corre otra vez
        self.assertEqual(sync.call_count, 1)

    def test_maybe_sync_never_raises(self):
        mesh._LAST_SYNC["at"] = 0.0
        with patch.object(mesh.time, "monotonic", return_value=99999.0), \
             patch.object(mesh, "is_active", side_effect=RuntimeError("boom")):
            self.assertEqual(mesh.maybe_sync(), 0)  # traga el error


if __name__ == "__main__":
    unittest.main()
