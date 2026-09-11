# -*- coding: utf-8 -*-
"""Pagination a crawler can follow without pressing a button, read from the head.

A shop's collection page carried `<link rel="next" href="/collections/bags?page=2">`
in its head and was told to "Add `<link rel="next">`". The pagination check had
been taught to read a `rel_next` field, and nothing recorded it: the extractor
kept the canonical link and dropped every other `<link>` in the head.

Every host below is invented.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import extract_page  # noqa: E402

SHOP = "https://bag-maker.test"


def _extract(head):
    html = ("<html lang=\"en\"><head><title>Bags</title>{}</head><body><main>"
            "<h1>Bags</h1><p>Leather bags, cut and stitched by hand.</p></main>"
            "</body></html>").format(head)
    return extract_page(url=SHOP + "/collections/bags", final_url=SHOP + "/collections/bags",
                        status=200, headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=5, depth=1, source="link",
                        origin=SHOP)


def test_the_next_page_the_head_declares_is_recorded_as_an_address():
    page = _extract('<link rel="next" href="/collections/bags?page=2">')
    assert page["rel_next"] == SHOP + "/collections/bags?page=2"


def test_a_page_with_no_next_link_records_nothing():
    assert _extract("")["rel_next"] == ""


def test_the_canonical_link_is_not_mistaken_for_the_next_one():
    page = _extract('<link rel="canonical" href="/collections/bags">')
    assert page["rel_next"] == ""
    assert page["canonical"] == SHOP + "/collections/bags"
