"""Unit tests for the in-repo Markdown subset renderer (gateway/shared/markdown.py)."""

from gateway.shared.markdown import Heading, render, render_inline, slugify


def test_headings_get_slugged_ids_and_feed_the_toc() -> None:
    doc = render("# Title\n\n## Character budget (GSM-7)\n\n### Links\n")
    assert '<h1 id="title">' in doc.html
    assert '<h2 id="character-budget-gsm-7">' in doc.html
    assert doc.toc == (
        Heading(level=2, text="Character budget (GSM-7)", slug="character-budget-gsm-7"),
        Heading(level=3, text="Links", slug="links"),
    )


def test_h1_and_h4_stay_out_of_the_toc() -> None:
    doc = render("# One\n\n#### Four\n")
    assert doc.toc == ()
    assert '<h4 id="four">' in doc.html


def test_duplicate_headings_get_unique_slugs() -> None:
    doc = render("## Notes\n\n## Notes\n")
    assert [heading.slug for heading in doc.toc] == ["notes", "notes-2"]


def test_paragraph_joins_soft_wrapped_lines() -> None:
    doc = render("first line\nsecond line\n\nnext paragraph\n")
    assert doc.html == "<p>first line\nsecond line</p>\n<p>next paragraph</p>"


def test_fenced_code_keeps_content_verbatim_and_tags_the_language() -> None:
    doc = render('```json\n{"to": "+41791234567", "body": "a < b"}\n```\n')
    # quotes stay readable inside text content; angle brackets must not become markup
    assert doc.html == (
        '<pre><code class="language-json">{"to": "+41791234567", "body": "a &lt; b"}</code></pre>'
    )


def test_markup_inside_code_is_not_interpreted() -> None:
    assert render_inline("use `**not bold**` here") == "use <code>**not bold**</code> here"


def test_inline_emphasis_links_and_autolinks() -> None:
    assert render_inline("**bold** and *italic* and _also_") == (
        "<strong>bold</strong> and <em>italic</em> and <em>also</em>"
    )
    assert render_inline("[docs](/admin/docs)") == '<a href="/admin/docs">docs</a>'
    assert (
        render_inline("<https://example.com/x>")
        == '<a href="https://example.com/x">https://example.com/x</a>'
    )


def test_underscores_inside_words_are_not_emphasis() -> None:
    assert render_inline("webhook_url and next_retry_at") == "webhook_url and next_retry_at"


def test_html_in_source_is_escaped_never_emitted() -> None:
    doc = render("<script>alert(1)</script>\n")
    assert "<script>" not in doc.html
    assert "&lt;script&gt;" in doc.html


def test_dangerous_link_schemes_are_neutralised() -> None:
    assert render_inline("[x](javascript:alert)") == '<a href="#">x</a>'
    assert render_inline("[x](data:text/html;base64,AA)") == '<a href="#">x</a>'
    assert render_inline("[x](https://ok.example)") == '<a href="https://ok.example">x</a>'
    assert render_inline("[x](#anchor)") == '<a href="#anchor">x</a>'


def test_pipe_table_with_alignment() -> None:
    doc = render("| Key | Value |\n|---|---:|\n| `to` | E.164 |\n")
    assert '<thead><tr><th>Key</th><th style="text-align:right">Value</th></tr></thead>' in doc.html
    assert '<td><code>to</code></td><td style="text-align:right">E.164</td>' in doc.html


def test_unordered_and_ordered_lists() -> None:
    assert render("- one\n- two\n").html == "<ul>\n<li>one</li>\n<li>two</li>\n</ul>"
    assert render("1. one\n2. two\n").html == "<ol>\n<li>one</li>\n<li>two</li>\n</ol>"


def test_nested_list_is_rendered_inside_its_parent_item() -> None:
    doc = render("- outer\n  - inner\n- second\n")
    assert doc.html == ("<ul>\n<li>outer\n<ul>\n<li>inner</li>\n</ul></li>\n<li>second</li>\n</ul>")


def test_list_interrupts_a_paragraph() -> None:
    doc = render("intro text\n- one\n")
    assert doc.html == "<p>intro text</p>\n<ul>\n<li>one</li>\n</ul>"


def test_blockquote_renders_nested_blocks() -> None:
    doc = render("> **Warning**\n> second line\n")
    assert doc.html == "<blockquote>\n<p><strong>Warning</strong>\nsecond line</p>\n</blockquote>"


def test_thematic_break() -> None:
    assert "<hr>" in render("a\n\n---\n\nb\n").html


def test_slugify_collapses_punctuation() -> None:
    assert slugify("GSM-7 vs. UCS-2 — what changes?") == "gsm-7-vs-ucs-2-what-changes"
    assert slugify("???") == "section"
