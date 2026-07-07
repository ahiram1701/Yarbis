import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib import request as _urlrequest

import yarbis_instance

MAX_BROWSER_TEXT_CHARS = 12_000
DEFAULT_SCREENSHOT_DIR = str(yarbis_instance.runtime_dir() / "browser")

# Sesion interactiva persistente (navegador propio de Yarbis, ventana visible).
CDP_URL = "http://127.0.0.1:9222"
_BROWSER_LOCK = threading.RLock()
# Botones/acciones sensibles: requieren confirmacion explicita antes del click.
_SENSITIVE_CLICK = re.compile(
    r"\b(publicar|publish|compartir|share|enviar\s+mensaje|enviar|send|pagar|pay|"
    r"comprar|buy|order|eliminar|delete|borrar|desactivar|deactivate|confirmar\s+pago|"
    r"aceptar\s+y\s+pagar)\b",
    re.IGNORECASE,
)


def _persistent_profile_dir() -> Path:
    profile = yarbis_instance.runtime_dir() / "browser_profile"
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _find_browser_executable(channel: str) -> str:
    channel = str(channel or "").strip().lower()
    candidates = {
        "msedge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "brave": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
    }
    for path in candidates.get(channel, []) + candidates["msedge"] + candidates["chrome"]:
        if Path(path).exists():
            return path
    raise RuntimeError(
        "No encontre un navegador (Edge/Chrome) instalado para abrir con depuracion remota."
    )


def _cdp_reachable() -> bool:
    try:
        with _urlrequest.urlopen(f"{CDP_URL}/json/version", timeout=1.5):
            return True
    except Exception:
        return False


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


def _active_page(browser):
    contexts = browser.contexts
    context = contexts[0] if contexts else browser.new_context()
    pages = context.pages
    if pages:
        return pages[-1]
    return context.new_page()


_ENUMERATE_JS = r"""
() => {
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[contenteditable=""],[contenteditable="true"],[tabindex]';
  const nodes = Array.from(document.querySelectorAll(sel));
  const out = [];
  let ref = 0;
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0) continue;
    ref++;
    el.setAttribute('data-yarbis-ref', String(ref));
    let t = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    out.push({ ref: ref, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', text: t });
  }
  return out;
}
"""


def _enumerate_interactive(page):
    try:
        return page.evaluate(_ENUMERATE_JS) or []
    except Exception:
        return []


def _resolve_locator(page, action):
    ref = str(action.get("ref", "")).strip()
    if ref:
        return page.locator(f'[data-yarbis-ref="{ref}"]'), f"ref {ref}"
    text = str(action.get("text", "")).strip()
    if text:
        return page.get_by_text(text, exact=False).first, f"texto '{text}'"
    selector = str(action.get("selector", "")).strip()
    if selector:
        return page.locator(selector), selector
    raise ValueError("El click/fill requiere ref, text o selector.")


def open_persistent_browser(url: str = "", channel: str = "msedge", workspace_root: Path | None = None) -> str:
    workspace_root = workspace_root or Path.cwd()
    with _BROWSER_LOCK:
        launched = False
        if not _cdp_reachable():
            exe = _find_browser_executable(channel)
            profile = _persistent_profile_dir()
            args = [
                exe,
                "--remote-debugging-port=9222",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(
                args,
                close_fds=True,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            launched = True
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not _cdp_reachable():
                time.sleep(0.5)
            if not _cdp_reachable():
                raise RuntimeError("No pude iniciar el navegador con depuracion remota.")
        sync_playwright = _load_playwright()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            try:
                page = _active_page(browser)
                if str(url).strip():
                    page.goto(str(url).strip(), wait_until="domcontentloaded")
                final_url = page.url
            finally:
                browser.close()
    estado = "Navegador de Yarbis abierto (nuevo)" if launched else "Navegador de Yarbis ya estaba abierto"
    return (
        f"{estado}. URL: {final_url}. El perfil es persistente (tu login se guarda). "
        "Usa browser_observe para ver la pagina y browser_act para hacer clic o escribir."
    )


def observe_browser(screenshot: bool = False, settings: dict | None = None, workspace_root: Path | None = None) -> str:
    workspace_root = workspace_root or Path.cwd()
    with _BROWSER_LOCK:
        if not _cdp_reachable():
            raise RuntimeError("No hay un navegador de Yarbis abierto. Usa browser_open primero.")
        sync_playwright = _load_playwright()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            try:
                page = _active_page(browser)
                url = page.url
                try:
                    title = page.title()
                except Exception:
                    title = ""
                elements = _enumerate_interactive(page)
                try:
                    body = page.locator("body").inner_text(timeout=4000)
                except Exception:
                    body = ""
                lines = [
                    f"URL: {url}",
                    f"Titulo: {title}",
                    f"Elementos interactivos ({len(elements)}, usa el numero como ref):",
                ]
                for el in elements[:80]:
                    label = el.get("text") or "(sin texto)"
                    role = f"/{el['role']}" if el.get("role") else ""
                    lines.append(f"  [{el['ref']}] {el['tag']}{role}: {label}")
                if _normalize_bool(screenshot):
                    shot = _resolve_output_path("", workspace_root, f"observe-{int(time.time())}.png")
                    page.screenshot(path=str(shot), full_page=False)
                    try:
                        import vision

                        desc = vision.analyze_image(
                            str(shot),
                            "Describe brevemente la pantalla y donde estan los botones o campos principales.",
                            settings,
                        )
                        lines.append(f"Vision de la captura:\n{_bounded_text(desc, 2000)}")
                    except Exception as exc:
                        lines.append(f"(No pude analizar la captura con vision: {exc})")
                lines.append("Texto visible:\n" + _bounded_text(body, 4000))
                return "\n".join(lines)
            finally:
                browser.close()


def act_on_live_page(actions_json: str = "", confirm: str = "", workspace_root: Path | None = None) -> str:
    workspace_root = workspace_root or Path.cwd()
    actions = _normalize_actions(actions_json)
    if not actions:
        raise ValueError("Indica actions_json con al menos una accion (click/fill/type/goto/press/scroll).")
    confirm_text = str(confirm or "").strip()
    with _BROWSER_LOCK:
        if not _cdp_reachable():
            raise RuntimeError("No hay un navegador de Yarbis abierto. Usa browser_open primero.")
        sync_playwright = _load_playwright()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            try:
                page = _active_page(browser)
                page.context.set_default_timeout(15000)
                lines = ["Acciones en la pagina viva:"]
                for index, action in enumerate(actions, start=1):
                    name = action["action"]
                    if name in ("click", "fill", "type"):
                        locator, desc = _resolve_locator(page, action)
                        if name == "click":
                            try:
                                label = (locator.inner_text(timeout=2000) or "").strip()
                            except Exception:
                                label = str(action.get("text", "")).strip()
                            if _SENSITIVE_CLICK.search(label) and confirm_text.lower() != label.lower():
                                return (
                                    f"BLOQUEADO por seguridad: '{label or desc}' parece una accion sensible "
                                    "(publicar/pagar/enviar/eliminar). Pide confirmacion al usuario y reintenta el "
                                    f'click con confirm="{label}".'
                                )
                            locator.click()
                            lines.append(f"{index}. click {desc} ({label[:40]})")
                        else:
                            value = str(action.get("value", action.get("text_value", "")))
                            locator.fill(value)
                            lines.append(f"{index}. escribi en {desc}")
                    elif name == "goto":
                        url = str(action.get("url", "")).strip()
                        if not url:
                            raise ValueError("goto requiere url.")
                        page.goto(url, wait_until="domcontentloaded")
                        lines.append(f"{index}. goto -> {page.url}")
                    elif name == "press":
                        key = str(action.get("key", "")).strip()
                        if not key:
                            raise ValueError("press requiere key.")
                        page.keyboard.press(key)
                        lines.append(f"{index}. press {key}")
                    elif name == "scroll":
                        dy = int(action.get("dy", 600))
                        page.mouse.wheel(0, dy)
                        lines.append(f"{index}. scroll {dy}")
                    elif name == "wait":
                        ms = max(0, min(120000, int(action.get("milliseconds", action.get("ms", 1000)))))
                        page.wait_for_timeout(ms)
                        lines.append(f"{index}. wait {ms}ms")
                    else:
                        lines.extend(_apply_action(page, action, workspace_root, index))
                lines.append(f"URL final: {page.url}")
                return "\n".join(lines)
            finally:
                browser.close()
