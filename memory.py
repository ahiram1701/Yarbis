import contextlib
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from uuid import uuid4

import memory_backup
import yarbis_instance
from atomic_io import file_has_bom, read_json_bom_safe

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows path is covered locally.
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX fallback only.
    fcntl = None

WORKSPACE_ROOT = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = Path("state.json")
STATE_FILE = yarbis_instance.state_file()
STATE_LOCK_FILE = yarbis_instance.state_lock_file()
DEFAULT_MEMORY_BACKUPS_DIR = yarbis_instance.memory_backups_dir()
MEMORY_BACKUPS_DIR = DEFAULT_MEMORY_BACKUPS_DIR
DEFAULT_MEMORY_PROTECTION_CONFIG_FILE = yarbis_instance.runtime_dir() / "memory_protection.json"
MEMORY_PROTECTION_CONFIG_FILE = DEFAULT_MEMORY_PROTECTION_CONFIG_FILE
STATE_LOCK = threading.RLock()
_STATE_TRANSACTION_LOCAL = threading.local()
_MEMORY_PROTECTION_MAINTENANCE_LOCK = threading.Lock()
_MEMORY_PROTECTION_MAINTENANCE_THREAD = None
_MEMORY_PROTECTION_BACKUP_QUEUE = queue.Queue()
_MEMORY_PROTECTION_BACKUP_THREAD_LOCK = threading.Lock()
_MEMORY_PROTECTION_BACKUP_THREAD = None
MEMORY_PROTECTION_MAINTENANCE_ASYNC = True
MEMORY_PROTECTION_AUTO_BACKUP_ASYNC = True
_STATE_LOCK_POLL_SECONDS = 0.05
DEFAULT_STATE_LOCK_TIMEOUT_SECONDS = 60.0
_STATE_LOCK_TIMEOUT_SECONDS = DEFAULT_STATE_LOCK_TIMEOUT_SECONDS
DEFAULT_GOAL = ""
LEGACY_DEFAULT_GOALS = {
    "Ayudar al usuario de forma autonoma con tareas locales.",
}
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4_000
MAX_LAST_RESULT_CHARS = 4_000
MAX_PROFILE_ITEMS = 12
MAX_PROFILE_ITEM_CHARS = 140
MAX_NOTES = 30
MAX_NOTE_TITLE_CHARS = 120
MAX_NOTE_CONTENT_CHARS = 1_200
MAX_TASKS = 60
MAX_TASK_TITLE_CHARS = 160
MAX_TASK_DETAILS_CHARS = 1_200
MAX_TASK_RESULT_CHARS = 600
MAX_PLAN_ITEMS = 12
MAX_PLAN_ITEM_CHARS = 220
MAX_IDEA_PROJECTS = 40
MAX_IDEA_PROJECT_TITLE_CHARS = 160
MAX_IDEA_PROJECT_TEXT_CHARS = 1_200
MAX_IDEA_PROJECT_ITEM_CHARS = 320
MAX_IDEA_PROJECT_ITEMS = 12
MAX_IDEA_PROJECT_TIMESTAMP_CHARS = 80
MAX_VISUAL_BOARDS_PER_PROJECT = 8
MAX_VISUAL_BOARD_TITLE_CHARS = 160
MAX_VISUAL_BOARD_ID_CHARS = 80
MAX_VISUAL_BOARD_KIND_CHARS = 40
MAX_VISUAL_BOARD_NODES = 80
MAX_VISUAL_BOARD_EDGES = 120
MAX_VISUAL_BOARD_LANES = 20
MAX_VISUAL_BOARD_NODE_TEXT_CHARS = 400
MAX_VISUAL_BOARD_META_ITEMS = 16
MAX_VISUAL_BOARD_EXPORT_PATH_CHARS = 1_000
MAX_AWAITING_INPUT_QUESTION_CHARS = 280
MAX_AWAITING_INPUT_REASON_CHARS = 240
MAX_AWAITING_INPUT_FIELDS = 8
STATE_SCHEMA_VERSION = 5
DEFAULT_MAX_STEPS_PER_CYCLE = None
LEGACY_DEFAULT_AUTO_CYCLES = 5
DEFAULT_AUTO_CYCLES = None
DEFAULT_SERVICE_PROACTIVE_ENABLED = True
DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS = 30 * 60
LEGACY_DEFAULT_SERVICE_PROACTIVE_CYCLES = 1
DEFAULT_SERVICE_PROACTIVE_CYCLES = None
DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS = 60
DEFAULT_SERVICE_PROACTIVE_MODEL = ""
DEFAULT_MOBILE_UI_PORT = 8787
DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS = 30 * 60
DEFAULT_MOBILE_UI_HTTPS_ENABLED = True
MIN_MOBILE_UI_JOB_TIMEOUT_SECONDS = 60
MAX_MOBILE_UI_JOB_TIMEOUT_SECONDS = 24 * 60 * 60
MODEL_PROVIDER_OLLAMA = "ollama"
MODEL_PROVIDER_OPENROUTER = "openrouter"
DEFAULT_MODEL_PROVIDER = MODEL_PROVIDER_OLLAMA
VALID_MODEL_PROVIDERS = {MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER}
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b"
DEFAULT_OLLAMA_HOST = ""
DEFAULT_OLLAMA_CLOUD_HOST = "https://ollama.com"
DEFAULT_OLLAMA_API_KEY_ENV_VAR = "OLLAMA_API_KEY"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 900
DEFAULT_OPENROUTER_MODEL = ""
DEFAULT_OPENROUTER_HOST = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_OPENROUTER_TIMEOUT_SECONDS = DEFAULT_OLLAMA_TIMEOUT_SECONDS
MAX_OLLAMA_API_KEY_CHARS = 512
MAX_OPENROUTER_API_KEY_CHARS = 512
MIN_OLLAMA_TIMEOUT_SECONDS = 1
MAX_OLLAMA_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_OLLAMA_MODEL_CHARS = 120
MAX_SERVICE_PROACTIVE_MODEL_CHARS = MAX_OLLAMA_MODEL_CHARS
MAX_MOBILE_UI_HASH_CHARS = 256
MAX_MOBILE_UI_SALT_CHARS = 128
MAX_MOBILE_UI_SESSION_SECRET_CHARS = 128
MAX_MOBILE_UI_BIND_ERROR_CHARS = 600
MAX_MOBILE_UI_SERVE_TARGET_CHARS = 240
MAX_OLLAMA_HOST_CHARS = 240
MAX_OLLAMA_API_KEY_ENV_VAR_CHARS = 80
MAX_OLLAMA_FALLBACK_MODELS = 8
MAX_OPENROUTER_MODEL_CHARS = MAX_OLLAMA_MODEL_CHARS
MAX_OPENROUTER_HOST_CHARS = MAX_OLLAMA_HOST_CHARS
MAX_OPENROUTER_API_KEY_ENV_VAR_CHARS = MAX_OLLAMA_API_KEY_ENV_VAR_CHARS
MAX_OPENROUTER_FALLBACK_MODELS = MAX_OLLAMA_FALLBACK_MODELS
VALID_TASK_STATUS = {"pending", "in_progress", "blocked", "done"}
VALID_TASK_PRIORITY = {"alta", "media", "baja"}
VALID_IDEA_PROJECT_STATUS = {"exploring", "planned", "active", "paused", "done", "archived"}
VALID_IDEA_PROJECT_KIND = {"producto_negocio", "vida_proyecto", "mixto", "otro"}
VALID_VISUAL_BOARD_KIND = {"idea_canvas", "decision_matrix", "roadmap_kanban", "mind_map"}
VALID_UI_THEME = {"light", "dark"}
VALID_NOTIFICATION_CHANNELS = {"windows", "ntfy", "telegram"}
VALID_NTFY_PRIORITIES = {"", "min", "low", "default", "high", "urgent", "1", "2", "3", "4", "5"}
DEFAULT_COMMUNICATION_TONE = "warm_brief"
DEFAULT_COMMUNICATION_DETAIL_LEVEL = "balanced"
DEFAULT_COMMUNICATION_PROACTIVITY = "moderate"
VALID_COMMUNICATION_TONES = {"warm_brief", "human", "direct"}
VALID_COMMUNICATION_DETAIL_LEVELS = {"brief", "balanced", "detailed"}
VALID_COMMUNICATION_PROACTIVITY = {"low", "moderate", "high"}
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
DEFAULT_NTFY_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TELEGRAM_TIMEOUT_SECONDS = 10
DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS = 25
DEFAULT_VOICE_ENABLED = True
DEFAULT_VOICE_LANGUAGE = "es"
DEFAULT_VOICE_STT_MODEL = "base"
DEFAULT_VOICE_STT_COMPUTE_TYPE = "int8"
DEFAULT_VOICE_MAX_AUDIO_SECONDS = 120
DEFAULT_VOICE_TTS_RATE = 175
DEFAULT_VOICE_TTS_VOICE_ID = ""
DEFAULT_VOICE_TTS_PROVIDER = "edge"
DEFAULT_VOICE_EDGE_VOICE = "es-MX-JorgeNeural"
MAX_VOICE_EDGE_VOICE_CHARS = 100
DEFAULT_VOICE_EDGE_RATE = 0
DEFAULT_VOICE_EDGE_PITCH = 0
DEFAULT_VOICE_EDGE_VOLUME = 0
DEFAULT_VOICE_BROWSER_VOICE_NAME = ""
DEFAULT_VOICE_BROWSER_TTS_RATE = 1.0
DEFAULT_VOICE_BROWSER_TTS_PITCH = 1.0
DEFAULT_VOICE_TELEGRAM_REPLY_MODE = "auto"
DEFAULT_VOICE_LIVE_WAKE_PHRASE = "Yarbis"
DEFAULT_VOICE_LIVE_WAKE_STT_MODEL = "tiny"
DEFAULT_VOICE_LIVE_TURN_STT_MODEL = ""
DEFAULT_VOICE_LIVE_SILENCE_MS = 900
DEFAULT_VOICE_LIVE_MAX_TURN_SECONDS = 45
DEFAULT_VOICE_LIVE_CONTINUOUS = True
DEFAULT_VOICE_LIVE_HOLD_SECONDS = 12
MIN_VOICE_LIVE_HOLD_SECONDS = 3
MAX_VOICE_LIVE_HOLD_SECONDS = 120
VALID_VOICE_TTS_PROVIDERS = {"system", "edge"}
VALID_VOICE_TELEGRAM_REPLY_MODES = {"off", "auto", "always"}
VALID_VOICE_STT_COMPUTE_TYPES = {"default", "int8", "int8_float16", "int16", "float16", "float32"}
MIN_VOICE_MAX_AUDIO_SECONDS = 1
MAX_VOICE_MAX_AUDIO_SECONDS = 600
MIN_VOICE_TTS_RATE = 80
MAX_VOICE_TTS_RATE = 320
MIN_VOICE_BROWSER_TTS_RATE = 0.5
MAX_VOICE_BROWSER_TTS_RATE = 2.0
MIN_VOICE_BROWSER_TTS_PITCH = 0.0
MAX_VOICE_BROWSER_TTS_PITCH = 2.0
MIN_VOICE_EDGE_RATE = -50
MAX_VOICE_EDGE_RATE = 100
MIN_VOICE_EDGE_PITCH = -50
MAX_VOICE_EDGE_PITCH = 50
MIN_VOICE_EDGE_VOLUME = -50
MAX_VOICE_EDGE_VOLUME = 100
MAX_VOICE_LANGUAGE_CHARS = 16
MAX_VOICE_STT_MODEL_CHARS = 80
MAX_VOICE_STT_COMPUTE_TYPE_CHARS = 24
MAX_VOICE_TTS_VOICE_ID_CHARS = 240
MAX_VOICE_TTS_PROVIDER_CHARS = 20
MAX_VOICE_LIVE_WAKE_PHRASE_CHARS = 60
MAX_VOICE_LIVE_STT_MODEL_CHARS = 80
MIN_VOICE_LIVE_SILENCE_MS = 250
MAX_VOICE_LIVE_SILENCE_MS = 5000
MIN_VOICE_LIVE_MAX_TURN_SECONDS = 3
MAX_VOICE_LIVE_MAX_TURN_SECONDS = 300
MAX_VOICE_BROWSER_VOICE_NAME_CHARS = 160
DEFAULT_INTERNET_MODE = "auto"
VALID_INTERNET_MODES = {"off", "auto"}
DEFAULT_SEARCH_PROVIDER = "duckduckgo_html"
VALID_SEARCH_PROVIDERS = {DEFAULT_SEARCH_PROVIDER}
DEFAULT_INTERNET_MAX_SEARCH_RESULTS = 5
DEFAULT_INTERNET_MAX_PAGE_CHARS = 12_000
DEFAULT_INTERNET_REQUEST_TIMEOUT_SECONDS = 10
MAX_INTERNET_DOMAIN_ITEMS = 20
MAX_INTERNET_DOMAIN_CHARS = 120
MAX_SELF_KNOWLEDGE_SUMMARY_CHARS = 20_000
MAX_SELF_KNOWLEDGE_TIMESTAMP_CHARS = 80
MAX_SELF_KNOWLEDGE_SOURCE_SIGNATURE_CHARS = 40_000
MAX_RUNTIME_OPERATION_LABEL_CHARS = 80
MAX_RUNTIME_OPERATION_SOURCE_CHARS = 40
MAX_RUNTIME_TIMESTAMP_CHARS = 80
MAX_RUNTIME_OPERATION_ID_CHARS = 80
MAX_RUNTIME_STOP_REASON_CHARS = 240
DEFAULT_LOCAL_CONTEXT_MODE = "safe"
VALID_LOCAL_CONTEXT_MODES = {"off", "safe", "detailed"}
DEFAULT_LOCAL_CONTEXT_SAMPLE_INTERVAL_SECONDS = 30
DEFAULT_LOCAL_CONTEXT_MAX_SNAPSHOT_AGE_SECONDS = 180
MAX_LOCAL_CONTEXT_MODE_CHARS = 20
DEFAULT_META_GRAPH_VERSION = "v24.0"
DEFAULT_LINKEDIN_VERSION = "202604"
VALID_SOCIAL_PLATFORMS = {
    "facebook_page",
    "facebook_personal",
    "instagram",
    "linkedin",
}
VALID_SOCIAL_ACCOUNT_TYPES = {
    "facebook_page",
    "facebook_personal",
    "instagram_professional",
    "linkedin_member",
    "linkedin_organization",
}
VALID_SOCIAL_DRAFT_STATUS = {"draft", "pending", "published", "failed", "archived"}
VALID_SOCIAL_PUBLICATION_STATUS = {"pending_confirmation", "published", "failed", "assisted_opened"}
MAX_SOCIAL_ITEMS = 80
MAX_SOCIAL_MEDIA_INBOX = 15
MAX_SOCIAL_TEXT_CHARS = 8_000
MAX_SOCIAL_METADATA_CHARS = 2_000
DEFAULT_CODING_MODE = "propose_first"
VALID_CODING_MODES = {DEFAULT_CODING_MODE}
MAX_CODING_WORKSPACE_PATH_CHARS = 1_000
MAX_CODING_PROPOSAL_IDS = 80
MAX_CODING_PROPOSAL_ID_CHARS = 80
MAX_CODING_VALIDATION_COMMAND_CHARS = 1_000
MAX_CODING_VALIDATION_OUTPUT_CHARS = 6_000
MAX_CODING_VALIDATION_TIMESTAMP_CHARS = 80
DEFAULT_EVOLUTION_ENABLED = False
DEFAULT_EVOLUTION_INTERVAL_HOURS = 6
DEFAULT_EVOLUTION_MAX_PENDING = 2
EVOLUTION_DIMENSIONS = ("code", "behavior", "goals", "skills")
MAX_EVOLUTION_DIRECTIVES = 40
MAX_EVOLUTION_DIRECTIVE_CHARS = 500
MAX_EVOLUTION_DIRECTIVE_REASON_CHARS = 500
MAX_EVOLUTION_DIRECTIVE_ID_CHARS = 80
MAX_EVOLUTION_SUGGESTIONS = 40
MAX_EVOLUTION_SUGGESTION_CHARS = 2_000
EVOLUTION_SUGGESTION_KINDS = ("goal", "memory")
DEFAULT_VISION_MODEL = "gemma3:12b-cloud"
DEFAULT_VISION_MAX_IMAGE_DIM = 1280
DEFAULT_VISION_TIMEOUT_SECONDS = 120
MAX_VISION_MODEL_CHARS = 200
DEFAULT_MEMORY_PROTECTION_ENABLED = True
DEFAULT_MEMORY_PROTECTION_BACKUP_ON_EVERY_CHANGE = True
DEFAULT_MEMORY_PROTECTION_INCLUDE_SECRETS = False
DEFAULT_MEMORY_PROTECTION_VERIFY_AFTER_WRITE = False
DEFAULT_MEMORY_PROTECTION_AUTO_RESTORE = True
DEFAULT_MEMORY_PROTECTION_MAX_AUTO_BACKUPS = 50
DEFAULT_MEMORY_PROTECTION_KEEP_DAILY_DAYS = 14
MAX_MEMORY_PROTECTION_MIRROR_DIR_CHARS = 1_000
MAX_MEMORY_PROTECTION_TIMESTAMP_CHARS = 80
MAX_MEMORY_PROTECTION_ERROR_CHARS = 600
UNLIMITED_CYCLE_TEXT_VALUES = {
    "",
    "none",
    "null",
    "unlimited",
    "sin limite",
    "sin límite",
    "ilimitado",
    "ilimitados",
    "hasta terminar",
}


def normalize_cycle_count(
    value,
    default: int | None = None,
    *,
    legacy_default: int | None = None,
    migrate_legacy: bool = False,
) -> int | None:
    if value is None:
        return None

    candidate = value
    if isinstance(candidate, str):
        cleaned = candidate.strip()
        if cleaned.lower() in UNLIMITED_CYCLE_TEXT_VALUES:
            return None
        candidate = cleaned

    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return default

    if parsed <= 0:
        return default
    if migrate_legacy and legacy_default is not None and parsed == legacy_default:
        return None
    return parsed


def format_cycle_count(value, *, unlimited_text: str = "hasta terminar") -> str:
    cycles = normalize_cycle_count(value)
    if cycles is None:
        return unlimited_text
    return f"{cycles} ciclo(s)"


def default_state():
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "goal": DEFAULT_GOAL,
        "messages": [],
        "last_result": "",
        "cycle_count": 0,
        "profile": {
            "name": "",
            "role": "",
            "preferences": [],
            "constraints": [],
            "timezone": "",
        },
        "notes": [],
        "tasks": [],
        "current_plan": [],
        "idea_projects": [],
        "awaiting_user_input": {
            "pending": False,
            "question": "",
            "reason": "",
            "fields": [],
        },
        "autonomy": {
            "max_steps_per_cycle": DEFAULT_MAX_STEPS_PER_CYCLE,
            "auto_cycles_default": DEFAULT_AUTO_CYCLES,
        },
        "coding": {
            "workspace_path": "",
            "mode": DEFAULT_CODING_MODE,
            "pending_proposal_ids": [],
            "validation_command": "",
            "last_validation": {
                "command": "",
                "proposal_id": "",
                "exit_code": None,
                "output": "",
                "ran_at": "",
            },
        },
        "evolution": {
            "enabled": DEFAULT_EVOLUTION_ENABLED,
            "interval_hours": DEFAULT_EVOLUTION_INTERVAL_HOURS,
            "last_run_at": "",
            "max_pending": DEFAULT_EVOLUTION_MAX_PENDING,
            "dimensions": list(EVOLUTION_DIMENSIONS),
            "directives": [],
            "directives_pending": [],
            "suggestions_pending": [],
        },
        "vision": {
            "model": DEFAULT_VISION_MODEL,
            "max_image_dim": DEFAULT_VISION_MAX_IMAGE_DIM,
            "timeout_seconds": DEFAULT_VISION_TIMEOUT_SECONDS,
        },
        "computer_control": {
            "enabled": False,
            "settings": {
                "browser_channel": "msedge",
                "headed": True,
                "confirm_sensitive": True,
                "os_control": True,
            },
        },
        "memory_protection": {
            "enabled": DEFAULT_MEMORY_PROTECTION_ENABLED,
            "backup_on_every_change": DEFAULT_MEMORY_PROTECTION_BACKUP_ON_EVERY_CHANGE,
            "mirror_dir": "",
            "include_secrets": DEFAULT_MEMORY_PROTECTION_INCLUDE_SECRETS,
            "retention": {
                "max_auto_backups": DEFAULT_MEMORY_PROTECTION_MAX_AUTO_BACKUPS,
                "keep_daily_days": DEFAULT_MEMORY_PROTECTION_KEEP_DAILY_DAYS,
            },
            "verify_after_write": DEFAULT_MEMORY_PROTECTION_VERIFY_AFTER_WRITE,
            "auto_restore": DEFAULT_MEMORY_PROTECTION_AUTO_RESTORE,
            "last_backup_at": "",
            "last_recovery_at": "",
            "last_error": "",
        },
        "ollama": {
            "model": DEFAULT_OLLAMA_MODEL,
            "fallback_models": [],
            "host": DEFAULT_OLLAMA_HOST,
            "api_key": "",
            "api_key_env_var": DEFAULT_OLLAMA_API_KEY_ENV_VAR,
            "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        },
        "model_provider": {
            "default": DEFAULT_MODEL_PROVIDER,
            "ollama": {
                "model": DEFAULT_OLLAMA_MODEL,
                "fallback_models": [],
                "host": DEFAULT_OLLAMA_HOST,
                "api_key": "",
                "api_key_env_var": DEFAULT_OLLAMA_API_KEY_ENV_VAR,
                "timeout_seconds": DEFAULT_OLLAMA_TIMEOUT_SECONDS,
            },
            "openrouter": {
                "model": DEFAULT_OPENROUTER_MODEL,
                "fallback_models": [],
                "host": DEFAULT_OPENROUTER_HOST,
                "api_key": "",
                "api_key_env_var": DEFAULT_OPENROUTER_API_KEY_ENV_VAR,
                "timeout_seconds": DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
            },
        },
        "service": {
            "proactive": {
                "enabled": DEFAULT_SERVICE_PROACTIVE_ENABLED,
                "interval_seconds": DEFAULT_SERVICE_PROACTIVE_INTERVAL_SECONDS,
                "cycles": DEFAULT_SERVICE_PROACTIVE_CYCLES,
                "start_delay_seconds": DEFAULT_SERVICE_PROACTIVE_START_DELAY_SECONDS,
                "model": DEFAULT_SERVICE_PROACTIVE_MODEL,
                "last_pulse_at": "",
            },
            "mobile_ui": {
                "enabled": False,
                "port": yarbis_instance.default_mobile_ui_port(),
                "job_timeout_seconds": DEFAULT_MOBILE_UI_JOB_TIMEOUT_SECONDS,
                "https_enabled": DEFAULT_MOBILE_UI_HTTPS_ENABLED,
                "pin_hash": "",
                "pin_salt": "",
                "session_secret": "",
                "last_bind_error": "",
                "https_last_error": "",
                "tailscale_serve_target": "",
            },
        },
        "ui": {
            "theme": "dark",
        },
        "runtime": {
            "thinking": {
                "active": False,
                "label": "",
                "source": "",
                "started_at": "",
                "operation_id": "",
            },
            "stop_requested": {
                "active": False,
                "operation_id": "",
                "requested_at": "",
                "source": "",
                "reason": "",
            },
        },
        "local_context": {
            "enabled": True,
            "mode": DEFAULT_LOCAL_CONTEXT_MODE,
            "sample_interval_seconds": DEFAULT_LOCAL_CONTEXT_SAMPLE_INTERVAL_SECONDS,
            "max_snapshot_age_seconds": DEFAULT_LOCAL_CONTEXT_MAX_SNAPSHOT_AGE_SECONDS,
            "include_window_title": False,
            "include_process_name": True,
            "include_workspace_changes": True,
            "include_system_health": True,
        },
        "internet": {
            "mode": DEFAULT_INTERNET_MODE,
            "provider": DEFAULT_SEARCH_PROVIDER,
            "max_search_results": DEFAULT_INTERNET_MAX_SEARCH_RESULTS,
            "max_page_chars": DEFAULT_INTERNET_MAX_PAGE_CHARS,
            "request_timeout_seconds": DEFAULT_INTERNET_REQUEST_TIMEOUT_SECONDS,
            "allowed_domains": [],
            "blocked_domains": [],
        },
        "self_knowledge": {
            "last_analyzed_at": "",
            "summary": "",
            "source_signature": "",
        },
        "notifications": {
            "enabled": True,
            "channels": ["windows"],
            "ntfy": {
                "server": DEFAULT_NTFY_SERVER,
                "topic": "",
                "token": "",
                "priority": "",
                "tags": "",
                "timeout_seconds": DEFAULT_NTFY_TIMEOUT_SECONDS,
            },
            "telegram": {
                "api_base": DEFAULT_TELEGRAM_API_BASE,
                "bot_token": "",
                "chat_id": "",
                "timeout_seconds": DEFAULT_TELEGRAM_TIMEOUT_SECONDS,
                "poll_timeout_seconds": DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS,
                "last_update_id": 0,
                "pending_power_confirmation": {
                    "action": "",
                    "delay_seconds": 0,
                    "token": "",
                    "chat_id": "",
                    "requested_at": "",
                },
            },
        },
        "communication": {
            "tone": DEFAULT_COMMUNICATION_TONE,
            "detail_level": DEFAULT_COMMUNICATION_DETAIL_LEVEL,
            "proactivity": DEFAULT_COMMUNICATION_PROACTIVITY,
        },
        "voice": {
            "enabled": DEFAULT_VOICE_ENABLED,
            "language": DEFAULT_VOICE_LANGUAGE,
            "stt_model": DEFAULT_VOICE_STT_MODEL,
            "stt_compute_type": DEFAULT_VOICE_STT_COMPUTE_TYPE,
            "max_audio_seconds": DEFAULT_VOICE_MAX_AUDIO_SECONDS,
            "tts_rate": DEFAULT_VOICE_TTS_RATE,
            "tts_voice_id": DEFAULT_VOICE_TTS_VOICE_ID,
            "tts_provider": DEFAULT_VOICE_TTS_PROVIDER,
            "edge_voice": DEFAULT_VOICE_EDGE_VOICE,
            "edge_rate": DEFAULT_VOICE_EDGE_RATE,
            "edge_pitch": DEFAULT_VOICE_EDGE_PITCH,
            "edge_volume": DEFAULT_VOICE_EDGE_VOLUME,
            "browser_voice_name": DEFAULT_VOICE_BROWSER_VOICE_NAME,
            "browser_tts_rate": DEFAULT_VOICE_BROWSER_TTS_RATE,
            "browser_tts_pitch": DEFAULT_VOICE_BROWSER_TTS_PITCH,
            "telegram_reply_mode": DEFAULT_VOICE_TELEGRAM_REPLY_MODE,
            "live_conversation": {
                "enabled": True,
                "wake_phrase": DEFAULT_VOICE_LIVE_WAKE_PHRASE,
                "surfaces": ["desktop", "mobile"],
                "wake_stt_model": DEFAULT_VOICE_LIVE_WAKE_STT_MODEL,
                "turn_stt_model": DEFAULT_VOICE_LIVE_TURN_STT_MODEL,
                "silence_ms": DEFAULT_VOICE_LIVE_SILENCE_MS,
                "max_turn_seconds": DEFAULT_VOICE_LIVE_MAX_TURN_SECONDS,
                "continuous": DEFAULT_VOICE_LIVE_CONTINUOUS,
                "hold_seconds": DEFAULT_VOICE_LIVE_HOLD_SECONDS,
                "cues": True,
                "auto_speak": True,
                "barge_in": True,
                "save_audio_debug": False,
            },
        },
        "social": {
            "settings": {
                "meta_graph_version": DEFAULT_META_GRAPH_VERSION,
                "linkedin_version": DEFAULT_LINKEDIN_VERSION,
                "require_confirmation": True,
            },
            "accounts": [],
            "drafts": [],
            "pending_publications": [],
            "history": [],
            "media_inbox": [],
        },
    }


def sanitize_unicode_text(value) -> str:
    return str(value).encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def sanitize_unicode_value(value):
    if isinstance(value, str):
        return sanitize_unicode_text(value)
    if isinstance(value, dict):
        return {
            sanitize_unicode_text(key) if isinstance(key, str) else key: sanitize_unicode_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_unicode_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_unicode_value(item) for item in value]
    return value


def _coerce_text(value, limit: int) -> str:
    return sanitize_unicode_text(value)


def _prepare_state_lock_file(handle):
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b" ")
        handle.flush()
    handle.seek(0)


def _lock_state_handle(handle, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))

    if msvcrt is not None:
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_STATE_LOCK_POLL_SECONDS)

    if fcntl is not None:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(_STATE_LOCK_POLL_SECONDS)

    return True


def _unlock_state_handle(handle):
    try:
        if msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _state_file_lock(label: str = "state"):
    depth = int(getattr(_STATE_TRANSACTION_LOCAL, "depth", 0) or 0)
    if depth > 0:
        _STATE_TRANSACTION_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _STATE_TRANSACTION_LOCAL.depth = depth
        return

    STATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(STATE_LOCK_FILE, "a+b")
    try:
        _prepare_state_lock_file(handle)
        if not _lock_state_handle(handle, _STATE_LOCK_TIMEOUT_SECONDS):
            raise TimeoutError(
                "No pude tomar el lock de estado "
                f"({STATE_LOCK_FILE}) para {str(label).strip() or 'state'}."
            )

        _STATE_TRANSACTION_LOCAL.depth = 1
        try:
            yield
        finally:
            _STATE_TRANSACTION_LOCAL.depth = 0
            _unlock_state_handle(handle)
    finally:
        handle.close()


def _normalize_message(message):
    if not isinstance(message, dict):
        return None

    role = str(message.get("role", "assistant"))
    normalized = {"role": role}

    content = message.get("content", "")
    normalized["content"] = _coerce_text(content, MAX_MESSAGE_CHARS)

    tool_name = message.get("tool_name")
    if tool_name:
        normalized["tool_name"] = str(tool_name)

    tool_call_id = message.get("tool_call_id")
    if tool_call_id:
        normalized["tool_call_id"] = str(tool_call_id)

    tool_calls = message.get("tool_calls")
    if tool_calls:
        normalized["tool_calls"] = tool_calls

    return normalized


def _normalize_string_list(value, item_limit: int, char_limit: int) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\n,;]+", value)]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []

    normalized = []
    seen = set()
    for item in raw_items:
        if not item:
            continue

        normalized_item = _coerce_text(item, char_limit)
        lowered = normalized_item.casefold()
        if lowered in seen:
            continue

        normalized.append(normalized_item)
        seen.add(lowered)
        if len(normalized) >= item_limit:
            break

    return normalized


def _fallback_id(prefix: str, seed: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(seed).strip().lower()).strip("-")
    if not cleaned:
        cleaned = "item"
    return f"{prefix}-{cleaned[:12]}"


def _normalize_note(note):
    if not isinstance(note, dict):
        return None

    title = _coerce_text(note.get("title", ""), MAX_NOTE_TITLE_CHARS).strip()
    content = _coerce_text(note.get("content", ""), MAX_NOTE_CONTENT_CHARS).strip()
    category = _coerce_text(note.get("category", "general"), 40).strip() or "general"

    if not title and not content:
        return None

    note_id = _coerce_text(note.get("id") or _fallback_id("note", title or content), 32).strip()

    return {
        "id": note_id,
        "title": title or "Nota sin titulo",
        "content": content,
        "category": category,
    }


def _normalize_task(task):
    if not isinstance(task, dict):
        return None

    title = _coerce_text(task.get("title", ""), MAX_TASK_TITLE_CHARS).strip()
    details = _coerce_text(task.get("details", ""), MAX_TASK_DETAILS_CHARS).strip()
    result = _coerce_text(task.get("result", ""), MAX_TASK_RESULT_CHARS).strip()

    if not title:
        return None

    raw_status = str(task.get("status", "pending")).strip().lower()
    raw_priority = str(task.get("priority", "media")).strip().lower()

    status = raw_status if raw_status in VALID_TASK_STATUS else "pending"
    priority = raw_priority if raw_priority in VALID_TASK_PRIORITY else "media"
    task_id = _coerce_text(task.get("id") or _fallback_id("task", title), 32).strip()

    return {
        "id": task_id,
        "title": title,
        "details": details,
        "status": status,
        "priority": priority,
        "result": result,
    }


def _visual_text(value, limit: int, *, strip: bool = True) -> str:
    text = _coerce_text(value, limit)
    if strip:
        text = text.strip()
    if limit > 0 and len(text) > limit:
        return text[:limit]
    return text


def _visual_number(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _visual_id(value, fallback_prefix: str, fallback_seed: str, seen: set[str] | None = None) -> str:
    cleaned = _visual_text(value, MAX_VISUAL_BOARD_ID_CHARS).strip()
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = _fallback_id(fallback_prefix, fallback_seed)
    if len(cleaned) > MAX_VISUAL_BOARD_ID_CHARS:
        cleaned = cleaned[:MAX_VISUAL_BOARD_ID_CHARS].rstrip("-")

    if seen is None or cleaned not in seen:
        return cleaned

    base = cleaned[: max(1, MAX_VISUAL_BOARD_ID_CHARS - 8)].rstrip("-") or fallback_prefix
    suffix = 2
    candidate = f"{base}-{suffix}"
    while candidate in seen:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate[:MAX_VISUAL_BOARD_ID_CHARS]


def _normalize_visual_meta(meta) -> dict:
    if not isinstance(meta, dict):
        return {}

    normalized = {}
    for raw_key, raw_value in meta.items():
        key = _visual_text(raw_key, 80)
        if not key:
            continue

        if isinstance(raw_value, bool) or raw_value is None:
            value = raw_value
        elif isinstance(raw_value, (int, float)):
            value = raw_value
        elif isinstance(raw_value, list):
            value = [
                _visual_text(item, MAX_VISUAL_BOARD_NODE_TEXT_CHARS)
                for item in raw_value[:MAX_VISUAL_BOARD_META_ITEMS]
            ]
        else:
            value = _visual_text(raw_value, MAX_VISUAL_BOARD_NODE_TEXT_CHARS)

        normalized[key] = value
        if len(normalized) >= MAX_VISUAL_BOARD_META_ITEMS:
            break

    return normalized


def _normalize_visual_node(node, seen: set[str]) -> dict | None:
    if not isinstance(node, dict):
        return None

    title = _visual_text(node.get("title", node.get("label", "")), MAX_VISUAL_BOARD_TITLE_CHARS)
    text = _visual_text(node.get("text", ""), MAX_VISUAL_BOARD_NODE_TEXT_CHARS)
    node_type = _visual_text(node.get("type", "note"), 40).lower() or "note"
    node_type = re.sub(r"[^a-z0-9_-]+", "-", node_type).strip("-") or "note"
    node_id = _visual_id(node.get("id", ""), "node", title or text or node_type, seen)
    seen.add(node_id)

    return {
        "id": node_id,
        "type": node_type,
        "title": title or "Nodo",
        "text": text,
        "x": _visual_number(node.get("x", 0), 0, -20_000, 20_000),
        "y": _visual_number(node.get("y", 0), 0, -20_000, 20_000),
        "width": _visual_number(node.get("width", 220), 220, 80, 800),
        "height": _visual_number(node.get("height", 120), 120, 60, 600),
        "lane": _visual_text(node.get("lane", ""), MAX_VISUAL_BOARD_ID_CHARS),
        "color": _visual_text(node.get("color", ""), 40),
        "meta": _normalize_visual_meta(node.get("meta", {})),
    }


def _normalize_visual_edge(edge, node_ids: set[str], seen: set[str]) -> dict | None:
    if not isinstance(edge, dict):
        return None

    source = _visual_text(edge.get("source", ""), MAX_VISUAL_BOARD_ID_CHARS)
    target = _visual_text(edge.get("target", ""), MAX_VISUAL_BOARD_ID_CHARS)
    if not source or not target or source == target:
        return None
    if source not in node_ids or target not in node_ids:
        return None

    edge_id = _visual_id(edge.get("id", ""), "edge", f"{source}-{target}", seen)
    seen.add(edge_id)
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "label": _visual_text(edge.get("label", ""), 120),
    }


def _normalize_visual_lane(lane, seen: set[str]) -> dict | None:
    if not isinstance(lane, dict):
        return None

    title = _visual_text(lane.get("title", lane.get("label", "")), MAX_VISUAL_BOARD_TITLE_CHARS)
    lane_id = _visual_id(lane.get("id", ""), "lane", title or "lane", seen)
    seen.add(lane_id)
    return {
        "id": lane_id,
        "title": title or "Lane",
        "x": _visual_number(lane.get("x", 0), 0, -20_000, 20_000),
        "y": _visual_number(lane.get("y", 0), 0, -20_000, 20_000),
        "width": _visual_number(lane.get("width", 260), 260, 120, 1_200),
        "height": _visual_number(lane.get("height", 420), 420, 120, 2_000),
        "color": _visual_text(lane.get("color", ""), 40),
    }


def _normalize_visual_viewport(viewport) -> dict:
    if not isinstance(viewport, dict):
        viewport = {}
    return {
        "x": _visual_number(viewport.get("x", 0), 0, -20_000, 20_000),
        "y": _visual_number(viewport.get("y", 0), 0, -20_000, 20_000),
        "zoom": _visual_number(viewport.get("zoom", 1), 1, 0.2, 3),
    }


def _normalize_visual_export_paths(export_paths) -> dict:
    if not isinstance(export_paths, dict):
        return {}

    normalized = {}
    for key in ("json", "svg", "html"):
        value = _visual_text(export_paths.get(key, ""), MAX_VISUAL_BOARD_EXPORT_PATH_CHARS)
        if value:
            normalized[key] = value
    return normalized


def _normalize_visual_board(board):
    if not isinstance(board, dict):
        return None

    raw_kind = _visual_text(board.get("kind", "idea_canvas"), MAX_VISUAL_BOARD_KIND_CHARS).lower()
    kind = raw_kind if raw_kind in VALID_VISUAL_BOARD_KIND else "idea_canvas"
    title = _visual_text(board.get("title", ""), MAX_VISUAL_BOARD_TITLE_CHARS)
    board_id = _visual_id(board.get("id", ""), "board", title or kind)

    node_seen = set()
    nodes = []
    raw_nodes = board.get("nodes", [])
    if isinstance(raw_nodes, list):
        for raw_node in raw_nodes:
            normalized_node = _normalize_visual_node(raw_node, node_seen)
            if normalized_node:
                nodes.append(normalized_node)
                if len(nodes) >= MAX_VISUAL_BOARD_NODES:
                    break

    node_ids = {node["id"] for node in nodes}
    edge_seen = set()
    edges = []
    raw_edges = board.get("edges", [])
    if isinstance(raw_edges, list):
        for raw_edge in raw_edges:
            normalized_edge = _normalize_visual_edge(raw_edge, node_ids, edge_seen)
            if normalized_edge:
                edges.append(normalized_edge)
                if len(edges) >= MAX_VISUAL_BOARD_EDGES:
                    break

    lane_seen = set()
    lanes = []
    raw_lanes = board.get("lanes", [])
    if isinstance(raw_lanes, list):
        for raw_lane in raw_lanes:
            normalized_lane = _normalize_visual_lane(raw_lane, lane_seen)
            if normalized_lane:
                lanes.append(normalized_lane)
                if len(lanes) >= MAX_VISUAL_BOARD_LANES:
                    break

    return {
        "id": board_id,
        "kind": kind,
        "title": title or kind.replace("_", " ").title(),
        "nodes": nodes,
        "edges": edges,
        "lanes": lanes,
        "viewport": _normalize_visual_viewport(board.get("viewport", {})),
        "export_paths": _normalize_visual_export_paths(board.get("export_paths", {})),
        "created_at": _visual_text(board.get("created_at", ""), MAX_IDEA_PROJECT_TIMESTAMP_CHARS),
        "updated_at": _visual_text(board.get("updated_at", ""), MAX_IDEA_PROJECT_TIMESTAMP_CHARS),
    }


def _normalize_idea_project(project):
    if not isinstance(project, dict):
        return None

    title = _coerce_text(project.get("title", ""), MAX_IDEA_PROJECT_TITLE_CHARS).strip()
    if not title:
        return None

    raw_kind = _coerce_text(project.get("kind", "mixto"), 40).strip().lower()
    raw_status = _coerce_text(project.get("status", "exploring"), 40).strip().lower()
    kind = raw_kind if raw_kind in VALID_IDEA_PROJECT_KIND else "mixto"
    status = raw_status if raw_status in VALID_IDEA_PROJECT_STATUS else "exploring"
    project_id = _coerce_text(project.get("id") or _fallback_id("idea", title), 40).strip()

    visual_boards = []
    raw_visual_boards = project.get("visual_boards", [])
    if isinstance(raw_visual_boards, list):
        for raw_board in raw_visual_boards:
            normalized_board = _normalize_visual_board(raw_board)
            if normalized_board:
                visual_boards.append(normalized_board)
                if len(visual_boards) >= MAX_VISUAL_BOARDS_PER_PROJECT:
                    break

    return {
        "id": project_id,
        "title": title,
        "kind": kind,
        "status": status,
        "summary": _coerce_text(project.get("summary", ""), MAX_IDEA_PROJECT_TEXT_CHARS).strip(),
        "audience": _coerce_text(project.get("audience", ""), MAX_IDEA_PROJECT_TEXT_CHARS).strip(),
        "desired_outcome": _coerce_text(project.get("desired_outcome", ""), MAX_IDEA_PROJECT_TEXT_CHARS).strip(),
        "problem": _coerce_text(project.get("problem", ""), MAX_IDEA_PROJECT_TEXT_CHARS).strip(),
        "creative_directions": _normalize_string_list(
            project.get("creative_directions", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "selected_direction": _coerce_text(
            project.get("selected_direction", ""),
            MAX_IDEA_PROJECT_TEXT_CHARS,
        ).strip(),
        "success_criteria": _normalize_string_list(
            project.get("success_criteria", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "constraints": _normalize_string_list(
            project.get("constraints", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "risks": _normalize_string_list(
            project.get("risks", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "open_questions": _normalize_string_list(
            project.get("open_questions", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "next_steps": _normalize_string_list(
            project.get("next_steps", []),
            item_limit=MAX_IDEA_PROJECT_ITEMS,
            char_limit=MAX_IDEA_PROJECT_ITEM_CHARS,
        ),
        "created_at": _coerce_text(project.get("created_at", ""), MAX_IDEA_PROJECT_TIMESTAMP_CHARS).strip(),
        "updated_at": _coerce_text(project.get("updated_at", ""), MAX_IDEA_PROJECT_TIMESTAMP_CHARS).strip(),
        "visual_boards": visual_boards,
    }


def _normalize_timezone(value) -> str:
    """Valida una zona horaria IANA (p.ej. 'America/Mexico_City'). Devuelve la
    cadena si es valida; '' si es invalida o vacia (usar la del sistema)."""
    cleaned = _coerce_text(value, 64).strip()
    if not cleaned:
        return ""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(cleaned)
    except Exception:
        return ""
    return cleaned


def _normalize_profile(profile):
    if not isinstance(profile, dict):
        profile = {}

    return {
        "name": _coerce_text(profile.get("name", ""), 80).strip(),
        "role": _coerce_text(profile.get("role", ""), 120).strip(),
        "preferences": _normalize_string_list(
            profile.get("preferences", []),
            item_limit=MAX_PROFILE_ITEMS,
            char_limit=MAX_PROFILE_ITEM_CHARS,
        ),
        "constraints": _normalize_string_list(
            profile.get("constraints", []),
            item_limit=MAX_PROFILE_ITEMS,
            char_limit=MAX_PROFILE_ITEM_CHARS,
        ),
        "timezone": _normalize_timezone(profile.get("timezone", "")),
    }


def _normalize_plan(plan):
    return _normalize_string_list(
        plan,
        item_limit=MAX_PLAN_ITEMS,
        char_limit=MAX_PLAN_ITEM_CHARS,
    )


def _normalize_autonomy(
    autonomy,
    *,
    migrate_legacy_cycles: bool = False,
    migrate_legacy_step_limit: bool = False,
):
    defaults = default_state()["autonomy"]
    if not isinstance(autonomy, dict):
        autonomy = {}

    normalized = {}

    if migrate_legacy_step_limit:
        normalized["max_steps_per_cycle"] = None
    else:
        raw_max_steps = autonomy.get("max_steps_per_cycle", defaults["max_steps_per_cycle"])
        if raw_max_steps is None:
            normalized["max_steps_per_cycle"] = None
        else:
            try:
                parsed_max_steps = int(raw_max_steps)
            except (TypeError, ValueError):
                normalized["max_steps_per_cycle"] = defaults["max_steps_per_cycle"]
            else:
                normalized["max_steps_per_cycle"] = parsed_max_steps if parsed_max_steps > 0 else None

    normalized["auto_cycles_default"] = normalize_cycle_count(
        autonomy.get("auto_cycles_default", defaults["auto_cycles_default"]),
        default=defaults["auto_cycles_default"],
        legacy_default=LEGACY_DEFAULT_AUTO_CYCLES,
        migrate_legacy=migrate_legacy_cycles,
    )

    return normalized


def _normalize_coding(coding):
    defaults = default_state()["coding"]
    if not isinstance(coding, dict):
        coding = {}

    workspace_path = _coerce_text(
        coding.get("workspace_path", defaults["workspace_path"]),
        MAX_CODING_WORKSPACE_PATH_CHARS,
    ).strip()

    mode = _coerce_text(
        coding.get("mode", defaults["mode"]),
        40,
    ).strip().lower()
    if mode not in VALID_CODING_MODES:
        mode = defaults["mode"]

    raw_pending_ids = coding.get("pending_proposal_ids", defaults["pending_proposal_ids"])
    if isinstance(raw_pending_ids, str):
        pending_candidates = re.split(r"[,;\n]+", raw_pending_ids)
    elif isinstance(raw_pending_ids, list):
        pending_candidates = raw_pending_ids
    else:
        pending_candidates = []

    pending_proposal_ids = []
    seen_ids = set()
    for candidate in pending_candidates:
        proposal_id = _coerce_text(candidate, MAX_CODING_PROPOSAL_ID_CHARS).strip()
        if not proposal_id or proposal_id in seen_ids:
            continue
        pending_proposal_ids.append(proposal_id)
        seen_ids.add(proposal_id)
        if len(pending_proposal_ids) >= MAX_CODING_PROPOSAL_IDS:
            break

    validation_command = _coerce_text(
        coding.get("validation_command", defaults["validation_command"]),
        MAX_CODING_VALIDATION_COMMAND_CHARS,
    ).strip()

    raw_last_validation = coding.get("last_validation", defaults["last_validation"])
    if not isinstance(raw_last_validation, dict):
        raw_last_validation = {}

    try:
        raw_exit_code = raw_last_validation.get("exit_code", defaults["last_validation"]["exit_code"])
        exit_code = None if raw_exit_code is None or raw_exit_code == "" else int(raw_exit_code)
    except (TypeError, ValueError):
        exit_code = defaults["last_validation"]["exit_code"]

    last_validation = {
        "command": _coerce_text(
            raw_last_validation.get("command", defaults["last_validation"]["command"]),
            MAX_CODING_VALIDATION_COMMAND_CHARS,
        ).strip(),
        "proposal_id": _coerce_text(
            raw_last_validation.get("proposal_id", defaults["last_validation"]["proposal_id"]),
            MAX_CODING_PROPOSAL_ID_CHARS,
        ).strip(),
        "exit_code": exit_code,
        "output": _coerce_text(
            raw_last_validation.get("output", defaults["last_validation"]["output"]),
            MAX_CODING_VALIDATION_OUTPUT_CHARS,
        ),
        "ran_at": _coerce_text(
            raw_last_validation.get("ran_at", defaults["last_validation"]["ran_at"]),
            MAX_CODING_VALIDATION_TIMESTAMP_CHARS,
        ).strip(),
    }

    return {
        "workspace_path": workspace_path,
        "mode": mode,
        "pending_proposal_ids": pending_proposal_ids,
        "validation_command": validation_command,
        "last_validation": last_validation,
    }


def _normalize_evolution_directives(raw_list) -> list[dict]:
    if not isinstance(raw_list, list):
        return []
    normalized = []
    seen_ids = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        text = _coerce_text(item.get("text", ""), MAX_EVOLUTION_DIRECTIVE_CHARS).strip()
        if not text:
            continue
        directive_id = _coerce_text(item.get("id", ""), MAX_EVOLUTION_DIRECTIVE_ID_CHARS).strip()
        if not directive_id or directive_id in seen_ids:
            directive_id = uuid4().hex[:12]
        seen_ids.add(directive_id)
        normalized.append({
            "id": directive_id,
            "text": text,
            "reason": _coerce_text(item.get("reason", ""), MAX_EVOLUTION_DIRECTIVE_REASON_CHARS).strip(),
            "created_at": _coerce_text(item.get("created_at", ""), MAX_CODING_VALIDATION_TIMESTAMP_CHARS).strip(),
        })
        if len(normalized) >= MAX_EVOLUTION_DIRECTIVES:
            break
    return normalized


def _normalize_evolution_suggestions(raw_list) -> list[dict]:
    if not isinstance(raw_list, list):
        return []
    normalized = []
    seen_ids = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        text = _coerce_text(item.get("text", ""), MAX_EVOLUTION_SUGGESTION_CHARS).strip()
        if not text:
            continue
        kind = str(item.get("kind", "")).strip().lower()
        if kind not in EVOLUTION_SUGGESTION_KINDS:
            continue
        suggestion_id = _coerce_text(item.get("id", ""), MAX_EVOLUTION_DIRECTIVE_ID_CHARS).strip()
        if not suggestion_id or suggestion_id in seen_ids:
            suggestion_id = uuid4().hex[:12]
        seen_ids.add(suggestion_id)
        normalized.append({
            "id": suggestion_id,
            "kind": kind,
            "text": text,
            "reason": _coerce_text(item.get("reason", ""), MAX_EVOLUTION_DIRECTIVE_REASON_CHARS).strip(),
            "created_at": _coerce_text(item.get("created_at", ""), MAX_CODING_VALIDATION_TIMESTAMP_CHARS).strip(),
        })
        if len(normalized) >= MAX_EVOLUTION_SUGGESTIONS:
            break
    return normalized


def _normalize_evolution(evolution):
    defaults = default_state()["evolution"]
    if not isinstance(evolution, dict):
        evolution = {}

    enabled = _normalize_bool(evolution.get("enabled", defaults["enabled"]), defaults["enabled"])

    try:
        interval_hours = int(evolution.get("interval_hours", defaults["interval_hours"]))
    except (TypeError, ValueError):
        interval_hours = defaults["interval_hours"]
    interval_hours = max(1, min(168, interval_hours))

    try:
        max_pending = int(evolution.get("max_pending", defaults["max_pending"]))
    except (TypeError, ValueError):
        max_pending = defaults["max_pending"]
    max_pending = max(1, min(20, max_pending))

    raw_dimensions = evolution.get("dimensions", defaults["dimensions"])
    if isinstance(raw_dimensions, str):
        raw_dimensions = re.split(r"[,;\s]+", raw_dimensions)
    if not isinstance(raw_dimensions, list):
        raw_dimensions = list(defaults["dimensions"])
    dimensions = [d for d in EVOLUTION_DIMENSIONS if d in {str(x).strip().lower() for x in raw_dimensions}]
    if not dimensions:
        dimensions = list(defaults["dimensions"])

    return {
        "enabled": enabled,
        "interval_hours": interval_hours,
        "last_run_at": _coerce_text(
            evolution.get("last_run_at", defaults["last_run_at"]),
            MAX_CODING_VALIDATION_TIMESTAMP_CHARS,
        ).strip(),
        "max_pending": max_pending,
        "dimensions": dimensions,
        "directives": _normalize_evolution_directives(evolution.get("directives", [])),
        "directives_pending": _normalize_evolution_directives(evolution.get("directives_pending", [])),
        "suggestions_pending": _normalize_evolution_suggestions(evolution.get("suggestions_pending", [])),
    }


def _normalize_vision(vision):
    defaults = default_state()["vision"]
    if not isinstance(vision, dict):
        vision = {}

    model = _coerce_text(vision.get("model", defaults["model"]), MAX_VISION_MODEL_CHARS).strip() or defaults["model"]

    try:
        max_image_dim = int(vision.get("max_image_dim", defaults["max_image_dim"]))
    except (TypeError, ValueError):
        max_image_dim = defaults["max_image_dim"]
    max_image_dim = max(256, min(4096, max_image_dim))

    try:
        timeout_seconds = int(vision.get("timeout_seconds", defaults["timeout_seconds"]))
    except (TypeError, ValueError):
        timeout_seconds = defaults["timeout_seconds"]
    timeout_seconds = max(10, min(600, timeout_seconds))

    return {
        "model": model,
        "max_image_dim": max_image_dim,
        "timeout_seconds": timeout_seconds,
    }


VALID_BROWSER_CHANNELS = {"msedge", "chrome", "chromium", "brave", "edge"}


def _normalize_computer_control(cc):
    defaults = default_state()["computer_control"]
    if not isinstance(cc, dict):
        cc = {}
    settings = cc.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    channel = _coerce_text(settings.get("browser_channel", defaults["settings"]["browser_channel"]), 20).strip().lower()
    if channel not in VALID_BROWSER_CHANNELS:
        channel = defaults["settings"]["browser_channel"]
    return {
        "enabled": bool(cc.get("enabled", defaults["enabled"])),
        "settings": {
            "browser_channel": channel,
            "headed": bool(settings.get("headed", defaults["settings"]["headed"])),
            "confirm_sensitive": bool(settings.get("confirm_sensitive", defaults["settings"]["confirm_sensitive"])),
            "os_control": bool(settings.get("os_control", defaults["settings"]["os_control"])),
        },
    }


def _normalize_provider_host(host, default: str, max_chars: int, *, strip_ollama_api: bool = False) -> str:
    cleaned = _coerce_text(host, max_chars).strip()
    if not cleaned:
        return str(default).strip().rstrip("/")
    if strip_ollama_api and cleaned.endswith("/api"):
        cleaned = cleaned[:-4].rstrip("/")
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    return cleaned.rstrip("/")


def _normalize_provider_settings(
    settings,
    defaults: dict,
    *,
    model_char_limit: int,
    host_char_limit: int,
    api_key_env_char_limit: int,
    fallback_limit: int,
    api_key_char_limit: int | None = None,
    allow_empty_model: bool = False,
    strip_ollama_api: bool = False,
) -> dict:
    if not isinstance(settings, dict):
        settings = {}

    default_model = defaults["model"]
    model = _coerce_text(
        settings.get("model", default_model),
        model_char_limit,
    ).strip()
    if not model and not allow_empty_model:
        model = default_model

    host = _normalize_provider_host(
        settings.get("host", defaults["host"]),
        defaults["host"],
        host_char_limit,
        strip_ollama_api=strip_ollama_api,
    )

    api_key_env_var = _coerce_text(
        settings.get("api_key_env_var", defaults["api_key_env_var"]),
        api_key_env_char_limit,
    ).strip() or defaults["api_key_env_var"]

    raw_fallback_models = settings.get("fallback_models", defaults["fallback_models"])
    if isinstance(raw_fallback_models, str):
        fallback_candidates = re.split(r"[,;\n]+", raw_fallback_models)
    elif isinstance(raw_fallback_models, list):
        fallback_candidates = raw_fallback_models
    else:
        fallback_candidates = []

    fallback_models = []
    seen_models = {model} if model else set()
    for candidate in fallback_candidates:
        fallback_model = _coerce_text(candidate, model_char_limit).strip()
        if not fallback_model or fallback_model in seen_models:
            continue
        fallback_models.append(fallback_model)
        seen_models.add(fallback_model)
        if len(fallback_models) >= fallback_limit:
            break

    try:
        timeout_seconds = max(
            MIN_OLLAMA_TIMEOUT_SECONDS,
            min(
                MAX_OLLAMA_TIMEOUT_SECONDS,
                int(settings.get("timeout_seconds", defaults["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        timeout_seconds = defaults["timeout_seconds"]

    normalized = {
        "model": model,
        "fallback_models": fallback_models,
        "host": host,
        "api_key_env_var": api_key_env_var,
        "timeout_seconds": timeout_seconds,
    }
    if api_key_char_limit is not None:
        normalized["api_key"] = _coerce_text(
            settings.get("api_key", defaults.get("api_key", "")),
            api_key_char_limit,
        ).strip()
    return normalized


def _normalize_ollama(ollama):
    defaults = default_state()["ollama"]
    return _normalize_provider_settings(
        ollama,
        defaults,
        model_char_limit=MAX_OLLAMA_MODEL_CHARS,
        host_char_limit=MAX_OLLAMA_HOST_CHARS,
        api_key_env_char_limit=MAX_OLLAMA_API_KEY_ENV_VAR_CHARS,
        fallback_limit=MAX_OLLAMA_FALLBACK_MODELS,
        api_key_char_limit=MAX_OLLAMA_API_KEY_CHARS,
        strip_ollama_api=True,
    )


def _normalize_openrouter(openrouter):
    defaults = default_state()["model_provider"]["openrouter"]
    return _normalize_provider_settings(
        openrouter,
        defaults,
        model_char_limit=MAX_OPENROUTER_MODEL_CHARS,
        host_char_limit=MAX_OPENROUTER_HOST_CHARS,
        api_key_env_char_limit=MAX_OPENROUTER_API_KEY_ENV_VAR_CHARS,
        fallback_limit=MAX_OPENROUTER_FALLBACK_MODELS,
        api_key_char_limit=MAX_OPENROUTER_API_KEY_CHARS,
        allow_empty_model=True,
    )


def _normalize_model_provider(state):
    defaults = default_state()["model_provider"]
    source = state.get("model_provider", {}) if isinstance(state, dict) else {}
    if not isinstance(source, dict):
        source = {}

    default_provider = str(source.get("default", defaults["default"])).strip().lower()
    if default_provider not in VALID_MODEL_PROVIDERS:
        default_provider = defaults["default"]

    legacy_ollama = state.get("ollama", {}) if isinstance(state, dict) else {}
    # The top-level key remains a compatibility surface for older callers.
    # If both copies exist, prefer the legacy key and mirror it into model_provider.
    ollama_source = legacy_ollama if isinstance(legacy_ollama, dict) else source.get("ollama", {})
    openrouter_source = source.get("openrouter", {})

    return {
        "default": default_provider,
        "ollama": _normalize_ollama(ollama_source),
        "openrouter": _normalize_openrouter(openrouter_source),
    }


def _normalize_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if not cleaned:
            return default
        return cleaned not in {"0", "false", "off", "no", "disabled"}
    if value is None:
        return default
    return bool(value)


def _normalize_memory_protection(memory_protection):
    defaults = default_state()["memory_protection"]
    if not isinstance(memory_protection, dict):
        memory_protection = {}

    retention = memory_protection.get("retention", {})
    if not isinstance(retention, dict):
        retention = {}
    retention_defaults = defaults["retention"]

    try:
        max_auto_backups = max(
            1,
            min(
                5000,
                int(retention.get("max_auto_backups", retention_defaults["max_auto_backups"])),
            ),
        )
    except (TypeError, ValueError):
        max_auto_backups = retention_defaults["max_auto_backups"]

    try:
        keep_daily_days = max(
            0,
            min(
                3650,
                int(retention.get("keep_daily_days", retention_defaults["keep_daily_days"])),
            ),
        )
    except (TypeError, ValueError):
        keep_daily_days = retention_defaults["keep_daily_days"]

    return {
        "enabled": _normalize_bool(
            memory_protection.get("enabled", defaults["enabled"]),
            defaults["enabled"],
        ),
        "backup_on_every_change": _normalize_bool(
            memory_protection.get("backup_on_every_change", defaults["backup_on_every_change"]),
            defaults["backup_on_every_change"],
        ),
        "mirror_dir": _coerce_text(
            memory_protection.get("mirror_dir", defaults["mirror_dir"]),
            MAX_MEMORY_PROTECTION_MIRROR_DIR_CHARS,
        ).strip(),
        "include_secrets": False,
        "retention": {
            "max_auto_backups": max_auto_backups,
            "keep_daily_days": keep_daily_days,
        },
        "verify_after_write": _normalize_bool(
            memory_protection.get("verify_after_write", defaults["verify_after_write"]),
            defaults["verify_after_write"],
        ),
        "auto_restore": _normalize_bool(
            memory_protection.get("auto_restore", defaults["auto_restore"]),
            defaults["auto_restore"],
        ),
        "last_backup_at": _coerce_text(
            memory_protection.get("last_backup_at", defaults["last_backup_at"]),
            MAX_MEMORY_PROTECTION_TIMESTAMP_CHARS,
        ).strip(),
        "last_recovery_at": _coerce_text(
            memory_protection.get("last_recovery_at", defaults["last_recovery_at"]),
            MAX_MEMORY_PROTECTION_TIMESTAMP_CHARS,
        ).strip(),
        "last_error": _coerce_text(
            memory_protection.get("last_error", defaults["last_error"]),
            MAX_MEMORY_PROTECTION_ERROR_CHARS,
        ).strip(),
    }


def _normalize_service(service, *, migrate_legacy_cycles: bool = False):
    defaults = default_state()["service"]
    if not isinstance(service, dict):
        service = {}

    proactive = service.get("proactive", {})
    if not isinstance(proactive, dict):
        proactive = {}
    proactive_defaults = defaults["proactive"]

    try:
        interval_seconds = max(
            60,
            min(
                24 * 60 * 60,
                int(proactive.get(
                    "interval_seconds",
                    proactive_defaults["interval_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        interval_seconds = proactive_defaults["interval_seconds"]

    cycles = normalize_cycle_count(
        proactive.get("cycles", proactive_defaults["cycles"]),
        default=proactive_defaults["cycles"],
        legacy_default=LEGACY_DEFAULT_SERVICE_PROACTIVE_CYCLES,
        migrate_legacy=migrate_legacy_cycles,
    )

    try:
        start_delay_seconds = max(
            0,
            min(
                24 * 60 * 60,
                int(proactive.get(
                    "start_delay_seconds",
                    proactive_defaults["start_delay_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        start_delay_seconds = proactive_defaults["start_delay_seconds"]

    mobile_ui = service.get("mobile_ui", {})
    if not isinstance(mobile_ui, dict):
        mobile_ui = {}
    mobile_defaults = defaults["mobile_ui"]
    try:
        mobile_port = int(mobile_ui.get("port", mobile_defaults["port"]))
    except (TypeError, ValueError):
        mobile_port = mobile_defaults["port"]
    if not 1 <= mobile_port <= 65535:
        mobile_port = mobile_defaults["port"]
    try:
        mobile_job_timeout_seconds = int(mobile_ui.get(
            "job_timeout_seconds",
            mobile_defaults["job_timeout_seconds"],
        ))
    except (TypeError, ValueError):
        mobile_job_timeout_seconds = mobile_defaults["job_timeout_seconds"]
    if not MIN_MOBILE_UI_JOB_TIMEOUT_SECONDS <= mobile_job_timeout_seconds <= MAX_MOBILE_UI_JOB_TIMEOUT_SECONDS:
        mobile_job_timeout_seconds = mobile_defaults["job_timeout_seconds"]

    return {
        "proactive": {
            "enabled": _normalize_bool(
                proactive.get("enabled", proactive_defaults["enabled"]),
                proactive_defaults["enabled"],
            ),
            "interval_seconds": interval_seconds,
            "cycles": cycles,
            "start_delay_seconds": start_delay_seconds,
            "model": _coerce_text(
                proactive.get(
                    "model",
                    proactive_defaults.get("model", DEFAULT_SERVICE_PROACTIVE_MODEL),
                ),
                MAX_SERVICE_PROACTIVE_MODEL_CHARS,
            ).strip(),
            "last_pulse_at": _coerce_text(
                proactive.get("last_pulse_at", proactive_defaults.get("last_pulse_at", "")),
                MAX_RUNTIME_TIMESTAMP_CHARS,
            ).strip(),
        },
        "mobile_ui": {
            "enabled": _normalize_bool(
                mobile_ui.get("enabled", mobile_defaults["enabled"]),
                mobile_defaults["enabled"],
            ),
            "port": mobile_port,
            "job_timeout_seconds": mobile_job_timeout_seconds,
            "https_enabled": _normalize_bool(
                mobile_ui.get("https_enabled", mobile_defaults["https_enabled"]),
                mobile_defaults["https_enabled"],
            ),
            "pin_hash": _coerce_text(
                mobile_ui.get("pin_hash", mobile_defaults["pin_hash"]),
                MAX_MOBILE_UI_HASH_CHARS,
            ).strip()[:MAX_MOBILE_UI_HASH_CHARS],
            "pin_salt": _coerce_text(
                mobile_ui.get("pin_salt", mobile_defaults["pin_salt"]),
                MAX_MOBILE_UI_SALT_CHARS,
            ).strip()[:MAX_MOBILE_UI_SALT_CHARS],
            "session_secret": _coerce_text(
                mobile_ui.get("session_secret", mobile_defaults["session_secret"]),
                MAX_MOBILE_UI_SESSION_SECRET_CHARS,
            ).strip()[:MAX_MOBILE_UI_SESSION_SECRET_CHARS],
            "last_bind_error": _coerce_text(
                mobile_ui.get("last_bind_error", mobile_defaults["last_bind_error"]),
                MAX_MOBILE_UI_BIND_ERROR_CHARS,
            ).strip()[:MAX_MOBILE_UI_BIND_ERROR_CHARS],
            "https_last_error": _coerce_text(
                mobile_ui.get("https_last_error", mobile_defaults["https_last_error"]),
                MAX_MOBILE_UI_BIND_ERROR_CHARS,
            ).strip()[:MAX_MOBILE_UI_BIND_ERROR_CHARS],
            "tailscale_serve_target": _coerce_text(
                mobile_ui.get("tailscale_serve_target", mobile_defaults["tailscale_serve_target"]),
                MAX_MOBILE_UI_SERVE_TARGET_CHARS,
            ).strip()[:MAX_MOBILE_UI_SERVE_TARGET_CHARS],
        },
    }


def _normalize_awaiting_user_input(awaiting_user_input):
    if not isinstance(awaiting_user_input, dict):
        awaiting_user_input = {}

    question = _coerce_text(
        awaiting_user_input.get("question", ""),
        MAX_AWAITING_INPUT_QUESTION_CHARS,
    ).strip()
    reason = _coerce_text(
        awaiting_user_input.get("reason", ""),
        MAX_AWAITING_INPUT_REASON_CHARS,
    ).strip()
    fields = _normalize_string_list(
        awaiting_user_input.get("fields", []),
        item_limit=MAX_AWAITING_INPUT_FIELDS,
        char_limit=80,
    )
    pending = bool(awaiting_user_input.get("pending")) and bool(question)

    return {
        "pending": pending,
        "question": question if pending else "",
        "reason": reason if pending else "",
        "fields": fields if pending else [],
    }


def _normalize_ui(ui):
    defaults = default_state()["ui"]
    if not isinstance(ui, dict):
        ui = {}

    theme = str(ui.get("theme", defaults["theme"])).strip().lower()
    if theme not in VALID_UI_THEME:
        theme = defaults["theme"]

    return {
        "theme": theme,
    }


def _normalize_runtime(runtime):
    defaults = default_state()["runtime"]
    if not isinstance(runtime, dict):
        runtime = {}

    normalized = {
        "thinking": defaults["thinking"],
        "stop_requested": defaults["stop_requested"],
    }

    thinking = runtime.get("thinking", {})
    if not isinstance(thinking, dict):
        thinking = {}

    label = _coerce_text(
        thinking.get("label", ""),
        MAX_RUNTIME_OPERATION_LABEL_CHARS,
    ).strip()
    source = _coerce_text(
        thinking.get("source", ""),
        MAX_RUNTIME_OPERATION_SOURCE_CHARS,
    ).strip()
    started_at = _coerce_text(
        thinking.get("started_at", ""),
        MAX_RUNTIME_TIMESTAMP_CHARS,
    ).strip()
    operation_id = _coerce_text(
        thinking.get("operation_id", ""),
        MAX_RUNTIME_OPERATION_ID_CHARS,
    ).strip()
    active = bool(thinking.get("active")) and bool(label)

    if active:
        normalized["thinking"] = {
            "active": True,
            "label": label,
            "source": source,
            "started_at": started_at,
            "operation_id": operation_id,
        }

    stop_requested = runtime.get("stop_requested", {})
    if not isinstance(stop_requested, dict):
        stop_requested = {}

    stop_operation_id = _coerce_text(
        stop_requested.get("operation_id", ""),
        MAX_RUNTIME_OPERATION_ID_CHARS,
    ).strip()
    stop_requested_at = _coerce_text(
        stop_requested.get("requested_at", ""),
        MAX_RUNTIME_TIMESTAMP_CHARS,
    ).strip()
    stop_source = _coerce_text(
        stop_requested.get("source", ""),
        MAX_RUNTIME_OPERATION_SOURCE_CHARS,
    ).strip()
    stop_reason = _coerce_text(
        stop_requested.get("reason", ""),
        MAX_RUNTIME_STOP_REASON_CHARS,
    ).strip()

    if bool(stop_requested.get("active")):
        normalized["stop_requested"] = {
            "active": True,
            "operation_id": stop_operation_id,
            "requested_at": stop_requested_at,
            "source": stop_source,
            "reason": stop_reason,
        }

    return normalized


def _normalize_local_context(local_context):
    defaults = default_state()["local_context"]
    if not isinstance(local_context, dict):
        local_context = {}

    mode = _coerce_text(
        local_context.get("mode", defaults["mode"]),
        MAX_LOCAL_CONTEXT_MODE_CHARS,
    ).strip().lower()
    if mode not in VALID_LOCAL_CONTEXT_MODES:
        mode = defaults["mode"]

    try:
        sample_interval_seconds = max(
            5,
            min(
                24 * 60 * 60,
                int(local_context.get(
                    "sample_interval_seconds",
                    defaults["sample_interval_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        sample_interval_seconds = defaults["sample_interval_seconds"]

    try:
        max_snapshot_age_seconds = max(
            15,
            min(
                24 * 60 * 60,
                int(local_context.get(
                    "max_snapshot_age_seconds",
                    defaults["max_snapshot_age_seconds"],
                )),
            ),
        )
    except (TypeError, ValueError):
        max_snapshot_age_seconds = defaults["max_snapshot_age_seconds"]

    enabled = _normalize_bool(local_context.get("enabled", defaults["enabled"]), defaults["enabled"])
    if mode == "off":
        enabled = False

    return {
        "enabled": enabled,
        "mode": mode,
        "sample_interval_seconds": sample_interval_seconds,
        "max_snapshot_age_seconds": max_snapshot_age_seconds,
        "include_window_title": (
            mode == "detailed"
            and _normalize_bool(
                local_context.get("include_window_title", defaults["include_window_title"]),
                defaults["include_window_title"],
            )
        ),
        "include_process_name": _normalize_bool(
            local_context.get("include_process_name", defaults["include_process_name"]),
            defaults["include_process_name"],
        ),
        "include_workspace_changes": _normalize_bool(
            local_context.get("include_workspace_changes", defaults["include_workspace_changes"]),
            defaults["include_workspace_changes"],
        ),
        "include_system_health": _normalize_bool(
            local_context.get("include_system_health", defaults["include_system_health"]),
            defaults["include_system_health"],
        ),
    }


def _normalize_domain_list(value) -> list[str]:
    items = _normalize_string_list(
        value,
        item_limit=MAX_INTERNET_DOMAIN_ITEMS,
        char_limit=MAX_INTERNET_DOMAIN_CHARS,
    )

    normalized = []
    seen = set()
    for item in items:
        domain = str(item).strip().lower()
        domain = re.sub(r"^https?://", "", domain)
        domain = domain.strip("/")
        if not domain:
            continue
        if domain in seen:
            continue
        normalized.append(domain)
        seen.add(domain)

    return normalized


def _normalize_internet(internet):
    defaults = default_state()["internet"]
    if not isinstance(internet, dict):
        internet = {}

    mode = str(internet.get("mode", defaults["mode"])).strip().lower()
    if mode not in VALID_INTERNET_MODES:
        mode = defaults["mode"]

    provider = str(internet.get("provider", defaults["provider"])).strip().lower()
    if provider not in VALID_SEARCH_PROVIDERS:
        provider = defaults["provider"]

    try:
        max_search_results = max(
            1,
            min(
                10,
                int(internet.get("max_search_results", defaults["max_search_results"])),
            ),
        )
    except (TypeError, ValueError):
        max_search_results = defaults["max_search_results"]

    try:
        max_page_chars = max(
            1_000,
            min(30_000, int(internet.get("max_page_chars", defaults["max_page_chars"]))),
        )
    except (TypeError, ValueError):
        max_page_chars = defaults["max_page_chars"]

    try:
        request_timeout_seconds = max(
            3,
            min(
                60,
                int(
                    internet.get(
                        "request_timeout_seconds",
                        defaults["request_timeout_seconds"],
                    )
                ),
            ),
        )
    except (TypeError, ValueError):
        request_timeout_seconds = defaults["request_timeout_seconds"]

    return {
        "mode": mode,
        "provider": provider,
        "max_search_results": max_search_results,
        "max_page_chars": max_page_chars,
        "request_timeout_seconds": request_timeout_seconds,
        "allowed_domains": _normalize_domain_list(internet.get("allowed_domains", [])),
        "blocked_domains": _normalize_domain_list(internet.get("blocked_domains", [])),
    }


def _normalize_self_knowledge(self_knowledge):
    if not isinstance(self_knowledge, dict):
        self_knowledge = {}

    return {
        "last_analyzed_at": _coerce_text(
            self_knowledge.get("last_analyzed_at", ""),
            MAX_SELF_KNOWLEDGE_TIMESTAMP_CHARS,
        ).strip(),
        "summary": _coerce_text(
            self_knowledge.get("summary", ""),
            MAX_SELF_KNOWLEDGE_SUMMARY_CHARS,
        ).strip(),
        "source_signature": _coerce_text(
            self_knowledge.get("source_signature", ""),
            MAX_SELF_KNOWLEDGE_SOURCE_SIGNATURE_CHARS,
        ).strip(),
    }


def _normalize_notification_channels(value):
    if isinstance(value, str):
        raw_channels = [item.strip().lower() for item in re.split(r"[\n,;]+", value)]
    elif isinstance(value, list):
        raw_channels = [str(item).strip().lower() for item in value]
    else:
        raw_channels = []

    channels = []
    for channel in raw_channels:
        if channel in VALID_NOTIFICATION_CHANNELS and channel not in channels:
            channels.append(channel)

    return channels or ["windows"]


def _normalize_notifications(notifications):
    defaults = default_state()["notifications"]
    if not isinstance(notifications, dict):
        notifications = {}

    ntfy = notifications.get("ntfy", {})
    if not isinstance(ntfy, dict):
        ntfy = {}

    telegram = notifications.get("telegram", {})
    if not isinstance(telegram, dict):
        telegram = {}

    priority = _coerce_text(ntfy.get("priority", defaults["ntfy"]["priority"]), 20).strip().lower()
    if priority not in VALID_NTFY_PRIORITIES:
        priority = defaults["ntfy"]["priority"]

    try:
        timeout_seconds = max(
            1,
            min(
                60,
                int(ntfy.get("timeout_seconds", defaults["ntfy"]["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        timeout_seconds = defaults["ntfy"]["timeout_seconds"]

    try:
        telegram_timeout_seconds = max(
            1,
            min(
                60,
                int(telegram.get("timeout_seconds", defaults["telegram"]["timeout_seconds"])),
            ),
        )
    except (TypeError, ValueError):
        telegram_timeout_seconds = defaults["telegram"]["timeout_seconds"]

    try:
        telegram_poll_timeout_seconds = max(
            1,
            min(
                60,
                int(
                    telegram.get(
                        "poll_timeout_seconds",
                        defaults["telegram"]["poll_timeout_seconds"],
                    )
                ),
            ),
        )
    except (TypeError, ValueError):
        telegram_poll_timeout_seconds = defaults["telegram"]["poll_timeout_seconds"]

    try:
        telegram_last_update_id = max(
            0,
            int(telegram.get("last_update_id", defaults["telegram"]["last_update_id"])),
        )
    except (TypeError, ValueError):
        telegram_last_update_id = defaults["telegram"]["last_update_id"]

    raw_pending_power = telegram.get("pending_power_confirmation", {})
    if not isinstance(raw_pending_power, dict):
        raw_pending_power = {}
    pending_action = _coerce_text(raw_pending_power.get("action", ""), 20).strip().lower()
    if pending_action not in {"shutdown", "restart"}:
        pending_action = ""
    try:
        pending_delay = max(0, min(3600, int(raw_pending_power.get("delay_seconds", 0))))
    except (TypeError, ValueError):
        pending_delay = 0

    return {
        "enabled": bool(notifications.get("enabled", defaults["enabled"])),
        "channels": _normalize_notification_channels(
            notifications.get("channels", defaults["channels"]),
        ),
        "ntfy": {
            "server": _coerce_text(
                ntfy.get("server", defaults["ntfy"]["server"]),
                200,
            ).strip() or defaults["ntfy"]["server"],
            "topic": _coerce_text(ntfy.get("topic", ""), 180).strip().strip("/"),
            "token": _coerce_text(ntfy.get("token", ""), 240).strip(),
            "priority": priority,
            "tags": _coerce_text(ntfy.get("tags", ""), 120).strip(),
            "timeout_seconds": timeout_seconds,
        },
        "telegram": {
            "api_base": _coerce_text(
                telegram.get("api_base", defaults["telegram"]["api_base"]),
                200,
            ).strip() or defaults["telegram"]["api_base"],
            "bot_token": _coerce_text(telegram.get("bot_token", ""), 240).strip(),
            "chat_id": _coerce_text(telegram.get("chat_id", ""), 80).strip(),
            "timeout_seconds": telegram_timeout_seconds,
            "poll_timeout_seconds": telegram_poll_timeout_seconds,
            "last_update_id": telegram_last_update_id,
            "pending_power_confirmation": {
                "action": pending_action,
                "delay_seconds": pending_delay,
                "token": _coerce_text(raw_pending_power.get("token", ""), 40).strip(),
                "chat_id": _coerce_text(raw_pending_power.get("chat_id", ""), 80).strip(),
                "requested_at": _coerce_text(raw_pending_power.get("requested_at", ""), 80).strip(),
            },
        },
    }


def _communication_value(value, aliases: dict[str, str], valid_values: set[str], default: str) -> str:
    rendered = _coerce_text(value, 40).strip().lower().replace("-", "_").replace(" ", "_")
    rendered = aliases.get(rendered, rendered)
    return rendered if rendered in valid_values else default


def _normalize_communication_settings(communication):
    defaults = default_state()["communication"]
    if not isinstance(communication, dict):
        communication = {}

    tone_aliases = {
        "calido": "warm_brief",
        "cálido": "warm_brief",
        "breve": "warm_brief",
        "warm": "warm_brief",
        "warm_brief": "warm_brief",
        "humano": "human",
        "human": "human",
        "directo": "direct",
        "operativo": "direct",
        "direct": "direct",
    }
    detail_aliases = {
        "breve": "brief",
        "corto": "brief",
        "brief": "brief",
        "balanceado": "balanced",
        "medio": "balanced",
        "balanced": "balanced",
        "detallado": "detailed",
        "detalle": "detailed",
        "detailed": "detailed",
    }
    proactivity_aliases = {
        "baja": "low",
        "low": "low",
        "moderada": "moderate",
        "moderado": "moderate",
        "media": "moderate",
        "moderate": "moderate",
        "alta": "high",
        "high": "high",
    }
    return {
        "tone": _communication_value(
            communication.get("tone", defaults["tone"]),
            tone_aliases,
            VALID_COMMUNICATION_TONES,
            defaults["tone"],
        ),
        "detail_level": _communication_value(
            communication.get("detail_level", defaults["detail_level"]),
            detail_aliases,
            VALID_COMMUNICATION_DETAIL_LEVELS,
            defaults["detail_level"],
        ),
        "proactivity": _communication_value(
            communication.get("proactivity", defaults["proactivity"]),
            proactivity_aliases,
            VALID_COMMUNICATION_PROACTIVITY,
            defaults["proactivity"],
        ),
    }


def _normalize_voice_settings(voice):
    defaults = default_state()["voice"]
    if not isinstance(voice, dict):
        voice = {}

    language = _coerce_text(
        voice.get("language", defaults["language"]),
        MAX_VOICE_LANGUAGE_CHARS,
    ).strip().lower()[:MAX_VOICE_LANGUAGE_CHARS]
    if not language:
        language = defaults["language"]

    compute_type = _coerce_text(
        voice.get("stt_compute_type", defaults["stt_compute_type"]),
        MAX_VOICE_STT_COMPUTE_TYPE_CHARS,
    ).strip().lower()[:MAX_VOICE_STT_COMPUTE_TYPE_CHARS]
    if compute_type not in VALID_VOICE_STT_COMPUTE_TYPES:
        compute_type = defaults["stt_compute_type"]

    reply_mode = _coerce_text(
        voice.get("telegram_reply_mode", defaults["telegram_reply_mode"]),
        20,
    ).strip().lower()[:20]
    if reply_mode not in VALID_VOICE_TELEGRAM_REPLY_MODES:
        reply_mode = defaults["telegram_reply_mode"]

    tts_provider = _coerce_text(
        voice.get("tts_provider", defaults["tts_provider"]),
        MAX_VOICE_TTS_PROVIDER_CHARS,
    ).strip().lower()[:MAX_VOICE_TTS_PROVIDER_CHARS]
    if tts_provider == "sistema":
        tts_provider = "system"
    # Migracion legacy: kokoro/piper ya no existen -> voz neural edge-tts.
    if tts_provider in ("kokoro", "piper"):
        tts_provider = "edge"
    if tts_provider not in VALID_VOICE_TTS_PROVIDERS:
        tts_provider = defaults["tts_provider"]

    try:
        max_audio_seconds = int(voice.get("max_audio_seconds", defaults["max_audio_seconds"]))
    except (TypeError, ValueError):
        max_audio_seconds = defaults["max_audio_seconds"]
    max_audio_seconds = max(
        MIN_VOICE_MAX_AUDIO_SECONDS,
        min(MAX_VOICE_MAX_AUDIO_SECONDS, max_audio_seconds),
    )

    try:
        tts_rate = int(voice.get("tts_rate", defaults["tts_rate"]))
    except (TypeError, ValueError):
        tts_rate = defaults["tts_rate"]
    tts_rate = max(MIN_VOICE_TTS_RATE, min(MAX_VOICE_TTS_RATE, tts_rate))

    try:
        browser_tts_rate = float(voice.get("browser_tts_rate", defaults["browser_tts_rate"]))
    except (TypeError, ValueError):
        browser_tts_rate = defaults["browser_tts_rate"]
    browser_tts_rate = max(MIN_VOICE_BROWSER_TTS_RATE, min(MAX_VOICE_BROWSER_TTS_RATE, browser_tts_rate))

    try:
        browser_tts_pitch = float(voice.get("browser_tts_pitch", defaults["browser_tts_pitch"]))
    except (TypeError, ValueError):
        browser_tts_pitch = defaults["browser_tts_pitch"]
    browser_tts_pitch = max(MIN_VOICE_BROWSER_TTS_PITCH, min(MAX_VOICE_BROWSER_TTS_PITCH, browser_tts_pitch))

    try:
        edge_rate = int(voice.get("edge_rate", defaults["edge_rate"]))
    except (TypeError, ValueError):
        edge_rate = defaults["edge_rate"]
    edge_rate = max(MIN_VOICE_EDGE_RATE, min(MAX_VOICE_EDGE_RATE, edge_rate))

    try:
        edge_pitch = int(voice.get("edge_pitch", defaults["edge_pitch"]))
    except (TypeError, ValueError):
        edge_pitch = defaults["edge_pitch"]
    edge_pitch = max(MIN_VOICE_EDGE_PITCH, min(MAX_VOICE_EDGE_PITCH, edge_pitch))

    try:
        edge_volume = int(voice.get("edge_volume", defaults["edge_volume"]))
    except (TypeError, ValueError):
        edge_volume = defaults["edge_volume"]
    edge_volume = max(MIN_VOICE_EDGE_VOLUME, min(MAX_VOICE_EDGE_VOLUME, edge_volume))

    live_defaults = defaults["live_conversation"]
    live = voice.get("live_conversation", {})
    if not isinstance(live, dict):
        live = {}
    raw_surfaces = live.get("surfaces", live_defaults["surfaces"])
    if not isinstance(raw_surfaces, list):
        raw_surfaces = live_defaults["surfaces"]
    live_surfaces = []
    for surface in raw_surfaces:
        cleaned_surface = _coerce_text(surface, 20).strip().lower()
        if cleaned_surface in {"desktop", "mobile"} and cleaned_surface not in live_surfaces:
            live_surfaces.append(cleaned_surface)
    if not live_surfaces:
        live_surfaces = list(live_defaults["surfaces"])

    try:
        live_silence_ms = int(live.get("silence_ms", live_defaults["silence_ms"]))
    except (TypeError, ValueError):
        live_silence_ms = live_defaults["silence_ms"]
    live_silence_ms = max(MIN_VOICE_LIVE_SILENCE_MS, min(MAX_VOICE_LIVE_SILENCE_MS, live_silence_ms))

    try:
        live_max_turn_seconds = int(live.get("max_turn_seconds", live_defaults["max_turn_seconds"]))
    except (TypeError, ValueError):
        live_max_turn_seconds = live_defaults["max_turn_seconds"]
    live_max_turn_seconds = max(
        MIN_VOICE_LIVE_MAX_TURN_SECONDS,
        min(MAX_VOICE_LIVE_MAX_TURN_SECONDS, live_max_turn_seconds),
    )

    try:
        live_hold_seconds = int(live.get("hold_seconds", live_defaults["hold_seconds"]))
    except (TypeError, ValueError):
        live_hold_seconds = live_defaults["hold_seconds"]
    live_hold_seconds = max(
        MIN_VOICE_LIVE_HOLD_SECONDS,
        min(MAX_VOICE_LIVE_HOLD_SECONDS, live_hold_seconds),
    )

    live_conversation = {
        "enabled": bool(live.get("enabled", live_defaults["enabled"])),
        "wake_phrase": _coerce_text(
            live.get("wake_phrase", live_defaults["wake_phrase"]),
            MAX_VOICE_LIVE_WAKE_PHRASE_CHARS,
        ).strip()[:MAX_VOICE_LIVE_WAKE_PHRASE_CHARS] or live_defaults["wake_phrase"],
        "surfaces": live_surfaces,
        "wake_stt_model": _coerce_text(
            live.get("wake_stt_model", live_defaults["wake_stt_model"]),
            MAX_VOICE_LIVE_STT_MODEL_CHARS,
        ).strip()[:MAX_VOICE_LIVE_STT_MODEL_CHARS] or live_defaults["wake_stt_model"],
        "turn_stt_model": _coerce_text(
            live.get("turn_stt_model", live_defaults["turn_stt_model"]),
            MAX_VOICE_LIVE_STT_MODEL_CHARS,
        ).strip()[:MAX_VOICE_LIVE_STT_MODEL_CHARS],
        "silence_ms": live_silence_ms,
        "max_turn_seconds": live_max_turn_seconds,
        "continuous": bool(live.get("continuous", live_defaults["continuous"])),
        "hold_seconds": live_hold_seconds,
        "cues": bool(live.get("cues", live_defaults["cues"])),
        "auto_speak": bool(live.get("auto_speak", live_defaults["auto_speak"])),
        "barge_in": bool(live.get("barge_in", live_defaults["barge_in"])),
        "save_audio_debug": bool(live.get("save_audio_debug", live_defaults["save_audio_debug"])),
    }

    return {
        "enabled": bool(voice.get("enabled", defaults["enabled"])),
        "language": language,
        "stt_model": _coerce_text(
            voice.get("stt_model", defaults["stt_model"]),
            MAX_VOICE_STT_MODEL_CHARS,
        ).strip()[:MAX_VOICE_STT_MODEL_CHARS] or defaults["stt_model"],
        "stt_compute_type": compute_type,
        "max_audio_seconds": max_audio_seconds,
        "tts_rate": tts_rate,
        "tts_voice_id": _coerce_text(
            voice.get("tts_voice_id", defaults["tts_voice_id"]),
            MAX_VOICE_TTS_VOICE_ID_CHARS,
        ).strip()[:MAX_VOICE_TTS_VOICE_ID_CHARS],
        "tts_provider": tts_provider,
        "edge_voice": _coerce_text(
            voice.get("edge_voice", defaults["edge_voice"]),
            MAX_VOICE_EDGE_VOICE_CHARS,
        ).strip()[:MAX_VOICE_EDGE_VOICE_CHARS] or defaults["edge_voice"],
        "edge_rate": edge_rate,
        "edge_pitch": edge_pitch,
        "edge_volume": edge_volume,
        "browser_voice_name": _coerce_text(
            voice.get("browser_voice_name", defaults["browser_voice_name"]),
            MAX_VOICE_BROWSER_VOICE_NAME_CHARS,
        ).strip()[:MAX_VOICE_BROWSER_VOICE_NAME_CHARS],
        "browser_tts_rate": browser_tts_rate,
        "browser_tts_pitch": browser_tts_pitch,
        "telegram_reply_mode": reply_mode,
        "live_conversation": live_conversation,
    }


def _normalize_metadata(value, limit: int = MAX_SOCIAL_METADATA_CHARS) -> dict:
    if not isinstance(value, dict):
        return {}

    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return {}

    if len(rendered) > limit:
        return {"summary": rendered[:limit]}

    return value


def _normalize_social_settings(settings):
    defaults = default_state()["social"]["settings"]
    if not isinstance(settings, dict):
        settings = {}

    meta_graph_version = _coerce_text(
        settings.get("meta_graph_version", defaults["meta_graph_version"]),
        20,
    ).strip()
    if not re.fullmatch(r"v\d+\.\d+", meta_graph_version):
        meta_graph_version = defaults["meta_graph_version"]

    linkedin_version = _coerce_text(
        settings.get("linkedin_version", defaults["linkedin_version"]),
        20,
    ).strip()
    if not re.fullmatch(r"\d{6}", linkedin_version):
        linkedin_version = defaults["linkedin_version"]

    return {
        "meta_graph_version": meta_graph_version,
        "linkedin_version": linkedin_version,
        "require_confirmation": bool(settings.get("require_confirmation", defaults["require_confirmation"])),
    }


def _normalize_social_platform(value: str, default: str = "facebook_page") -> str:
    platform = str(value).strip().lower()
    return platform if platform in VALID_SOCIAL_PLATFORMS else default


def _normalize_social_account(account):
    if not isinstance(account, dict):
        return None

    account_id = _coerce_text(
        account.get("id") or _fallback_id("social-account", account.get("display_name", "")),
        64,
    ).strip()
    account_type = str(account.get("account_type", "facebook_page")).strip().lower()
    if account_type not in VALID_SOCIAL_ACCOUNT_TYPES:
        return None

    default_platform_by_account_type = {
        "facebook_page": "facebook_page",
        "facebook_personal": "facebook_personal",
        "instagram_professional": "instagram",
        "linkedin_member": "linkedin",
        "linkedin_organization": "linkedin",
    }
    platform = _normalize_social_platform(
        account.get("platform", default_platform_by_account_type.get(account_type, "facebook_page"))
    )
    display_name = _coerce_text(account.get("display_name", ""), 160).strip()
    external_id = _coerce_text(account.get("external_id", ""), 160).strip()
    token_ref = _coerce_text(account.get("token_ref", ""), 160).strip()

    if not account_id or not display_name:
        return None

    return {
        "id": account_id,
        "platform": platform,
        "account_type": account_type,
        "display_name": display_name,
        "external_id": external_id,
        "token_ref": token_ref,
        "scopes": _normalize_string_list(account.get("scopes", []), 40, 80),
        "connected_at": _coerce_text(account.get("connected_at", ""), 80).strip(),
        "expires_at": _coerce_text(account.get("expires_at", ""), 80).strip(),
        "metadata": _normalize_metadata(account.get("metadata", {})),
    }


def _normalize_social_draft(draft):
    if not isinstance(draft, dict):
        return None

    title = _coerce_text(draft.get("title", ""), 180).strip()
    body = _coerce_text(draft.get("body", ""), MAX_SOCIAL_TEXT_CHARS).strip()
    if not title and not body:
        return None

    status = str(draft.get("status", "draft")).strip().lower()
    if status not in VALID_SOCIAL_DRAFT_STATUS:
        status = "draft"

    return {
        "id": _coerce_text(draft.get("id") or _fallback_id("draft", title or body), 64).strip(),
        "title": title or "Draft social",
        "platform": _normalize_social_platform(draft.get("platform", "facebook_page")),
        "target_account_id": _coerce_text(draft.get("target_account_id", ""), 64).strip(),
        "body": body,
        "link_url": _coerce_text(draft.get("link_url", ""), 500).strip(),
        "media_url": _coerce_text(draft.get("media_url", ""), 500).strip(),
        "media_path": _coerce_text(draft.get("media_path", ""), 500).strip(),
        "media_type": _coerce_text(draft.get("media_type", ""), 40).strip().lower(),
        "alt_text": _coerce_text(draft.get("alt_text", ""), 500).strip(),
        "hashtags": _normalize_string_list(draft.get("hashtags", []), 40, 80),
        "status": status,
        "created_at": _coerce_text(draft.get("created_at", ""), 80).strip(),
        "scheduled_for": _coerce_text(draft.get("scheduled_for", ""), 80).strip(),
        "metadata": _normalize_metadata(draft.get("metadata", {})),
    }


def _normalize_social_publication(publication):
    if not isinstance(publication, dict):
        return None

    title = _coerce_text(publication.get("title", ""), 180).strip()
    body = _coerce_text(publication.get("body", ""), MAX_SOCIAL_TEXT_CHARS).strip()
    platform = _normalize_social_platform(publication.get("platform", "facebook_page"))
    publication_id = _coerce_text(
        publication.get("id") or _fallback_id("pub", title or body or platform),
        64,
    ).strip()
    if not publication_id or (not body and not publication.get("link_url") and not publication.get("media_url")):
        return None

    status = str(publication.get("status", "pending_confirmation")).strip().lower()
    if status not in VALID_SOCIAL_PUBLICATION_STATUS:
        status = "pending_confirmation"

    return {
        "id": publication_id,
        "draft_id": _coerce_text(publication.get("draft_id", ""), 64).strip(),
        "title": title or "Publicacion social",
        "platform": platform,
        "target_account_id": _coerce_text(publication.get("target_account_id", ""), 64).strip(),
        "target_label": _coerce_text(publication.get("target_label", ""), 180).strip(),
        "body": body,
        "link_url": _coerce_text(publication.get("link_url", ""), 500).strip(),
        "media_url": _coerce_text(publication.get("media_url", ""), 500).strip(),
        "media_path": _coerce_text(publication.get("media_path", ""), 500).strip(),
        "media_type": _coerce_text(publication.get("media_type", ""), 40).strip().lower(),
        "alt_text": _coerce_text(publication.get("alt_text", ""), 500).strip(),
        "hashtags": _normalize_string_list(publication.get("hashtags", []), 40, 80),
        "status": status,
        "confirmation_phrase": _coerce_text(
            publication.get("confirmation_phrase", f"PUBLICAR {publication_id}"),
            100,
        ).strip() or f"PUBLICAR {publication_id}",
        "created_at": _coerce_text(publication.get("created_at", ""), 80).strip(),
        "scheduled_for": _coerce_text(publication.get("scheduled_for", ""), 80).strip(),
        "published_at": _coerce_text(publication.get("published_at", ""), 80).strip(),
        "external_post_id": _coerce_text(publication.get("external_post_id", ""), 200).strip(),
        "last_error": _coerce_text(publication.get("last_error", ""), 600).strip(),
        "metadata": _normalize_metadata(publication.get("metadata", {})),
    }


def _normalize_social_media_entry(entry):
    if not isinstance(entry, dict):
        return None
    path = _coerce_text(entry.get("path", ""), 500).strip()
    file_id = _coerce_text(entry.get("telegram_file_id", ""), 300).strip()
    if not path and not file_id:
        return None
    entry_id = _coerce_text(entry.get("id") or _fallback_id("media", path or file_id), 64).strip()
    if not entry_id:
        return None
    return {
        "id": entry_id,
        "path": path,
        "telegram_file_id": file_id,
        "source": _coerce_text(entry.get("source", "telegram"), 40).strip().lower() or "telegram",
        "chat_id": _coerce_text(entry.get("chat_id", ""), 64).strip(),
        "caption": _coerce_text(entry.get("caption", ""), 500).strip(),
        "media_type": _coerce_text(entry.get("media_type", "image"), 40).strip().lower() or "image",
        "alt_text": _coerce_text(entry.get("alt_text", ""), 500).strip(),
        "received_at": _coerce_text(entry.get("received_at", ""), 80).strip(),
    }


def _normalize_social(social):
    defaults = default_state()["social"]
    if not isinstance(social, dict):
        social = {}

    accounts = []
    for account in social.get("accounts", []):
        normalized_account = _normalize_social_account(account)
        if normalized_account:
            accounts.append(normalized_account)
            if len(accounts) >= MAX_SOCIAL_ITEMS:
                break

    drafts = []
    for draft in social.get("drafts", []):
        normalized_draft = _normalize_social_draft(draft)
        if normalized_draft:
            drafts.append(normalized_draft)
            if len(drafts) >= MAX_SOCIAL_ITEMS:
                break

    pending_publications = []
    for publication in social.get("pending_publications", []):
        normalized_publication = _normalize_social_publication(publication)
        if normalized_publication:
            pending_publications.append(normalized_publication)
            if len(pending_publications) >= MAX_SOCIAL_ITEMS:
                break

    history = []
    for publication in social.get("history", []):
        normalized_publication = _normalize_social_publication(publication)
        if normalized_publication:
            history.append(normalized_publication)
            if len(history) >= MAX_SOCIAL_ITEMS:
                break

    media_inbox = []
    for entry in social.get("media_inbox", []):
        normalized_entry = _normalize_social_media_entry(entry)
        if normalized_entry:
            media_inbox.append(normalized_entry)
    # Conservar solo las mas recientes (las ultimas de la lista).
    media_inbox = media_inbox[-MAX_SOCIAL_MEDIA_INBOX:]

    return {
        "settings": _normalize_social_settings(social.get("settings", defaults["settings"])),
        "accounts": accounts,
        "drafts": drafts,
        "pending_publications": pending_publications,
        "history": history,
        "media_inbox": media_inbox,
    }


def normalize_state(state):
    normalized = default_state()

    if not isinstance(state, dict):
        return normalized

    try:
        source_schema_version = int(state.get("state_schema_version", 1))
    except (TypeError, ValueError):
        source_schema_version = 1
    migrate_legacy_cycles = source_schema_version < 2
    migrate_legacy_step_limit = source_schema_version < 3
    normalized["state_schema_version"] = STATE_SCHEMA_VERSION

    goal = str(state.get("goal", normalized["goal"])).strip()
    if goal in LEGACY_DEFAULT_GOALS:
        goal = DEFAULT_GOAL
    normalized["goal"] = goal

    try:
        normalized["cycle_count"] = max(0, int(state.get("cycle_count", 0)))
    except (TypeError, ValueError):
        normalized["cycle_count"] = 0

    normalized["last_result"] = str(state.get("last_result", ""))
    normalized["profile"] = _normalize_profile(state.get("profile", {}))
    normalized["current_plan"] = _normalize_plan(state.get("current_plan", []))
    normalized["awaiting_user_input"] = _normalize_awaiting_user_input(
        state.get("awaiting_user_input", {}),
    )
    normalized["autonomy"] = _normalize_autonomy(
        state.get("autonomy", {}),
        migrate_legacy_cycles=migrate_legacy_cycles,
        migrate_legacy_step_limit=migrate_legacy_step_limit,
    )
    normalized["coding"] = _normalize_coding(state.get("coding", {}))
    normalized["evolution"] = _normalize_evolution(state.get("evolution", {}))
    normalized["vision"] = _normalize_vision(state.get("vision", {}))
    normalized["computer_control"] = _normalize_computer_control(state.get("computer_control", {}))
    normalized["memory_protection"] = _normalize_memory_protection(state.get("memory_protection", {}))
    normalized["model_provider"] = _normalize_model_provider(state)
    normalized["ollama"] = dict(normalized["model_provider"][MODEL_PROVIDER_OLLAMA])
    normalized["service"] = _normalize_service(
        state.get("service", {}),
        migrate_legacy_cycles=migrate_legacy_cycles,
    )
    normalized["ui"] = _normalize_ui(state.get("ui", {}))
    normalized["runtime"] = _normalize_runtime(state.get("runtime", {}))
    normalized["local_context"] = _normalize_local_context(state.get("local_context", {}))
    normalized["internet"] = _normalize_internet(state.get("internet", {}))
    normalized["self_knowledge"] = _normalize_self_knowledge(state.get("self_knowledge", {}))
    normalized["notifications"] = _normalize_notifications(state.get("notifications", {}))
    normalized["communication"] = _normalize_communication_settings(state.get("communication", {}))
    normalized["voice"] = _normalize_voice_settings(state.get("voice", {}))
    normalized["social"] = _normalize_social(state.get("social", {}))

    raw_messages = state.get("messages", [])
    if isinstance(raw_messages, list):
        for message in raw_messages:
            normalized_message = _normalize_message(message)
            if normalized_message:
                normalized["messages"].append(normalized_message)

    raw_notes = state.get("notes", [])
    if isinstance(raw_notes, list):
        for note in raw_notes:
            normalized_note = _normalize_note(note)
            if normalized_note:
                normalized["notes"].append(normalized_note)

    raw_tasks = state.get("tasks", [])
    if isinstance(raw_tasks, list):
        for task in raw_tasks:
            normalized_task = _normalize_task(task)
            if normalized_task:
                normalized["tasks"].append(normalized_task)

    raw_idea_projects = state.get("idea_projects", [])
    if isinstance(raw_idea_projects, list):
        for project in raw_idea_projects:
            normalized_project = _normalize_idea_project(project)
            if normalized_project:
                normalized["idea_projects"].append(normalized_project)
                if len(normalized["idea_projects"]) >= MAX_IDEA_PROJECTS:
                    break

    return sanitize_unicode_value(normalized)


def render_state_summary(
    state,
    task_limit: int = 8,
    note_limit: int = 3,
    include_runtime: bool = True,
    include_last_result: bool = True,
) -> str:
    normalized = normalize_state(state)
    profile = normalized["profile"]
    model_provider = normalized["model_provider"]
    active_provider_name = model_provider["default"]
    active_model_settings = model_provider[active_provider_name]
    pending_tasks = [
        task for task in normalized["tasks"]
        if task["status"] in {"pending", "in_progress", "blocked"}
    ]
    done_tasks = [task for task in normalized["tasks"] if task["status"] == "done"]
    auto_cycles_text = format_cycle_count(normalized["autonomy"]["auto_cycles_default"])
    proactive_cycles_text = format_cycle_count(normalized["service"]["proactive"]["cycles"])
    max_steps = normalized["autonomy"]["max_steps_per_cycle"]
    max_steps_text = (
        "sin limite de pasos/ciclo"
        if max_steps is None
        else f"{max_steps} pasos/ciclo"
    )

    lines = [
        f"Objetivo: {normalized['goal']}",
        (
            "Identidad: "
            "asistente=Yarbis; "
            f"usuario={profile['name'] or 'sin nombre definido'}"
        ),
        f"Ciclos ejecutados: {normalized['cycle_count']}",
        (
            "Autonomia: "
            f"{max_steps_text}, "
            f"{auto_cycles_text} por defecto"
        ),
        (
            "Coding: "
            f"workspace={normalized['coding']['workspace_path'] or '-'}, "
            f"modo={normalized['coding']['mode']}, "
            f"propuestas_pendientes={len(normalized['coding']['pending_proposal_ids'])}, "
            f"validacion={normalized['coding']['validation_command'] or '-'}"
        ),
        (
            "Proveedor de modelo: "
            f"{active_provider_name}, "
            f"modelo={active_model_settings['model'] or '-'}, "
            f"fallbacks={', '.join(active_model_settings['fallback_models']) or '-'}, "
            f"host={active_model_settings['host'] or 'local'}, "
            f"timeout={active_model_settings['timeout_seconds']}s"
        ),
        (
            "Ollama: "
            f"modelo={normalized['ollama']['model']}, "
            f"fallbacks={', '.join(normalized['ollama']['fallback_models']) or '-'}, "
            f"host={normalized['ollama']['host'] or 'local'}, "
            f"timeout={normalized['ollama']['timeout_seconds']}s"
        ),
        (
            "Pulso proactivo: "
            f"{'activo' if normalized['service']['proactive']['enabled'] else 'desactivado'}, "
            f"modelo={normalized['service']['proactive']['model'] or 'modelo principal'}, "
            f"{proactive_cycles_text} cada "
            f"{normalized['service']['proactive']['interval_seconds']}s, "
            f"espera inicial {normalized['service']['proactive']['start_delay_seconds']}s"
        ),
    ]

    local_context = normalized["local_context"]
    local_context_status = "activo" if local_context["enabled"] else "desactivado"
    lines.append(
        "Contexto local: "
        f"{local_context_status}, modo={local_context['mode']}, "
        f"muestra cada {local_context['sample_interval_seconds']}s, "
        f"vigencia {local_context['max_snapshot_age_seconds']}s, "
        f"proceso={'si' if local_context['include_process_name'] else 'no'}, "
        f"titulos={'si' if local_context['include_window_title'] else 'no'}, "
        f"workspace={'si' if local_context['include_workspace_changes'] else 'no'}, "
        f"sistema={'si' if local_context['include_system_health'] else 'no'}"
    )

    voice_settings = normalized["voice"]
    live_voice = voice_settings.get("live_conversation", {})
    live_text = "live=off"
    if isinstance(live_voice, dict) and live_voice.get("enabled"):
        live_text = (
            f"live=on/{live_voice.get('wake_phrase', DEFAULT_VOICE_LIVE_WAKE_PHRASE)}"
        )
    lines.append(
        "Voz: "
        f"{'activa' if voice_settings['enabled'] else 'desactivada'}, "
        f"idioma={voice_settings['language']}, "
        f"stt={voice_settings['stt_model']} ({voice_settings['stt_compute_type']}), "
        f"tts={voice_settings.get('tts_provider', DEFAULT_VOICE_TTS_PROVIDER)}, "
        f"telegram={voice_settings['telegram_reply_mode']}, "
        f"{live_text}"
    )

    communication = normalized["communication"]
    lines.append(
        "Comunicacion: "
        f"tono={communication['tone']}, "
        f"detalle={communication['detail_level']}, "
        f"proactividad={communication['proactivity']}"
    )

    if include_last_result:
        lines.append(
            f"Ultimo resultado: {normalized['last_result'] or 'Sin resultados previos.'}"
        )

    if include_runtime:
        thinking = normalized["runtime"]["thinking"]
        stop_requested = normalized["runtime"]["stop_requested"]
        if thinking["active"]:
            started_at = f" desde {thinking['started_at']}" if thinking["started_at"] else ""
            lines.append(
                f"Estado operativo: estoy pensando en {thinking['label']}{started_at}."
            )
        else:
            lines.append("Estado operativo: listo.")
        if stop_requested["active"]:
            requested_at = f" en {stop_requested['requested_at']}" if stop_requested["requested_at"] else ""
            lines.append(
                "Parada solicitada: activa"
                f"{requested_at}"
                + (f" por {stop_requested['source']}" if stop_requested["source"] else "")
                + "."
            )

    lines.append(
        f"Perfil: nombre={profile['name'] or '-'}, rol={profile['role'] or '-'}"
    )
    lines.append(
        "Preferencias: "
        + (", ".join(profile["preferences"]) if profile["preferences"] else "Sin definir.")
    )
    lines.append(
        "Restricciones: "
        + (", ".join(profile["constraints"]) if profile["constraints"] else "Sin definir.")
    )

    if normalized["current_plan"]:
        lines.append("Plan actual:")
        for index, item in enumerate(normalized["current_plan"], start=1):
            lines.append(f"{index}. {item}")
    else:
        lines.append("Plan actual: sin plan explicito.")

    open_projects = [
        project for project in normalized["idea_projects"]
        if project["status"] not in {"done", "archived"}
    ]
    if open_projects:
        lines.append("Proyectos de ideas abiertos:")
        for project in open_projects[:5]:
            lines.append(
                f"- [{project['id']}] {project['title']} "
                f"(tipo={project['kind']}, estado={project['status']})"
            )
            if project["summary"]:
                lines.append(f"  Resumen: {project['summary']}")
            if project["selected_direction"]:
                lines.append(f"  Direccion elegida: {project['selected_direction']}")
            elif project["creative_directions"]:
                lines.append(
                    "  Direcciones creativas: "
                    + "; ".join(project["creative_directions"][:3])
                )
            if project["open_questions"]:
                lines.append(
                    "  Preguntas abiertas: "
                    + "; ".join(project["open_questions"][:3])
                )
            if project["next_steps"]:
                lines.append(
                    "  Proximos pasos: "
                    + "; ".join(project["next_steps"][:3])
                )
    else:
        lines.append("Proyectos de ideas abiertos: ninguno.")

    awaiting_user_input = normalized["awaiting_user_input"]
    if awaiting_user_input["pending"]:
        lines.append(
            "Esperando respuesta del usuario: "
            + awaiting_user_input["question"]
        )
        if awaiting_user_input["reason"]:
            lines.append("Motivo de la pausa: " + awaiting_user_input["reason"])
        if awaiting_user_input["fields"]:
            lines.append(
                "Datos faltantes: " + ", ".join(awaiting_user_input["fields"])
            )
    else:
        lines.append("Esperando respuesta del usuario: no.")

    internet_settings = normalized["internet"]
    lines.append(
        "Internet: "
        f"modo={internet_settings['mode']}, "
        f"proveedor={internet_settings['provider']}, "
        f"max_resultados={internet_settings['max_search_results']}, "
        f"max_pagina={internet_settings['max_page_chars']} chars, "
        f"timeout={internet_settings['request_timeout_seconds']}s"
    )
    if internet_settings["allowed_domains"]:
        lines.append(
            "Internet permitido solo para: "
            + ", ".join(internet_settings["allowed_domains"])
        )
    if internet_settings["blocked_domains"]:
        lines.append(
            "Internet bloqueado para: "
            + ", ".join(internet_settings["blocked_domains"])
        )

    social = normalized["social"]
    pending_social = [
        publication
        for publication in social["pending_publications"]
        if publication["status"] == "pending_confirmation"
    ]
    lines.append(
        "Redes sociales: "
        f"{len(social['accounts'])} cuenta(s), "
        f"{len(social['drafts'])} draft(s), "
        f"{len(pending_social)} pendiente(s) de confirmacion, "
        f"Meta={social['settings']['meta_graph_version']}, "
        f"LinkedIn={social['settings']['linkedin_version']}"
    )
    if pending_social:
        for publication in pending_social[:3]:
            lines.append(
                f"- Pendiente social [{publication['id']}]: "
                f"{publication['platform']} -> {publication['target_label'] or publication['target_account_id'] or 'sin destino'}; "
                f"confirmar con {publication['confirmation_phrase']}"
            )

    self_knowledge = normalized["self_knowledge"]
    if self_knowledge["last_analyzed_at"]:
        lines.append(
            "Autoconocimiento: "
            f"actualizado en {self_knowledge['last_analyzed_at']}."
        )
    else:
        lines.append("Autoconocimiento: pendiente de autoanalisis inicial.")

    protection = normalized["memory_protection"]
    protection_status = "activa" if protection["enabled"] else "desactivada"
    mirror_status = "con espejo" if protection["mirror_dir"] else "sin espejo externo"
    backup_status = "por cambio" if protection["backup_on_every_change"] else "manual"
    lines.append(
        "Proteccion de memoria: "
        f"{protection_status}, {backup_status}, {mirror_status}."
    )
    if protection["last_error"]:
        lines.append(f"Ultimo aviso de proteccion: {protection['last_error']}")

    notification_settings = normalized["notifications"]
    notification_status = "activadas" if notification_settings["enabled"] else "desactivadas"
    lines.append(
        "Notificaciones: "
        f"{notification_status}, canales={', '.join(notification_settings['channels'])}"
    )
    if "telegram" in notification_settings["channels"]:
        telegram = notification_settings.get("telegram", {})
        if telegram.get("chat_id"):
            lines.append(f"Telegram: vinculado al chat {telegram['chat_id']}.")
        elif telegram.get("bot_token"):
            lines.append("Telegram: pendiente de vincular. Envia /start al bot para completar el enlace.")
        else:
            lines.append("Telegram: activado, pero falta configurar el bot token.")

    if pending_tasks:
        lines.append("Tareas abiertas:")
        for task in pending_tasks[:task_limit]:
            lines.append(
                f"- [{task['id']}] {task['title']} "
                f"(estado={task['status']}, prioridad={task['priority']})"
            )
    else:
        lines.append("Tareas abiertas: ninguna.")

    if done_tasks:
        lines.append(f"Tareas completadas registradas: {len(done_tasks)}")

    recent_notes = normalized["notes"][-note_limit:]
    if recent_notes:
        lines.append("Notas recientes:")
        for note in recent_notes:
            preview = note["content"][:140] if note["content"] else ""
            if note["content"] and len(note["content"]) > 140:
                preview += "..."
            lines.append(f"- [{note['id']}] {note['title']} ({note['category']}): {preview}")
    else:
        lines.append("Notas recientes: ninguna.")

    return "\n".join(lines)


def _state_file_path() -> Path:
    return Path(STATE_FILE)


def _state_file_uses_default_path() -> bool:
    return _state_file_path() == DEFAULT_STATE_FILE


def _runtime_dir_for_state() -> Path:
    if _state_file_uses_default_path():
        return WORKSPACE_ROOT / ".yarbis_runtime"
    return _state_file_path().parent / ".yarbis_runtime"


def _memory_backups_dir() -> Path:
    configured_dir = Path(MEMORY_BACKUPS_DIR)
    if configured_dir != DEFAULT_MEMORY_BACKUPS_DIR:
        return configured_dir
    if _state_file_uses_default_path():
        return DEFAULT_MEMORY_BACKUPS_DIR
    state_path = _state_file_path()
    if state_path.name == DEFAULT_STATE_FILE.name:
        return state_path.parent / ".yarbis_memory_backups"
    return state_path.parent / f".{state_path.stem}_memory_backups"


def _memory_protection_config_file() -> Path:
    configured_file = Path(MEMORY_PROTECTION_CONFIG_FILE)
    if configured_file != DEFAULT_MEMORY_PROTECTION_CONFIG_FILE:
        return configured_file
    return _runtime_dir_for_state() / "memory_protection.json"


def _load_memory_protection_runtime_config() -> dict:
    config_path = _memory_protection_config_file()
    try:
        payload = read_json_bom_safe(config_path)
    except (OSError, json.JSONDecodeError):
        return default_state()["memory_protection"]

    if not isinstance(payload, dict):
        return default_state()["memory_protection"]
    source = payload.get("memory_protection", payload)
    return _normalize_memory_protection(source)


def _write_memory_protection_runtime_config(settings: dict) -> None:
    normalized_settings = _normalize_memory_protection(settings)
    payload = {
        "memory_protection": {
            "enabled": normalized_settings["enabled"],
            "backup_on_every_change": normalized_settings["backup_on_every_change"],
            "mirror_dir": normalized_settings["mirror_dir"],
            "include_secrets": False,
            "retention": normalized_settings["retention"],
            "verify_after_write": normalized_settings["verify_after_write"],
            "auto_restore": normalized_settings["auto_restore"],
        }
    }
    config_path = _memory_protection_config_file()
    try:
        existing = read_json_bom_safe(config_path)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        existing = None
    if existing == payload:
        return

    try:
        memory_backup.write_json_atomic(
            config_path,
            payload,
            ensure_ascii=True,
            indent=2,
            verify=True,
        )
    except memory_backup.MemoryBackupError:
        pass


def _memory_backup_dirs_for_settings(settings: dict) -> list[Path]:
    backup_dirs = [_memory_backups_dir()]
    mirror_dir = str(settings.get("mirror_dir", "")).strip()
    if mirror_dir:
        backup_dirs.append(Path(mirror_dir).expanduser())
    return backup_dirs


def _write_state_file_atomic(normalized: dict, verify_after_write: bool = True) -> None:
    memory_backup.write_json_atomic(
        _state_file_path(),
        normalized,
        ensure_ascii=False,
        indent=2,
        verify=verify_after_write,
    )


def _preserve_unreadable_state_file(reason: str) -> str:
    state_path = _state_file_path()
    if not state_path.exists():
        return ""

    timestamp = memory_backup.utc_now().strftime("%Y%m%d-%H%M%S%f")
    recovery_dir = _runtime_dir_for_state() / "memory_recovery"
    target_path = recovery_dir / f"{state_path.name}.{reason}-{timestamp}.json"
    try:
        return str(memory_backup.copy_file_atomic(state_path, target_path))
    except memory_backup.MemoryBackupError:
        return ""


def _recovery_error_text(reason: str, error: Exception | str, preserved_path: str = "") -> str:
    rendered_error = str(error).strip()
    message = f"No pude leer {STATE_FILE} ({reason}): {rendered_error}."
    if preserved_path:
        message += f" Copia preservada: {preserved_path}."
    return message[:MAX_MEMORY_PROTECTION_ERROR_CHARS]


def _recover_state_from_backups_unlocked(reason: str, error: Exception | str, missing: bool = False):
    settings = _load_memory_protection_runtime_config()
    if not settings.get("auto_restore", True):
        return None

    latest = memory_backup.latest_valid_backup(
        backup_dirs=_memory_backup_dirs_for_settings(settings),
        normalizer=normalize_state,
    )
    if latest:
        preserved_path = "" if missing else _preserve_unreadable_state_file(reason)
        restored_state = normalize_state(latest["package"]["state"])
        restored_state["memory_protection"]["last_recovery_at"] = memory_backup.utc_now_text()
        restored_state["memory_protection"]["last_error"] = (
            f"Estado restaurado desde {latest['path']} por {reason}."
        )[:MAX_MEMORY_PROTECTION_ERROR_CHARS]
        if preserved_path:
            restored_state["memory_protection"]["last_error"] = (
                f"{restored_state['memory_protection']['last_error']} "
                f"Copia previa: {preserved_path}."
            )[:MAX_MEMORY_PROTECTION_ERROR_CHARS]
        _write_state_file_atomic(
            restored_state,
            verify_after_write=restored_state["memory_protection"]["verify_after_write"],
        )
        _write_memory_protection_runtime_config(restored_state["memory_protection"])
        return restored_state

    if missing:
        return None

    preserved_path = _preserve_unreadable_state_file(reason)
    fallback_state = default_state()
    fallback_state["memory_protection"]["last_error"] = _recovery_error_text(
        reason,
        error,
        preserved_path=preserved_path,
    )
    _write_state_file_atomic(fallback_state, verify_after_write=True)
    _write_memory_protection_runtime_config(fallback_state["memory_protection"])
    return fallback_state


def _load_state_unlocked():
    state_path = _state_file_path()
    if not state_path.exists():
        recovered_state = _recover_state_from_backups_unlocked(
            "missing",
            "state.json no existe",
            missing=True,
        )
        return recovered_state or default_state()

    had_bom = file_has_bom(state_path)
    try:
        # utf-8-sig tolera un BOM opcional (p.ej. si el estado se trasplanto con
        # PowerShell, que escribe UTF-8 CON BOM). Antes se usaba utf-8 estricto y
        # un BOM tumbaba una memoria perfectamente valida -> reset a "como nueva".
        with open(state_path, "r", encoding="utf-8-sig") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        recovered_state = _recover_state_from_backups_unlocked("corrupt", exc)
        return recovered_state or default_state()

    normalized = normalize_state(state)
    _write_memory_protection_runtime_config(normalized["memory_protection"])
    if had_bom:
        # Auto-sanado: reescribir sin BOM para que el archivo quede limpio.
        try:
            _write_state_file_atomic(normalized, verify_after_write=False)
        except Exception:
            pass
    return normalized


def _apply_memory_protection_after_save_unlocked(normalized: dict) -> dict:
    protected_state = normalize_state(normalized)
    settings = protected_state["memory_protection"]
    _write_memory_protection_runtime_config(settings)

    if not (settings["enabled"] and settings["backup_on_every_change"]):
        return protected_state

    if MEMORY_PROTECTION_AUTO_BACKUP_ASYNC and MEMORY_PROTECTION_MAINTENANCE_ASYNC:
        _schedule_memory_protection_auto_backup(protected_state)
        return protected_state

    return _run_memory_protection_auto_backup_sync(protected_state)


def _create_memory_protection_auto_backup(protected_state: dict) -> tuple[dict | None, str, dict | None]:
    settings = protected_state["memory_protection"]
    backup_result = None
    last_error = ""
    maintenance_job = None
    try:
        local_backups_dir = _memory_backups_dir()
        backup_result = memory_backup.write_backup_package(
            protected_state,
            backups_dir=local_backups_dir,
            normalizer=None,
            include_secrets=False,
            reason=memory_backup.AUTO_BACKUP_REASON,
        )
        maintenance_job = {
            "backup_path": backup_result["path"],
            "local_backups_dir": local_backups_dir,
            "mirror_dir": settings.get("mirror_dir", ""),
            "retention": dict(settings.get("retention", {})),
        }
    except memory_backup.MemoryBackupError as exc:
        last_error = str(exc)

    return backup_result, last_error, maintenance_job


def _run_memory_protection_auto_backup_sync(protected_state: dict) -> dict:
    protected_state = normalize_state(protected_state)
    backup_result, last_error, maintenance_job = _create_memory_protection_auto_backup(protected_state)

    if backup_result or last_error:
        protected_state["memory_protection"]["last_error"] = last_error[:MAX_MEMORY_PROTECTION_ERROR_CHARS]
        if backup_result:
            protected_state["memory_protection"]["last_backup_at"] = backup_result["created_at"]
        _write_state_file_atomic(
            protected_state,
            verify_after_write=protected_state["memory_protection"]["verify_after_write"],
        )
        _write_memory_protection_runtime_config(protected_state["memory_protection"])

    if maintenance_job:
        _schedule_memory_protection_maintenance(**maintenance_job)

    return protected_state


def _record_memory_protection_auto_backup_result(backup_result: dict | None = None, last_error: str = "") -> None:
    if not backup_result and not str(last_error).strip():
        return

    rendered_error = str(last_error).strip()[:MAX_MEMORY_PROTECTION_ERROR_CHARS]
    backup_created_at = str((backup_result or {}).get("created_at", "")).strip()

    try:
        def mutate(state):
            memory_protection = state.setdefault("memory_protection", {})
            memory_protection["last_error"] = rendered_error
            if backup_created_at:
                memory_protection["last_backup_at"] = backup_created_at

        state_transaction("memory_protection_auto_backup_result", mutate, create_backup=False)
    except Exception:
        pass


def _run_memory_protection_auto_backup_job(job: dict) -> None:
    protected_state = normalize_state(job.get("state", {}))
    backup_result, last_error, maintenance_job = _create_memory_protection_auto_backup(protected_state)
    _record_memory_protection_auto_backup_result(backup_result=backup_result, last_error=last_error)
    if maintenance_job:
        _schedule_memory_protection_maintenance(**maintenance_job)


def _memory_protection_auto_backup_worker() -> None:
    global _MEMORY_PROTECTION_BACKUP_THREAD

    while True:
        try:
            job = _MEMORY_PROTECTION_BACKUP_QUEUE.get(timeout=0.1)
        except queue.Empty:
            with _MEMORY_PROTECTION_BACKUP_THREAD_LOCK:
                if _MEMORY_PROTECTION_BACKUP_QUEUE.empty():
                    _MEMORY_PROTECTION_BACKUP_THREAD = None
                    return
            continue

        try:
            _run_memory_protection_auto_backup_job(job)
        finally:
            _MEMORY_PROTECTION_BACKUP_QUEUE.task_done()


def _schedule_memory_protection_auto_backup(protected_state: dict) -> None:
    global _MEMORY_PROTECTION_BACKUP_THREAD

    while True:
        try:
            _MEMORY_PROTECTION_BACKUP_QUEUE.get_nowait()
        except queue.Empty:
            break
        _MEMORY_PROTECTION_BACKUP_QUEUE.task_done()

    _MEMORY_PROTECTION_BACKUP_QUEUE.put({"state": protected_state})

    with _MEMORY_PROTECTION_BACKUP_THREAD_LOCK:
        if _MEMORY_PROTECTION_BACKUP_THREAD is not None and _MEMORY_PROTECTION_BACKUP_THREAD.is_alive():
            return

        thread = threading.Thread(
            target=_memory_protection_auto_backup_worker,
            name="yarbis-memory-protection-auto-backup",
            daemon=True,
        )
        _MEMORY_PROTECTION_BACKUP_THREAD = thread
        thread.start()


def _record_memory_protection_maintenance_error(error_text: str) -> None:
    rendered_error = str(error_text).strip()[:MAX_MEMORY_PROTECTION_ERROR_CHARS]
    if not rendered_error:
        return

    try:
        def mutate(state):
            memory_protection = state.setdefault("memory_protection", {})
            memory_protection["last_error"] = rendered_error

        state_transaction("memory_protection_maintenance_error", mutate, create_backup=False)
    except Exception:
        pass


def _run_memory_protection_maintenance(
    *,
    backup_path: str | Path,
    local_backups_dir: str | Path,
    mirror_dir: str = "",
    retention: dict | None = None,
) -> None:
    settings = retention if isinstance(retention, dict) else {}
    max_auto_backups = settings.get("max_auto_backups", DEFAULT_MEMORY_PROTECTION_MAX_AUTO_BACKUPS)
    keep_daily_days = settings.get("keep_daily_days", DEFAULT_MEMORY_PROTECTION_KEEP_DAILY_DAYS)
    rendered_mirror_dir = str(mirror_dir).strip()

    try:
        if rendered_mirror_dir:
            memory_backup.mirror_backup(backup_path, rendered_mirror_dir)

        memory_backup.prune_auto_backups(
            backups_dir=local_backups_dir,
            max_auto_backups=max_auto_backups,
            keep_daily_days=keep_daily_days,
        )
        memory_backup.prune_stale_temp_files(local_backups_dir)
        memory_backup.prune_stale_temp_files(
            _state_file_path().parent,
            name_prefix=f"{_state_file_path().name}.tmp-",
        )
        if rendered_mirror_dir:
            memory_backup.prune_auto_backups(
                backups_dir=Path(rendered_mirror_dir).expanduser(),
                max_auto_backups=max_auto_backups,
                keep_daily_days=keep_daily_days,
            )
            memory_backup.prune_stale_temp_files(Path(rendered_mirror_dir).expanduser())
    except memory_backup.MemoryBackupError as exc:
        _record_memory_protection_maintenance_error(str(exc))
    except Exception as exc:
        _record_memory_protection_maintenance_error(str(exc))


def _schedule_memory_protection_maintenance(
    *,
    backup_path: str | Path,
    local_backups_dir: str | Path,
    mirror_dir: str = "",
    retention: dict | None = None,
) -> None:
    global _MEMORY_PROTECTION_MAINTENANCE_THREAD

    job = {
        "backup_path": str(backup_path),
        "local_backups_dir": str(local_backups_dir),
        "mirror_dir": str(mirror_dir).strip(),
        "retention": dict(retention or {}),
    }

    if not MEMORY_PROTECTION_MAINTENANCE_ASYNC:
        _run_memory_protection_maintenance(**job)
        return

    if not _MEMORY_PROTECTION_MAINTENANCE_LOCK.acquire(blocking=False):
        return

    def worker():
        try:
            _run_memory_protection_maintenance(**job)
        finally:
            _MEMORY_PROTECTION_MAINTENANCE_LOCK.release()

    thread = threading.Thread(
        target=worker,
        name="yarbis-memory-protection-maintenance",
        daemon=True,
    )
    _MEMORY_PROTECTION_MAINTENANCE_THREAD = thread
    thread.start()


def wait_for_memory_protection_maintenance(timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))

    for thread_getter in (
        lambda: _MEMORY_PROTECTION_BACKUP_THREAD,
        lambda: _MEMORY_PROTECTION_MAINTENANCE_THREAD,
    ):
        thread = thread_getter()
        if thread is None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)
        if thread.is_alive():
            return False

    return _MEMORY_PROTECTION_BACKUP_QUEUE.empty()


def _save_state_unlocked(state, create_backup: bool = True):
    normalized = normalize_state(state)
    verify_after_write = normalized["memory_protection"]["verify_after_write"]
    _write_state_file_atomic(normalized, verify_after_write=verify_after_write)
    if create_backup:
        return _apply_memory_protection_after_save_unlocked(normalized)
    _write_memory_protection_runtime_config(normalized["memory_protection"])
    return normalized


def load_state():
    with STATE_LOCK:
        with _state_file_lock("load_state"):
            return _load_state_unlocked()


def save_state(state):
    with STATE_LOCK:
        with _state_file_lock("save_state"):
            _save_state_unlocked(state)


def state_transaction(label: str, mutator, create_backup: bool = True):
    if not callable(mutator):
        raise TypeError("state_transaction requiere un mutator callable.")

    with STATE_LOCK:
        with _state_file_lock(label):
            state = _load_state_unlocked()
            result = mutator(state)
            _save_state_unlocked(state, create_backup=create_backup)
            return result


def verify_memory_backups() -> dict:
    with STATE_LOCK:
        with _state_file_lock("verify_memory_backups"):
            state = _load_state_unlocked()
            settings = state["memory_protection"]
            verified = memory_backup.verify_backups(
                backup_dirs=_memory_backup_dirs_for_settings(settings),
                normalizer=normalize_state,
            )
            return {
                "settings": settings,
                "local_dir": str(_memory_backups_dir()),
                "mirror_dir": settings.get("mirror_dir", ""),
                **verified,
            }


def memory_protection_status() -> dict:
    verified = verify_memory_backups()
    settings = verified["settings"]
    return {
        "enabled": settings["enabled"],
        "backup_on_every_change": settings["backup_on_every_change"],
        "mirror_dir": settings["mirror_dir"],
        "include_secrets": settings["include_secrets"],
        "retention": settings["retention"],
        "verify_after_write": settings["verify_after_write"],
        "auto_restore": settings["auto_restore"],
        "last_backup_at": settings["last_backup_at"],
        "last_recovery_at": settings["last_recovery_at"],
        "last_error": settings["last_error"],
        "local_dir": verified["local_dir"],
        "valid_backups": verified["valid_count"],
        "invalid_backups": verified["invalid_count"],
        "latest_backup": verified["latest"],
        "invalid": verified["invalid"],
    }
