"""A site that is not in English must be told what we did not judge.

Every prose check here reasons about English: the definition pattern
"<Brand> is a ...", the warm-up phrases that mark a section as not answering
first, the thirty-word sentence threshold, the imperative verbs that identify a
call to action, and the list of headings that say nothing. On a German page they
do not degrade gracefully - they produce confident falsehoods. Reporting that no
call-to-action link was found, about a page carrying a perfectly good button, is
worse than saying nothing.

So the language is detected once during the crawl and the checks that cannot
judge it decline, with a reason a site owner can read. These tests hold that in
place: the prose findings must not appear, the structural ones must, and the
report must say which is which.

The second half of the file is the other side of the same promise. Declining is
not enough: whatever *is* still measured on a non-English page has to be
measured the way that script works. Four sites proved it was not -

  a whole Japanese page      one sentence, so the sentence-length check skipped
                             with "no page has enough prose to measure"
  an Arabic news page        the same, because Arabic has no capital letter for
                             the splitter's lookahead to find
  a Hindi newspaper          3,541 words measured against a real 2,254, then
                             reported as running 393 words between subheadings
                             when the true figure is 250
  a German research society  "Beschäftigenvertretungen" split into "besch" and
                             "ftigenvertretungen", and those two fragments
                             printed as evidence that the page does not deliver
                             what its title promises
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import ROOT, SCRIPTS, run_script
from fixture_server import FixtureServer

# Checks that reason about English sentences and must decline on a German site.
ENGLISH_ONLY = {"entity-definition", "answer-first-paragraphs", "long-sentences"}

# Root causes those checks produce. None may appear on a non-English site.
ENGLISH_ONLY_CAUSES = {"no-entity-definition", "fluff-first", "long-sentences"}

GERMAN_PAGE = """<!DOCTYPE html>
<html lang="de-DE">
<head>
<meta charset="utf-8">
<title>{title} | Nordwind Logistik</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{{{{BASE}}}}/{slug}">
</head>
<body>
<header>
  <nav aria-label="Haupt">
    <a href="{{{{BASE}}}}/index.html">Startseite</a>
    <a href="{{{{BASE}}}}/leistungen.html">Leistungen</a>
    <a href="{{{{BASE}}}}/kontakt.html">Kontakt</a>
  </nav>
</header>
<main>
  <h1>{heading}</h1>
  {body}
  <p><a href="{{{{BASE}}}}/kontakt.html" class="btn">Jetzt Beratung anfordern</a></p>
</main>
<footer>
  <p>&copy; 2026 Nordwind Logistik. Hafenstrasse 12, 20457 Hamburg.</p>
</footer>
</body>
</html>
"""

PAGES = {
    "index.html": dict(
        slug="index.html", title="Nordwind Logistik",
        description="Nordwind Logistik plant und steuert die Warenverteilung fuer den "
                    "mittelstaendischen Handel in Norddeutschland.",
        heading="Warenverteilung fuer den Mittelstand",
        body="<p>Nordwind Logistik plant und steuert die Warenverteilung fuer den "
             "mittelstaendischen Handel in Norddeutschland. Wir betreiben vier Lager und "
             "beliefern rund 900 Filialen mit einer Zustellquote von 98 Prozent.</p>"
             "<h2>Was wir tun</h2><p>Die Disposition erfolgt taeglich um 05:00 Uhr fuer "
             "alle Standorte, und die Fahrer erhalten ihre Touren auf dem Mobilgeraet.</p>"),
    "leistungen.html": dict(
        slug="leistungen.html", title="Leistungen",
        description="Lagerhaltung, Kommissionierung und Filialbelieferung fuer Haendler "
                    "mit vier bis vierzig Standorten in Norddeutschland.",
        heading="Unsere Leistungen",
        body="<p>Wir uebernehmen Lagerhaltung, Kommissionierung und die Belieferung Ihrer "
             "Filialen. Der Vertrag laeuft monatlich und kostet ab 4.800 EUR pro Monat.</p>"),
    "kontakt.html": dict(
        slug="kontakt.html", title="Kontakt",
        description="Nordwind Logistik erreichen Sie in Hamburg unter der Rufnummer "
                    "+49 40 555 0142 oder per E-Mail.",
        heading="Kontakt",
        body="<p>Nordwind Logistik, Hafenstrasse 12, 20457 Hamburg. "
             "Telefon +49 40 555 0142, E-Mail hallo@nordwind-logistik.example.</p>"),
}


@pytest.fixture(scope="module")
def german_site(tmp_path_factory):
    """Serve a small, well-built German site and audit it end to end."""
    work = str(tmp_path_factory.mktemp("german"))
    site = os.path.join(work, "site")
    os.makedirs(site)
    for name, fields in PAGES.items():
        with open(os.path.join(site, name), "w", encoding="utf-8") as handle:
            handle.write(GERMAN_PAGE.format(**fields))
    with open(os.path.join(site, "robots.txt"), "w", encoding="utf-8") as handle:
        handle.write("User-agent: *" + os.linesep + "Allow: /" + os.linesep)

    with FixtureServer(site) as server:
        snapshot_path = os.path.join(work, "snapshot.json")
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                    "--out", snapshot_path, "--budget", "60", "--delay", "0"], "crawl.py [de]")
        findings = {}
        for skill in ("fact-extractability-audit", "engagement-audit"):
            out = os.path.join(work, "{}.json".format(skill))
            run_script([os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                        "--snapshot", snapshot_path, "--out", out, "--no-network"],
                       "{} [de]".format(skill))
            with open(out, encoding="utf-8") as handle:
                findings[skill] = json.load(handle)
    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    return snapshot, findings


def test_the_language_is_detected_from_the_declaration(german_site):
    snapshot, _ = german_site
    language = snapshot["site_language"]
    assert language["code"] == "de", language
    assert language["source"] == "declared"
    assert language["prose_checks_apply"] is False


def test_english_only_checks_decline_rather_than_guess(german_site):
    _, findings = german_site
    declined = {s["check"]: s["reason"]
                for s in findings["fact-extractability-audit"].get("not_applicable", [])}
    for name in ENGLISH_ONLY:
        assert name in declined, "{} should have declined on a German site".format(name)
        assert "English" in declined[name], (
            "the reason must say why, so a site owner can judge it: {}".format(declined[name]))


def test_no_english_only_finding_is_reported(german_site):
    _, findings = german_site
    reported = {f["root_cause"] for result in findings.values() for f in result["findings"]}
    leaked = reported & ENGLISH_ONLY_CAUSES
    assert not leaked, (
        "these findings come from English-only reasoning and were reported about a German "
        "site: {}".format(sorted(leaked)))


def test_structural_checks_still_run(german_site):
    """Declining the prose checks must not silence the rest of the skill."""
    _, findings = german_site
    ran = set(findings["fact-extractability-audit"].get("checks_run") or [])
    for name in ("heading-hierarchy", "core-facts-present", "brand-naming-consistency"):
        assert name in ran, "{} is structural and should still have run".format(name)


def test_orientation_does_not_claim_a_missing_call_to_action(german_site):
    """The button reads "Jetzt Beratung anfordern". Our verb list has never met it."""
    _, findings = german_site
    for finding in findings["engagement-audit"]["findings"]:
        assert "call-to-action link was found" not in finding.get("evidence", ""), (
            "claimed the page has no call to action; it has one, in German")
    declined = {s["check"]: s["reason"]
                for s in findings["engagement-audit"].get("not_applicable", [])}
    if "homepage-orientation" in declined:
        assert "English" in declined["homepage-orientation"]


def test_an_english_site_is_unaffected(audit):
    """The gate must not fire on the language the checks were written for."""
    result = audit("good-site")
    language = result.snapshot["site_language"]
    assert language["code"] == "en"
    assert language["prose_checks_apply"] is True
    ran = set(result.findings["fact-extractability-audit"].get("checks_run") or [])
    assert ENGLISH_ONLY <= ran, "English-only checks must still run on an English site"


# ==========================================================================
# Measurement, in the script the page is written in
#
# Declining a check is only half of it. The numbers that do get measured on a
# non-English page have to be measured the way that script works, and they were
# not: a whole Japanese page counted as one sentence, an Arabic page as one
# too, a Hindi page counted at twice its real word count, and a German
# compound split at the first letter English lacks. Each produced a confident
# finding whose evidence was an artefact of the measurement.
# ==========================================================================

import importlib.util  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    dominant_script, is_question_heading, letter_runs, other_language_pages,
    pages_in_prose_language, sentences, word_count, words_are_separated,
    written_in_the_same_script,
)

JAPANESE = ("日本郵政グループは、郵便・物流事業を営んでおります。"
            "私たちは全国の郵便局ネットワークを通じて、サービスを提供しています。"
            "今後ともよろしくお願いいたします。")
ARABIC = ("نحن مؤسسة غير ربحية تعمل في مجال التعليم. "
          "نقدم خدماتنا في أكثر من عشرين دولة. اتصل بنا اليوم.")


def test_a_page_is_split_into_sentences_in_every_script():
    """Read for a full stop followed by a capital letter, a whole Japanese page
    is one sentence, and the check that measures sentence length skipped with
    "no page has enough prose to measure". Arabic has no capital letters, so
    the lookahead never fired there either - and English must still split the
    way it always did."""
    assert len(sentences(JAPANESE)) == 3
    assert len(sentences(ARABIC)) == 3
    assert len(sentences("It opened in 1998. Patients come from three counties.")) == 2


def test_a_word_count_is_not_a_measurement_in_a_script_without_spaces():
    assert words_are_separated("The studio was founded in 2015 by two designers.")
    assert not words_are_separated(JAPANESE)
    assert words_are_separated(ARABIC)
    assert dominant_script("Hi") is None, "too short to characterise"


@pytest.mark.parametrize("text,expected,script", [
    (u"मतदाताओं को "
     u"लुभाने पर होगी "
     u"कार्रवाई", 6, "Devanagari"),
    (u"कार्रवाई", 1, "one Hindi word"),
    (u"Beschäftigenvertretungen ist ein Wort", 4, "German"),
])
def test_a_vowel_sign_does_not_end_a_word(text, expected, script):
    """`\\w` holds no combining marks, so an abugida was counted at twice its
    length: a Hindi page measured 3,541 words against a real 2,254, and was
    then reported as running 393 words between subheadings - over the
    threshold of 300 - when the true figure is 250, under it."""
    assert word_count(text) == expected, script


def test_a_title_word_is_not_split_at_the_first_letter_english_lacks():
    """"Beschäftigenvertretungen" became "besch" and "ftigenvertretungen",
    neither of which appears on the page, and the page was reported as not
    delivering what its title promises - with those two fragments printed as
    the evidence."""
    runs = letter_runs(u"Beschäftigenvertretungen".lower(), 4)
    assert runs == [u"beschäftigenvertretungen"]


def test_a_question_mark_is_a_question_whatever_the_language():
    assert is_question_heading(u"¿Qué beneficios tiene una bañera?")
    assert is_question_heading(u"How do I activate my new debit card?")
    assert is_question_heading(u"このサービスは何ですか？")


def test_a_statement_is_still_not_a_question():
    assert not is_question_heading("Duplication is evil")
    assert not is_question_heading("Code style")
    assert not is_question_heading("")


# --------------------------------------------------------------------------
# An English threshold measures English
# --------------------------------------------------------------------------

MIXED = [
    {"url": "https://x.test/en/a", "lang": "en"},
    {"url": "https://x.test/de/a", "lang": "de"},
    {"url": "https://x.test/fr/a", "lang": "fr-FR"},
    {"url": "https://x.test/plain", "lang": ""},
]


def test_a_page_declaring_another_language_is_not_measured_in_english():
    """Site language is one majority vote. On a bilingual broadcaster it was
    decided by luck - 28 pages tagged en against 18 tagged de - and the English
    30-word sentence threshold then ran over the German pages too. One scored
    59% long sentences and escaped a finding only because its page type was not
    in the allowlist."""
    kept = [p["url"] for p in pages_in_prose_language(MIXED)]
    # A page declaring nothing inherits the site verdict: the common case on
    # smaller sites, and the best answer available.
    assert kept == ["https://x.test/en/a", "https://x.test/plain"]
    assert len(other_language_pages(MIXED)) == 2


@pytest.mark.parametrize("name,text,expected", [
    # A foundation's Arabic edition was told at high severity that its
    # structured data contradicted the page, about markup that was correct and
    # a page that was correct - on a comparison that could only have one
    # answer.
    ("Example Foundation", "مؤسسة ويكي", False),
    # No letters shared and none to compare: nothing can be decided, so the
    # check runs as before rather than declining on a technicality.
    ("Example Foundation", "A page with no mention of it at all", True),
    ("トヨタ", "トヨタは会社です", True),
])
def test_a_name_is_only_looked_for_where_it_could_appear(name, text, expected):
    assert written_in_the_same_script(name, text) is expected


# --------------------------------------------------------------------------
# A word-overlap rule needs words
# --------------------------------------------------------------------------

def _facts_module():
    path = os.path.join(ROOT, "skills", "fact-extractability-audit", "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("facts_for_language_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_heading_vocabulary_is_not_scored_on_a_language_it_cannot_read():
    """"1 page uses slogan headings that do not name their topic" fired on the
    table of contents of a Japanese manual, whose H2s are numbered section
    titles. Its three sibling prose checks all declined on the same site.

    The check was called from inside the structural heading check, which runs
    whatever the site is written in, so it was the one that escaped the gate.
    Word overlap needs words, and a script that writes no spaces between them
    has none to compare."""
    facts = _facts_module()
    host = "https://an-invented-host.test"
    titles = ["第1章 表記の原則",
              "第2章 話者の区別",
              "第3章 記号の使い方",
              "第4章 数字と単位",
              "第5章 固有名詞",
              "第6章 時間表記",
              "第7章 提出の手順"]
    manual = {
        "url": host + "/manual/", "page_type": "documentation", "status": 200,
        "lang": "ja",
        "body_text": " ".join(titles),
        "headings": {"h1": ["表記マニュアル"], "h2": titles},
        "headings_in_markup": {"h1": ["a heading"]},
        "jsonld": [], "jsonld_types": [], "links": {},
        "sections": [{"heading": t, "first_paragraph": "あ" * 60,
                      "navigational": False} for t in titles],
        "prices": [], "contact_facts": {}, "readability": {},
        "word_count": 0, "meta_description": "",
    }
    snapshot = {"pages": [manual], "brand": {"name": "Invented"}, "sitemaps": [],
                "crawl": {"pages_crawled": 1, "max_pages": 60},
                "site_language": {"code": "ja", "source": "declared",
                                  "prose_checks_apply": False}}

    result = facts.run(snapshot)
    declined = {entry["check"]: entry["reason"] for entry in result.not_applicable}
    for name in ("entity-definition", "answer-first-paragraphs", "long-sentences",
                 "headings-name-their-topic"):
        assert name in declined, "{} should have declined on a Japanese site".format(name)
        assert "English" in declined[name], declined[name]
    assert not [f for f in result.findings if f["root_cause"] == "heading-structure"]
