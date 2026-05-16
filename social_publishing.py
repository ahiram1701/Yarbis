import json
import mimetypes
import time
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError

META_GRAPH_BASE = "https://graph.facebook.com"
LINKEDIN_API_BASE = "https://api.linkedin.com"


class SocialPublishError(ValueError):
    pass


def _read_response(response) -> dict:
    raw = response.read()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SocialPublishError(f"La API devolvio JSON invalido: {exc}") from exc


def _http_json(
    url: str,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 30,
) -> dict:
    final_url = str(url)
    if params:
        separator = "&" if "?" in final_url else "?"
        final_url += separator + parse.urlencode(params)

    body = None
    request_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = parse.urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = request.Request(final_url, data=body, headers=request_headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return _read_response(response)
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            error_body = ""
        raise SocialPublishError(f"HTTP {exc.code} en {final_url}: {error_body or exc.reason}") from exc
    except URLError as exc:
        raise SocialPublishError(f"No pude conectar con {final_url}: {exc.reason}") from exc


def _http_upload(url: str, content: bytes, headers: dict | None = None, timeout: int = 60) -> None:
    req = request.Request(str(url), data=content, headers=dict(headers or {}), method="PUT")
    try:
        with request.urlopen(req, timeout=timeout):
            return
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            error_body = ""
        raise SocialPublishError(f"HTTP {exc.code} subiendo media: {error_body or exc.reason}") from exc
    except URLError as exc:
        raise SocialPublishError(f"No pude subir media: {exc.reason}") from exc


def _meta_url(version: str, path: str) -> str:
    cleaned_version = str(version or "v24.0").strip().strip("/")
    cleaned_path = str(path).strip("/")
    return f"{META_GRAPH_BASE}/{cleaned_version}/{cleaned_path}"


def _linkedin_headers(token: str, version: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": str(version or "202604"),
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _publication_text(publication: dict) -> str:
    body = str(publication.get("body", "")).strip()
    hashtags = [
        item.strip()
        for item in publication.get("hashtags", [])
        if str(item).strip()
    ]
    if hashtags:
        rendered_tags = " ".join(tag if tag.startswith("#") else f"#{tag}" for tag in hashtags)
        body = f"{body}\n\n{rendered_tags}".strip()
    return body


def _target_external_id(account: dict) -> str:
    external_id = str(account.get("external_id", "")).strip()
    if not external_id:
        raise SocialPublishError("La cuenta social no tiene external_id.")
    return external_id


def publish_facebook_page(account: dict, token: str, publication: dict, settings: dict) -> dict:
    page_id = _target_external_id(account)
    version = settings.get("meta_graph_version", "v24.0")
    message = _publication_text(publication)
    media_url = str(publication.get("media_url", "")).strip()
    link_url = str(publication.get("link_url", "")).strip()

    if media_url:
        response = _http_json(
            _meta_url(version, f"{page_id}/photos"),
            method="POST",
            data={
                "access_token": token,
                "url": media_url,
                "message": message,
            },
        )
    else:
        payload = {"access_token": token, "message": message}
        if link_url:
            payload["link"] = link_url
        response = _http_json(
            _meta_url(version, f"{page_id}/feed"),
            method="POST",
            data=payload,
        )

    return {
        "platform": "facebook_page",
        "external_post_id": str(response.get("post_id") or response.get("id") or ""),
        "raw": response,
    }


def publish_instagram(account: dict, token: str, publication: dict, settings: dict) -> dict:
    ig_user_id = _target_external_id(account)
    version = settings.get("meta_graph_version", "v24.0")
    caption = _publication_text(publication)
    media_url = str(publication.get("media_url", "")).strip()
    media_path = str(publication.get("media_path", "")).strip()
    media_type = str(publication.get("media_type", "image")).strip().lower() or "image"

    if media_path and not media_url:
        raise SocialPublishError("Instagram requiere una URL publica de media; no puede publicar un archivo local directo.")
    if not media_url:
        raise SocialPublishError("Instagram requiere media_url para crear el contenedor.")

    create_payload = {
        "access_token": token,
        "caption": caption,
    }
    if media_type in {"video", "reel", "reels"}:
        create_payload["video_url"] = media_url
        create_payload["media_type"] = "REELS" if media_type in {"reel", "reels"} else "VIDEO"
    elif media_type in {"story", "stories"}:
        create_payload["image_url"] = media_url
        create_payload["media_type"] = "STORIES"
    else:
        create_payload["image_url"] = media_url

    container = _http_json(
        _meta_url(version, f"{ig_user_id}/media"),
        method="POST",
        data=create_payload,
    )
    creation_id = str(container.get("id", "")).strip()
    if not creation_id:
        raise SocialPublishError("Instagram no devolvio id de contenedor.")

    for _attempt in range(6):
        status = _http_json(
            _meta_url(version, creation_id),
            params={
                "fields": "status_code",
                "access_token": token,
            },
        )
        status_code = str(status.get("status_code", "")).upper()
        if status_code in {"", "FINISHED"}:
            break
        if status_code == "ERROR":
            raise SocialPublishError("Instagram marco el contenedor como ERROR.")
        time.sleep(1)

    response = _http_json(
        _meta_url(version, f"{ig_user_id}/media_publish"),
        method="POST",
        data={
            "access_token": token,
            "creation_id": creation_id,
        },
    )
    return {
        "platform": "instagram",
        "external_post_id": str(response.get("id", "")),
        "raw": response,
    }


def _linkedin_author(account: dict) -> str:
    metadata = account.get("metadata", {}) if isinstance(account.get("metadata", {}), dict) else {}
    author = str(metadata.get("author_urn") or account.get("external_id") or "").strip()
    if not author.startswith("urn:li:"):
        account_type = str(account.get("account_type", "linkedin_member"))
        if account_type == "linkedin_organization":
            author = f"urn:li:organization:{author}"
        else:
            author = f"urn:li:person:{author}"
    return author


def _linkedin_upload_image(token: str, version: str, author: str, media_path: str) -> str:
    path = Path(media_path).expanduser()
    if not path.exists() or not path.is_file():
        raise SocialPublishError(f"No existe la imagen para LinkedIn: {media_path}")

    initialize = _http_json(
        f"{LINKEDIN_API_BASE}/rest/images?action=initializeUpload",
        method="POST",
        headers=_linkedin_headers(token, version),
        json_body={"initializeUploadRequest": {"owner": author}},
    )
    upload_info = initialize.get("value", {}) if isinstance(initialize.get("value", {}), dict) else {}
    upload_url = str(upload_info.get("uploadUrl", "")).strip()
    image_urn = str(upload_info.get("image", "")).strip()
    if not upload_url or not image_urn:
        raise SocialPublishError("LinkedIn no devolvio uploadUrl/image para la imagen.")

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    _http_upload(
        upload_url,
        path.read_bytes(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    return image_urn


def publish_linkedin(account: dict, token: str, publication: dict, settings: dict) -> dict:
    version = settings.get("linkedin_version", "202604")
    author = _linkedin_author(account)
    text = _publication_text(publication)
    media_path = str(publication.get("media_path", "")).strip()
    media_url = str(publication.get("media_url", "")).strip()

    content = {}
    if media_path:
        image_urn = _linkedin_upload_image(token, version, author, media_path)
        content = {
            "media": {
                "title": str(publication.get("title", "")).strip() or "Imagen",
                "id": image_urn,
            }
        }
    elif media_url:
        raise SocialPublishError("LinkedIn requiere media_path local para subir imagenes; media_url no es suficiente.")

    response = _http_json(
        f"{LINKEDIN_API_BASE}/rest/posts",
        method="POST",
        headers=_linkedin_headers(token, version),
        json_body={
            "author": author,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
            "content": content,
        },
    )
    return {
        "platform": "linkedin",
        "external_post_id": str(response.get("id", "")),
        "raw": response,
    }


def publish_publication(account: dict, token: str, publication: dict, settings: dict) -> dict:
    platform = str(publication.get("platform") or account.get("platform", "")).strip().lower()
    if platform == "facebook_page":
        return publish_facebook_page(account, token, publication, settings)
    if platform == "instagram":
        return publish_instagram(account, token, publication, settings)
    if platform == "linkedin":
        return publish_linkedin(account, token, publication, settings)
    if platform == "facebook_personal":
        raise SocialPublishError("Facebook personal usa publicacion asistida; no hay POST automatico.")
    raise SocialPublishError(f"Plataforma social no soportada: {platform}")


def facebook_assisted_url(link_url: str = "") -> str:
    cleaned_link = str(link_url).strip()
    if cleaned_link:
        return "https://www.facebook.com/sharer/sharer.php?" + parse.urlencode({"u": cleaned_link})
    return "https://www.facebook.com/"

