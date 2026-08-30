"""Text that is not plain ASCII must survive the crawl intact.

`requests` follows HTTP/1.1: a `text/*` response with no `charset` in the header
is ISO-8859-1. Almost no site means that. Most declare UTF-8 in a `<meta>` tag
and say nothing in the header, so `response.text` silently produced mojibake -
and a Spanish retailer's own name arrived in the report with a replacement
character in the middle of it, in the evidence line a judge would read.

Nothing failed. Every fixture was ASCII, so no test could have noticed. These
serve the same bytes the real site did.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from conftest import SCRIPTS, run_script
from fixture_server import FixtureServer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# One accented brand, one with a character outside Latin-1 entirely, so a
# fallback that merely swaps one 8-bit codec for another still fails.
BRAND = "Café Solidário"
TAGLINE = "Rôti, Mühle, Ægir, 東京, café"


def _site(tmp_path, omit_charset):
    """A one-page site whose encoding is declared only where the rules say."""
    root = os.path.join(str(tmp_path), "site")
    shutil.copytree(os.path.join(FIXTURES, "good-site"), root)
    for name in os.listdir(root):
        if name.endswith(".html") and name != "index.html":
            os.remove(os.path.join(root, name))
    shutil.rmtree(os.path.join(root, "blog"), ignore_errors=True)

    page = (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<title>{brand}</title>'
        '<meta name="description" content="{brand} is a coffee importer. {tagline}">'
        '<link rel="canonical" href="{{{{BASE}}}}/index.html">'
        '<script type="application/ld+json">'
        '{{"@context":"https://schema.org","@type":"Organization",'
        '"name":"{brand}","url":"{{{{BASE}}}}/"}}'
        '</script></head><body><main><h1>{brand}</h1>'
        '<p>{brand} is a coffee importer working with growers in three countries. '
        '{tagline}</p></main></body></html>'
    ).format(brand=BRAND, tagline=TAGLINE)
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(page)

    rules = {"omit_charset": True} if omit_charset else {}
    with open(os.path.join(root, "_rules.json"), "w", encoding="utf-8") as handle:
        json.dump(rules, handle)
    return root


def _crawl(tmp_path, omit_charset):
    root = _site(tmp_path, omit_charset)
    with FixtureServer(root) as server:
        out = os.path.join(str(tmp_path), "snapshot.json")
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                    "--out", out, "--budget", "60", "--delay", "0"], "crawl.py")
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.parametrize("omit_charset", [False, True],
                         ids=["charset-in-header", "charset-only-in-meta"])
def test_brand_name_survives_the_crawl(tmp_path, omit_charset):
    snapshot = _crawl(tmp_path, omit_charset)
    name = snapshot["brand"]["name"]
    assert "�" not in name, (
        "the brand name came back with a replacement character: {!r}. The page "
        "declares UTF-8; only the HTTP header is silent.".format(name))
    assert name == BRAND, "expected {!r}, got {!r}".format(BRAND, name)


@pytest.mark.parametrize("omit_charset", [False, True],
                         ids=["charset-in-header", "charset-only-in-meta"])
def test_page_text_survives_the_crawl(tmp_path, omit_charset):
    snapshot = _crawl(tmp_path, omit_charset)
    home = snapshot["pages"][0]
    assert "�" not in home["body_text"], "mojibake in the extracted body text"
    for fragment in ("Rôti", "Mühle", "Ægir", "東京"):
        assert fragment in home["body_text"], (
            "{!r} did not survive; a Latin-1 fallback loses this".format(fragment))


def test_no_replacement_characters_anywhere_in_the_snapshot(tmp_path):
    """One assertion over the whole record, so a new field cannot quietly rot."""
    snapshot = _crawl(tmp_path, omit_charset=True)
    blob = json.dumps(snapshot, ensure_ascii=False)
    assert "�" not in blob, "a replacement character reached the snapshot"
