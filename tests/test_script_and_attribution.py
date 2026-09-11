# -*- coding: utf-8 -*-
"""Two ways an audit invents a fact: reading a script it cannot read, and
reading somebody else's line as the site's own.

We audited a plain-HTML library written entirely in Cyrillic. It carries
no `lang` attribute on any page, which is a real defect and was reported as
one. Everything else that report said about it was produced by English-only
matchers running on Russian prose:

  "No page states in one sentence      its homepage says what it is, in one
  what the brand is"                   Cyrillic sentence

  "no founding year appears in the     the same sentence states the year, and
  page text or in the page markup"     the audit had already stored the text

  "The page text reads as `en`"        every crawled page is Cyrillic; acting
                                       on this stamps `lang="en"` on a site
                                       that is not in English, which is worse
                                       than the attribute being absent

  "The copyright year in the footer    the only notice on that page is a
  is 2000"                             publisher's credit for text it donated,
                                       sitting mid-document; the site's own
                                       pages carry no notice at all

  "5 pages are written in blocks       one of them is 240 links and no
  too large to skim"                   paragraph at all - the "words" counted
                                       are link labels

The language gate that should have stopped the first three already existed and
already worked: it is what makes this audit decline on a Japanese site. It
never engaged, because it was keyed to a declared language and this site
declares none. The script the letters are in is the second way of knowing, it
needs nothing from the site, and it is what these tests hold in place.

The site here is invented, and so are the Russian strings in it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS, REFERENCE_DATE, run_script
from fixture_server import FixtureServer

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    detect_site_language, dominant_script, is_listing_page,
    NON_LATIN_PROSE_SCRIPTS, other_language_pages, pages_in_prose_language,
    prose_skip_reason, site_script,
)


def _module(skill, name):
    """One sub-skill's check.py, imported under a name of its own."""
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ==========================================================================
# Part 1 - the script is a second way of knowing the language
# ==========================================================================

# Invented prose, in the scripts a site can be written in. Each is long enough
# for `dominant_script` to characterise, which is 20 letters.
CYRILLIC = (u"Светлая Библиотека — сетевая библиотека, открыта в 1993 году. "
            u"Здесь собраны книги, переводы и журналы, которые читатели "
            u"присылают уже тридцать лет.")
GREEK = (u"Το Φωτεινό Αναγνωστήριο είναι μια ψηφιακή βιβλιοθήκη. "
         u"Ξεκίνησε το 1993 και λειτουργεί χωρίς διαφημίσεις.")
ARABIC = (u"مكتبة النور هي مكتبة رقمية مفتوحة للجميع. "
          u"بدأت في عام ألف وتسعمائة وثلاثة وتسعين وتعمل بدون إعلانات.")
DEVANAGARI = (u"उजाला पुस्तकालय एक डिजिटल पुस्तकालय है। "
              u"यह उन्नीस सौ तिरानबे में शुरू हुआ और बिना विज्ञापन के चलता है।")
HEBREW = (u"ספריית האור היא ספרייה דיגיטלית פתוחה לכולם. "
          u"היא נפתחה בשנת אלף תשע מאות תשעים ושלוש.")
ENGLISH = ("Lantern Library is a digital reading room that has been open "
           "since 1993. The books here are sent in by the people who read "
           "them, and the whole of it is free to use.")


def _pages(text, lang=None, count=3):
    return [{"url": "https://reading-room.example/{}".format(i), "status": 200,
             "lang": lang, "body_text": text} for i in range(count)]


@pytest.mark.parametrize("name,text,script", [
    ("cyrillic", CYRILLIC, "cyrillic"),
    ("greek", GREEK, "greek"),
    ("arabic", ARABIC, "arabic"),
    ("devanagari", DEVANAGARI, "devanagari"),
    ("hebrew", HEBREW, "hebrew"),
])
def test_a_site_that_declares_nothing_is_still_not_an_english_site(name, text, script):
    """The gate was keyed to a declaration, and this site makes none.

    `guess_language_from_text` scores Latin function words, so on a page with
    no Latin words in it the best it can do is return nothing - and the record
    it returned then said the prose checks apply, which is how the English
    matchers came to run on Russian.
    """
    language = detect_site_language(_pages(text))
    assert language["script"] == script
    assert language["prose_checks_apply"] is False, (
        "{} text was passed to the English matchers".format(name))


def test_the_script_is_named_and_the_language_is_not_guessed():
    """Cyrillic is Russian, Ukrainian, Bulgarian and Serbian.

    Naming one of them would be an invention. The report that did this printed
    "The page text reads as `en`" beside a true finding that no page declares
    a language, and a site owner acting on the pair stamps `lang="en"` on
    pages that are not in English.
    """
    language = detect_site_language(_pages(CYRILLIC))
    assert language["code"] == "", (
        "named a language it cannot know from the script alone: {}".format(language))
    assert "cyrillic" in language["source"] or "writing system" in language["source"]


def test_the_letters_overrule_a_declaration_that_contradicts_them():
    """A site may declare `en` and not be in English. The letters decide."""
    language = detect_site_language(_pages(CYRILLIC, lang="en"))
    assert language["prose_checks_apply"] is False
    assert language["script"] == "cyrillic"
    assert "contradicted" in language["source"], language["source"]


def test_a_declared_language_is_still_the_first_answer():
    """Nothing here replaces the declaration; it only adds a second source."""
    language = detect_site_language(_pages(ENGLISH, lang="en"))
    assert language["code"] == "en"
    assert language["source"] == "declared"
    assert language["prose_checks_apply"] is True


def test_an_undeclared_english_site_is_unaffected():
    """The common case, and the one this must not break."""
    language = detect_site_language(_pages(ENGLISH))
    assert language["prose_checks_apply"] is True
    assert language["code"] in ("en", "")


def test_too_little_text_decides_nothing():
    """`dominant_script` needs 20 letters. Below that it answers None, and a
    guess made from three words is not a second source."""
    language = detect_site_language(_pages(u"Привет", count=1))
    assert language["script"] == ""
    assert language["prose_checks_apply"] is True, (
        "silenced the whole audit on a site it had not read")


def test_every_non_latin_script_this_audit_can_meet_is_covered():
    for text in (CYRILLIC, GREEK, ARABIC, DEVANAGARI, HEBREW):
        assert dominant_script(text) in NON_LATIN_PROSE_SCRIPTS
    assert dominant_script(ENGLISH) == "latin"
    assert "latin" not in NON_LATIN_PROSE_SCRIPTS


def test_the_reason_names_the_script_rather_than_an_undetermined_language():
    """"This site is in AN UNDETERMINED LANGUAGE" hides what was established.

    The script is known. The language is not. A site owner reading the reason
    has to be able to tell those apart, because only the second is a reason to
    doubt the verdict.
    """
    reason = prose_skip_reason(detect_site_language(_pages(CYRILLIC)))
    assert "Cyrillic" in reason
    assert "English" in reason, "must still say what the check does reason about"
    assert "UNDETERMINED" not in reason


def test_a_page_of_another_script_inside_an_english_site_is_set_aside():
    """The site verdict is a majority vote, and a page is the authority on
    itself. A page that declares nothing used to inherit `en` whatever it was
    written in."""
    mixed = [{"url": "https://reading-room.example/a", "lang": "en", "body_text": ENGLISH},
             {"url": "https://reading-room.example/b", "lang": "", "body_text": CYRILLIC}]
    kept = [p["url"] for p in pages_in_prose_language(mixed)]
    assert kept == ["https://reading-room.example/a"]
    assert len(other_language_pages(mixed)) == 1


def test_site_script_reads_the_crawl_rather_than_one_page():
    assert site_script(_pages(CYRILLIC)) == "cyrillic"
    assert site_script([]) == ""


# ==========================================================================
# Part 2 - whose copyright line is it
# ==========================================================================

FRESHNESS = _module("freshness-corroboration-audit", "freshness_check_attribution")

# A publisher's credit for text it gave the library, in the middle of a book
# page. Invented, like everything else here.
DONATED_TEXT_NOTICE = u"© 2001 Текст предоставлен издательством «Заря»"


def _copyright_page(url, text, years):
    return {"url": url, "status": 200, "page_type": "article",
            "text": text, "body_text": text, "word_count": 200,
            "dates": {"copyright_years": years}}


def test_a_publishers_notice_is_not_the_sites_copyright():
    assert FRESHNESS._notice_names_another_party(
        DONATED_TEXT_NOTICE, u"Светлая Библиотека") is True


def test_a_bare_notice_naming_nobody_is_the_sites_own():
    """The ordinary footer. A notice that names no party is the site's, and
    the staleness finding has to keep firing on it."""
    for line in ("(c) 2001 All rights reserved", "Copyright 2001", u"© 2001"):
        assert FRESHNESS._notice_names_another_party(line, "Lantern Library") is False, line


def test_a_notice_naming_the_brand_is_the_sites_own():
    assert FRESHNESS._notice_names_another_party(
        "(c) 2001 Lantern Library Ltd. All rights reserved.", "Lantern Library") is False


def test_one_unrecognised_word_is_not_another_party():
    """A trading name, an abbreviation or the name without its suffix. Reading
    those as somebody else's would silence a real finding on the sites whose
    brand name this audit spells differently from their footer."""
    assert FRESHNESS._notice_names_another_party(
        "(c) 2001 Lanternco", "Lantern Library") is False


def test_a_donated_text_credit_does_not_become_the_sites_stale_footer():
    """The finding it produced: "The copyright year in the footer is 2000, 26
    years behind the reference date", about a publisher's line for text the
    library reproduces, on a site whose own pages carry no notice at all."""
    import datetime
    result = FRESHNESS.SkillResult("freshness-corroboration-audit")
    body = (u"Тихие реки, роман в двух частях. " * 12) + DONATED_TEXT_NOTICE + \
           (u" Перевод и примечания подготовлены читателями библиотеки. " * 24)
    FRESHNESS._check_copyright_year(
        result, [_copyright_page("https://reading-room.example/kniga", body, [2001])],
        datetime.date(2026, 9, 1), u"Светлая Библиотека")
    findings = [f["title"] for f in result.to_dict()["findings"]]
    assert not findings, "reported somebody else's copyright line as the site's: {}".format(
        findings)
    declined = {s["check"]: s["reason"] for s in result.to_dict()["not_applicable"]}
    assert "another party" in declined["footer-copyright-year"], declined


def test_a_real_stale_footer_still_fires():
    """The other direction. Nothing here may make a stale footer unreportable."""
    import datetime
    result = FRESHNESS.SkillResult("freshness-corroboration-audit")
    body = ("Lantern Library is a digital reading room. " * 30) + \
           "(c) 2001 Lantern Library. All rights reserved."
    FRESHNESS._check_copyright_year(
        result, [_copyright_page("https://reading-room.example/", body, [2001])],
        datetime.date(2026, 9, 1), "Lantern Library")
    findings = result.to_dict()["findings"]
    assert len(findings) == 1, result.to_dict()["not_applicable"]
    assert "2001" in findings[0]["title"]


def test_the_finding_says_where_the_notice_actually_is():
    """"In the footer" was asserted about a `<div>` in the middle of the
    document. The owner it is addressed to would edit a footer template that
    has nothing to do with the line."""
    import datetime
    tail = " Everything here is sent in by the people who read it. " * 30
    body = ("Lantern Library is a digital reading room. "
            "(c) 2001 Lantern Library. All rights reserved." + tail)
    result = FRESHNESS.SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_copyright_year(
        result, [_copyright_page("https://reading-room.example/", body, [2001])],
        datetime.date(2026, 9, 1), "Lantern Library")
    title = result.to_dict()["findings"][0]["title"]
    assert "in the footer" not in title, title
    assert "on the page" in title


def test_a_footer_range_is_read_at_its_newest_year():
    """Footers that list every year rather than a range. Reading only the
    anchored year reported a current site as decades stale."""
    import datetime
    result = FRESHNESS.SkillResult("freshness-corroboration-audit")
    body = ("Lantern Library is a digital reading room. " * 30) + \
           "(c) 2001 2002 2003 2026 Lantern Library."
    FRESHNESS._check_copyright_year(
        result, [_copyright_page("https://reading-room.example/", body,
                                 [2001, 2002, 2003, 2026])],
        datetime.date(2026, 9, 1), "Lantern Library")
    assert not result.to_dict()["findings"]


def test_a_snapshot_with_no_text_still_uses_the_years_it_has():
    """A hand-written snapshot, which SKILL.md invites, and a footer sitting
    outside the region the snapshot stores. Neither is a site with no notice,
    and neither may lose the finding."""
    import datetime
    result = FRESHNESS.SkillResult("freshness-corroboration-audit")
    page = {"url": "https://reading-room.example/", "status": 200, "page_type": "home",
            "text": "", "body_text": "", "word_count": 0,
            "dates": {"copyright_years": [2001]}}
    FRESHNESS._check_copyright_year(result, [page], datetime.date(2026, 9, 1),
                                    "Lantern Library")
    assert len(result.to_dict()["findings"]) == 1


# ==========================================================================
# Part 3 - a directory of links is not prose
# ==========================================================================

ENGAGEMENT = _module("engagement-audit", "engagement_check_directory")


def _directory_page(labels, url="https://reading-room.example/katalog"):
    text = " ".join(labels)
    return {
        "url": url, "status": 200, "page_type": "other",
        "body_text": text, "text": text,
        "word_count": sum(len(label.split()) for label in labels),
        "headings": {"h1": ["Catalogue"], "h2": ["Everything in the collection"]},
        "links": {"internal": [{"url": "{}/{}".format(url, i), "text": label}
                               for i, label in enumerate(labels)],
                  "external": []},
        "readability": {"wall_of_text_count": 0, "subheading_count": 1,
                        "words_per_subheading": float(sum(len(l.split()) for l in labels)),
                        "list_text_share": 0.0, "avg_sentence_words": 90.0},
    }


LABELS = ["Volume {} translated with notes by readers".format(n) for n in range(150)]


def test_a_directory_built_from_bare_anchors_is_recognised_as_a_list():
    """`list_text_share` counts words inside `li`, `td`, `th`, `dt` and `dd`.
    A page built the way 1990s pages were built - anchors and line breaks, no
    list markup at all - scores 0.0 on it and is not a page of prose."""
    page = _directory_page(LABELS)
    assert page["readability"]["list_text_share"] == 0.0
    assert not is_listing_page(page), (
        "if the shared detector already caught this, the new test is not the "
        "thing under test")
    assert ENGAGEMENT._is_a_directory_of_links(page)


def test_a_directory_is_not_told_to_add_subheadings():
    """"5 pages are written in blocks too large to skim", naming a page of
    1,015 words and one subheading. The page is 240 links and no paragraph;
    the advice was to add a subheading every three to five paragraphs."""
    result = ENGAGEMENT.SkillResult("engagement-audit")
    ENGAGEMENT._check_readability(result, [_directory_page(LABELS)])
    assert not result.to_dict()["findings"], "measured a link list as prose"


def test_a_page_of_prose_with_a_few_links_is_still_measured():
    """The other direction. An article that links its sources is prose, and a
    real wall of text on it must still be reported."""
    sentence = ("The reading room was rebuilt over one winter and the shelves "
                "were carried up two flights of stairs by hand. ")
    text = sentence * 60
    page = {
        "url": "https://reading-room.example/story", "status": 200, "page_type": "article",
        "body_text": text, "text": text, "word_count": len(text.split()),
        "headings": {"h1": ["The winter rebuild"], "h2": ["How it went"]},
        "links": {"internal": [{"url": "https://reading-room.example/a",
                                "text": "the shelves"}], "external": []},
        "readability": {"wall_of_text_count": 0, "subheading_count": 1,
                        "words_per_subheading": float(len(text.split())),
                        "list_text_share": 0.0, "avg_sentence_words": 20.0},
    }
    assert not ENGAGEMENT._is_a_directory_of_links(page)
    result = ENGAGEMENT.SkillResult("engagement-audit")
    ENGAGEMENT._check_readability(result, [page])
    assert result.to_dict()["findings"], "stopped reporting a real wall of text"


def test_a_short_page_of_links_is_not_a_directory():
    """A stub with three links is a stub, and the stub checks are the ones
    that should speak about it."""
    page = _directory_page(["Volume {} with notes".format(n) for n in range(4)])
    assert not ENGAGEMENT._is_a_directory_of_links(page)


# ==========================================================================
# Part 4 - the whole audit, on a site of the shape that produced all of this
# ==========================================================================

PAGE = (u"<HTML>\n<HEAD>\n"
        u"<META HTTP-EQUIV=\"Content-Type\" CONTENT=\"text/html; charset=utf-8\">\n"
        u"<TITLE>{title}</TITLE>\n"
        u"</HEAD>\n<BODY BGCOLOR=\"#FFFFFF\">\n{body}\n"
        u"<HR>\n<A HREF=\"index.html\">Начало</A> | "
        u"<A HREF=\"katalog.html\">Каталог</A> | "
        u"<A HREF=\"kniga.html\">Тихие реки</A>\n"
        u"</BODY>\n</HTML>\n")

HOME_BODY = (
    u"<H1>Светлая Библиотека</H1>\n"
    u"<P>Светлая Библиотека — самая известная сетевая библиотека, открыта в 1993 году.</P>\n"
    u"<P>Здесь собраны книги, переводы и журналы, которые читатели присылают уже тридцать "
    u"лет. Библиотека работает без рекламы и живёт на пожертвования читателей.</P>\n"
    u"<P>Пишите нам по адресу pochta@svetlaya.example. Почтовый адрес: улица Тихая, "
    u"дом 4, город Заречный.</P>\n")

BOOK_BODY = (
    u"<H1>Тихие реки</H1>\n"
    u"<P>Роман в двух частях. Перевод с французского, примечания переводчика.</P>\n"
    u"<P>" + (u"Река у деревни поворачивала на запад, и лодки уходили туда каждое утро. " * 8) +
    u"</P>\n"
    u"<DIV ALIGN=RIGHT>&copy; 2001 Текст предоставлен издательством «Заря»</DIV>\n"
    u"<P>" + (u"Вечером в доме зажигали лампу и читали вслух до самой полуночи, пока "
              u"за окном шёл снег. " * 16) + u"</P>\n")

CATALOGUE_BODY = (
    u"<H1>Каталог</H1>\n<H2>Полный список переводов и авторов</H2>\n"
    + u"".join(u"<A HREF=\"/k/{0:04d}.html\">Книга номер {0}, перевод и примечания "
               u"читателей</A><BR>\n".format(n) for n in range(150)))


@pytest.fixture(scope="module")
def library_site(tmp_path_factory):
    """Serve an invented plain-HTML library written in Cyrillic and audit it.

    No `lang` attribute anywhere, which is the condition the language gate was
    never written for, and 1990s markup: no `<main>`, no `<p>` on the
    catalogue page, a mid-document rights credit on the book page and no
    copyright notice of the site's own anywhere.
    """
    work = str(tmp_path_factory.mktemp("library"))
    site = os.path.join(work, "site")
    os.makedirs(site)
    pages = {"index.html": (u"Светлая Библиотека", HOME_BODY),
             "kniga.html": (u"Тихие реки", BOOK_BODY),
             "katalog.html": (u"Каталог", CATALOGUE_BODY)}
    for name, (title, body) in pages.items():
        with open(os.path.join(site, name), "w", encoding="utf-8") as handle:
            handle.write(PAGE.format(title=title, body=body))
    with open(os.path.join(site, "robots.txt"), "w", encoding="utf-8") as handle:
        # The catalogue's 150 entries are link labels for this test, not pages
        # to crawl, and a crawl that spent its budget on them would read none
        # of the three pages that matter.
        handle.write("User-agent: *\nDisallow: /k/\n")

    with FixtureServer(site) as server:
        snapshot_path = os.path.join(work, "snapshot.json")
        run_script([os.path.join(SCRIPTS, "crawl.py"), server.base_url,
                    "--out", snapshot_path, "--budget", "20", "--delay", "0",
                    "--no-render"], "crawl.py [library]")
        findings = {}
        for skill in ("fact-extractability-audit", "freshness-corroboration-audit",
                      "engagement-audit", "structured-data-audit"):
            out = os.path.join(work, "{}.json".format(skill))
            args = [os.path.join(ROOT, "skills", skill, "scripts", "check.py"),
                    "--snapshot", snapshot_path, "--out", out, "--no-network"]
            if skill == "freshness-corroboration-audit":
                args += ["--now", REFERENCE_DATE]
            run_script(args, "{} [library]".format(skill))
            with open(out, encoding="utf-8") as handle:
                findings[skill] = json.load(handle)
    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    return snapshot, findings


def _all_findings(findings):
    return [f for result in findings.values() for f in result["findings"]]


def _text_of(finding):
    return " ".join(str(finding.get(key) or "")
                    for key in ("title", "evidence", "summary", "rationale"))


def test_the_crawl_read_the_cyrillic_text(library_site):
    """Everything below is about what was done with the text. If the crawl
    did not decode it, the rest of this file is measuring the wrong failure."""
    snapshot, _ = library_site
    home = next(p for p in snapshot["pages"] if p["url"].rstrip("/").endswith(".html")
                or p["url"].endswith("/"))
    assert dominant_script(home.get("body_text") or "") == "cyrillic"


def test_the_site_language_names_the_script_and_declines_the_prose_checks(library_site):
    snapshot, _ = library_site
    language = snapshot["site_language"]
    assert language["prose_checks_apply"] is False
    assert language["script"] == "cyrillic"
    assert language["code"] == ""


def test_no_finding_claims_the_site_states_no_definition(library_site):
    """Its homepage says what it is, in one sentence, in Cyrillic."""
    _, findings = library_site
    for finding in _all_findings(findings):
        assert finding.get("root_cause") != "no-entity-definition", _text_of(finding)


def test_no_finding_claims_the_site_states_no_founding_year(library_site):
    """The same sentence states the year, and the audit had stored the text."""
    _, findings = library_site
    for finding in _all_findings(findings):
        assert "no founding year appears" not in _text_of(finding), _text_of(finding)


def test_the_english_only_checks_decline_by_name(library_site):
    _, findings = library_site
    declined = {s["check"]: s["reason"]
                for s in findings["fact-extractability-audit"]["not_applicable"]}
    for name in ("entity-definition", "answer-first-paragraphs", "long-sentences",
                 "headings-name-their-topic"):
        assert name in declined, "{} ran on Cyrillic prose".format(name)
        assert "English" in declined[name] and "Cyrillic" in declined[name], declined[name]


def test_the_checks_that_work_in_any_script_still_run(library_site):
    """Declining the prose checks may not silence the audit. Headings, markup,
    links, status codes and dates are readable in any script."""
    _, findings = library_site
    ran = set(findings["fact-extractability-audit"]["checks_run"])
    for name in ("heading-hierarchy", "core-facts-present", "brand-naming-consistency"):
        assert name in ran, "{} is structural and should still have run".format(name)
    assert findings["engagement-audit"]["checks_run"], "the whole skill went quiet"


def test_the_missing_lang_finding_still_fires(library_site):
    """The one thing that report got right about this site. Fixing the guess
    must not remove the finding."""
    _, findings = library_site
    causes = {f.get("root_cause") for f in _all_findings(findings)}
    assert "missing-lang" in causes, sorted(c for c in causes if c)


def test_nothing_says_the_page_text_reads_as_english(library_site):
    """"No crawled page declares a language" is true. "The page text reads as
    `en`" was next to it, and is what an owner would act on."""
    _, findings = library_site
    for finding in _all_findings(findings):
        text = _text_of(finding)
        assert "reads as `en`" not in text, text
        assert "reads as `english`" not in text.lower(), text


def test_no_finding_reports_the_publishers_year_as_the_sites_footer(library_site):
    """The only copyright line on this site belongs to a publisher, sits
    mid-document, and is about text the library reproduces."""
    _, findings = library_site
    for finding in _all_findings(findings):
        assert "copyright year" not in _text_of(finding).lower(), _text_of(finding)


def test_the_catalogue_is_not_reported_as_a_wall_of_text(library_site):
    """240 anchors and no paragraph. The words counted were link labels."""
    snapshot, findings = library_site
    catalogue = next((p for p in snapshot["pages"] if p["url"].endswith("katalog.html")), None)
    assert catalogue is not None, "the crawl did not read the catalogue page"
    assert catalogue["word_count"] >= 700, (
        "below the long-page threshold, so this page could not have produced "
        "the finding either way: {}".format(catalogue["word_count"]))
    for finding in _all_findings(findings):
        assert "too large to skim" not in _text_of(finding), _text_of(finding)


# ==========================================================================
# Part 4 - the contradiction was asked of one language and is a general fault
# ==========================================================================

# A restaurant chain was serving `<html lang="en-US">` on a site
# written entirely in Arabic, on every page. The tool computed the sentence
# "declared as en, contradicted by page text written in the arabic script" and
# used it only to decide which checks to skip.
#
# The test that produced that sentence read `non_latin and code == "en"`, so it
# could only ever catch a site that claimed to be English. A site declaring
# `lang="ar"` while writing in Cyrillic is the same fault and was invisible.


@pytest.mark.parametrize("declared,text,script", [
    ("ar", CYRILLIC, "cyrillic"),
    ("ru", ARABIC, "arabic"),
    ("he", GREEK, "greek"),
    ("el", DEVANAGARI, "devanagari"),
    ("hi", HEBREW, "hebrew"),
    ("en", ARABIC, "arabic"),
])
def test_a_declaration_no_language_writes_that_way_is_contradicted(declared, text, script):
    language = detect_site_language(_pages(text, lang=declared))
    assert language["code"] == declared
    assert "contradicted by page text written in the {} script".format(script) \
        in language["source"]


@pytest.mark.parametrize("declared,text", [
    ("ru", CYRILLIC),
    ("uk", CYRILLIC),      # one script, several languages: naming the script
    ("bg", CYRILLIC),      # is not naming the language, and neither is this
    ("ar", ARABIC),
    ("fa", ARABIC),        # Persian is written in the Arabic script
    ("ur", ARABIC),
    ("el", GREEK),
    ("he", HEBREW),
    ("yi", HEBREW),        # Yiddish, likewise
    ("hi", DEVANAGARI),
    ("mr", DEVANAGARI),
    ("en", ENGLISH),
])
def test_a_declaration_that_agrees_with_the_letters_is_left_alone(declared, text):
    language = detect_site_language(_pages(text, lang=declared))
    assert language["source"] == "declared", language["source"]


@pytest.mark.parametrize("declared,text", [
    # Serbian is written in both, and calling either community's spelling of
    # its own language a mistake would be the worst version of this finding.
    ("sr", CYRILLIC),
    ("sr", ENGLISH),
    # A language this map does not list makes no claim at all rather than a
    # guess. Silence is the right answer for a language nobody here knows.
    ("qya", CYRILLIC),
    ("zxx", ARABIC),
])
def test_no_claim_is_made_where_a_language_writes_both_ways_or_is_unknown(declared, text):
    assert detect_site_language(_pages(text, lang=declared))["source"] == "declared"


def test_a_plurality_of_letters_is_not_enough_to_call_a_declaration_wrong():
    """`dominant_script` answers with a plurality, and a plurality is not
    grounds for telling a site its own `lang` is a mistake. A shop writing in
    one script whose product codes and brand names are in another can have the
    second as its commonest script on a short sample."""
    from audit_common import script_contradicts_language

    mixed = ARABIC[:60] + " " + ENGLISH
    # Latin leads on this sample, and Arabic is nowhere near absent, so there
    # is nothing here anybody could call a contradiction.
    assert script_contradicts_language("ar", mixed) == ""
    # The unmixed case still answers.
    assert script_contradicts_language("ar", CYRILLIC) == "cyrillic"
    # And too little text to characterise is never a contradiction.
    assert script_contradicts_language("ar", u"Привет") == ""
