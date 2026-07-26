"""Minimal Markdown -> HTML renderer for the built-in manual (docs/manual/*.md).

Deliberately not a general-purpose Markdown implementation. It covers exactly the
subset the manual uses: ATX headings, paragraphs, fenced code, GFM pipe tables,
nested lists, blockquotes, thematic breaks, and inline code/strong/emphasis/links.
Keeping it in-repo avoids a runtime Markdown dependency — the gateway targets small
self-hosted hardware and must render the manual with no network and no build step.

Input is trusted (files shipped with the app), but every text run is HTML-escaped
anyway, so a stray tag in a document can never turn into live markup.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

TOC_LEVELS = (2, 3)  # headings that end up in the on-page table of contents
SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")

# Rewrites a link target before it is emitted. The manual links between pages the
# way GitHub does ("settings.md"), and the admin UI maps those to its own routes.
LinkResolver = Callable[[str], str]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```\s*([A-Za-z0-9_+.-]*)\s*$")
_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

# One alternation, one pass: a match inside `code` can never be re-scanned for
# emphasis, which is what naive multi-pass replacement gets wrong.
_INLINE_RE = re.compile(
    r"(?P<ticks>`+)(?P<code>.+?)(?P=ticks)"
    r"|\[(?P<link_text>[^\]]*)\]\((?P<link_href>[^)\s]*)\)"
    r"|<(?P<autolink>(?:https?://|mailto:)[^>\s]+)>"
    r"|\*\*(?P<strong>\S(?:.*?\S)?)\*\*"
    r"|(?<![\w*])\*(?P<em>\S(?:.*?\S)?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<em_u>\S(?:.*?\S)?)_(?![\w_])",
    re.DOTALL,
)


@dataclass(frozen=True)
class Heading:
    """A table-of-contents entry."""

    level: int
    text: str
    slug: str


@dataclass(frozen=True)
class Document:
    """Rendered manual page: HTML body plus the headings for the on-page TOC."""

    html: str
    toc: tuple[Heading, ...]


def slugify(text: str) -> str:
    """URL fragment for a heading: 'Character budget (GSM-7)' -> 'character-budget-gsm-7'."""
    return _SLUG_STRIP_RE.sub("-", text.lower()).strip("-") or "section"


def _text(value: str) -> str:
    return html.escape(value, quote=False)


def _attr(value: str) -> str:
    return html.escape(value, quote=True)


def _safe_href(href: str) -> str:
    """Allow only in-document, site-relative and http(s)/mailto targets."""
    if href.startswith(("#", "/")) or href.startswith(SAFE_LINK_SCHEMES):
        return href
    return "#"


def render_inline(text: str, resolve_link: LinkResolver | None = None) -> str:
    """Render inline markup in a single left-to-right pass."""
    out: list[str] = []
    position = 0
    for match in _INLINE_RE.finditer(text):
        out.append(_text(text[position : match.start()]))
        position = match.end()
        if match.group("code") is not None:
            out.append(f"<code>{_text(match.group('code').strip())}</code>")
        elif match.group("link_href") is not None:
            target = match.group("link_href")
            href = _safe_href(resolve_link(target) if resolve_link else target)
            label = render_inline(match.group("link_text"), resolve_link)
            out.append(f'<a href="{_attr(href)}">{label}</a>')
        elif match.group("autolink") is not None:
            url = match.group("autolink")
            out.append(f'<a href="{_attr(url)}">{_text(url)}</a>')
        elif match.group("strong") is not None:
            out.append(f"<strong>{render_inline(match.group('strong'), resolve_link)}</strong>")
        else:
            emphasis = match.group("em") if match.group("em") is not None else match.group("em_u")
            out.append(f"<em>{render_inline(emphasis, resolve_link)}</em>")
    out.append(_text(text[position:]))
    return "".join(out)


def _is_table_divider(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _alignments(divider: str) -> list[str]:
    styles = []
    for cell in _split_row(divider):
        if cell.startswith(":") and cell.endswith(":"):
            styles.append(' style="text-align:center"')
        elif cell.endswith(":"):
            styles.append(' style="text-align:right"')
        else:
            styles.append("")
    return styles


def _starts_block(line: str) -> bool:
    """True if the line opens a construct that must interrupt a paragraph."""
    stripped = line.strip()
    if not stripped:
        return True
    return bool(
        _HEADING_RE.match(line)
        or _FENCE_RE.match(line)
        or _RULE_RE.match(stripped)
        or _QUOTE_RE.match(line)
        or _UL_RE.match(line)
        or _OL_RE.match(line)
    )


class _Renderer:
    def __init__(self, lines: list[str], resolve_link: LinkResolver | None = None) -> None:
        self.lines = lines
        self.resolve_link = resolve_link
        self.index = 0
        self.toc: list[Heading] = []
        self.slugs: dict[str, int] = {}

    # --- helpers ---

    def _inline(self, text: str) -> str:
        return render_inline(text, self.resolve_link)

    def _unique_slug(self, text: str) -> str:
        slug = slugify(text)
        count = self.slugs.get(slug, 0)
        self.slugs[slug] = count + 1
        return slug if count == 0 else f"{slug}-{count + 1}"

    def _peek(self, offset: int = 0) -> str | None:
        position = self.index + offset
        return self.lines[position] if position < len(self.lines) else None

    # --- block parsers ---

    def blocks(self, stop_indent: int = 0) -> list[str]:
        out: list[str] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.strip():
                self.index += 1
                continue
            if stop_indent and len(line) - len(line.lstrip(" ")) < stop_indent:
                break
            if (fence := _FENCE_RE.match(line)) is not None:
                out.append(self._fence(fence.group(1)))
            elif (heading := _HEADING_RE.match(line)) is not None:
                out.append(self._heading(heading))
            elif _RULE_RE.match(line.strip()):
                self.index += 1
                out.append("<hr>")
            elif _QUOTE_RE.match(line) is not None:
                out.append(self._blockquote())
            elif self._at_table():
                out.append(self._table())
            elif _UL_RE.match(line) or _OL_RE.match(line):
                out.append(self._list())
            else:
                out.append(self._paragraph())
        return out

    def _fence(self, language: str) -> str:
        self.index += 1
        body: list[str] = []
        while self.index < len(self.lines) and not self.lines[self.index].startswith("```"):
            body.append(self.lines[self.index])
            self.index += 1
        self.index += 1  # closing fence (or end of input)
        css = f' class="language-{_attr(language)}"' if language else ""
        return f"<pre><code{css}>{_text(chr(10).join(body))}</code></pre>"

    def _heading(self, match: re.Match[str]) -> str:
        self.index += 1
        level = len(match.group(1))
        raw = match.group(2)
        slug = self._unique_slug(re.sub(r"[`*_]", "", raw))
        if level in TOC_LEVELS:
            self.toc.append(Heading(level=level, text=raw, slug=slug))
        anchor = f'<a class="doc-anchor" href="#{_attr(slug)}" aria-label="Link to this section">#</a>'
        return f'<h{level} id="{_attr(slug)}">{self._inline(raw)}{anchor}</h{level}>'

    def _blockquote(self) -> str:
        inner: list[str] = []
        while self.index < len(self.lines):
            match = _QUOTE_RE.match(self.lines[self.index])
            if match is None:
                break
            inner.append(match.group(1))
            self.index += 1
        nested = _Renderer(inner, self.resolve_link)
        body = "\n".join(nested.blocks())
        return f"<blockquote>\n{body}\n</blockquote>"

    def _at_table(self) -> bool:
        line = self._peek()
        follower = self._peek(1)
        return bool(line and "|" in line and follower and _is_table_divider(follower))

    def _table(self) -> str:
        headers = _split_row(self.lines[self.index])
        styles = _alignments(self.lines[self.index + 1])
        self.index += 2
        head = "".join(
            f"<th{style}>{self._inline(cell)}</th>"
            for cell, style in zip(headers, styles + [""] * len(headers), strict=False)
        )
        rows: list[str] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.strip() or "|" not in line:
                break
            cells = _split_row(self.lines[self.index])
            body = "".join(
                f"<td{style}>{self._inline(cell)}</td>"
                for cell, style in zip(cells, styles + [""] * len(cells), strict=False)
            )
            rows.append(f"<tr>{body}</tr>")
            self.index += 1
        body_rows = "\n".join(rows)
        return (
            '<div class="doc-table-wrap"><table>\n'
            f"<thead><tr>{head}</tr></thead>\n"
            f"<tbody>\n{body_rows}\n</tbody>\n</table></div>"
        )

    def _list(self) -> str:
        first = _UL_RE.match(self.lines[self.index]) or _OL_RE.match(self.lines[self.index])
        assert first is not None
        base_indent = len(first.group(1))
        ordered = _OL_RE.match(self.lines[self.index]) is not None
        items: list[str] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.strip():
                # a blank line only ends the list if what follows is not still part of it
                follower = self._peek(1)
                if follower is None or not follower.strip():
                    break
                nxt = _UL_RE.match(follower) or _OL_RE.match(follower)
                if nxt is None or len(nxt.group(1)) < base_indent:
                    break
                self.index += 1
                continue
            match = _UL_RE.match(line) or _OL_RE.match(line)
            if match is None:
                # lazy continuation: an indented plain line belongs to the current item
                if items and line.startswith(" " * (base_indent + 1)):
                    items[-1] += " " + self._inline(line.strip())
                    self.index += 1
                    continue
                break
            indent = len(match.group(1))
            if indent < base_indent:
                break
            if indent > base_indent:
                if items:
                    items[-1] += "\n" + self._list()
                else:  # over-indented first item: treat it as this level
                    items.append(self._inline(match.group(2)))
                    self.index += 1
                continue
            items.append(self._inline(match.group(2)))
            self.index += 1
        tag = "ol" if ordered else "ul"
        body = "\n".join(f"<li>{item}</li>" for item in items)
        return f"<{tag}>\n{body}\n</{tag}>"

    def _paragraph(self) -> str:
        body: list[str] = [self.lines[self.index]]
        self.index += 1
        while self.index < len(self.lines) and not _starts_block(self.lines[self.index]):
            if self._at_table():
                break
            body.append(self.lines[self.index])
            self.index += 1
        return f"<p>{self._inline(chr(10).join(line.strip() for line in body))}</p>"


def render(markdown_text: str, resolve_link: LinkResolver | None = None) -> Document:
    """Render a manual page to HTML plus its table of contents."""
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n")
    renderer = _Renderer(lines, resolve_link)
    body = "\n".join(renderer.blocks())
    return Document(html=body, toc=tuple(renderer.toc))
