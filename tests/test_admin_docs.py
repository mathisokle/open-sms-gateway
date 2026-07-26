"""Admin acceptance tests: the built-in manual under /admin/docs."""

import re

import pytest
from fastapi.testclient import TestClient

from gateway.api.docs_pages import PAGES, list_pages, load_page, neighbours
from tests.conftest import admin_login


def test_every_registered_page_exists_on_disk_and_renders() -> None:
    """The registry and docs/manual/ must not drift apart."""
    assert len(list_pages()) == len(PAGES)


@pytest.mark.parametrize("meta", PAGES, ids=lambda meta: meta.slug)
def test_each_page_has_a_title_summary_and_body(meta) -> None:
    page = load_page(meta.slug)

    assert page is not None
    assert page.title and not page.title.startswith("#")
    assert page.summary, f"{meta.slug} is missing the summary paragraph after its H1"
    assert page.html.strip()
    assert page.toc, f"{meta.slug} has no ## headings"


def test_docs_index_lists_every_page(api: TestClient) -> None:
    admin_login(api)

    response = api.get("/admin/docs")

    assert response.status_code == 200
    for meta in PAGES:
        assert f'href="/admin/docs/{meta.slug}"' in response.text


@pytest.mark.parametrize("meta", PAGES, ids=lambda meta: meta.slug)
def test_each_page_renders_over_http(api: TestClient, meta) -> None:
    admin_login(api)

    response = api.get(f"/admin/docs/{meta.slug}")

    assert response.status_code == 200
    assert "doc-body" in response.text


def test_sms_format_page_documents_the_envelope_and_the_limits(api: TestClient) -> None:
    admin_login(api)

    text = api.get("/admin/docs/sms-format").text

    assert "SOURCE: Headline" in text
    assert "160" in text and "153" in text and "70" in text and "67" in text
    assert "UCS-2" in text


def test_cross_page_markdown_links_are_rewritten_to_admin_routes(api: TestClient) -> None:
    admin_login(api)

    text = api.get("/admin/docs/getting-started").text

    assert 'href="/admin/docs/settings"' in text
    assert ".md" not in text.split("</article>")[0].split('class="card doc-body"')[-1]


def test_no_dead_links_or_anchors_anywhere_in_the_manual() -> None:
    """Every cross-page link and every #fragment must resolve. Docs rot silently otherwise."""
    pages = {page.slug: page for page in list_pages()}
    ids = {slug: set(re.findall(r'id="([^"]+)"', page.html)) for slug, page in pages.items()}
    broken: list[str] = []

    for slug, page in pages.items():
        for href in re.findall(r'href="([^"]+)"', page.html):
            if ".md" in href:
                broken.append(f"{slug}: unrewritten Markdown link {href}")
            elif href.startswith("#"):
                if href[1:] not in ids[slug]:
                    broken.append(f"{slug}: no such anchor {href}")
            elif href.startswith("/admin/docs/"):
                target, _, fragment = href.removeprefix("/admin/docs/").partition("#")
                if target not in pages:
                    broken.append(f"{slug}: link to unknown page {href}")
                elif fragment and fragment not in ids[target]:
                    broken.append(f"{slug}: no anchor #{fragment} on {target}")
            elif not href.startswith(("/admin/", "http://", "https://")):
                broken.append(f"{slug}: unexpected link target {href}")

    assert not broken, "\n".join(broken)


def test_unknown_page_is_404(api: TestClient) -> None:
    admin_login(api)

    assert api.get("/admin/docs/does-not-exist").status_code == 404


def test_slug_cannot_escape_the_docs_directory(api: TestClient) -> None:
    admin_login(api)

    for slug in ("../SPEC", "..%2FSPEC", "....//SPEC"):
        assert api.get(f"/admin/docs/{slug}").status_code == 404


def test_docs_require_a_session(api: TestClient) -> None:
    response = api.get("/admin/docs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_neighbours_walk_the_registry_order() -> None:
    first, last = PAGES[0].slug, PAGES[-1].slug

    assert neighbours(first)[0] is None
    assert neighbours(first)[1].slug == PAGES[1].slug
    assert neighbours(last)[1] is None
    assert neighbours(last)[0].slug == PAGES[-2].slug


def test_docs_link_is_in_the_navigation(api: TestClient) -> None:
    admin_login(api)

    assert "<span>Docs</span>" in api.get("/admin").text
