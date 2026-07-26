"""Admin acceptance tests: webhook target configuration (gateway_config)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.configstore import WEBHOOK_SECRET, WEBHOOK_URL, get_config
from tests.conftest import admin_login


def read_config(api: TestClient) -> tuple[str | None, str | None]:
    with Session(api.app.state.engine) as session:
        return get_config(session, WEBHOOK_URL), get_config(session, WEBHOOK_SECRET)


def test_setting_webhook_url_generates_secret(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/settings/webhook",
        data={"webhook_url": "https://receiver.example/hook"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    url, secret = read_config(api)
    assert url == "https://receiver.example/hook"
    assert secret is not None and secret.startswith("whsec_")


def test_rotate_changes_secret(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/settings/webhook", data={"webhook_url": "https://receiver.example/hook"})
    _, first = read_config(api)

    api.post("/admin/settings/webhook/rotate-secret", follow_redirects=False)

    _, rotated = read_config(api)
    assert rotated is not None and rotated.startswith("whsec_")
    assert rotated != first


def test_clearing_url_clears_secret(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/settings/webhook", data={"webhook_url": "https://receiver.example/hook"})

    api.post("/admin/settings/webhook", data={"webhook_url": ""}, follow_redirects=False)

    assert read_config(api) == (None, None)


def test_settings_page_shows_url_and_secret(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/settings/webhook", data={"webhook_url": "https://receiver.example/hook"})
    _, secret = read_config(api)

    response = api.get("/admin/settings")

    assert response.status_code == 200
    assert "https://receiver.example/hook" in response.text
    assert secret in response.text  # the operator needs it to verify signatures


def test_language_switching_is_gone_the_ui_is_english_only(api: TestClient) -> None:
    admin_login(api)

    assert "Sent today" in api.get("/admin").text
    assert api.post("/admin/settings/language", data={"language": "de"}).status_code == 404
    assert "Deutsch" not in api.get("/admin/settings").text


def test_webhook_url_requires_http_scheme(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/settings/webhook",
        data={"webhook_url": "ftp://receiver.example/hook"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    with Session(api.app.state.engine) as session:
        assert get_config(session, WEBHOOK_URL) is None


def test_rotate_secret_without_url_is_rejected(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/settings/webhook/rotate-secret", follow_redirects=False)

    assert response.status_code == 422
    with Session(api.app.state.engine) as session:
        assert get_config(session, WEBHOOK_SECRET) is None
