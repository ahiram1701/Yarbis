import html
import ipaddress
import re
from html.parser import HTMLParser
from urllib import parse, request
from urllib.error import HTTPError, URLError

DEFAULT_USER_AGENT = "Yarbis/1.0 (+local agent)"
MAX_SEARCH_HTML_BYTES = 350_000
MAX_PAGE_BYTES = 500_000
_BLOCK_TAGS = {
    "article",
    "blockquote",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}
_RESULT_LINK_RE = re.compile(
    r'<a(?P<attrs>[^>]+href="(?P<href>[^"]+)"[^>]*)>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="[^"]*(?:result__snippet|result-snippet|snippet)[^"]*"[^>]*>(?P<text>.*?)</',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class VisibleTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._title_depth = 0
        self._title_chunks = []
        self._text_chunks = []

    @property
    def title(self) -> str:
        return _compact_text(" ".join(self._title_chunks))

    def get_text(self) -> str:
        rendered = "".join(self._text_chunks)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        return _compact_text(rendered, preserve_newlines=True)

    def handle_starttag(self, tag, attrs):
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if lowered == "title":
            self._title_depth += 1
            return

        if self._skip_depth == 0 and lowered in _BLOCK_TAGS:
            self._text_chunks.append("\n")

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if lowered == "title" and self._title_depth > 0:
            self._title_depth -= 1
            return

        if self._skip_depth == 0 and lowered in _BLOCK_TAGS:
            self._text_chunks.append("\n")

    def handle_data(self, data):
        if self._title_depth > 0:
            self._title_chunks.append(data)
            return

        if self._skip_depth > 0:
            return

        if data:
            self._text_chunks.append(data)


def _compact_text(text: str, preserve_newlines: bool = False) -> str:
    if preserve_newlines:
        parts = []
        for line in str(text).splitlines():
            normalized_line = re.sub(r"\s+", " ", line).strip()
            if not normalized_line:
                if parts and parts[-1] != "":
                    parts.append("")
                continue
            parts.append(normalized_line)
        while parts and parts[-1] == "":
            parts.pop()
        return "\n".join(parts)

    return re.sub(r"\s+", " ", str(text)).strip()


def _decode_response_bytes(raw_body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^\s;]+)", content_type or "", re.IGNORECASE)
    encoding = charset_match.group(1).strip("\"'") if charset_match else "utf-8"
    try:
        return raw_body.decode(encoding, errors="replace")
    except LookupError:
        return raw_body.decode("utf-8", errors="replace")


def _normalize_result_url(url: str) -> str:
    cleaned = html.unescape(str(url).strip())
    if not cleaned:
        return ""

    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"

    if cleaned.startswith("/"):
        cleaned = f"https://duckduckgo.com{cleaned}"

    parsed = parse.urlsplit(cleaned)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        params = parse.parse_qs(parsed.query)
        target = params.get("uddg", [""])[0]
        if target:
            return parse.unquote(target)

    return cleaned


def _domain_matches(hostname: str, configured_domain: str) -> bool:
    cleaned_domain = str(configured_domain).strip().lower().lstrip(".")
    cleaned_hostname = str(hostname).strip().lower().rstrip(".")
    if not cleaned_domain or not cleaned_hostname:
        return False
    return cleaned_hostname == cleaned_domain or cleaned_hostname.endswith(f".{cleaned_domain}")


def validate_public_url(
    url: str,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> tuple[str | None, str | None]:
    cleaned = str(url).strip()
    if not cleaned:
        return None, "Debes indicar una URL."

    parsed = parse.urlsplit(cleaned)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None, "Solo se permiten URLs http o https."

    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return None, "La URL no incluye un host valido."

    if hostname == "localhost" or hostname.endswith(".local"):
        return None, "No se permiten hosts locales."

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return None, "No se permiten IPs privadas o locales."

    allowed_domains = allowed_domains or []
    blocked_domains = blocked_domains or []

    for blocked_domain in blocked_domains:
        if _domain_matches(hostname, blocked_domain):
            return None, f"El dominio esta bloqueado por politica: {blocked_domain}"

    if allowed_domains and not any(
        _domain_matches(hostname, allowed_domain)
        for allowed_domain in allowed_domains
    ):
        return None, "La URL no pertenece a un dominio permitido."

    normalized = parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc,
        parsed.path or "/",
        parsed.query,
        "",
    ))
    return normalized, None


def _http_get(
    url: str,
    timeout_seconds: int,
    max_bytes: int,
) -> tuple[bytes, str, str, bool]:
    req = request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "es,en;q=0.8",
        },
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read(max_bytes + 1) if max_bytes > 0 else response.read()
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
    except HTTPError as exc:
        raise RuntimeError(f"Error HTTP {exc.code} al consultar {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"No pude conectar con {url}: {exc.reason}") from exc
    except OSError as exc:
        raise RuntimeError(f"Error de red consultando {url}: {exc}") from exc

    was_truncated = max_bytes > 0 and len(raw_body) > max_bytes
    if was_truncated:
        raw_body = raw_body[:max_bytes]

    return raw_body, final_url, content_type, was_truncated


def _strip_html(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", str(fragment))
    return _compact_text(html.unescape(without_tags))


def _fallback_title_from_html(html_text: str) -> str:
    match = _TITLE_RE.search(html_text)
    if not match:
        return ""
    return _strip_html(match.group(1))


def _extract_snippet(window: str) -> str:
    match = _SNIPPET_RE.search(window)
    if match:
        snippet = _strip_html(match.group("text"))
        if snippet:
            return snippet

    snippet = _strip_html(window[:500])
    if len(snippet) > 260:
        snippet = snippet[:257].rstrip() + "..."
    return snippet


def _parse_duckduckgo_results(
    html_text: str,
    limit: int,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    results = []
    seen_urls = set()

    for match in _RESULT_LINK_RE.finditer(html_text):
        attrs = match.group("attrs") or ""
        href = _normalize_result_url(match.group("href") or "")
        title = _strip_html(match.group("title") or "")

        lowered_attrs = attrs.lower()
        if "result__a" not in lowered_attrs and "rel=\"nofollow\"" not in lowered_attrs:
            continue
        if not href or not title:
            continue

        safe_url, error = validate_public_url(
            href,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        if error or not safe_url:
            continue

        if safe_url in seen_urls:
            continue

        snippet_window = html_text[match.end():match.end() + 1_500]
        snippet = _extract_snippet(snippet_window)

        results.append({
            "title": title,
            "url": safe_url,
            "snippet": snippet,
        })
        seen_urls.add(safe_url)

        if len(results) >= limit:
            break

    return results


def search_web(
    query: str,
    limit: int = 5,
    timeout_seconds: int = 10,
    provider: str = "duckduckgo_html",
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict]:
    cleaned_query = _compact_text(query)
    if not cleaned_query:
        raise ValueError("Debes indicar una consulta web.")

    if provider != "duckduckgo_html":
        raise ValueError(f"Proveedor de busqueda no soportado: {provider}")

    normalized_limit = max(1, min(10, int(limit)))
    search_url = "https://html.duckduckgo.com/html/?" + parse.urlencode({"q": cleaned_query})

    raw_body, _, content_type, _ = _http_get(
        search_url,
        timeout_seconds=timeout_seconds,
        max_bytes=MAX_SEARCH_HTML_BYTES,
    )
    html_text = _decode_response_bytes(raw_body, content_type)

    return _parse_duckduckgo_results(
        html_text,
        limit=normalized_limit,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )


def fetch_web_page(
    url: str,
    timeout_seconds: int = 10,
    max_page_chars: int = 12_000,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> dict:
    safe_url, error = validate_public_url(
        url,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    if error or not safe_url:
        raise ValueError(error or "URL no valida.")

    raw_body, final_url, content_type, was_truncated = _http_get(
        safe_url,
        timeout_seconds=timeout_seconds,
        max_bytes=0,
    )

    final_safe_url, final_error = validate_public_url(
        final_url,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    if final_error or not final_safe_url:
        raise ValueError(
            "La redireccion final no es valida para esta politica de internet."
        )

    text = _decode_response_bytes(raw_body, content_type)
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()

    title = ""
    if normalized_content_type in {"", "text/html", "application/xhtml+xml"}:
        parser = VisibleTextExtractor()
        parser.feed(text)
        title = parser.title or _fallback_title_from_html(text)
        content_text = parser.get_text()
    elif normalized_content_type.startswith("text/"):
        content_text = _compact_text(text, preserve_newlines=True)
    else:
        raise ValueError(
            f"Tipo de contenido no soportado para lectura: {normalized_content_type or content_type}"
        )

    cleaned_content = content_text.strip()
    if not cleaned_content:
        raise ValueError("La pagina no devolvio texto legible.")

    return {
        "url": final_safe_url,
        "title": title,
        "content": cleaned_content,
        "content_type": normalized_content_type or content_type,
        "truncated": bool(was_truncated),
    }
