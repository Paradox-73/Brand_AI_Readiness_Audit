# -*- coding: utf-8 -*-
"""A page's `<title>` is not a line the site writes in its own prose.

The reading that finds a definition in a line the site repeats on every page -
a footer tagline, which the boilerplate strip removes from the body text - read
its blocks from the extractor's `prose_blocks`, and those included the `<title>`.
Every page carries a title, so "<Brand> | <what it does>" passed for a repeated
line with the shape of a tagline definition, and a site that states what it is
nowhere was reported as stating it on its homepage.

Both halves are held: the extractor never records the title as a prose block,
and the definition check never reads a block equal to the page's title.

Every host and organisation below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from page_extract import extract_page  # noqa: E402

SITE = "https://forecast-maker.test"
TITLE = "Harbourline Analytics | Warehouse forecasting for mid-market retailers"


def _facts_module():
    path = os.path.join(ROOT, "skills", "fact-extractability-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("facts_for_title_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _facts_module()


def _page(footer=""):
    html = ("<html lang=\"en\"><head><title>{}</title></head><body><main>"
            "<h1>Demand forecasting for mid-market retail warehouses</h1>"
            "<p>We believe the future belongs to those who move first, and our customers "
            "know it.</p></main><footer>{}</footer></body></html>").format(TITLE, footer)
    return extract_page(url=SITE + "/", final_url=SITE + "/", status=200,
                        headers={"content-type": "text/html"}, html=html,
                        redirect_chain=[], elapsed_ms=5, depth=0,
                        source="homepage", origin=SITE)


def test_the_extractor_never_records_the_title_as_a_prose_block():
    page = _page()
    assert page["title"] == TITLE
    assert TITLE not in (page.get("prose_blocks") or [])


def test_a_real_footer_tagline_is_still_a_prose_block():
    """The field exists for this, and dropping the head must not drop it."""
    tagline = "Harbourline Analytics is a forecasting platform for mid-market retailers."
    page = _page(footer="<div class=\"tagline\">{}</div>".format(tagline))
    assert tagline in (page.get("prose_blocks") or [])


def test_the_definition_check_never_reads_a_block_equal_to_the_title():
    page = {"title": TITLE,
            "prose_blocks": [TITLE, "Harbourline Analytics is a forecasting platform."]}
    blocks = FACTS._quotable_blocks(page)
    assert TITLE not in blocks
    assert "Harbourline Analytics is a forecasting platform." in blocks
