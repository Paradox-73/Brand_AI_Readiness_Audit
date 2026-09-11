# -*- coding: utf-8 -*-
"""A title with the template's own line breaks still in it.

A real page carried `'\\n  <product> by <brand>\\n\\n\\n'` - a template writing a
variable across three lines of its own source, and the line breaks travelling
with the string. Whatever reads that title gets the whitespace.

This is an extraction fault rather than a check that is missing, and the reason
no check reported it is that a check cannot: what a check receives is the
record, and only some of the record was folded. The `<title>` element itself
went through `_text_or_empty`, which folds - so the fault survived in the two
other places a page states its own name, and from there into every comparison
made on those strings and every snippet that quotes them back at the owner.

The record is what six checks, both renderers and every paste-ready block read,
so one fold in the extractor fixes all of them; a fold inside one check would
fix one and leave five.
"""

from __future__ import annotations

import os
import sys

from conftest import SCRIPTS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from audit_common import make_soup  # noqa: E402
from page_extract import _document_title, _extract_jsonld, _meta_tags, extract_page  # noqa: E402

SITE = "https://an-invented-host.test"

# As a commerce template emits it: the variable on its own line, and the
# closing tag three lines further down.
BROKEN = "\n  Slate Widget by Northmoor\n\n\n"
WHOLE = "Slate Widget by Northmoor"


def _extract(html):
    return extract_page(
        url=SITE + "/p/slate-widget", final_url=SITE + "/p/slate-widget", status=200,
        headers={"content-type": "text/html"}, html=html, redirect_chain=[],
        elapsed_ms=12, depth=1, source="bfs", origin=SITE)


def _strings(value, path="", found=None):
    """Every string leaf in the record, with the field path that holds it."""
    found = [] if found is None else found
    if isinstance(value, str):
        found.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            _strings(item, "{}/{}".format(path, key), found)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _strings(item, "{}[{}]".format(path, index), found)
    return found


def test_the_title_element_is_folded():
    soup = make_soup("<html><head><title>{}</title></head><body></body></html>".format(BROKEN))
    assert _document_title(soup) == WHOLE


def test_a_meta_content_value_is_folded_and_not_merely_stripped():
    """`.strip()` alone cleared the ends and left the middle, so a description
    written as three lines of template source arrived as one string carrying
    two line breaks."""
    soup = make_soup(
        '<html><head><meta name="description" content="{}">'
        '<meta property="og:title" content="Slate\nWidget"></head></html>'.format(BROKEN))
    meta = _meta_tags(soup)
    assert meta["description"] == WHOLE
    assert meta["og:title"] == "Slate Widget"


def test_a_json_ld_value_is_folded():
    """The exact shape seen in the wild. A JSON string can carry `\\n`, and
    nothing between `json.loads` and the report touched it."""
    soup = make_soup(
        '<html><head><script type="application/ld+json">'
        '{"@type": "Product", "name": "\\n  Slate Widget by Northmoor\\n\\n\\n"}'
        "</script></head></html>")
    blocks, errors = _extract_jsonld(soup)
    assert not errors
    assert blocks[0]["name"] == WHOLE


def test_no_field_of_the_record_carries_a_line_break_the_page_wrote():
    """The guarantee, stated over the whole record rather than field by field,
    so a field added later cannot quietly reopen this."""
    html = (
        '<html lang="en"><head>'
        "<title>{broken}</title>"
        '<meta name="description" content="{broken}">'
        '<meta property="og:title" content="{broken}">'
        '<script type="application/ld+json">'
        '{{"@type": "Product", "name": "\\n  Slate Widget by Northmoor\\n\\n\\n",'
        ' "brand": {{"@type": "Brand", "name": "\\nNorthmoor\\n"}}}}'
        "</script></head>"
        "<body><h1>\n  Slate Widget\n</h1><p>A widget, in slate.</p></body></html>"
    ).format(broken=BROKEN)

    record = _extract(html)
    carrying = [path for path, value in _strings(record) if "\n" in value or "\r" in value]
    assert not carrying, "these fields still carry the template's line breaks: {}".format(
        carrying)


def test_the_value_is_folded_and_not_edited():
    """Folding whitespace changes no character the site chose. The separator
    repair `_text_or_empty` runs is deliberately not applied here: it exists to
    undo the spaces `get_text(" ")` inserts around inline markup, and running it
    over an attribute value would silently rewrite punctuation the site wrote.
    """
    soup = make_soup(
        '<html><head><meta name="description" content="Sizes 6 , 7 and 8 ( in stock )">'
        "</head></html>")
    assert _meta_tags(soup)["description"] == "Sizes 6 , 7 and 8 ( in stock )"
