"""Memoria profesional — modelo de nota rico + consolidacion (Pilar 3).

Sin red ni modelo: son transformaciones puras del estado + tools con
state_transaction mockeado.
"""

import unittest
from unittest.mock import patch

import memory


class NoteSchemaTestCase(unittest.TestCase):
    def test_old_note_migrates_with_defaults(self):
        note = memory._normalize_note({"id": "n1", "title": "vieja", "content": "algo", "category": "general"})
        self.assertEqual(note["tags"], [])
        self.assertEqual(note["importance"], 0)
        self.assertFalse(note["pinned"])
        self.assertEqual(note["access_count"], 0)
        self.assertEqual(note["created_at"], "")  # no se fabrica en normalize (debe ser puro)

    def test_metadata_is_normalized(self):
        note = memory._normalize_note({
            "title": "t", "content": "c",
            "tags": ["A", "a", " b ", "A"], "importance": 9, "pinned": True, "access_count": "5",
        })
        self.assertEqual(note["tags"], ["a", "b"])         # minusculas, sin duplicados
        self.assertEqual(note["importance"], memory.MAX_NOTE_IMPORTANCE)  # topado a 3
        self.assertTrue(note["pinned"])
        self.assertEqual(note["access_count"], 5)


class EvictionTestCase(unittest.TestCase):
    def test_cap_is_enforced(self):
        notes = [{"id": f"n{i}", "title": f"t{i}", "content": "x", "created_at": f"2020-01-{i % 28 + 1:02d}"} for i in range(memory.MAX_NOTES + 20)]
        result = memory.normalize_state({"notes": notes})["notes"]
        self.assertEqual(len(result), memory.MAX_NOTES)

    def test_pinned_survives_eviction(self):
        notes = [{"id": f"n{i}", "title": f"t{i}", "content": "x", "created_at": "2024-06-01"} for i in range(memory.MAX_NOTES + 5)]
        notes[0] = {"id": "keepme", "title": "vieja pinned", "content": "x", "pinned": True, "created_at": "2000-01-01"}
        result = memory.normalize_state({"notes": notes})["notes"]
        self.assertIn("keepme", {n["id"] for n in result})

    def test_important_survives_over_trivial(self):
        notes = [{"id": f"n{i}", "title": f"t{i}", "content": "x", "created_at": "2024-06-01"} for i in range(memory.MAX_NOTES + 5)]
        notes[0] = {"id": "important", "title": "clave", "content": "x", "importance": 3, "created_at": "2000-01-01"}
        result = memory.normalize_state({"notes": notes})["notes"]
        self.assertIn("important", {n["id"] for n in result})

    def test_kept_notes_preserve_order(self):
        notes = [{"id": f"n{i}", "title": f"t{i}", "content": "x", "created_at": f"2024-06-{i % 28 + 1:02d}"} for i in range(memory.MAX_NOTES + 3)]
        result = memory.normalize_state({"notes": notes})["notes"]
        ids = [n["id"] for n in result]
        self.assertEqual(ids, sorted(ids, key=lambda x: int(x[1:])))  # mismo orden relativo


class SaveNoteTestCase(unittest.TestCase):
    def test_save_note_stores_metadata(self):
        import tools

        captured = {}

        def fake_txn(_label, mutate):
            state = {"notes": []}
            mutate(state)
            captured["note"] = state["notes"][0]

        with patch.object(tools, "state_transaction", side_effect=fake_txn):
            out = tools.save_note("Deploy", "prod en VPS", category="tecnico", tags="infra, deploy", importance=2, pinned=True)

        note = captured["note"]
        self.assertEqual(note["tags"], ["infra", "deploy"])
        self.assertEqual(note["importance"], 2)
        self.assertTrue(note["pinned"])
        self.assertTrue(note["created_at"])
        self.assertIn("fijada", out)


class ConsolidateTestCase(unittest.TestCase):
    def test_merges_near_duplicates(self):
        import tools

        state = {"notes": [
            {"id": "a", "title": "Cafe", "content": "al usuario le gusta el cafe negro sin azucar", "tags": ["gusto"], "importance": 0, "access_count": 1},
            {"id": "b", "title": "Cafe preferencia", "content": "al usuario le gusta el cafe negro sin azucar por la manana", "tags": ["bebida"], "importance": 2, "access_count": 3},
            {"id": "c", "title": "Servidor", "content": "produccion en VPS con systemd", "tags": [], "importance": 0, "access_count": 0},
        ]}

        def fake_txn(_label, mutate, **_kw):
            mutate(state)

        with patch.object(tools, "load_state", return_value={"notes": list(state["notes"])}), \
             patch.object(tools, "state_transaction", side_effect=fake_txn):
            out = tools.memory_consolidate(threshold=0.5)

        ids = {n["id"] for n in state["notes"]}
        self.assertEqual(len(state["notes"]), 2)          # a y b se fusionaron
        self.assertIn("c", ids)                            # la distinta se conserva
        self.assertIn("b", ids)                            # se conservo la mas rica (importancia 2)
        self.assertNotIn("a", ids)
        kept = next(n for n in state["notes"] if n["id"] == "b")
        self.assertEqual(kept["access_count"], 4)          # 3 + 1 sumados
        self.assertIn("gusto", kept["tags"])               # tags combinados
        self.assertIn("Consolide", out)

    def test_no_duplicates_reports_clean(self):
        import tools

        state = {"notes": [
            {"id": "a", "title": "Cafe", "content": "cafe negro"},
            {"id": "b", "title": "Servidor", "content": "produccion systemd vps"},
        ]}
        with patch.object(tools, "load_state", return_value=state):
            out = tools.memory_consolidate()
        self.assertIn("No encontre", out)


class AccessTrackingTestCase(unittest.TestCase):
    def test_memory_search_bumps_access(self):
        import tools

        state = {"notes": [
            {"id": "n1", "title": "Deploy", "content": "produccion en VPS con systemd", "access_count": 0},
        ]}
        applied = {}

        def fake_txn(_label, mutate, **_kw):
            mutate(state)
            applied["done"] = True

        with patch.object(tools, "load_state", return_value=state), \
             patch.object(tools, "state_transaction", side_effect=fake_txn):
            tools.memory_search("deploy en el servidor de produccion")

        self.assertTrue(applied.get("done"))
        self.assertEqual(state["notes"][0]["access_count"], 1)
        self.assertTrue(state["notes"][0]["last_accessed"])


if __name__ == "__main__":
    unittest.main()
