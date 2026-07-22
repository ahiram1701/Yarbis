"""Memoria profesional — motor de recuperacion por relevancia (BM25 + opcional).

Sin dependencias de terceros: el motor lexico corre en Python puro. El backend
de embeddings se prueba mockeado (nunca toca la red).
"""

import unittest
from unittest.mock import patch

import memory_recall


def _state():
    return {
        "goal": "ayudar con el proyecto",
        "notes": [
            {"id": "n1", "title": "Cafe", "content": "Al usuario le gusta el cafe negro sin azucar", "category": "aprendizaje"},
            {"id": "n2", "title": "Deploy", "content": "Produccion corre en un VPS con systemd y Tailscale", "category": "tecnico", "pinned": True},
            {"id": "n3", "title": "Trivial", "content": "comprar leche y pan", "category": "general"},
        ],
        "self_knowledge": {"insights": [
            {"id": "i1", "category": "limite", "text": "Me cuesta estimar tiempos de tareas de codigo largas"},
        ]},
        "idea_projects": [
            {"id": "p1", "title": "App de notas", "summary": "una app para tomar notas rapidas", "status": "active"},
            {"id": "p2", "title": "Vieja", "summary": "algo archivado", "status": "archived"},
        ],
    }


class TokenizeTestCase(unittest.TestCase):
    def test_strips_accents_and_stopwords(self):
        tokens = memory_recall.tokenize("El café está en la configuración del servidor")
        self.assertIn("cafe", tokens)
        self.assertIn("configuracion", tokens)
        self.assertIn("servidor", tokens)
        self.assertNotIn("el", tokens)      # stopword
        self.assertNotIn("la", tokens)      # stopword

    def test_drops_single_chars(self):
        self.assertEqual(memory_recall.tokenize("a b cd"), ["cd"])


class CorpusTestCase(unittest.TestCase):
    def test_corpus_includes_notes_insights_and_open_ideas(self):
        corpus = memory_recall.build_corpus(_state())
        types = {item["type"] for item in corpus}
        self.assertEqual(types, {"note", "insight", "idea"})
        # El proyecto archivado NO entra al corpus.
        ids = {item["id"] for item in corpus}
        self.assertIn("p1", ids)
        self.assertNotIn("p2", ids)


class RecallTestCase(unittest.TestCase):
    def test_ranks_relevant_over_irrelevant(self):
        results = memory_recall.recall(_state(), "como es el deploy de produccion en el servidor", k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "n2")

    def test_retrieves_by_meaning_not_recency(self):
        # La nota mas vieja (cafe) se recupera si es la relevante, aunque haya mas nuevas.
        results = memory_recall.recall(_state(), "que cafe le gusta tomar", k=1)
        self.assertEqual(results[0]["id"], "n1")

    def test_recall_across_types(self):
        results = memory_recall.recall(_state(), "estimar tiempos de tareas de codigo", k=1)
        self.assertEqual(results[0]["type"], "insight")

    def test_no_match_returns_empty(self):
        self.assertEqual(memory_recall.recall(_state(), "zxqw nada que ver aqui", k=3), [])

    def test_never_raises_on_bad_state(self):
        self.assertEqual(memory_recall.recall(None, "algo", 3), [])
        self.assertEqual(memory_recall.recall({}, "algo", 3), [])

    def test_pinned_and_importance_boost_ties(self):
        # Con dos notas igual de relevantes, la 'pinned'/importante gana.
        state = {"notes": [
            {"id": "a", "title": "servidor", "content": "el servidor de produccion", "pinned": False},
            {"id": "b", "title": "servidor", "content": "el servidor de produccion", "pinned": True},
        ]}
        results = memory_recall.recall(state, "servidor de produccion", k=2)
        self.assertEqual(results[0]["id"], "b")

    def test_overlap_score_detects_duplicates(self):
        high = memory_recall.overlap_score("cafe negro sin azucar", "al usuario le gusta el cafe negro")
        low = memory_recall.overlap_score("cafe negro", "servidor de produccion systemd")
        self.assertGreater(high, low)
        self.assertGreater(high, 0.2)


class EmbeddingsRerankTestCase(unittest.TestCase):
    def test_embeddings_reorder_when_available(self):
        state = _state()
        state["recall"] = {"embeddings": {"enabled": True}}

        # Embeddings simulados que empujan a n1 al frente sin importar el score lexico.
        def fake_embed(_state, texts):
            vecs = []
            for text in texts:
                vecs.append([1.0, 0.0] if "cafe" in text.lower() else [0.0, 1.0])
            return vecs

        with patch("memory_embeddings.embed_texts", side_effect=fake_embed):
            results = memory_recall.recall(state, "cafe", k=2, use_embeddings=True)
        self.assertEqual(results[0]["id"], "n1")

    def test_falls_back_to_lexical_when_embeddings_unavailable(self):
        state = _state()
        with patch("memory_embeddings.embed_texts", return_value=[]):
            results = memory_recall.recall(state, "deploy produccion servidor", k=1, use_embeddings=True)
        # Sin vectores -> orden lexico (n2 gana).
        self.assertEqual(results[0]["id"], "n2")

    def test_embeddings_error_does_not_break_recall(self):
        state = _state()
        with patch("memory_embeddings.embed_texts", side_effect=RuntimeError("boom")):
            results = memory_recall.recall(state, "deploy produccion", k=1, use_embeddings=True)
        self.assertEqual(results[0]["id"], "n2")


class PromptIntegrationTestCase(unittest.TestCase):
    def test_build_messages_injects_recall_and_drops_recency_notes(self):
        import agent
        import memory

        state = memory.default_state()
        state["notes"] = [
            {"id": "n1", "title": "Deploy", "content": "Produccion corre en VPS con systemd", "category": "tecnico"},
            {"id": "n2", "title": "Trivial", "content": "comprar leche", "category": "general"},
        ]
        state["messages"] = [{"role": "user", "content": "como esta el deploy de produccion?"}]
        second_system = agent.build_messages(state)[1]["content"]
        self.assertIn("Recuerdos relevantes", second_system)
        self.assertIn("Deploy", second_system)
        self.assertNotIn("Notas recientes", second_system)  # ya no se vuelca por recencia


class MemorySearchToolTestCase(unittest.TestCase):
    def test_memory_search_returns_relevant(self):
        import tools

        with patch.object(tools, "load_state", return_value=_state()):
            out = tools.memory_search("deploy en el servidor", limit=1)
        self.assertIn("Deploy", out)

    def test_memory_search_no_match(self):
        import tools

        with patch.object(tools, "load_state", return_value=_state()):
            out = tools.memory_search("zxqw irrelevante")
        self.assertIn("No encontre", out)


if __name__ == "__main__":
    unittest.main()
