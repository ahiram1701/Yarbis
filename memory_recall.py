"""Recuperacion de memoria por relevancia para Yarbis.

En vez de meter al prompt "las 3 notas mas recientes", Yarbis RECUPERA los
recuerdos relevantes al turno actual. El motor por defecto es **BM25 en Python
puro** (sin dependencias: corre en Termux/Raspberry/VPS minimo). Si hay un
proveedor de embeddings disponible y activado, se puede reordenar el top-N por
similitud coseno como mejora opcional; sin el, cae al lexico en silencio.

Corpus: notas (usuario), self-insights (modelo de si mismo) y resumenes de
proyectos de ideas. Es un modulo hoja: sin imports de agent/session/tools.
"""

import math
import re
import time
import unicodedata

CACHE_SECONDS = 60
_CACHE: dict = {"signature": "", "index": None, "built_at": 0.0}

# Stopwords minimas ES/EN: quitan ruido sin depender de librerias de NLP.
_STOPWORDS = {
    # espanol
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por",
    "un", "para", "con", "no", "una", "su", "al", "lo", "como", "mas", "pero",
    "sus", "le", "ya", "o", "este", "si", "porque", "esta", "entre", "cuando",
    "muy", "sin", "sobre", "tambien", "me", "hasta", "hay", "donde", "quien",
    "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni", "contra",
    "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mi", "antes", "algunos",
    "que", "unos", "yo", "otro", "otras", "otra", "tanto", "esa", "estos", "mucho",
    "es", "son", "fue", "ser", "soy", "eres", "tu", "te", "del",
    # ingles
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on",
    "with", "as", "at", "by", "this", "that", "be", "are", "was", "were", "from",
    "but", "not", "you", "i", "he", "she", "they", "we", "his", "her", "its",
}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Minusculas, sin acentos, alfanumerico, sin stopwords ni tokens de 1 char."""
    lowered = _strip_accents(str(text or "").lower())
    tokens = _TOKEN_RE.findall(lowered)
    return [tok for tok in tokens if len(tok) > 1 and tok not in _STOPWORDS]


# --------------------------------------------------------------------------- #
# Corpus: extrae items recuperables del estado
# --------------------------------------------------------------------------- #

def build_corpus(state: dict) -> list[dict]:
    """Items recuperables del estado: notas, insights y proyectos de ideas."""
    corpus = []
    if not isinstance(state, dict):
        return corpus

    for note in state.get("notes", []) or []:
        if not isinstance(note, dict):
            continue
        text = f"{note.get('title', '')} {note.get('content', '')}".strip()
        if text:
            corpus.append({
                "id": str(note.get("id", "")),
                "type": "note",
                "title": note.get("title", ""),
                "text": text,
                "category": note.get("category", "general"),
                "importance": int(note.get("importance", 0) or 0),
                "pinned": bool(note.get("pinned", False)),
            })

    insights = state.get("self_knowledge", {})
    insights = insights.get("insights", []) if isinstance(insights, dict) else []
    for insight in insights or []:
        if not isinstance(insight, dict):
            continue
        text = str(insight.get("text", "")).strip()
        if text:
            corpus.append({
                "id": str(insight.get("id", "")),
                "type": "insight",
                "title": f"[{insight.get('category', 'leccion')}]",
                "text": text,
                "category": insight.get("category", "leccion"),
                "importance": 1,
                "pinned": False,
            })

    for project in state.get("idea_projects", []) or []:
        if not isinstance(project, dict):
            continue
        if str(project.get("status", "")) in {"done", "archived"}:
            continue
        text = f"{project.get('title', '')} {project.get('summary', '')}".strip()
        if text:
            corpus.append({
                "id": str(project.get("id", "")),
                "type": "idea",
                "title": project.get("title", ""),
                "text": text,
                "category": "idea",
                "importance": 1,
                "pinned": False,
            })

    return corpus


def _corpus_signature(corpus: list[dict]) -> str:
    return "|".join(f"{item['type']}:{item['id']}:{len(item['text'])}" for item in corpus)


# --------------------------------------------------------------------------- #
# Indice BM25 (Python puro)
# --------------------------------------------------------------------------- #

class _Bm25Index:
    K1 = 1.5
    B = 0.75

    def __init__(self, corpus: list[dict]):
        self.items = corpus
        self.doc_tokens = [tokenize(item["text"]) for item in corpus]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.doc_freq: dict[str, int] = {}
        self.term_freq: list[dict[str, int]] = []
        for tokens in self.doc_tokens:
            counts: dict[str, int] = {}
            for tok in tokens:
                counts[tok] = counts.get(tok, 0) + 1
            self.term_freq.append(counts)
            for tok in counts:
                self.doc_freq[tok] = self.doc_freq.get(tok, 0) + 1
        self.total_docs = len(corpus)

    def _idf(self, term: str) -> float:
        n_qi = self.doc_freq.get(term, 0)
        if n_qi == 0:
            return 0.0
        # BM25 idf con suavizado; max(0,...) evita negativos por terminos ubicuos.
        return max(0.0, math.log((self.total_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0))

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        counts = self.term_freq[doc_index]
        length = self.doc_len[doc_index] or 1
        score = 0.0
        for term in query_tokens:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            denom = tf + self.K1 * (1 - self.B + self.B * length / (self.avg_len or 1))
            score += idf * (tf * (self.K1 + 1)) / denom
        return score

    def search(self, query: str, k: int) -> list[tuple[dict, float]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scored = []
        for index, item in enumerate(self.items):
            base = self.score(query_tokens, index)
            if base <= 0:
                continue
            # Un empujon pequeno por importancia/pinned: a igual relevancia,
            # gana lo que el usuario marco como importante. No domina el score.
            boost = 1.0 + 0.15 * item.get("importance", 0) + (0.2 if item.get("pinned") else 0.0)
            scored.append((item, base * boost))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:max(1, int(k))]


def _get_index(state: dict) -> _Bm25Index:
    corpus = build_corpus(state)
    signature = _corpus_signature(corpus)
    now = time.monotonic()
    if (
        _CACHE["index"] is not None
        and _CACHE["signature"] == signature
        and now - _CACHE["built_at"] < CACHE_SECONDS
    ):
        return _CACHE["index"]
    index = _Bm25Index(corpus)
    _CACHE.update({"signature": signature, "index": index, "built_at": now})
    return index


# --------------------------------------------------------------------------- #
# API publica
# --------------------------------------------------------------------------- #

def recall(state: dict, query: str, k: int = 5, use_embeddings: bool | None = None) -> list[dict]:
    """Top-K recuerdos relevantes a la consulta. Nunca lanza; [] si no hay match."""
    try:
        index = _get_index(state)
    except Exception:
        return []
    results = index.search(query, k * 3 if _embeddings_wanted(state, use_embeddings) else k)
    items = [item for item, _score in results]

    if _embeddings_wanted(state, use_embeddings):
        try:
            items = _rerank_by_embeddings(state, query, items)
        except Exception:
            pass  # fallback lexico silencioso
    return items[:max(1, int(k))]


def overlap_score(text_a: str, text_b: str) -> float:
    """Solapamiento lexico (Jaccard) entre dos textos, para consolidar duplicados."""
    set_a, set_b = set(tokenize(text_a)), set(tokenize(text_b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def render_recall_block(items: list[dict], header: str = "Recuerdos relevantes") -> str:
    if not items:
        return ""
    lines = [f"{header}:"]
    for item in items:
        prefix = {"note": "nota", "insight": "aprendizaje", "idea": "idea"}.get(item["type"], item["type"])
        title = str(item.get("title", "")).strip()
        text = str(item.get("text", "")).strip()
        preview = text[:200] + ("…" if len(text) > 200 else "")
        pin = " *" if item.get("pinned") else ""
        lines.append(f"- [{prefix}{pin}] {title}: {preview}" if title else f"- [{prefix}{pin}] {preview}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Backend de embeddings OPCIONAL (rerank del top-N lexico). Nunca requerido.
# --------------------------------------------------------------------------- #

def _embeddings_wanted(state: dict, override: bool | None) -> bool:
    if override is not None:
        return bool(override)
    recall_cfg = state.get("recall", {}) if isinstance(state, dict) else {}
    embeddings = recall_cfg.get("embeddings", {}) if isinstance(recall_cfg, dict) else {}
    return bool(embeddings.get("enabled", False))


def _rerank_by_embeddings(state: dict, query: str, items: list[dict]) -> list[dict]:
    """Reordena por coseno con embeddings del proveedor configurado.

    Se importa perezosamente para no acoplar el modulo hoja al agente y para que
    la ausencia del proveedor sea un no-op limpio.
    """
    from memory_embeddings import embed_texts  # modulo opcional, lazy

    query_vec = embed_texts(state, [query])
    if not query_vec:
        return items
    doc_vecs = embed_texts(state, [item["text"] for item in items])
    if not doc_vecs or len(doc_vecs) != len(items):
        return items

    q = query_vec[0]
    ranked = sorted(
        zip(items, doc_vecs),
        key=lambda pair: _cosine(q, pair[1]),
        reverse=True,
    )
    return [item for item, _vec in ranked]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
