import json
from pathlib import Path

import yarbis_instance

MAX_BROWSER_TEXT_CHARS = 12_000
DEFAULT_SCREENSHOT_DIR = str(yarbis_instance.runtime_dir() / "browser")


def _bounded_text(text: str, limit: int = MAX_BROWSER_TEXT_CHARS) -> str:
    rendered = str(text)
    if len(rendered) <= limit:
        return rendered

    marker = f"\n...[truncado {len(rendered) - limit} caracteres]...\n"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return rendered[:head].rstrip() + marker + rendered[-tail:].lstrip()


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright no esta instalado. Ejecuta `.venv\\Scripts\\python.exe -m pip install playwright` "
            "y usa Edge/Chrome instalado, o ejecuta `python -m playwright install chromium`."
        ) from exc

    return sync_playwright


def _normalize_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "off", "no"}


def _normalize_actions(actions_json) -> list[dict]:
    if actions_json in (None, ""):
        return []

    if isinstance(actions_json, list):
        parsed = actions_json
    else:
        try:
            parsed = json.loads(str(actions_json))
        except json.JSONDecodeError as exc:
            raise ValueError(f"actions_json no es JSON valido: {exc}") from exc

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("actions_json debe ser una lista JSON de acciones.")

    actions = []
    for index, action in enumerate(parsed, start=1):
        if not isinstance(action, dict):
            raise ValueError(f"La accion {index} debe ser un objeto JSON.")
        action_name = str(action.get("action", "")).strip().lower()
        if not action_name:
            raise ValueError(f"La accion {index} no tiene campo `action`.")
        normalized = dict(action)
        normalized["action"] = action_name
        actions.append(normalized)
    return actions


def _resolve_output_path(path: str, workspace_root: Path, default_name: str) -> Path:
    cleaned_path = str(path).strip()
    if not cleaned_path:
        candidate = workspace_root / DEFAULT_SCREENSHOT_DIR / default_name
    else:
        raw_path = Path(cleaned_path)
        candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path

    resolved = candidate.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_existing_optional_path(path: str, workspace_root: Path) -> Path | None:
    cleaned_path = str(path).strip()
    if not cleaned_path:
        return None
    raw_path = Path(cleaned_path)
    resolved = (raw_path if raw_path.is_absolute() else workspace_root / raw_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _require_selector(action: dict) -> str:
    selector = str(action.get("selector", "")).strip()
    if not selector:
        raise ValueError(f"La accion `{action['action']}` requiere selector.")
    return selector


def _launch_or_connect_browser(playwright, headless: bool, browser_channel: str):
    """
    Lanza o conecta un navegador via Playwright.

    Si browser_channel es "brave" (u otro canal CDP), intenta conectar a un
    navegador ya abierto con --remote-debugging-port=9222. Si no lo encuentra,
    lanza Brave automaticamente con el flag de debugging.

    Para otros canales, funciona como antes: lanza una instancia nueva.
    """
    channel = str(browser_channel).strip().lower()

    # Canales que soportan conexion CDP para sesion persistente
    cdp_channels = {"brave", "chrome", "chromium", "msedge", "edge"}

    if channel in cdp_channels:
        # Intentar conectar a navegador ya abierto con remote-debugging
        cdp_url = "http://127.0.0.1:9222"
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            return browser
        except Exception:
            pass  # No habia navegador escuchando, lanzaremos uno nuevo

        # Lanzar navegador con remote-debugging automaticamente
        launch_options = {"headless": False}
        if channel:
            launch_options["channel"] = channel
        launch_options["args"] = ["--remote-debugging-port=9222"]
        try:
            return playwright.chromium.launch(**launch_options)
        except Exception:
            if not channel:
                raise
            launch_options.pop("channel", None)
            return playwright.chromium.launch(**launch_options)

    # Canales sin CDP: lanzamiento normal (headless respeta el parametro)
    launch_options = {"headless": headless}
    if channel:
        launch_options["channel"] = channel
    try:
        return playwright.chromium.launch(**launch_options)
    except Exception:
        if not channel:
            raise
        launch_options.pop("channel", None)
        return playwright.chromium.launch(**launch_options)


def _apply_action(page, action: dict, workspace_root: Path, index: int) -> list[str]:
    name = action["action"]
    lines = []

    if name == "goto":
        url = str(action.get("url", "")).strip()
        if not url:
            raise ValueError("La accion `goto` requiere url.")
        page.goto(url, wait_until=str(action.get("wait_until", "domcontentloaded")).strip() or "domcontentloaded")
        lines.append(f"{index}. goto -> {page.url}")
    elif name == "click":
        page.locator(_require_selector(action)).click()
        lines.append(f"{index}. click {action['selector']}")
    elif name == "fill":
        page.locator(_require_selector(action)).fill(str(action.get("value", "")))
        lines.append(f"{index}. fill {action['selector']}")
    elif name == "press":
        key = str(action.get("key", "")).strip()
        if not key:
            raise ValueError("La accion `press` requiere key.")
        selector = str(action.get("selector", "")).strip()
        if selector:
            page.locator(selector).press(key)
            lines.append(f"{index}. press {key} en {selector}")
        else:
            page.keyboard.press(key)
            lines.append(f"{index}. press {key}")
    elif name == "wait":
        milliseconds = max(0, min(120_000, int(action.get("milliseconds", action.get("ms", 1000)))))
        page.wait_for_timeout(milliseconds)
        lines.append(f"{index}. wait {milliseconds}ms")
    elif name == "wait_for_selector":
        selector = _require_selector(action)
        state = str(action.get("state", "visible")).strip() or "visible"
        page.locator(selector).wait_for(state=state)
        lines.append(f"{index}. wait_for_selector {selector} ({state})")
    elif name == "select_option":
        selector = _require_selector(action)
        value = action.get("value", "")
        page.locator(selector).select_option(value)
        lines.append(f"{index}. select_option {selector}")
    elif name == "check":
        page.locator(_require_selector(action)).check()
        lines.append(f"{index}. check {action['selector']}")
    elif name == "uncheck":
        page.locator(_require_selector(action)).uncheck()
        lines.append(f"{index}. uncheck {action['selector']}")
    elif name == "screenshot":
        path = _resolve_output_path(
            str(action.get("path", "")).strip(),
            workspace_root,
            f"screenshot-{index}.png",
        )
        page.screenshot(path=str(path), full_page=_normalize_bool(action.get("full_page", True)))
        lines.append(f"{index}. screenshot -> {path}")
    elif name == "text":
        selector = str(action.get("selector", "body")).strip() or "body"
        text = page.locator(selector).inner_text(timeout=5_000)
        label = str(action.get("label", selector)).strip() or selector
        lines.append(f"{index}. text {label}:\n{_bounded_text(text)}")
    elif name == "evaluate":
        script = str(action.get("script", "")).strip()
        if not script:
            raise ValueError("La accion `evaluate` requiere script.")
        value = page.evaluate(script)
        lines.append(f"{index}. evaluate:\n{_bounded_text(json.dumps(value, ensure_ascii=False, default=str))}")
    else:
        raise ValueError(f"Accion de navegador no soportada: {name}")

    return lines


def run_browser_automation(
    start_url: str = "",
    actions_json: str = "",
    headless: bool = True,
    timeout_seconds: int = 30,
    browser_channel: str = "msedge",
    storage_state_path: str = "",
    screenshot_path: str = "",
    workspace_root: Path | None = None,
) -> str:
    workspace_root = workspace_root or Path.cwd()
    actions = _normalize_actions(actions_json)
    if str(start_url).strip():
        actions.insert(0, {"action": "goto", "url": str(start_url).strip()})
    if not actions and not str(screenshot_path).strip():
        raise ValueError("Indica start_url, actions_json o screenshot_path.")

    normalized_timeout_ms = max(1, min(3600, int(timeout_seconds))) * 1000
    sync_playwright = _load_playwright()
    storage_state = _resolve_existing_optional_path(storage_state_path, workspace_root)
    lines = ["Automatizacion de navegador completada."]

    with sync_playwright() as playwright:
        browser = _launch_or_connect_browser(
            playwright,
            headless=_normalize_bool(headless),
            browser_channel=browser_channel,
        )
        context_options = {}
        if storage_state and storage_state.exists():
            context_options["storage_state"] = str(storage_state)

        try:
            context = browser.new_context(**context_options)
            context.set_default_timeout(normalized_timeout_ms)
            page = context.new_page()

            for index, action in enumerate(actions, start=1):
                lines.extend(_apply_action(page, action, workspace_root, index))

            if str(screenshot_path).strip():
                path = _resolve_output_path(screenshot_path, workspace_root, "screenshot-final.png")
                page.screenshot(path=str(path), full_page=True)
                lines.append(f"Captura final: {path}")

            if storage_state:
                context.storage_state(path=str(storage_state))
                lines.append(f"Sesion guardada: {storage_state}")

            lines.append(f"URL final: {page.url}")
            context.close()
        finally:
            browser.close()

    return "\n".join(lines)
