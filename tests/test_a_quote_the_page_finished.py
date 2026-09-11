# -*- coding: utf-8 -*-
"""The citation table quotes sentences, not the page's chrome or its headlines.

Two rows from one batch of audits, both then repeated by the verdict as "what an
assistant can repeat":

    a national museum's homepage    "<museum name> previous next opening hours
                                    Mon/Tue/Thu/Fri/Sun 09:30 ~ 17:30 ..." - the
                                    page has no paragraphs, and the first
                                    sentence of its flattened text is the logo,
                                    the carousel buttons and the hours run
                                    together
    a university's homepage         a list of admission-document titles with no
                                    full stop, from a page whose own writing
                                    ends every sentence with one

Every host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _compose():
    path = os.path.join(ROOT, "skills", "audit-orchestrator", "scripts", "compose_report.py")
    spec = importlib.util.spec_from_file_location("compose_for_quote_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

HOME = "https://www.larkmoor-museum.test/"


def _snapshot(page):
    base = {"url": HOME, "page_type": "home", "status": 200, "content_from": "static",
            "headings": {}, "links": {}, "word_count": 200}
    base.update(page)
    return {"site": "www.larkmoor-museum.test", "origin": HOME.rstrip("/"),
            "brand": {"name": "Larkmoor Museum"}, "pages": [base],
            "crawl": {"pages_crawled": 1, "pages_ok": 1, "pages_read": 1}}


def _row(page):
    rows = compose.simulate_citations(_snapshot(page), "Larkmoor Museum")
    return rows[0]


def test_the_first_run_of_flattened_text_is_not_quoted():
    body = ("Larkmoor Museum previous next opening hours Mon/Tue 09:30 ~ 17:30 last entry 30 "
            "minutes before closing. The galleries hold 4,000 objects from the valley's mills. "
            "Entry to the permanent collection costs nothing.")
    row = _row({"body_text": body, "paragraphs": []})
    assert "previous next" not in (row["likely_citation"] or ""), row


def test_a_headline_without_a_full_stop_is_not_quoted_where_the_page_uses_them():
    paragraphs = [u"大学案内2027、令和9年度 入学者選抜要項の公表",
                  u"本学は1877年に設立された。研究と教育の拠点である。学生数は約28,000人である。"]
    row = _row({"body_text": " ".join(paragraphs), "paragraphs": paragraphs})
    assert row["likely_citation"] and not row["likely_citation"].startswith(u"大学案内"), row


def test_writing_that_does_not_end_sentences_with_a_mark_is_not_asked_to():
    paragraphs = [u"ธนาคารก่อตั้งขึ้นในปี 2485 และมีสำนักงานใหญ่ในกรุงเทพ"]
    row = _row({"body_text": paragraphs[0], "paragraphs": paragraphs})
    assert row["likely_citation"], row
