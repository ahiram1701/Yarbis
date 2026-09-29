import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import credential_store
import memory
import notifications
import secrets_redaction
import social_publishing
import tools

TEST_RUNTIME_DIR = Path.cwd() / "tests_runtime"


class SocialFeatureTestCase(unittest.TestCase):
    def setUp(self):
        TEST_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def test_normalize_state_keeps_social_shape(self):
        normalized = memory.normalize_state({
            "social": {
                "settings": {
                    "meta_graph_version": "v24.0",
                    "linkedin_version": "202604",
                    "require_confirmation": False,
                },
                "accounts": [{
                    "id": "acct-1",
                    "platform": "linkedin",
                    "account_type": "linkedin_member",
                    "display_name": "Usuario",
                    "external_id": "urn:li:person:123",
                    "token_ref": "cred-1",
                }],
                "drafts": [{
                    "id": "draft-1",
                    "title": "Lanzamiento",
                    "platform": "instagram",
                    "body": "Hola mundo",
                    "hashtags": "yarbis, ia",
                }],
                "pending_publications": [{
                    "id": "pub-1",
                    "title": "Post",
                    "platform": "facebook_page",
                    "body": "Listo",
                    "confirmation_phrase": "PUBLICAR pub-1",
                }],
            }
        })

        # require_confirmation ahora es configurable (antes estaba hardcodeado a True).
        self.assertFalse(normalized["social"]["settings"]["require_confirmation"])
        self.assertEqual(normalized["social"]["accounts"][0]["platform"], "linkedin")
        self.assertEqual(normalized["social"]["drafts"][0]["hashtags"], ["yarbis", "ia"])
        self.assertEqual(
            normalized["social"]["pending_publications"][0]["confirmation_phrase"],
            "PUBLICAR pub-1",
        )

    def test_redacts_social_tokens_from_credential_store_and_urls(self):
        credential_dir = TEST_RUNTIME_DIR / f"credentials_redact_{id(self)}"
        with patch.object(credential_store, "CREDENTIALS_DIR", credential_dir):
            token_ref = credential_store.save_secret("social-token-123", kind="social")
            state = memory.normalize_state({
                "social": {
                    "accounts": [{
                        "id": "acct-1",
                        "platform": "facebook_page",
                        "account_type": "facebook_page",
                        "display_name": "Page",
                        "external_id": "42",
                        "token_ref": token_ref,
                    }]
                }
            })

            redacted = secrets_redaction.redact_secrets(
                "Bearer social-token-123&access_token=social-token-123&client_secret=abc123",
                state=state,
            )

        self.assertNotIn("social-token-123", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertIn("[redacted]", redacted)

    def test_start_social_oauth_stores_meta_accounts_without_tokens_in_state(self):
        state_path = TEST_RUNTIME_DIR / f"social_oauth_state_{id(self)}.json"
        credential_dir = TEST_RUNTIME_DIR / f"credentials_oauth_{id(self)}"
        responses = [
            {"access_token": "short"},
            {"access_token": "long", "expires_in": 3600},
            {"id": "user-1", "name": "Usuario"},
            {
                "data": [{
                    "id": "page-1",
                    "name": "Yarbis Page",
                    "access_token": "page-token",
                    "instagram_business_account": {
                        "id": "ig-1",
                        "username": "yarbis_ig",
                    },
                }]
            },
        ]

        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(credential_store, "CREDENTIALS_DIR", credential_dir):
                memory.save_state(memory.default_state())
                with patch("social_oauth._http_json", side_effect=responses):
                    result = tools.start_social_oauth(
                        provider="meta",
                        client_id="app-id",
                        client_secret="app-secret",
                        authorization_response_url="http://127.0.0.1:8765/oauth/meta/callback?code=abc",
                        open_browser=False,
                    )
                state = memory.load_state()

        self.assertIn("Cuentas sociales conectadas", result)
        self.assertEqual(len(state["social"]["accounts"]), 3)
        self.assertNotIn("page-token", str(state["social"]))
        self.assertTrue(any(account["platform"] == "facebook_personal" for account in state["social"]["accounts"]))
        self.assertTrue(any(account["platform"] == "instagram" for account in state["social"]["accounts"]))

    def test_set_social_confirmation_and_publish_without_phrase_when_disabled(self):
        state_path = TEST_RUNTIME_DIR / f"social_noconfirm_state_{id(self)}.json"
        credential_dir = TEST_RUNTIME_DIR / f"credentials_noconfirm_{id(self)}"
        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(credential_store, "CREDENTIALS_DIR", credential_dir):
                token_ref = credential_store.save_secret("page-token", kind="social")
                state = memory.default_state()
                state["social"]["accounts"].append({
                    "id": "acct-page",
                    "platform": "facebook_page",
                    "account_type": "facebook_page",
                    "display_name": "Page",
                    "external_id": "page-1",
                    "token_ref": token_ref,
                })
                memory.save_state(state)
                self.assertIn("desactivada", tools.set_social_confirmation(False).lower())
                self.assertFalse(memory.load_state()["social"]["settings"]["require_confirmation"])
                prepared = tools.prepare_social_publication(
                    platform="facebook_page", target_account_id="acct-page", title="Post", body="Hola",
                )
                publication_id = prepared.split("[", 1)[1].split("]", 1)[0]
                with patch("social_publishing._http_json", return_value={"id": "page-1_1"}) as http_mock:
                    published = tools.confirm_social_publication(publication_id, "")
                state_after = memory.load_state()
        self.assertIn("Publicacion social completada", published)
        self.assertEqual(http_mock.call_count, 1)
        self.assertEqual(state_after["social"]["history"][0]["status"], "published")

    def test_confirm_social_publication_requires_exact_phrase_before_posting(self):
        state_path = TEST_RUNTIME_DIR / f"social_publish_state_{id(self)}.json"
        credential_dir = TEST_RUNTIME_DIR / f"credentials_publish_{id(self)}"
        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(credential_store, "CREDENTIALS_DIR", credential_dir):
                token_ref = credential_store.save_secret("page-token", kind="social")
                state = memory.default_state()
                state["social"]["accounts"].append({
                    "id": "acct-page",
                    "platform": "facebook_page",
                    "account_type": "facebook_page",
                    "display_name": "Page",
                    "external_id": "page-1",
                    "token_ref": token_ref,
                })
                memory.save_state(state)
                prepared = tools.prepare_social_publication(
                    platform="facebook_page",
                    target_account_id="acct-page",
                    title="Post",
                    body="Hola",
                )
                publication_id = prepared.split("[", 1)[1].split("]", 1)[0]
                blocked = tools.confirm_social_publication(publication_id, "publicar")
                with patch("social_publishing._http_json", return_value={"id": "page-1_99"}) as http_mock:
                    published = tools.confirm_social_publication(
                        publication_id,
                        f"PUBLICAR {publication_id}",
                    )
                state_after = memory.load_state()

        self.assertIn("bloqueada", blocked)
        self.assertIn("Publicacion social completada", published)
        self.assertEqual(http_mock.call_count, 1)
        self.assertEqual(state_after["social"]["history"][0]["status"], "published")

    def test_list_recent_media_lists_registered_images(self):
        state_path = TEST_RUNTIME_DIR / f"social_media_list_{id(self)}.json"
        with patch.object(memory, "STATE_FILE", state_path):
            state = memory.default_state()
            state["social"]["media_inbox"].append({
                "id": "media-xyz",
                "path": "C:/x/a.jpg",
                "telegram_file_id": "fid",
                "source": "telegram",
                "caption": "un gato",
                "media_type": "image",
                "received_at": "2026-07-06T00:00:00+00:00",
            })
            memory.save_state(state)
            out = tools.list_recent_media()

        self.assertIn("media-xyz", out)
        self.assertIn("un gato", out)

    def test_save_social_draft_with_media_id_attaches_local_path(self):
        state_path = TEST_RUNTIME_DIR / f"social_draft_media_{id(self)}.json"
        with patch.object(memory, "STATE_FILE", state_path):
            state = memory.default_state()
            state["social"]["media_inbox"].append({
                "id": "media-d1",
                "path": "C:/x/pic.jpg",
                "telegram_file_id": "fidd",
                "source": "telegram",
                "media_type": "image",
                "received_at": "2026-07-06T00:00:00+00:00",
            })
            memory.save_state(state)
            result = tools.save_social_draft(
                title="T", platform="instagram", body="Hola", media_id="media-d1"
            )
            state_after = memory.load_state()

        self.assertIn("Draft social guardado", result)
        draft = state_after["social"]["drafts"][0]
        self.assertEqual(draft["media_path"], "C:/x/pic.jpg")
        self.assertEqual(draft["media_type"], "image")
        self.assertEqual(draft["metadata"].get("telegram_file_id"), "fidd")

    def test_confirm_instagram_uses_fresh_telegram_url_from_media(self):
        state_path = TEST_RUNTIME_DIR / f"social_ig_media_state_{id(self)}.json"
        credential_dir = TEST_RUNTIME_DIR / f"credentials_ig_{id(self)}"
        with patch.object(memory, "STATE_FILE", state_path):
            with patch.object(credential_store, "CREDENTIALS_DIR", credential_dir):
                token_ref = credential_store.save_secret("ig-token", kind="social")
                state = memory.default_state()
                state["social"]["accounts"].append({
                    "id": "acct-ig",
                    "platform": "instagram",
                    "account_type": "instagram_professional",
                    "display_name": "IG",
                    "external_id": "ig-1",
                    "token_ref": token_ref,
                })
                state["social"]["media_inbox"].append({
                    "id": "media-abc",
                    "path": str(TEST_RUNTIME_DIR / "img.jpg"),
                    "telegram_file_id": "tg-file-1",
                    "source": "telegram",
                    "media_type": "image",
                    "received_at": "2026-07-06T00:00:00+00:00",
                })
                memory.save_state(state)
                prepared = tools.prepare_social_publication(
                    platform="instagram",
                    target_account_id="acct-ig",
                    title="Foto",
                    body="Mira esto",
                    media_id="media-abc",
                )
                publication_id = prepared.split("[", 1)[1].split("]", 1)[0]
                responses = [
                    {"id": "cont-1"},
                    {"status_code": "FINISHED"},
                    {"id": "ig-post-9"},
                ]
                with patch.object(
                    notifications, "telegram_file_public_url", return_value="https://fresh.telegram/url.jpg"
                ) as url_mock:
                    with patch("social_publishing._http_json", side_effect=responses) as http_mock:
                        published = tools.confirm_social_publication(
                            publication_id, f"PUBLICAR {publication_id}"
                        )
                state_after = memory.load_state()

        self.assertIn("Publicacion social completada", published)
        url_mock.assert_called_once_with("tg-file-1")
        first_call = http_mock.call_args_list[0]
        self.assertEqual(first_call.kwargs["data"]["image_url"], "https://fresh.telegram/url.jpg")
        self.assertEqual(state_after["social"]["history"][0]["status"], "published")

    def test_facebook_personal_confirmation_opens_assisted_flow_without_post(self):
        state_path = TEST_RUNTIME_DIR / f"social_assisted_state_{id(self)}.json"
        with patch.object(memory, "STATE_FILE", state_path):
            memory.save_state(memory.default_state())
            prepared = tools.prepare_social_publication(
                platform="facebook_personal",
                title="Personal",
                body="Post asistido",
                link_url="https://example.com",
            )
            publication_id = prepared.split("[", 1)[1].split("]", 1)[0]
            with patch("tools._copy_text_to_clipboard", return_value="copiado") as copy_mock:
                with patch("tools.open_system_target_impl", return_value="abierto") as open_mock:
                    with patch("social_publishing._http_json") as http_mock:
                        result = tools.confirm_social_publication(
                            publication_id,
                            f"PUBLICAR {publication_id}",
                        )
            state_after = memory.load_state()

        self.assertIn("Publicacion asistida", result)
        self.assertEqual(copy_mock.call_count, 1)
        self.assertEqual(open_mock.call_count, 1)
        self.assertEqual(http_mock.call_count, 0)
        self.assertEqual(state_after["social"]["history"][0]["status"], "assisted_opened")

    def test_social_publishing_adapters_call_expected_endpoints(self):
        with patch("social_publishing._http_json", return_value={"id": "page-1_1"}) as http_mock:
            facebook = social_publishing.publish_facebook_page(
                {"external_id": "page-1"},
                "token",
                {"body": "Hola", "platform": "facebook_page"},
                {"meta_graph_version": "v24.0"},
            )

        self.assertEqual(facebook["platform"], "facebook_page")
        self.assertEqual(http_mock.call_count, 1)

    def test_instagram_publish_uses_container_then_publish(self):
        responses = [
            {"id": "container-1"},
            {"status_code": "FINISHED"},
            {"id": "ig-post-1"},
        ]
        with patch("social_publishing._http_json", side_effect=responses) as http_mock:
            result = social_publishing.publish_instagram(
                {"external_id": "ig-1"},
                "token",
                {
                    "body": "Caption",
                    "platform": "instagram",
                    "media_url": "https://example.com/image.jpg",
                    "media_type": "image",
                },
                {"meta_graph_version": "v24.0"},
            )

        self.assertEqual(result["external_post_id"], "ig-post-1")
        self.assertEqual(http_mock.call_count, 3)

    def test_linkedin_publish_uploads_image_then_posts(self):
        image_path = TEST_RUNTIME_DIR / f"linkedin_{id(self)}.jpg"
        image_path.write_bytes(b"fake-image")
        responses = [
            {"value": {"uploadUrl": "https://upload.linkedin.test", "image": "urn:li:image:1"}},
            {"id": "urn:li:share:1"},
        ]
        with patch("social_publishing._http_json", side_effect=responses) as http_mock:
            with patch("social_publishing._http_upload") as upload_mock:
                result = social_publishing.publish_linkedin(
                    {"account_type": "linkedin_member", "external_id": "urn:li:person:123"},
                    "token",
                    {
                        "body": "Hola LinkedIn",
                        "platform": "linkedin",
                        "media_path": str(image_path),
                    },
                    {"linkedin_version": "202604"},
                )

        self.assertEqual(result["external_post_id"], "urn:li:share:1")
        self.assertEqual(http_mock.call_count, 2)
        self.assertEqual(upload_mock.call_count, 1)

    def test_agent_registers_social_tools(self):
        for name in (
            "social_accounts_overview",
            "start_social_oauth",
            "save_social_draft",
            "list_social_drafts",
            "prepare_social_publication",
            "confirm_social_publication",
            "open_assisted_social_post",
        ):
            self.assertIn(name, agent.available_functions)


if __name__ == "__main__":
    unittest.main()
