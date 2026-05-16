import json
import secrets
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import parse, request
from urllib.error import HTTPError, URLError

META_GRAPH_BASE = "https://graph.facebook.com"
META_DIALOG_URL = "https://www.facebook.com/dialog/oauth"
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_BASE = "https://api.linkedin.com"
DEFAULT_REDIRECT_PORT = 8765
DEFAULT_META_SCOPES = (
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
)
DEFAULT_LINKEDIN_SCOPES = (
    "openid",
    "profile",
    "w_member_social",
    "r_organization_social",
    "w_organization_social",
)


class SocialOAuthError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _http_json(
    url: str,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
) -> dict:
    final_url = str(url)
    if params:
        separator = "&" if "?" in final_url else "?"
        final_url += separator + parse.urlencode(params)

    body = None
    request_headers = dict(headers or {})
    if data is not None:
        body = parse.urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = request.Request(final_url, data=body, headers=request_headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            error_body = ""
        raise SocialOAuthError(f"HTTP {exc.code} en OAuth: {error_body or exc.reason}") from exc
    except URLError as exc:
        raise SocialOAuthError(f"No pude conectar con OAuth: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SocialOAuthError(f"OAuth devolvio JSON invalido: {exc}") from exc


def _split_scopes(value: str, defaults: tuple[str, ...]) -> list[str]:
    raw = str(value).strip()
    if not raw:
        return list(defaults)
    return [
        item.strip()
        for item in raw.replace(",", " ").split()
        if item.strip()
    ]


def default_redirect_uri(provider: str) -> str:
    cleaned_provider = str(provider).strip().lower()
    return f"http://127.0.0.1:{DEFAULT_REDIRECT_PORT}/oauth/{cleaned_provider}/callback"


def _is_loopback_redirect(redirect_uri: str) -> bool:
    parsed = parse.urlparse(str(redirect_uri))
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}


def build_authorization_url(provider: str, client_id: str, redirect_uri: str, scopes: str = "", state: str = "") -> str:
    cleaned_provider = str(provider).strip().lower()
    cleaned_client_id = str(client_id).strip()
    cleaned_redirect_uri = str(redirect_uri).strip() or default_redirect_uri(cleaned_provider)
    state = state or secrets.token_urlsafe(24)

    if cleaned_provider == "meta":
        scope_items = _split_scopes(scopes, DEFAULT_META_SCOPES)
        return META_DIALOG_URL + "?" + parse.urlencode({
            "client_id": cleaned_client_id,
            "redirect_uri": cleaned_redirect_uri,
            "state": state,
            "scope": ",".join(scope_items),
            "response_type": "code",
        })

    if cleaned_provider == "linkedin":
        scope_items = _split_scopes(scopes, DEFAULT_LINKEDIN_SCOPES)
        return LINKEDIN_AUTH_URL + "?" + parse.urlencode({
            "response_type": "code",
            "client_id": cleaned_client_id,
            "redirect_uri": cleaned_redirect_uri,
            "state": state,
            "scope": " ".join(scope_items),
        })

    raise SocialOAuthError("Proveedor OAuth social invalido. Usa meta o linkedin.")


def _parse_authorization_response(url: str) -> dict:
    parsed = parse.urlparse(str(url).strip())
    query = parse.parse_qs(parsed.query)
    if "error" in query:
        raise SocialOAuthError(query.get("error_description", query["error"])[0])
    code = query.get("code", [""])[0]
    state = query.get("state", [""])[0]
    if not code:
        raise SocialOAuthError("La URL de callback no contiene codigo OAuth.")
    return {"code": code, "state": state}


def _wait_for_local_callback(redirect_uri: str, expected_state: str, timeout_seconds: int) -> dict:
    parsed = parse.urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or DEFAULT_REDIRECT_PORT), _OAuthCallbackHandler)
    server.expected_path = parsed.path
    server.expected_state = expected_state
    server.result = {}
    server.error = ""

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=max(5, int(timeout_seconds)))
    server.server_close()

    if thread.is_alive():
        raise SocialOAuthError("Tiempo agotado esperando el callback OAuth local.")
    if server.error:
        raise SocialOAuthError(server.error)
    result = server.result
    if not result.get("code"):
        raise SocialOAuthError("No recibi codigo OAuth en el callback local.")
    return result


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):  # pragma: no cover - evita ruido en consola
        return

    def do_GET(self):
        parsed = parse.urlparse(self.path)
        if parsed.path != self.server.expected_path:
            self.send_error(404)
            return

        query = parse.parse_qs(parsed.query)
        if query.get("error"):
            self.server.error = query.get("error_description", query["error"])[0]
            self._send_result_page("No pude completar la conexion. Puedes cerrar esta ventana.")
            return

        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        if state != self.server.expected_state:
            self.server.error = "El state OAuth no coincide; se cancelo por seguridad."
        else:
            self.server.result = {"code": code, "state": state}

        self._send_result_page("Yarbis recibio la autorizacion. Puedes cerrar esta ventana.")

    def _send_result_page(self, body: str):
        content = f"<html><body><h1>{body}</h1></body></html>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def _exchange_meta_code(client_id: str, client_secret: str, redirect_uri: str, code: str, version: str) -> dict:
    token = _http_json(
        f"{META_GRAPH_BASE}/{version}/oauth/access_token",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "client_secret": client_secret,
            "code": code,
        },
    )
    short_token = str(token.get("access_token", "")).strip()
    if not short_token:
        raise SocialOAuthError("Meta no devolvio access_token.")

    try:
        long_token = _http_json(
            f"{META_GRAPH_BASE}/{version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": short_token,
            },
        )
        if long_token.get("access_token"):
            token = long_token
    except SocialOAuthError:
        pass
    return token


def _exchange_linkedin_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    token = _http_json(
        LINKEDIN_TOKEN_URL,
        method="POST",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not token.get("access_token"):
        raise SocialOAuthError("LinkedIn no devolvio access_token.")
    return token


def _expires_at(token: dict) -> str:
    try:
        seconds = int(token.get("expires_in", 0))
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return ""
    return (_utc_now() + timedelta(seconds=seconds)).isoformat()


def _discover_meta_accounts(token: dict, version: str, scopes: list[str]) -> list[dict]:
    user_token = str(token.get("access_token", "")).strip()
    accounts = []
    me = _http_json(
        f"{META_GRAPH_BASE}/{version}/me",
        params={"fields": "id,name", "access_token": user_token},
    )
    user_name = str(me.get("name") or "Perfil personal de Facebook")
    accounts.append({
        "platform": "facebook_personal",
        "account_type": "facebook_personal",
        "display_name": user_name,
        "external_id": str(me.get("id", "")),
        "token": "",
        "scopes": scopes,
        "expires_at": _expires_at(token),
        "metadata": {"mode": "assisted_only"},
    })

    pages = _http_json(
        f"{META_GRAPH_BASE}/{version}/me/accounts",
        params={
            "fields": "id,name,access_token,tasks,instagram_business_account{id,username,name}",
            "access_token": user_token,
        },
    )
    for page in pages.get("data", []):
        if not isinstance(page, dict):
            continue
        page_token = str(page.get("access_token") or user_token).strip()
        page_id = str(page.get("id", "")).strip()
        page_name = str(page.get("name", "")).strip() or f"Facebook Page {page_id}"
        accounts.append({
            "platform": "facebook_page",
            "account_type": "facebook_page",
            "display_name": page_name,
            "external_id": page_id,
            "token": page_token,
            "scopes": scopes,
            "expires_at": _expires_at(token),
            "metadata": {"tasks": page.get("tasks", [])},
        })
        instagram = page.get("instagram_business_account")
        if isinstance(instagram, dict) and instagram.get("id"):
            ig_id = str(instagram.get("id", "")).strip()
            ig_name = str(instagram.get("username") or instagram.get("name") or f"Instagram {ig_id}")
            accounts.append({
                "platform": "instagram",
                "account_type": "instagram_professional",
                "display_name": ig_name,
                "external_id": ig_id,
                "token": page_token,
                "scopes": scopes,
                "expires_at": _expires_at(token),
                "metadata": {"facebook_page_id": page_id, "facebook_page_name": page_name},
            })
    return accounts


def _linkedin_headers(token: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": version,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _discover_linkedin_accounts(token: dict, version: str, scopes: list[str]) -> list[dict]:
    access_token = str(token.get("access_token", "")).strip()
    headers = _linkedin_headers(access_token, version)
    accounts = []
    member_id = ""
    member_name = "LinkedIn"

    try:
        userinfo = _http_json(f"{LINKEDIN_API_BASE}/v2/userinfo", headers=headers)
        member_id = str(userinfo.get("sub", "")).strip()
        member_name = str(userinfo.get("name") or userinfo.get("localizedFirstName") or member_name)
    except SocialOAuthError:
        try:
            profile = _http_json(f"{LINKEDIN_API_BASE}/v2/me", headers=headers)
            member_id = str(profile.get("id", "")).strip()
            member_name = str(profile.get("localizedFirstName") or member_name)
        except SocialOAuthError:
            pass

    if member_id:
        accounts.append({
            "platform": "linkedin",
            "account_type": "linkedin_member",
            "display_name": member_name,
            "external_id": f"urn:li:person:{member_id}",
            "token": access_token,
            "scopes": scopes,
            "expires_at": _expires_at(token),
            "metadata": {"author_urn": f"urn:li:person:{member_id}"},
        })

    try:
        organizations = _http_json(
            f"{LINKEDIN_API_BASE}/rest/organizationalEntityAcls",
            params={"q": "roleAssignee"},
            headers=headers,
        )
    except SocialOAuthError:
        organizations = {}

    for item in organizations.get("elements", []):
        if not isinstance(item, dict):
            continue
        org = str(item.get("organizationalTarget", "")).strip()
        if not org:
            continue
        accounts.append({
            "platform": "linkedin",
            "account_type": "linkedin_organization",
            "display_name": org,
            "external_id": org,
            "token": access_token,
            "scopes": scopes,
            "expires_at": _expires_at(token),
            "metadata": {"author_urn": org, "role": item.get("role", "")},
        })

    if not accounts:
        raise SocialOAuthError("No pude descubrir cuentas publicables en LinkedIn.")
    return accounts


def connect_social_account(
    provider: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "",
    scopes: str = "",
    authorization_response_url: str = "",
    open_browser: bool = True,
    timeout_seconds: int = 180,
    meta_graph_version: str = "v24.0",
    linkedin_version: str = "202604",
) -> dict:
    cleaned_provider = str(provider).strip().lower()
    cleaned_client_id = str(client_id).strip()
    cleaned_client_secret = str(client_secret).strip()
    if cleaned_provider not in {"meta", "linkedin"}:
        raise SocialOAuthError("Proveedor OAuth social invalido. Usa meta o linkedin.")
    if not cleaned_client_id or not cleaned_client_secret:
        raise SocialOAuthError("Necesitas client_id y client_secret para OAuth.")

    redirect_uri = str(redirect_uri).strip() or default_redirect_uri(cleaned_provider)
    state = secrets.token_urlsafe(24)
    auth_url = build_authorization_url(cleaned_provider, cleaned_client_id, redirect_uri, scopes, state)
    scope_items = _split_scopes(scopes, DEFAULT_META_SCOPES if cleaned_provider == "meta" else DEFAULT_LINKEDIN_SCOPES)

    if authorization_response_url:
        auth_response = _parse_authorization_response(authorization_response_url)
    elif not _is_loopback_redirect(redirect_uri):
        return {
            "status": "authorization_required",
            "authorization_url": auth_url,
            "accounts": [],
            "message": "Abre la URL de autorizacion y vuelve a ejecutar la tool con authorization_response_url.",
        }
    else:
        if open_browser:
            webbrowser.open(auth_url)
        auth_response = _wait_for_local_callback(redirect_uri, state, timeout_seconds)

    if auth_response.get("state") and auth_response["state"] != state and not authorization_response_url:
        raise SocialOAuthError("El state OAuth no coincide.")

    if cleaned_provider == "meta":
        token = _exchange_meta_code(
            cleaned_client_id,
            cleaned_client_secret,
            redirect_uri,
            auth_response["code"],
            meta_graph_version,
        )
        accounts = _discover_meta_accounts(token, meta_graph_version, scope_items)
    else:
        token = _exchange_linkedin_code(
            cleaned_client_id,
            cleaned_client_secret,
            redirect_uri,
            auth_response["code"],
        )
        accounts = _discover_linkedin_accounts(token, linkedin_version, scope_items)

    return {
        "status": "connected",
        "authorization_url": auth_url,
        "accounts": accounts,
        "message": f"Cuentas {cleaned_provider} conectadas: {len(accounts)}",
    }

