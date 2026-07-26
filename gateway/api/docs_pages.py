"""Loads the operator manual (docs/manual/*.md) for the admin panel's Docs section.

The Markdown files are the single source: they render here and stay readable on GitHub.
Each page must start with an H1 (the title) followed by one paragraph (the index-card
summary); both are lifted out of the body so the panel can render its own page header.

Rendered pages are cached per file modification time — the manual never changes at
runtime in production, and editing a file during development still refreshes the page.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from gateway.shared.markdown import Heading, render

# repo root / docs / manual — the Dockerfile copies this directory into the image
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "manual"

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
# a link to another manual page, the way GitHub writes it: "settings.md#data-cleanup"
_PAGE_LINK_RE = re.compile(r"^([a-z0-9-]+)\.md(#.*)?$")

SECTION_START = "Start here"
SECTION_MENUS = "Admin menus"
SECTION_INTEGRATION = "Integration and operations"


@dataclass(frozen=True)
class PageMeta:
    """Registry entry: nav order, grouping and icon name.

    `icon` names a branch of the doc_icon() macro in templates/_doc_icons.html — the SVG
    itself lives in the template, not here. Title and summary come from the Markdown file.
    """

    slug: str
    section: str
    icon: str


# Order defines the index layout and the previous/next links.
PAGES: tuple[PageMeta, ...] = (
    PageMeta("getting-started", SECTION_START, "book"),
    PageMeta("sms-format", SECTION_START, "type"),
    PageMeta("dashboard", SECTION_MENUS, "grid"),
    PageMeta("chats", SECTION_MENUS, "chat"),
    PageMeta("messages", SECTION_MENUS, "message"),
    PageMeta("api-tokens", SECTION_MENUS, "key"),
    PageMeta("webhook-log", SECTION_MENUS, "bolt"),
    PageMeta("logs", SECTION_MENUS, "terminal"),
    PageMeta("users", SECTION_MENUS, "users"),
    PageMeta("settings", SECTION_MENUS, "gear"),
    PageMeta("rest-api", SECTION_INTEGRATION, "code"),
    PageMeta("webhooks", SECTION_INTEGRATION, "share"),
    PageMeta("troubleshooting", SECTION_INTEGRATION, "lifebuoy"),
)

_BY_SLUG = {meta.slug: meta for meta in PAGES}


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    summary: str
    icon: str
    section: str
    html: str
    toc: tuple[Heading, ...]


def _resolve_link(target: str) -> str:
    """Rewrite 'settings.md#anchor' to '/admin/docs/settings#anchor'; leave the rest alone."""
    match = _PAGE_LINK_RE.fullmatch(target)
    if match is None or match.group(1) not in _BY_SLUG:
        return target
    return f"/admin/docs/{match.group(1)}{match.group(2) or ''}"


def _split_header(text: str) -> tuple[str, str, str]:
    """Pull the H1 title and the summary paragraph out of the body.

    Returns (title, summary, remaining markdown). A file that does not follow the
    convention still renders — it just falls back to its slug and an empty summary.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    heading = _H1_RE.match(lines[index]) if index < len(lines) else None
    if heading is None:
        return "", "", text
    title = heading.group(1)
    index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    summary: list[str] = []
    while index < len(lines) and lines[index].strip() and not lines[index].startswith("#"):
        summary.append(lines[index].strip())
        index += 1
    return title, " ".join(summary), "\n".join(lines[index:])


_cache: dict[str, tuple[float, DocPage]] = {}


def _source_path(slug: str) -> Path | None:
    """Path of a registered page, or None. Guards against traversal via the slug."""
    if slug not in _BY_SLUG or not _SLUG_RE.fullmatch(slug):
        return None
    path = DOCS_DIR / f"{slug}.md"
    return path if path.is_file() else None


def load_page(slug: str) -> DocPage | None:
    """Render one manual page, or None if it is unknown or missing on disk."""
    path = _source_path(slug)
    if path is None:
        return None
    stamp = path.stat().st_mtime
    cached = _cache.get(slug)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    meta = _BY_SLUG[slug]
    title, summary, body = _split_header(path.read_text(encoding="utf-8"))
    document = render(body, _resolve_link)
    page = DocPage(
        slug=slug,
        title=title or slug.replace("-", " ").capitalize(),
        summary=summary,
        icon=meta.icon,
        section=meta.section,
        html=document.html,
        toc=document.toc,
    )
    _cache[slug] = (stamp, page)
    return page


def list_pages() -> list[DocPage]:
    """Every page that exists on disk, in registry order."""
    return [page for meta in PAGES if (page := load_page(meta.slug)) is not None]


def sections() -> list[tuple[str, list[DocPage]]]:
    """Pages grouped for the index, preserving registry order within each section."""
    grouped: dict[str, list[DocPage]] = {}
    for page in list_pages():
        grouped.setdefault(page.section, []).append(page)
    return list(grouped.items())


def neighbours(slug: str) -> tuple[DocPage | None, DocPage | None]:
    """Previous and next page for the footer navigation."""
    pages = list_pages()
    position = next((index for index, page in enumerate(pages) if page.slug == slug), None)
    if position is None:
        return None, None
    return (pages[position - 1] if position > 0 else None), (
        pages[position + 1] if position + 1 < len(pages) else None
    )
