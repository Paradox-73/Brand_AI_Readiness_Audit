"""Text that is not plain ASCII must survive the crawl intact.

`requests` follows HTTP/1.1: a `text/*` response with no `charset` in the header
is ISO-8859-1. Almost no site means that. Most declare UTF-8 in a `<meta>` tag
and say nothing in the header, so `response.text` silently produced mojibake -
and a Spanish retailer's own name arrived in the report with a replacement
character in the middle of it, in the evidence line a reader would check.

Nothing failed. Every fixture was ASCII, so no test could have noticed. These
serve the same bytes the real site did.

The last test is the same class of silent corruption, one layer up, in this
repository's own files: `\\b` written into a patch through a shell heredoc
arrived as a backspace byte, and the two regular expressions holding it matched
nothing from then on. A corrupted pattern looks exactly like a working one.
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


# --------------------------------------------------------------------------
# The escape that was not an escape
# --------------------------------------------------------------------------

def test_no_source_file_contains_a_control_character():
    """`\\b` written into a patch through a shell heredoc arrived as a
    backspace, and two regular expressions in this repo silently matched
    nothing for it. A corrupted pattern looks exactly like a working one.
    """
    import io
    from conftest import ROOT
    offenders = []
    for base in ("skills", "tests"):
        for directory, _, files in os.walk(os.path.join(ROOT, base)):
            if "__pycache__" in directory:
                continue
            for filename in files:
                if not filename.endswith((".py", ".md", ".json")):
                    continue
                path = os.path.join(directory, filename)
                with io.open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
                for index, char in enumerate(text):
                    if ord(char) < 32 and char not in "\n\r\t":
                        offenders.append("{}: U+{:04X} at offset {}".format(
                            os.path.relpath(path, ROOT), ord(char), index))
                        break
    assert not offenders, "control characters in source:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------
# A percent-encoded address must reach the reader as the address
# --------------------------------------------------------------------------

def test_a_percent_encoded_url_is_not_eaten_by_the_render():
    """A Japanese URL copied out of the evidence returned a 404.

    `%83` had been replaced by the literal text `5 of 6` - a percent-style
    format applied to a string carrying an address, where `%8` is a width
    specifier. `affected_pages` in the JSON was clean, so only the rendered
    document was wrong and nothing that read the JSON could see it. This holds
    the whole render boundary rather than the one call site, because the call
    site was found by reading and a second one would be found the same way.
    """
    import re
    import sys
    sys.path.insert(0, SCRIPTS)
    from compose_report import decode_character_references, decode_rendered_markdown

    url = ("https://shiryo.example.jp/"
           "%E3%82%A2%E3%83%BC%E3%82%AB%E3%82%A4%E3%83%96/%E5%B9%B4%E5%A0%B1.html")
    assert decode_character_references(url) == url
    rendered = decode_rendered_markdown(
        "Example: {} was read.\n\n`{}`\n".format(url, url))
    assert url in rendered, "the address did not survive the render"
    assert rendered.count(url) == 2, "one of the two copies was altered"
    assert re.search(r"%[0-9A-Fa-f]{2}", rendered), (
        "the percent escapes were resolved, so the printed address is not the "
        "address that was fetched")


def test_no_report_prose_is_built_by_percent_formatting():
    """The mechanism, not the symptom. `"...%s..." % value` on a string that
    may carry a URL is the fault; `.format` cannot produce it.
    """
    import io
    from conftest import ROOT
    offenders = []
    scripts = sorted(
        os.path.join(directory, name)
        for directory, _, files in os.walk(os.path.join(ROOT, "skills"))
        for name in files
        if name.endswith(".py") and "__pycache__" not in directory)
    assert scripts, "no skill scripts were read"
    for path in scripts:
        name = os.path.relpath(path, ROOT)
        for number, line in enumerate(
                io.open(path, encoding="utf-8").read().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '" % ' in stripped or "' % " in stripped:
                offenders.append("{}:{}: {}".format(name, number, stripped[:100]))
    assert not offenders, (
        "percent-formatting on a literal, which eats a percent-encoded URL:\n"
        + "\n".join(offenders))
