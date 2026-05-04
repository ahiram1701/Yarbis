import re
import unicodedata

AFFIRMATIVE_ACTION_REPLIES = {
    "si",
    "si hazlo",
    "si adelante",
    "si por favor",
    "hazlo",
    "adelante",
    "dale",
    "ok",
    "okay",
    "de acuerdo",
    "correcto",
    "confirmo",
    "procede",
    "avanza",
    "ejecutalo",
    "implementalo",
}
AFFIRMATIVE_ACTION_HINTS = (
    "hazlo",
    "adelante",
    "procede",
    "avanza",
    "ejecuta",
    "implementa",
)
YARBIS_PREFIXES = ("yarbis ", "oye yarbis ", "hey yarbis ")


def normalize_intent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text).strip().lower())
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    without_punctuation = re.sub(r"[^\w\s]", " ", without_accents)
    return " ".join(without_punctuation.replace("_", " ").split())


def strip_yarbis_prefix(text: str) -> str:
    normalized = normalize_intent_text(text)
    for prefix in YARBIS_PREFIXES:
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def looks_like_affirmative_action_reply(text: str) -> bool:
    normalized = normalize_intent_text(text)
    if not normalized:
        return False
    if normalized in AFFIRMATIVE_ACTION_REPLIES:
        return True
    return normalized.startswith("si ") and any(
        phrase in normalized
        for phrase in AFFIRMATIVE_ACTION_HINTS
    )
