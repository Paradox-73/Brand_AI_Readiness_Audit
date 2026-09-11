#!/usr/bin/env python3
"""render-readability-audit: can a machine read the delivered HTML? (mechanisms A, C)

Reads snapshot.json, writes findings JSON. Makes no network requests: every
judgement comes from the HTML the crawl already captured, plus the optional
Playwright measurements if the crawl ran with --render.

Usage:
    python check.py --snapshot snapshot.json --out render-readability-audit.findings.json
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))

# The marketplace's shared library: one definition of the finding schema, the
# root-cause vocabulary and the page-type detector, so six skills cannot drift
# apart on any of the three.
#
# Looked for beside this file first, then in the orchestrator. `package.py`
# writes a copy into every skill directory when it builds the submission, so a
# skill folder lifted out on its own still runs; the checkout keeps a single
# source of truth so the copies cannot diverge from it.
_SHARED_CANDIDATES = (
    _HERE,
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "audit-orchestrator", "scripts"),
)
_SHARED = next(
    (path for path in _SHARED_CANDIDATES
     if os.path.isfile(os.path.join(path, "audit_common.py"))),
    None,
)
if _SHARED is None:
    raise SystemExit(os.linesep.join([
        "Cannot find the shared library that this skill depends on.",
        "  Looked in: " + "; ".join(_SHARED_CANDIDATES),
        "",
        "This skill reads audit_common.py, which should sit either beside this",
        "file or in skills/audit-orchestrator/scripts/. Copy the whole",
        "marketplace, or rebuild the submission with package.py.",
        "",
        "To perform these checks without it, follow the Procedure section of",
        "this skill's SKILL.md by hand. It states every check in prose and",
        "produces the same findings.",
    ]))
sys.path.insert(0, _SHARED)

from audit_common import (
    unclassified_note,  # noqa: E402
    example_urls, extraction_looks_incomplete, has_price, length_floor_for,
    load_snapshot, pages_of, pct, plural, PUBLIC_BODY, sells_something,
    site_kind, SkillResult, truncate,
)

# The two vocabularies the shell signals are limited by, read from the
# extractor that applies them rather than copied here.
#
# A candle shop's report said the thin pages carried "no framework mount point,
# no state blob" while the homepage held `viewerModel` fifteen times,
# `wixBiSession` nine times, `warmupData` and `siteAssets`. Neither clause was
# a statement about the document: both were statements about these two lists,
# and the report printed them as facts about the site. The evidence now names
# the lists and their sizes, so widening either one widens the sentence with
# it, and a reader can see that "none found" means "none of these".
from page_extract import SPA_ROOT_SELECTORS, STATE_BLOB_PATTERNS  # noqa: E402

SKILL = "render-readability-audit"

# Thresholds, each with the reason it sits where it does.
MIN_QUOTABLE_TEXT = 300      # below ~300 chars a page carries no self-contained fact
# ...in English, which is where the 300 was measured and which the pages it was
# measured on were written in. A character is not the same quantity in every
# script: "People's Republic of China" is 26 characters and seven in Han, so a
# page holding 150 Han characters holds what an English page needs about 500
# for, and this check told it at medium severity that it carries "too little
# text to be quoted". `quotable_floor` restates the bar in the characters the
# page is actually written in; `audit_common.CHARACTERS_PER_ENGLISH_CHARACTER`
# holds the ratios and says where they came from.


def quotable_floor(page):
    """`MIN_QUOTABLE_TEXT`, in the characters this page is written in.

    Returns 300 unchanged for every script the ratio table makes no claim
    about - Latin, Cyrillic, Greek, Arabic, Hebrew, Devanagari, Thai - so the
    pages the number was measured on are judged by the number that was
    measured. Read the body text rather than the whole document, because that
    is the text the bar is about.
    """
    return length_floor_for(page.get("body_text") or page.get("text") or "",
                            MIN_QUOTABLE_TEXT)
HEAVY_SCRIPT_BYTES = 50000   # 50 KB of script alongside no prose means the page is assembled client-side
EMPTY_ROOT_TEXT = 200        # a framework root with under 200 chars has not rendered content
SHELL_SHARE_HIGH = 0.5       # more than half of content pages being shells is a site-wide failure
RENDER_GAP_SIGNIFICANT = 0.6 # JavaScript adding 60%+ of the text means the static HTML is a stub
IMAGE_DOMINANT_TEXT = 400    # a page with a hero image and under 400 chars is carrying facts in pixels
EXTERNAL_BUNDLE_COUNT = 3    # modern builds ship code-split external chunks and no inline script at all
THIN_SHARE = 0.3             # a third of content pages too short to quote is a site-wide problem

# The share of assessable content pages that must deliver no chrome at all
# before this is the site's template rather than one odd page. Half, and for
# the same reason `SHELL_SHARE_HIGH` sits at half: the claim is about the
# layout every page is built from, and one page of twenty missing its menu is
# an oversight on that page.
CHROME_IN_BROWSER_SHARE = 0.5

# Body text above which a page explains itself in writing whatever its player
# holds, so no transcript of that player is missing anything. Higher than
# MIN_QUOTABLE_TEXT because one quotable paragraph is not a substitute for
# forty minutes of speech, while three or four are enough that the page stands
# on its own.
VIDEO_EXPLAINS_ITSELF_TEXT = 800

# Bytes of script per character of visible text, above which the delivered
# document is code around a container rather than a page with a lot of code on
# it.
#
# Measured against the WHOLE delivered document's visible text - navigation,
# header and footer included - not against the extracted body. A site's own
# copy can be thin for an ordinary reason: a contact page is three lines. But
# every real page still ships its menu and its footer as HTML however thin its
# copy is, so the whole document's visible text is what separates "this site
# wrote very little here" from "this response is not a page at all". Fifty
# rather than a larger number because the thing being excluded is an ordinary
# page carrying an analytics or consent bundle, and such a page clears the
# 300-character floor on its chrome alone before the ratio is ever consulted.
SCRIPT_BYTES_PER_CHARACTER = 50

# Link text that promises the numbers a buyer needs.
FACT_BEARING_PDF_RE = re.compile(
    r"\b(price|pricing|rate card|tariff|fee|cost|quote|spec|specification|"
    r"datasheet|data sheet|brochure|catalog(ue)?|menu|product sheet|"
    r"technical detail|dimensions|sizing|price list)\b", re.I)

# Link text that names a document a body publishes as its record: what it
# decided, when, and under what authority.
#
# A town whose agendas, minutes, ordinances, emergency management plan and
# primary ballot are every one of them PDF-only got nothing at all from this
# check, because the only branch that existed was gated on whether the site
# sells anything and a town sells nothing. The gate was right about prices and
# wrong about the check. What this check is for, once commerce is taken out of
# it, is a linked file holding something the site states nowhere else and a
# reader would need - and that is as true of an ordinance as of a spec sheet.
#
# Deliberately narrow, and every alternative names a kind of document rather
# than a subject. `report` on its own matches half the anchors on the web;
# `annual report` names one publication. `plan` on its own matches "plan your
# visit"; `emergency management plan` does not.
PUBLIC_RECORD_PDF_RE = re.compile(
    r"\b(agenda|minutes|ordinances?|by-?laws?|resolution|warrant|ballot|"
    r"annual report|audit report|budget|financial statements?|"
    r"public notice|legal notice|hearing notice|proclamation|"
    r"zoning (?:map|regulations?|by-?laws?|ordinances?)|"
    r"(?:town|city|village|management|master|comprehensive|emergency|"
    r"strategic|action|development) plan|"
    r"application form|permit application|code of ordinances|"
    # A company's record is the same kind of thing: a holding company publishes
    # every news release, quarterly result and shareholder letter as a PDF with
    # nothing on the page but the link, and none of it was a document of record
    # to this pattern.
    r"(?:news|press) releases?|shareholder letters?|letters? to (?:the )?shareholders|"
    r"(?:quarterly|interim|half[- ]year(?:ly)?|annual|full[- ]year) (?:results|report)|"
    r"(?:form )?(?:10-?k|10-?q|20-?f)|proxy statement|prospectus)\b", re.I)

# How much a page must say in its own words, beside its list of document
# links, before it counts as explaining what it links to. One quotable
# paragraph: the same bar the rest of this skill uses for "is there anything
# here to lift".
PDF_INDEX_PROSE_FLOOR = MIN_QUOTABLE_TEXT

# The share of content pages that being bare document indexes makes this a
# property of the site rather than of a page or two. A third, the same share
# `thin-html` uses, and for the same reason: below it a handful of pages are an
# oversight, above it the site publishes this way.
PDF_INDEX_SHARE_HIGH = 0.3

# How a reference to somebody else's document is written. A statistics charity
# cites a climate-science chapter titled "Annex III: Technology-specific cost
# and performance parameters. In: Climate Change 2014: Mitigation of Climate
# Change" from a reference list. It does not own that document and cannot
# republish it; it matched only because the title contains the word "cost".
# The fix offered was "publish an HTML version of the numbers", which asks a
# charity to republish another organisation's report.
#
# Matched against the link text and against the sentence around it, because a
# citation carries its markers in either half: the authors and the year sit in
# the running text, the "In:" and the volume title in the anchor.
CITATION_RE = re.compile(
    r"(\bin:\s|\bet al\b|\bdoi\b|\bisbn\b|\bpp?\.\s*\d|\bvol\.\s*\d|"
    r"\bjournal\b|\bproceedings\b|\bworking paper\b|\bassessment report\b|"
    r"\bworking group\b|\bcited in\b|\bretrieved from\b|"
    r"\b(?:19|20)\d{2}:\s|\(\s*(?:19|20)\d{2}\s*\))", re.I)
# Headings a page puts above the documents it did not write.
CITATION_HEADING_RE = re.compile(
    r"\b(references?|bibliograph|citations?|endnotes?|footnotes?|"
    r"further reading|sources?|cite this)\b", re.I)
# How much of the page around the link is read for those markers. One sentence
# either side: long enough to hold the author list and the year that precede
# the anchor and the "Contribution of Working Group III" that follows it, short
# enough that the next paragraph's prose is not what decides this.
CITATION_CONTEXT_CHARS = 300

LOAD_MORE_RE = re.compile(r"\b(load more|show more|view more|see more|load additional)\b", re.I)
# `/all` counts. A restaurant group's journal index links `/journal/all/`,
# labelled "All" in the markup and "View more" only in an aria-label, and the
# page was reported as hiding its catalogue behind a button a crawler cannot
# press - about an anchor returning 200 that any crawler follows. A full-list
# URL is a crawlable route to the rest of the items, which is the whole thing
# this check asks for.
PAGINATION_RE = re.compile(
    r"(?:[?&](?:page|pg|p|page_number|pagenumber|offset|start|from|skip)="
    r"\d+|/page/\d+|/pg/\d+|/p/\d+/?$|/c/\d+/?$|-page-\d+"
    r"|/all/?$|[?&]all=)", re.I)


def run(snapshot):
    result = SkillResult(SKILL)
    content_pages = [p for p in pages_of(snapshot, content_only=True)]
    render_mode = (snapshot.get("crawl") or {}).get("render_mode", "static")

    # `render-mode` is not a test of the site: it records which of two ways
    # this audit read it. Registered and never answered, it could only ever be
    # swept up as "fired" by an unrelated finding, because `SkillResult` treats
    # every check a function registers as one that function's findings might be
    # about - and `run` is on the stack for all of them. It now says what it
    # measured, which is the honest thing for a check whose subject is the
    # auditor rather than the audited.
    result.check("render-mode")
    result.skip("render-mode",
                "this audit read the site with a browser, so the JavaScript gap below is "
                "measured rather than inferred" if render_mode == "rendered" else
                "this audit read only the HTML the server delivered; where that matters, "
                "the checks below say so and lower their own confidence")
    if render_mode == "rendered":
        result.check("static-vs-rendered-text-gap")
    else:
        # Why it did not run, rather than one stock reason for every case.
        # A museum's report said "Playwright was not available on the auditing
        # machine" on a run whose own startup line read "Playwright found,
        # rendering on" - the pass had not run because the crawl was blocked
        # before there was anything to render. An explanation that is wrong is
        # worse than no explanation, because a reader acts on it: this one
        # would send someone to install a browser they already had.
        notes = " ".join((snapshot.get("crawl") or {}).get("notes") or [])
        if not [p for p in pages_of(snapshot) if p.get("status") == 200]:
            reason = ("no page returned HTTP 200, so there was nothing to render. Whether "
                      "JavaScript supplies the text on this site is unknown")
        elif "budget" in notes and "render" in notes:
            reason = ("the crawl used its wall-clock budget before the rendered pass could "
                      "run, so the JavaScript gap is inferred from the static HTML rather "
                      "than measured")
        elif "Playwright" in notes:
            reason = next((n for n in ((snapshot.get("crawl") or {}).get("notes") or [])
                           if "Playwright" in n), "the rendered pass did not run")
        else:
            reason = ("the rendered pass did not run, so only the delivered HTML was "
                      "analysed")
        result.skip("static-vs-rendered-text-gap",
                    "{}. This is a property of the audit run, not a defect on the "
                    "site.".format(reason[0].upper() + reason[1:]))

    # Every page that answered 200, whatever this audit decided its type was.
    # `unrendered-template-artefact` is the one check here that needs no page
    # typing at all: it reports a literal string in the delivered document, so
    # the type is irrelevant to whether the claim is true. A store-locator page
    # turned up whose whole visible content is `[wpsl]`, and a page like that
    # types `other` on any site whose URLs do not use the words the type table
    # knows - the same shape as an earlier defect, one check skipping a page
    # while the others graded it.
    readable = pages_of(snapshot)
    artefacts = _template_artefacts_by_page(readable)

    if not content_pages:
        # Registered and answered before the early return, because it reads
        # `readable` rather than `content_pages`: a crawl of pages this audit
        # could not type still delivers HTML, and a placeholder printed on one
        # of them is still on the page.
        _check_template_artefacts(result, readable, artefacts)
        for name in ("thin-html", "spa-shell-detection", "client-rendered-chrome",
                     "facts-locked-in-images",
                     "facts-locked-in-pdfs", "video-transcripts", "iframed-main-content",
                     "image-alt-coverage", "crawlable-pagination"):
            result.skip(name, "no content pages returned HTTP 200, so there is no delivered "
                              "HTML to assess")
        return result

    # Measured before the shell checks so `spa-shell-detection`'s pass sentence
    # can name it. That sentence - "every crawled content page delivered
    # readable text in the initial HTML response" - was printed verbatim about
    # a storefront whose header, menu and footer were absent from every page,
    # and it stood in a report carrying four separate findings caused by that.
    # It is still true as far as it goes; it may not go there alone.
    chrome_pages = {p["url"]: reasons for p in content_pages
                    for reasons in [_chrome_built_in_the_browser(p)] if reasons}

    shells = _check_shells(result, snapshot, content_pages, render_mode,
                           chrome_pages=chrome_pages, artefacts=artefacts)
    _check_render_gap(result, content_pages, render_mode)
    # After the render-gap check and not before it. `SkillResult` sweeps up
    # every check a function on the stack has registered and not yet answered,
    # and in rendered mode `static-vs-rendered-text-gap` is registered by `run`
    # itself at the top of this function. Reporting from here, once that check
    # has fired or declined, keeps a finding about a template placeholder from
    # claiming the render-gap measurement as its own.
    _check_template_artefacts(result, readable, artefacts)
    # Called from here rather than from `_check_shells`, because `SkillResult`
    # sweeps up every check registered by a function on the stack when a
    # finding is added: raised inside `_check_shells` this finding would mark
    # `spa-shell-detection` as having fired on a run where it passed.
    _check_client_rendered_chrome(result, content_pages, shells, chrome_pages)

    # The shell and gap checks above are about pages the browser could not
    # fill, so they need those pages. Every check below asks what a page does
    # not contain - no text beside the picture, no transcript, no alt
    # attribute - and a page the crawl knows it read as a stub cannot answer
    # that question: what arrived over the wire and what a visitor sees are
    # two different documents there.
    judged = [p for p in content_pages if not p.get("render_skipped")]
    _check_image_locked(result, judged, shells)
    _check_pdf_locked(result, snapshot, judged)
    _check_video_transcripts(result, judged)
    _check_iframed_content(result, judged, shells)
    _check_alt_coverage(result, judged)
    _check_pagination(result, snapshot, judged)

    result.signal("shell_page_count", len(shells))
    result.signal("render_mode", render_mode)
    return result


# --------------------------------------------------------------------------

# The two verdicts `shell_verdict` can reach when it saw anything at all.
SHELL = "shell"
SHELL_UNSETTLED = "unsettled"


def _delivered_text_len(page):
    """Every visible character the server sent, navigation and footer included.

    Not `body_text_len`, which is the page's own copy after the chrome has been
    stripped out. A thin page and a container are told apart by what surrounds
    the copy, not by the copy: a three-line contact page still delivers its
    menu and its footer as HTML, and a container delivers neither because both
    are inside the framework too.

    `body_text_len` is the last fallback rather than a zero. A snapshot written
    by hand, which SKILL.md invites, can carry the extracted copy and neither
    whole-document count, and reading that page as zero visible characters
    would call it a container on the strength of a field nobody wrote. The
    extracted copy is a subset of the visible text, so it is a floor.
    """
    spa = page.get("spa_shell") or {}
    if spa.get("visible_text_len") is not None:
        return spa["visible_text_len"]
    return page.get("text_len") or page.get("body_text_len") or 0


def _script_bytes(page):
    """The script this document carries inline, counted once.

    It used to be `inline_bytes + state_blob_bytes`, and a hydration payload is
    an inline script - so every byte of it was counted twice, in a number the
    evidence prints and a reader is invited to re-measure. The payload count
    stayed near zero while it recognised only one framework's name, which hid
    the doubling; widening that vocabulary would have made a shop's homepage
    report a million bytes of script in a 1.3-million-byte document.

    `max` rather than either one alone: the payload is a subset of the inline
    script on every document seen so far, and a snapshot where it is not - a
    payload the script scan missed - should raise the total rather than lower
    it.
    """
    scripts = page.get("scripts") or {}
    spa = page.get("spa_shell") or {}
    return max(scripts.get("inline_bytes", 0) or 0,
               spa.get("state_blob_bytes", 0) or 0)


def shell_verdict(page):
    """One answer to "is this a container rather than a page", for both checks.

    Returns `(verdict, reasons)`, where verdict is `SHELL` - report it and fix
    it by rendering on the server - or `SHELL_UNSETTLED` - one signal, not
    enough to accuse a template, but enough that no other check here may call
    this page empty - or `""`.

    Two checks in this skill ask this question and they used to answer it
    separately, so they could disagree about one page and did. An education
    site's homepage delivered 227,011 bytes of which 224,637 were inline
    script, around twelve characters of visible text. `spa-shell-detection`
    wanted two signals, found the script-versus-prose one alone, and printed
    "every crawled content page delivered readable text in the initial HTML
    response"; `thin-html` fired high, was ranked "do first" in the confident
    tier, and told the owner to write two or three sentences of body copy for a
    document the crawler could not read at all. Same page, opposite
    conclusions, and the wrong fix ranked first - a shell is fixed by rendering
    on the server, thin copy by writing more, and neither fix helps the other
    defect.

    So the precedence is stated here once. Where the delivered bytes are
    overwhelmingly script, or a framework mount point is sitting empty, the
    page is a container and nothing downstream may describe it as a page that
    was written short.

    Every signal is measured against the whole delivered document's visible
    text, never against the extracted prose, and a document delivering a
    quotable paragraph of visible text is a page whatever markers it carries.
    See the comment on `delivers_a_page` below for the report that forced it.

    Two independent signals are normally required, so a genuinely short page is
    not misread as broken. Two shapes are enough on their own, because nothing
    but client-side assembly produces either:

      an empty framework mount point beside almost no text - required because a
        React or Vue build whose scripts are all external never trips the
        inline-script signal, which is the single most common shape on the web;

      a document whose inline script outweighs its entire visible text by
        `SCRIPT_BYTES_PER_CHARACTER`, where that whole visible text would not
        fill one quotable paragraph. This is the one that was missing. It does
        not depend on recognising the mount node by name, which is what let the
        homepage above through: `page_extract.SPA_ROOT_SELECTORS` matches ids
        exactly, so a build that names its mount anything the list does not
        hold records no root at all.
    """
    # Read the delivered document, not the rendered one. The crawl replaces a
    # shell's record with what the browser produced so the other five skills
    # judge the page a visitor sees; this function is the one place that must
    # still see what arrived over the wire, or the shell it is looking for has
    # already been filled in by the time it looks.
    page = static_view(page)
    spa = page.get("spa_shell") or {}
    scripts = page.get("scripts") or {}
    reasons = []

    # A page whose text this audit failed to extract is not a page with no
    # text. On a news site's liveblog template the extractor kept 89 of 1,660
    # visible characters, and the report said the page was an empty JavaScript
    # shell and too thin to quote - about server-rendered Arabic prose sitting
    # in a nested div none of the content selectors reach. The fix offered was
    # several days of development work.
    #
    # A state blob or an empty framework root is the site telling us the text
    # arrives later, and that evidence still counts. A short extraction on its
    # own does not.
    if extraction_looks_incomplete(page) and not (
            spa.get("state_blobs") or spa.get("root_selector")):
        return "", []

    script_bytes = _script_bytes(page)
    root_len = spa.get("root_text_len")
    empty_root = bool(spa.get("root_selector")) and root_len is not None \
        and root_len < EMPTY_ROOT_TEXT
    delivered_text_len = _delivered_text_len(page)

    # Every signal below is read against `delivered_text_len` - every character
    # the response puts on the screen - and none of them against
    # `body_text_len`, the prose left after the site furniture is stripped out.
    #
    # One audit covered a server-rendered storefront. Its homepage delivers 3,517
    # visible characters, the whole product grid with names and prices among
    # them, and the report's first finding - `critical`, ranked above
    # everything else - read "173 chars of visible text against 269,238 bytes
    # of inline script". 173 was `body_text_len`. The extractor had kept the
    # prose and dropped the grid, and two signals here read that number: the
    # script-versus-text one and the hydration-payload one. Two signals is a
    # shell, so the page was called a shell, and the prescribed fix -
    # server-render the homepage, "several days of development time" - was
    # already done. The same reading called three product pages carrying 2,672
    # to 3,254 visible characters "JavaScript shells". Five of that report's
    # ten findings came out of this one field.
    #
    # `body_text_len` answers "is there enough prose here to quote", which is
    # `thin-html`'s question and stays `thin-html`'s. "Did a page arrive at
    # all" is answered by what a visitor sees, and an extractor that can lose a
    # product grid is not the witness for it.
    #
    # So a document holding a quotable paragraph of visible text is a page,
    # whatever else it carries. That costs one shape: a build that
    # server-renders 300-odd characters of menu and footer around an empty
    # mount point is no longer named here. It is still returned as unsettled
    # below, so `thin-html` cannot call it short, and `client-rendered-chrome`
    # reads the same pages for the opposite half of that split.
    delivers_a_page = delivered_text_len >= MIN_QUOTABLE_TEXT

    script_dwarfs_the_page = (
        not delivers_a_page
        and script_bytes > HEAVY_SCRIPT_BYTES
        and script_bytes >= delivered_text_len * SCRIPT_BYTES_PER_CHARACTER)

    # Three external bundles and a few hundred bytes inline is not "a lot of
    # script", and the signal this skill documents is almost no visible text
    # alongside a lot of script. A furniture retailer's root address is a
    # server-rendered country picker: 127 visible characters, five plain links
    # to regional storefronts, 492 bytes of inline script and three external
    # bundles. The report led with "the homepage is delivered as an empty
    # JavaScript shell ... against 492 bytes of inline script", ranked it
    # critical and priced the fix at several days of development.
    #
    # The bundle count stays on record, because a code-split build that ships
    # every byte externally is real and common. It is recorded as what it is -
    # how the page loads its code - and it no longer counts as one of the two
    # signals a shell needs, so on its own it can hold `thin-html` back and it
    # can never make a shell.
    bundle_reason = None
    if script_dwarfs_the_page:
        reasons.append("the whole delivered document holds {} chars of visible text, its own "
                       "header, menu and footer included, around {:,} bytes of inline script"
                       .format(delivered_text_len, script_bytes))
    elif not delivers_a_page and script_bytes > HEAVY_SCRIPT_BYTES:
        reasons.append("the whole delivered document holds {} chars of visible text, its own "
                       "header, menu and footer included, against {:,} bytes of inline script "
                       "and {} external script(s)".format(
                           delivered_text_len, script_bytes,
                           scripts.get("external_count", 0)))
    elif not delivers_a_page and scripts.get("external_count", 0) >= EXTERNAL_BUNDLE_COUNT:
        bundle_reason = (
            "the whole delivered document holds {} chars of visible text and loads {} "
            "external script bundles, with {:,} bytes of script inline - how a build that "
            "assembles its page in the browser ships, though not a lot of script on this "
            "page".format(delivered_text_len, scripts.get("external_count", 0),
                          script_bytes))
        reasons.append(bundle_reason)
    if empty_root:
        reasons.append("framework root `{}` contains {} chars".format(
            spa["root_selector"], root_len))
    # One bar, not the doubled one this used to carry. The double existed
    # because `body_text_len` excludes the menu and the footer, so 300 was too
    # tight a floor for it; the whole document's visible text includes them and
    # needs no allowance.
    if spa.get("state_blobs") and not delivers_a_page:
        reasons.append("content held in {} rather than in HTML".format(
            ", ".join(spa["state_blobs"][:2])))
    if spa.get("noscript_demands_js") and not delivers_a_page:
        reasons.append("<noscript> tells the visitor to enable JavaScript")

    if not reasons:
        return "", []
    if delivers_a_page:
        # A mount point sitting empty while 300 or more characters arrive
        # around it is worth recording - nothing else here may then call the
        # page short - and it is not enough to accuse the template of shipping
        # nothing, because something did ship.
        return SHELL_UNSETTLED, reasons
    counted = [reason for reason in reasons if reason is not bundle_reason]
    if script_dwarfs_the_page or empty_root or len(counted) >= 2:
        return SHELL, reasons
    return SHELL_UNSETTLED, reasons


# --------------------------------------------------------------------------
# A page whose content is its links
# --------------------------------------------------------------------------

# How many of a page's own links have to reach pages that delivered their own
# content before the page is a way in rather than a container. Two, because
# one link is a redirect written by hand and two is a choice offered.
GATEWAY_MIN_DESTINATIONS = 2


def _same_page_key(url):
    """One spelling for a same-site URL, so a link and a crawled page compare."""
    parts = urlparse(url or "")
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return "{}{}{}".format(host, parts.path.rstrip("/") or "/",
                           ("?" + parts.query) if parts.query else "")


def pages_that_delivered_themselves(pages):
    """`{key: url}` for every page whose delivered document is a page.

    Read off the delivered HTML: a quotable paragraph of visible text, and no
    shell verdict. These are the destinations a gateway may point at.
    """
    out = {}
    for page in pages:
        view = static_view(page)
        if _delivered_text_len(view) < MIN_QUOTABLE_TEXT:
            continue
        if shell_verdict(page)[0] == SHELL:
            continue
        out[_same_page_key(page.get("url"))] = page.get("url")
    return out


def gateway_destinations(page, delivered):
    """The crawled pages this page hands the reader to, or [] if it is not a gateway.

    A furniture retailer's root address holds "Choose your local site to shop"
    and five plain links - United States, Singapore, Australia, Canada, United
    Kingdom - each to a regional storefront that is itself a complete
    server-rendered site of 4,000 to 8,000 visible characters. The framework's
    mount point held those 86 characters, so it read as a mount point nothing
    had rendered into, and that one signal made the page a shell. The page is
    not a container waiting on its script: it is a door, and every room behind
    it was measured and had arrived.

    So a page is a gateway when all of these hold in its delivered HTML:

      its own links reach pages that delivered themselves - at least
        `GATEWAY_MIN_DESTINATIONS`, and at least half of the distinct same-site
        addresses it links;
      those links are the content - inside the mount point where there is one
        (the mount holds at least as many characters as the link text, so the
        links were rendered into it on the server), and otherwise at least half
        of the visible text outside the `<title>`;
      nothing says the content comes later - no state blob, no `<noscript>`
        demanding JavaScript.

    The second condition is the guard against the false pass. A client-rendered
    page that server-renders a menu around an empty mount point links to real
    pages too; its mount point holds nothing, so it is still a shell.
    """
    if (page.get("content_from") or "") == "rendered":
        return []
    view = static_view(page)
    spa = view.get("spa_shell") or {}
    if spa.get("state_blobs") or spa.get("noscript_demands_js"):
        return []
    here = {_same_page_key(page.get(field)) for field in ("url", "final_url")
            if page.get(field)}
    targets = {}
    for link in (view.get("links") or {}).get("internal") or []:
        if not isinstance(link, dict) or not link.get("url") or link.get("fragment"):
            continue
        key = _same_page_key(link["url"])
        if key in here:
            continue
        targets.setdefault(key, (link.get("text") or "").strip())
    arrived = sorted(delivered[key] for key in targets if key in delivered)
    if len(arrived) < GATEWAY_MIN_DESTINATIONS or len(arrived) * 2 < len(targets):
        return []
    link_text = sum(len(text) for text in targets.values())
    if not link_text:
        return []
    if spa.get("root_selector"):
        if (spa.get("root_text_len") or 0) < link_text:
            return []
    else:
        outside_title = _delivered_text_len(view) - len((view.get("title") or "").strip())
        if link_text * 2 < outside_title:
            return []
    return arrived


# --------------------------------------------------------------------------
# Chrome that arrives only when the script runs
# --------------------------------------------------------------------------

# `shell_verdict` above grades the page's *body*: an empty mount point, a state
# blob holding the copy, script bytes dwarfing the visible text. A page can
# answer "no" correctly to every one of those and still be a page whose
# template runs in the browser, because a framework can server-render the route
# component and mount the layout around it client-side.
#
# Measured on a shoe brand's storefront built on a Vue framework: 1,088
# characters of static visible text, zero `<nav>` elements, no `<h1>`, no
# footer links, and the framework's mount point in the markup. The product copy
# is in the delivered HTML; the header, menu and footer are not.
# `spa-shell-detection` passed it - "every crawled content page delivered
# readable text in the initial HTML response" - and four findings then fired
# across four other skills, one for each thing the missing chrome takes with
# it: no off-site profile links, a navigation with zero items, no `<h1>`, no
# route to a contact page. Those are all the same fact, and the report never
# named client-side rendering. The owner was handed four unrelated-looking
# chores instead of one diagnosis.
#
# This is deliberately not a lower shell threshold. Lowering the threshold is
# how the opposite failure was produced - a complete server-rendered page
# called a shell because the extractor had missed its text, with several days
# of development offered as the fix - and this page is not a shell: its copy
# arrived. What is absent is the chrome, so it gets its own finding and its own
# fix, which is to server-render the layout rather than the whole application.

# What a page has to be missing before its chrome counts as absent, and it is
# four readings rather than one because each detector has a different blind
# spot and a negative in evidence has to rest on more than one of them:
#
#   nav_links     `page_extract._links` reads `<nav>`, the ARIA menu roles, a
#                 dozen menu class and id spellings, and - when none of those
#                 match - any block near the top of the document holding three
#                 or more links. A site that writes its menu in `<div>`s is
#                 caught by that last fallback, so zero here is not a statement
#                 about element names.
#   footer_links  `<footer>` and `[role=contentinfo]`, counted separately
#                 because plenty of sites put four items in the header and the
#                 whole site index in the footer.
#   chrome_paths  the wider sweep that also reads a bare `<header>`, which the
#                 nav selector only enters through `header nav`.
#   h1            counted from the markup as well as from the visible tree, so
#                 a screen-reader-only `<h1>` counts as delivered.
CHROME_READINGS = ("nav_links", "footer_links", "chrome_paths", "h1")


def _delivered_chrome(page):
    """The four chrome readings of one page, as counts."""
    links = page.get("links") or {}
    signature = page.get("chrome_signature") or {}
    paths = signature.get("nav_path_count")
    if paths is None:
        # A hand-written snapshot, which SKILL.md invites, records the paths
        # and not the count. Defaulting the count to zero would read that page
        # as delivering no chrome on the strength of a field nobody wrote.
        paths = len(signature.get("nav_paths") or ())
    return {
        "nav_links": len(links.get("nav") or ()),
        "footer_links": len(links.get("footer") or ()),
        "chrome_paths": paths,
        # The larger of the two readings, because either one finding an `<h1>`
        # means the delivered document carries one.
        "h1": max(len((page.get("headings_in_markup") or {}).get("h1") or ()),
                  len((page.get("headings") or {}).get("h1") or ())),
    }


def _chrome_built_in_the_browser(page):
    """Why this page's header, nav and footer run in the browser, or [].

    Returns the measurements, not a verdict: the caller decides whether enough
    of the site is shaped this way to name the template.
    """
    # A page the crawl re-read from the rendered DOM is not evidence about the
    # delivered chrome. `static_view` keeps that page's delivered lengths and
    # shell markers but not its links, headings or chrome fingerprint, so those
    # three describe the browser's document here - and claiming an absence from
    # a document that was never fetched is the mistake the whole absence rule
    # exists to stop.
    if (page.get("content_from") or "") == "rendered":
        return []
    view = static_view(page)
    spa = view.get("spa_shell") or {}
    root, blobs = spa.get("root_selector"), spa.get("state_blobs") or []
    if not (root or blobs):
        # Without a mount point or a hydration payload there is nothing saying
        # a template runs here at all, and a static page with no menu is a
        # site that never built one - a different defect, owned by
        # `engagement-audit`.
        return []
    counts = _delivered_chrome(page)
    if any(counts[name] for name in CHROME_READINGS):
        return []
    return [
        "no `<nav>`, `<header>` or `<footer>` in the delivered HTML links anywhere "
        "on this site, and the document carries no `<h1>`",
        "the framework mount point `{}` is in the markup".format(root) if root else
        "the hydration payload {} is in the markup".format(", ".join(blobs[:2])),
        "{:,} characters of visible text did arrive, so the body copy is "
        "server-rendered and the layout around it is not".format(_delivered_text_len(view)),
    ]


def _check_client_rendered_chrome(result, content_pages, shells, chrome_pages):
    """One diagnosis behind the four symptoms four other skills each reported.

    `chrome_pages` is `{url: reasons}` for every content page whose delivered
    document carries a framework marker and none of its own chrome.
    """
    result.check("client-rendered-chrome")

    judged = [p for p in content_pages if not p.get("render_skipped")]
    shell_urls = {p["url"] for p in shells}
    # Reported above as shells, where the body copy is missing too. Naming the
    # chrome separately on those pages would be a second finding about one
    # document, and the shell fix - render on the server - already covers it.
    adopted = [p for p in judged if (p.get("content_from") or "") == "rendered"]
    assessable = [p for p in judged
                  if p["url"] not in shell_urls and p not in adopted]
    adopted_note = "" if not adopted else (
        ". {} re-read from the browser's document because the delivered HTML held "
        "almost nothing, so what the server sent for those is no longer in the "
        "snapshot to be read for its chrome ({})".format(
            plural(len(adopted), "page was", "pages were"),
            ", ".join(example_urls([p["url"] for p in adopted], 3))))

    if not assessable:
        result.skip("client-rendered-chrome",
                    "no content page's delivered HTML was available to read for its own "
                    "header, menu and footer{}".format(
                        adopted_note or ". Every content page is reported above as a "
                        "JavaScript shell, where the body copy is missing as well"))
        return []

    affected = [p for p in assessable if chrome_pages.get(p["url"])]
    if not affected:
        # Scoped to the pages it read, and it says which ones it did not. "Each
        # of the 7 content pages read here delivers its own navigation ... so
        # the layout is not waiting on JavaScript" was printed beside "4 of 12
        # content pages are delivered as JavaScript shells" from this same
        # skill: two answers to whether the site's pages arrived, one sentence
        # apart. The seven were the twelve minus the shells, and the sentence
        # never said so.
        shells_note = "" if not shell_urls else (
            ". The other {} reported above as JavaScript {} missing {} layout along "
            "with {} copy, and that finding covers {}".format(
                plural(len(shell_urls), "content page, the one", "content pages, the ones"),
                "shell, is" if len(shell_urls) == 1 else "shells, are",
                "its" if len(shell_urls) == 1 else "their",
                "its" if len(shell_urls) == 1 else "their",
                "it" if len(shell_urls) == 1 else "them"))
        result.skip("client-rendered-chrome",
                    "each of the {} content pages read here {}delivers its own navigation, "
                    "footer or heading in the initial HTML response, so on {} the layout is "
                    "not waiting on JavaScript{}{}".format(
                        len(assessable),
                        "that is not reported above as a JavaScript shell " if shell_urls
                        else "",
                        "those pages" if shell_urls else "them",
                        shells_note, adopted_note))
        return []

    result.signal("pages_whose_chrome_is_client_rendered",
                  sorted(p["url"] for p in affected)[:10])

    if len(affected) < CHROME_IN_BROWSER_SHARE * len(assessable):
        # Under half. One page missing its menu is a page, not a template, and
        # the fix for a template is not the fix for a page.
        result.skip("client-rendered-chrome",
                    "{} of the {} content pages read here deliver no navigation, footer or "
                    "heading of their own ({}), which is under the half at which this would "
                    "be the layout every page is built from rather than those pages{}".format(
                        len(affected), len(assessable),
                        ", ".join(example_urls([p["url"] for p in affected], 3)),
                        adopted_note))
        return affected

    example = sorted(affected, key=lambda p: p["url"])[0]
    result.add(
        id_hint="chrome-is-client-rendered",
        title="The header, navigation and footer are built in the browser, not delivered "
              "in the HTML",
        severity="high", confidence="high",
        evidence="{} of {} content pages deliver no navigation link inside a <nav>, <header> "
                 "or <footer>, no footer link, and no <h1>, while carrying a framework mount "
                 "point or hydration payload. On {}: {}. These are not empty shells: what "
                 "is missing is the layout, not the copy. Any check on this site reporting "
                 "an empty navigation, a missing "
                 "<h1>, no off-site profile links or no route to a contact page is reading "
                 "this same absence rather than four separate ones.{}".format(
                     len(affected), len(assessable), example["url"],
                     "; ".join(chrome_pages[example["url"]]), adopted_note),
        mechanism="C", root_cause="js-shell",
        summary="Server-render the layout - header, navigation and footer - as well as the "
                "page body.",
        how_to_fix=[
            "Turn on server-side rendering or static generation for the layout components, "
            "not only the route components. Next.js, Nuxt, Remix, Angular Universal and "
            "SvelteKit all render layouts on the server under the same switch.",
            "At minimum, ship the top-level menu links, one <h1> and the footer's links to "
            "contact, about and any off-site profiles as static HTML, and let the "
            "interactive menu hydrate over them.",
            "Check with `curl -s <url> | grep -c '<nav'` or View Source, not DevTools: "
            "DevTools shows the document after the framework has mounted.",
            "Re-run the rest of this audit afterwards. An empty navigation, a missing "
            "heading, absent social profile links and an unreachable contact page on this "
            "site are symptoms of this one defect and should resolve together.",
        ],
        effort="medium", owner="developer",
        rationale="Navigation is how a crawler reaches the rest of the site and how an "
                  "assistant learns what the brand offers and how to contact it. A consumer "
                  "that does not run JavaScript gets a page with no menu, no heading and no "
                  "way out of it, however complete the body copy is.",
        affected_pages=[p["url"] for p in affected],
    )
    return affected


# --------------------------------------------------------------------------
# A page publishing its own template source
# --------------------------------------------------------------------------
#
# The same defect showed up on three unrelated sites and nothing in the
# marketplace looked for it:
#
#   `{{getCtrlKey()}}` and     a single-page app served both as visible text -
#   `#native_company#`         a template expression and an ad-network merge
#                              field, neither ever substituted, and
#                              render-readability reported 0 findings.
#   `[wpsl]`                   a store-locator page whose entire visible
#                              content is that literal string. The report said
#                              "1 page carries too little text to be quoted"
#                              and never named the shortcode, which is the
#                              entire fix.
#
# One defect behind all three: the page is publishing an instruction to its own
# template instead of the content that instruction was supposed to produce. An
# assistant quoting the page quotes the placeholder, and unlike almost anything
# else this audit reports, the owner can usually fix it in minutes once they
# know - which is what makes the miss expensive.
#
# The shapes below are punctuation around an identifier, never words, so this
# reads the same on a Thai page as on an English one. That is deliberate and it
# is why the finding carries no "read in" sentence: the shared rule attaches
# that statement to a claim a word list decided, and no word list decides
# anything here.
TEMPLATE_ARTEFACT_PATTERNS = (
    # `{{ user.name }}`, `{{getCtrlKey()}}` - a moustache or Angular-style
    # expression. `<` and `>` are excluded so a stray brace either side of
    # markup cannot join two unrelated places in the text into one "artefact".
    ("template expression", re.compile(r"\{\{[^{}<>\n]{1,80}\}\}")),
    # `{% include "x" %}` - the statement form of the same family.
    ("template expression", re.compile(r"\{%[^{}%<>\n]{1,80}%\}")),
    # A field between sentinels. `%%FIELD%%`, `[[field]]`, `${field}`.
    ("merge field", re.compile(r"%%[A-Za-z][A-Za-z0-9_.\-]{1,38}%%")),
    ("merge field", re.compile(r"\[\[[A-Za-z][A-Za-z0-9_.\- ]{1,38}\]\]")),
    ("merge field", re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_.\-]{0,38}\}")),
    # `#native_company#`. Hashes are the one sentinel ordinary writing also
    # uses, so the identifier between them has to look like a field name and
    # not like a hashtag: either lowercase words joined by underscores, or
    # three or more characters of upper case. `#sale#` and `#3#` are not
    # matched; `#native_company#` and `#COMPANY_NAME#` are.
    ("merge field", re.compile(r"#(?:[a-z0-9]+_[a-z0-9_]+|[A-Z][A-Z0-9_]{2,39})#")),
    # `[wpsl]`, `[contact-form-7 id="4"]`. A shortcode tag is one word; what
    # may follow it is an attribute, which is a word with an `=` in it. That
    # clause is what separates a shortcode from the bracketed labels ordinary
    # pages carry - `[read more]`, `[click here]` - which match a looser
    # bracket pattern and are not defects. This shape is still the weakest of
    # the seven, so it is counted only where the artefact is the page's whole
    # content and never in the middle of prose; see
    # `_template_artefacts_by_page`.
    ("shortcode",
     re.compile(r"\[[a-z][a-z0-9_-]{2,39}(?:\s+[a-z0-9_-]+=[^\]\n]{1,80})*\]")),
)

def _the_pages_own_copy(page):
    """`body_text` without the `<title>` the extractor prepends to it.

    The denominator for the share below, and it had to be its own function
    because the two were measured against different things. `body_text` opens
    with the page's title - "A long page title here Store locator [wpsl]" for a
    page whose visible content is a heading and a placeholder - and the
    measurement that set the threshold read a page with no title, at 6
    characters in 34.

    Left as it was, an ordinary store-locator page with a sixty-character title
    puts its placeholder at under 7% and the check declines on the exact defect
    it was written for. The title is not the page's copy; it is what the tab
    says.
    """
    body = page.get("body_text") or ""
    title = (page.get("title") or "").strip()
    if title and body.startswith(title):
        return body[len(title):].strip()
    return body


# What share of a page's own copy the artefacts have to account for before the
# page is publishing them rather than mentioning them.
#
# A tenth, measured rather than chosen. The store-locator page this was written
# for, rebuilt and put through this marketplace's own extractor, records its own
# copy as "Store locator Store locator [wpsl]" - 6 artefact characters in 34, or
# 17.6%, because the extracted copy repeats the page's `<h1>` and the `<h1>` is
# all the page has besides the placeholder. A fifth would have declined the
# exact page the defect was found on.
#
# Measured against `_the_pages_own_copy` and not `body_text`, because the two
# are different strings: `body_text` opens with the page's `<title>`, which the
# measurement above did not include. Against `body_text` the same page with an
# ordinary sixty-character title falls to under 7% and this check declines on
# the defect it exists for - which is how the fixture built from the real
# page first failed.
#
# The companion test is the one carrying the weight: what is left once the
# artefacts are removed would not fill a quotable paragraph. Both have to hold,
# so an ordinary page of copy with one stray field in it is never read as a
# page whose content is a placeholder.
ARTEFACT_DOMINANT_SHARE = 0.1

# How little has to be left, once the artefacts are removed, before the page is
# publishing them rather than containing them.
#
# This was `MIN_QUOTABLE_TEXT`, 300 characters - the bar for whether a *whole
# page* has enough text to quote. Read as "what is left is not content" it is
# far too generous, and an end-to-end audit of a page built from the shapes
# seen on those sites proved it: a page carrying 198 characters of ordinary prose with
# `{{getCtrlKey()}}` and `#native_company#` in one sentence left a 166-character
# remainder, was labelled "the whole of the page's own content", and was
# reported at `high`. It is a page with two stray fields in it, which is the
# `low` case this check is supposed to separate out.
#
# Sixty, which is under half of `EVIDENCE_SENTENCE_MAX` and comfortably below
# one ordinary sentence. The real store-locator page leaves 27 characters -
# its own `<h1>`, repeated by the extractor - so it still stands alone, and any
# page with a sentence left standing no longer does.
ARTEFACT_LEAVES_NOTHING = 60

# How many pages have to print the same string before it is the template rather
# than one page's typo. Three, the same number `CHROME_SAMPLE_MIN_PAGES` uses
# and for the same reason: two is a coincidence a single odd page can produce.
ARTEFACT_TEMPLATE_PAGES = 3

# The share of pages carrying a code sample above which this site publishes
# code for a living - a templating tutorial, an API reference, a documentation
# tree - and a template expression in its prose is far more likely to be its
# subject than its defect. Half, and above it only the "this is the whole
# page" reading is kept, because no tutorial's entire content is one
# unexplained placeholder.
CODE_PUBLISHING_SHARE = 0.5


def _artefact_key(text):
    """The spelling two readings of one artefact compare on.

    Every space is removed, not merely collapsed, because the same string
    reaches this check in two spellings. `visible_text` runs `tidy_spacing`
    over the page, which deletes the space after an opening bracket and before
    a closing one, so `{{ value | json }}` in a paragraph is recorded as
    `{{value | json}}`; `code_text` keeps the sample as written. Comparing the
    two as they stand, the documentation guard below silently matches nothing -
    which is the whole guard, failing quietly, on the exact site it exists for.
    """
    return re.sub(r"\s+", "", text or "")


def _artefacts_in(text):
    """Every template artefact in one string.

    `(kind, string as the page prints it, comparison key)`. The printed form is
    what the evidence quotes, so a reader can search for it; the key is what
    two readings are compared on. See `_artefact_key`.
    """
    out = []
    for kind, pattern in TEMPLATE_ARTEFACT_PATTERNS:
        for match in pattern.finditer(text or ""):
            shown = " ".join(match.group(0).split())
            out.append((kind, shown, _artefact_key(shown)))
    return out


def _quoted_as_a_sample(pages):
    """Artefact strings this site prints inside its own code samples.

    A templating tutorial, an API reference and this repository's own README
    all contain `{{ value | json }}`, and a code sample is not a defect. The
    crawl already separates the two: `code_text` holds the text of every block
    `<pre>` and standalone `<code>` on the page, and `body_text` is the prose
    with exactly those removed - a separation added earlier so prose readings
    could exclude code.

    So the rule is the site's own: a string the site publishes as a sample
    somewhere is a string this check will not accuse anywhere. Matched on the
    exact string rather than on the shape, so a page showing `{{ value | json }}`
    in a sample does not license a different expression printed by a broken
    template on another page.
    """
    quoted = set()
    for page in pages:
        for _, _, key in _artefacts_in(page.get("code_text") or ""):
            quoted.add(key)
    return quoted


def _shows_code(page):
    """Does this page display code at all?"""
    return bool((page.get("code_sample_count") or 0) or (page.get("code_text") or ""))


def _template_artefacts_by_page(pages):
    """`{url: {...}}` for every page publishing an unsubstituted artefact.

    Pure: it takes measurements and reports no finding, so `_check_thin_pages`
    can read the same answer this check publishes rather than asking a second
    time. Two checks answering one question separately is the failure
    `shell_verdict` was written to end.

    Each record holds `artefacts` - `(kind, string, where)` triples - and
    `stands_alone`, which is the reading that lets `thin-html` name the cause
    instead of the symptom.

    Three things decide whether a match is counted, and they are what separates
    a check from a false-positive machine:

      the site's own samples   a string printed inside any page's `<pre>` or
                               standalone `<code>` is documentation and is
                               dropped everywhere. See `_quoted_as_a_sample`.
      the page's own samples   a page that displays code at all is read only
                               for the "this is the whole page" case. A
                               tutorial explaining `{{ }}` inline, with a
                               sample beside it, is a page about template
                               syntax.
      position and proportion  `[wpsl]` as a page's entire content is a broken
                               plugin; the same string inside a paragraph about
                               shortcodes is prose. So a match in the middle of
                               a page's writing is counted only where the page
                               shows no code at all, and the shortcode shape -
                               the loosest of the seven - only where the
                               artefact is the whole content.

    Read from the page record as it stands, not from `static_view`. Every other
    reading in this file wants the document the server sent, because the
    finding is the difference between that and the browser's; this one wants
    the text a reader is shown, and on the single-page app that produced this
    defect the expression is visible after the framework has run.
    """
    quoted = _quoted_as_a_sample(pages)
    publishes_code = bool(pages) and (
        len([p for p in pages if _shows_code(p)])
        >= CODE_PUBLISHING_SHARE * len(pages))

    found = {}
    for page in pages:
        visible = page.get("text") or page.get("body_text") or ""
        # The page's own copy where there is one, so "how much of this page is
        # placeholder" is measured against what the page says rather than
        # against its menu and its footer.
        own = _the_pages_own_copy(page) or visible
        seen = []
        for kind, shown, key in _artefacts_in(visible):
            if key not in quoted and (kind, key) not in [(k, c) for k, _, c in seen]:
                seen.append((kind, shown, key))

        # The slots a template fills with the page's own identity. Read
        # separately from the visible text and not out of it: a `<title>` is
        # not visible text at all, and it is the slot a broken template fills
        # with `{{ page.title }}` most often. Nobody writes a code example into
        # a page's own name, so a placeholder here is the template's output
        # rather than the page's subject.
        slots = []
        for where, value in (("the page title", page.get("title") or ""),
                             ("an <h1>", " ".join((page.get("headings") or {}).get("h1") or ())),
                             ("the meta description", page.get("meta_description") or "")):
            for kind, shown, key in _artefacts_in(value):
                if kind != "shortcode" and key not in quoted \
                        and (kind, shown, where) not in slots:
                    slots.append((kind, shown, where, key))

        if not seen and not slots:
            continue

        artefact_chars = sum(len(shown) * own.count(shown) for _, shown, _ in seen)
        remainder = own
        for _, shown, _ in seen:
            remainder = remainder.replace(shown, " ")
        remainder = " ".join(remainder.split())
        stands_alone = (
            artefact_chars > 0
            and len(remainder) < ARTEFACT_LEAVES_NOTHING
            and artefact_chars >= ARTEFACT_DOMINANT_SHARE * max(len(own.strip()), 1))

        if stands_alone:
            record = {"artefacts": [(kind, shown, "the whole of the page's own content")
                                    for kind, shown, _ in seen],
                      "keys": {key for _, _, key in seen},
                      "stands_alone": True,
                      "own_chars": len(own.strip()), "artefact_chars": artefact_chars}
        elif _shows_code(page) or publishes_code:
            # This page, or this site, publishes code. Only the reading above -
            # the placeholder *is* the page - survives that, and it did not
            # fire here.
            found.setdefault("_declined", []).append(page["url"])
            continue
        elif slots:
            record = {"artefacts": [(kind, shown, where) for kind, shown, where, _ in slots],
                      "keys": {key for _, _, _, key in slots},
                      "stands_alone": False}
        else:
            prose = [(kind, shown, "the page's visible text", key)
                     for kind, shown, key in seen if kind != "shortcode"]
            if not prose:
                continue
            record = {"artefacts": [(kind, shown, where) for kind, shown, where, _ in prose],
                      "keys": {key for _, _, _, key in prose},
                      "stands_alone": False}
        found[page["url"]] = record
    return found


def _check_template_artefacts(result, pages, artefacts):
    """The page is publishing an instruction to its own template.

    `artefacts` comes from `_template_artefacts_by_page` so this check and
    `thin-html` cannot reach different answers about one page.
    """
    result.check("unrendered-template-artefact")
    declined = artefacts.get("_declined") or []
    affected = {url: record for url, record in artefacts.items()
                if url != "_declined"}

    read_note = ""
    if declined:
        read_note = (" {} carrying code samples of their own were read only for a "
                     "placeholder standing as the whole page, because a page that "
                     "displays code is a page that may be quoting one ({})".format(
                         plural(len(declined), "page", "pages"),
                         ", ".join(example_urls(sorted(declined), 3))))

    if not pages:
        # A pass phrased as a measurement over nothing is what this skill's
        # sibling checks used to print - "each of the 0 pages this check
        # could assess carries at least 300 characters".
        result.skip("unrendered-template-artefact",
                    "no page returned HTTP 200, so there was no delivered HTML to read for a "
                    "template placeholder")
        return {}

    if not affected:
        result.skip("unrendered-template-artefact",
                    "no page prints a template expression, a merge field between sentinels "
                    "or a shortcode as visible text where its content should be. {} that "
                    "answered 200 {} read, matched on {} punctuation shapes rather "
                    "than on any word list, and a string this site prints inside its own "
                    "code samples was not counted anywhere.{}".format(
                        plural(len(pages), "page", "pages"),
                        "was" if len(pages) == 1 else "were",
                        len(TEMPLATE_ARTEFACT_PATTERNS), read_note))
        return {}

    standing = {url: record for url, record in affected.items()
                if record.get("stands_alone")}
    named = {url: record for url, record in affected.items()
             if not record.get("stands_alone")
             and any(where != "the page's visible text"
                     for _, _, where in record["artefacts"])}
    result.signal("pages_publishing_template_source", sorted(affected)[:10])

    # How many pages print the same string. One page is a typo somebody left
    # behind; the same string on three is the template every page is built
    # from, and the fix is one edit either way but the reader needs to know
    # which.
    repeats, spelling = {}, {}
    for record in affected.values():
        for key in record.get("keys") or ():
            repeats[key] = repeats.get(key, 0) + 1
        for _, shown, _ in record["artefacts"]:
            spelling.setdefault(_artefact_key(shown), shown)
    template_wide = sorted(spelling.get(key, key) for key, count in repeats.items()
                           if count >= ARTEFACT_TEMPLATE_PAGES)

    # A homepage publishing `{{getCtrlKey()}}` and a footer with one stray
    # merge field are not the same problem, and the difference is where the
    # placeholder is rather than how many there are.
    home = sorted(url for url, page in
                  ((p["url"], p) for p in pages)
                  if page.get("page_type") == "home" and url in affected)
    several = sorted(url for url, record in affected.items()
                     if len(record.get("keys") or ()) > 1)

    if standing or named:
        # The placeholder is standing where the page's content or its name
        # should be: there is nothing else on the page, or the template put it
        # in the title, the heading or the description. A visitor sees it too.
        severity, confidence = "high", "high"
    elif template_wide or home or several:
        # The template rather than one page. Repetition says so directly; the
        # homepage says it by position, which is why every other check in this
        # file judges the homepage on its own bar; and two different
        # placeholders in one document say it because a typo does not happen
        # twice in two spellings.
        severity, confidence = "medium", "high"
    else:
        # One stray field inside a page that otherwise reads normally. Certain
        # as an observation - the string is quoted below and a reader can find
        # it in View Source - and the weaker claim is that it is a defect
        # rather than something the author typed on purpose.
        severity, confidence = "low", "medium"

    kinds = {kind for record in affected.values() for kind, _, _ in record["artefacts"]}
    steps = [
        "Open View Source on the page listed - not DevTools, which shows the document "
        "after any script has run - and search for the exact string quoted above. It is "
        "in the delivered HTML, so a visitor, a crawler and an assistant all read it.",
    ]
    if "shortcode" in kinds:
        steps.append(
            "A shortcode in square brackets reaches the page unrendered when the plugin "
            "that registers that tag is deactivated, removed or renamed. The tag inside "
            "the brackets is the handle the plugin registers, so searching your installed "
            "plugins - or your platform's plugin directory - for that exact word names "
            "the one to reinstall. Until it is back, write the facts the plugin was "
            "producing (the addresses, the opening hours, the telephone numbers) into the "
            "page as HTML.")
    if "template expression" in kinds:
        steps.append(
            "An expression in braces printed as text means either that the template was "
            "served without being rendered, or that the value it names does not exist in "
            "the data the template was given. Fix the binding, or delete the expression - "
            "an expression a reader can see is never the intended output.")
    if "merge field" in kinds:
        steps.append(
            "A field between sentinels belongs to a snippet from somewhere else - an ad "
            "unit, a mail merge, an affiliate tag - whose script was supposed to replace "
            "it before the page was shown. Either make that script load, or remove the "
            "snippet: as it stands the placeholder is being published as the brand's own "
            "words.")
    steps.append(
        "Check the fix with `curl -s <url> | grep -F '<the string>'`, which should return "
        "nothing. Then look for the same string on the rest of the site: a placeholder in "
        "a shared template appears on every page that template builds.")

    example_lines = []
    for url in sorted(affected)[:5]:
        record = affected[url]
        pieces = []
        for _, shown, where in record["artefacts"][:3]:
            piece = "`{}` in {}".format(truncate(shown, 60), where)
            if record.get("stands_alone"):
                piece += " ({} of {} characters)".format(
                    record.get("artefact_chars", 0), record.get("own_chars", 0))
            pieces.append(piece)
        example_lines.append("{}: {}".format(url, "; ".join(pieces)))

    result.add(
        id_hint="unrendered-template-artefact",
        title="{} template source instead of the content it was meant to produce".format(
            plural(len(affected), "page publishes", "pages publish")),
        severity=severity, confidence=confidence,
        # The verb agrees with the count. Reports printed "1 URL were crawled"
        # and "1 of the profile link the brand publishes lead nowhere", and a
        # report which cannot conjugate its own sentence reads as one that did
        # not check its numbers either.
        evidence="{} of the {} pages that answered 200 {} an unsubstituted template "
                 "artefact - a template expression, a merge field between sentinels, or a "
                 "shortcode - as visible text: {}.{}{}{} These are punctuation shapes, not "
                 "words, so this reading does not depend on the language the site is "
                 "written in; a string this site prints inside its own code samples was "
                 "not counted anywhere.{}".format(
                     len(affected), len(pages),
                     "prints" if len(affected) == 1 else "print",
                     "; ".join(example_lines),
                     " The same string appears on {} or more pages ({}), so it is in a "
                     "shared template rather than on one page.".format(
                         ARTEFACT_TEMPLATE_PAGES,
                         ", ".join("`{}`".format(truncate(k, 40))
                                   for k in template_wide[:3]))
                     if template_wide else "",
                     " On {} the placeholder is the whole of the page's content, so there "
                     "is nothing else on the page for anyone to read.".format(
                         ", ".join(example_urls(sorted(standing), 3)))
                     if standing else "",
                     " One of them is the homepage ({}), which is the page most consumers "
                     "read first.".format(home[0]) if home and not (standing or named)
                     else "",
                     read_note),
        mechanism="C",
        # Its own cause, not `js-shell`, and the distinction is the fix. A
        # shell is a page whose content arrives when a browser runs the script,
        # and the fix is rendering - days of work. This is a page that
        # published the instruction instead of what the instruction was meant
        # to produce, and the fix is one broken plugin or one unsubstituted
        # merge field. Filing them under one tag would let the report offer the
        # expensive fix for the cheap defect.
        root_cause="unrendered-template-artefact",
        summary="Replace the placeholder these pages print with the content it was supposed "
                "to produce, and find the template it came from.",
        how_to_fix=steps,
        # Minutes to hours, and that is the point of reporting it at all: the
        # owner can usually fix this the day they read it.
        effort="low", owner="developer",
        rationale="An assistant quoting this page quotes the placeholder. The page is "
                  "publishing an instruction to its own template rather than the content "
                  "that instruction was supposed to produce, so whatever the template was "
                  "meant to say - a store locator, a form, a company name - is on the page "
                  "for nobody, human or machine.",
        affected_pages=sorted(affected),
    )
    return standing


def _check_shells(result, snapshot, content_pages, render_mode, chrome_pages=None,
                  artefacts=None):
    # `thin-html` belongs to `_check_thin_pages` and was registered here too.
    # `SkillResult` records the first function to register a name as its owner,
    # so a shell finding raised here marked `thin-html` as having fired, and
    # the thin check could then skip that same name into `not_applicable` -
    # one check in two buckets the report presents as exclusive.
    result.check("spa-shell-detection")

    # Asked once per page, and every reader below - this check, the homepage
    # branch, and `_check_thin_pages` - answers from this one dictionary. Two
    # checks calling the detector separately is how they came to contradict
    # each other about the same page; see `shell_verdict`.
    verdicts = {p["url"]: shell_verdict(p) for p in content_pages}
    # A page whose content is its own links to pages that arrived is a way in,
    # not a container, whatever its mount point looks like. See
    # `gateway_destinations`. Decided before `shells` is built, so every reader
    # of the verdict below - this check, the homepage branch and `thin-html` -
    # sees the same answer.
    readable = pages_of(snapshot) if snapshot is not None else content_pages
    delivered = pages_that_delivered_themselves(readable)
    gateways = {}
    for page in content_pages:
        if verdicts[page["url"]][0] not in (SHELL, SHELL_UNSETTLED):
            continue
        reached = gateway_destinations(page, delivered)
        if reached:
            gateways[page["url"]] = reached
            verdicts[page["url"]] = ("", [])
    if gateways:
        result.signal("pages_that_are_gateways", sorted(gateways))
    shells = [p for p in content_pages if verdicts[p["url"]][0] == SHELL]
    # One signal each: not enough to accuse a template, and far too much to let
    # `thin-html` tell the owner these pages were written short.
    unsettled = [p for p in content_pages if verdicts[p["url"]][0] == SHELL_UNSETTLED]
    home = next((p for p in content_pages if p.get("page_type") == "home"), None)
    home_is_shell = home is not None and verdicts[home["url"]][0] == SHELL
    recovered = None
    if home_is_shell and "rendered_text_len" in (home or {}):
        # "Recovered" means JavaScript supplied the bulk of the text and there
        # is now enough of it to quote. The bar used to be a flat 900 chars,
        # which called a homepage that went from 13 characters to 538 "not
        # reachable even with JavaScript enabled" - the opposite of what had
        # just been measured, and a `critical` verdict on a site whose real
        # problem is `high`.
        # Two separate questions, kept separate. "Is there now enough text to
        # quote" decides the sentence and the severity; "did JavaScript supply
        # the bulk of it" only chooses the wording. Fused into one boolean, a
        # homepage the browser read 1,500 characters of was told "a Playwright
        # pass recovered only 1,500 chars, so the content is not reachable even
        # with JavaScript enabled" - at critical severity, leading the report.
        rendered_len = home["rendered_text_len"]
        recovered = rendered_len >= MIN_QUOTABLE_TEXT
        js_supplied_bulk = rendered_len >= _static_text_len(home) * 3

    if home_is_shell:
        if recovered is True:
            severity, confidence = "high", "high"
            extra = ("A browser pass read {} chars after JavaScript ran, so the content "
                     "exists but only for consumers that execute JavaScript.{}".format(
                         home.get("rendered_text_len"),
                         "" if js_supplied_bulk else
                         " Most of it is already in the delivered HTML; JavaScript adds the rest."))
        elif recovered is False:
            severity, confidence = "critical", "high"
            extra = "A Playwright pass recovered only {} chars, so the content is not reachable " \
                    "even with JavaScript enabled.".format(home.get("rendered_text_len"))
        elif render_mode == "rendered":
            # The browser ran; it failed on this one page. Saying no browser
            # was available sends the reader to install one they already have,
            # which is the mistake this skill's own comments say must not be
            # made about the rendering mode.
            severity, confidence = "critical", "medium"
            extra = ("The browser pass ran but failed on this page ({}), so it could not be "
                     "confirmed whether JavaScript recovers the text.".format(
                         truncate(home.get("render_error") or "no reason was recorded", 120)))
        else:
            severity, confidence = "critical", "medium"
            extra = ("No rendered pass was available on this machine, so it could not be "
                     "confirmed whether JavaScript recovers the text.")
        result.add(
            id_hint="homepage-is-javascript-shell",
            title="The homepage is delivered as an empty JavaScript shell",
            severity=severity, confidence=confidence,
            evidence="{}: {}. {}".format(
                home["url"], "; ".join(verdicts[home["url"]][1]), extra),
            mechanism="C", root_cause="js-shell",
            summary="Server-render the homepage, or pre-render it to static HTML at build time.",
            how_to_fix=[
                "Turn on server-side rendering or static generation for the homepage in your "
                "framework (Next.js, Nuxt, Remix, Angular Universal and SvelteKit all support this).",
                "Verify with `curl -s <url> | grep -c '<h1'` or by using View Source, not "
                "DevTools: DevTools shows the page after JavaScript has run.",
                "At minimum, put the H1, the one-sentence description of the brand, and the "
                "primary facts into the server response even if the rest hydrates client-side.",
                "Keep the client-side app; this is about what the first response contains, "
                "not about abandoning the framework.",
            ],
            effort="high", owner="developer",
            rationale="A page that looks complete to a human can be empty to a "
                      "machine. Many crawlers and assistant fetchers read only the first HTML "
                      "response, so an empty shell means the brand has no homepage at all.",
            affected_pages=[home["url"]],
        )

    others = [p for p in shells if p is not home]
    if others:
        # `rate` decides severity and is never printed. It counts every shell,
        # homepage included, because "more than half this site's pages ship
        # empty" is a fact about the site rather than about this finding's list.
        rate = len(shells) / float(len(content_pages))
        # What the rendered sample actually showed about pages of this shape.
        #
        # This finding is a claim about every page in the list, drawn from
        # static HTML, while at most five of them were rendered. On a shop
        # built with a JavaScript framework it said 30 of 33 pages ship empty
        # and asked for "several days of development time" re-architecting
        # rendering - and the pages were not empty; the browser had simply
        # been given four and a half seconds to hydrate them. The settle wait
        # is fixed in the crawl, but the shape of the claim was wrong too: if
        # the sample that was measured recovered its text, that is evidence
        # about the unmeasured pages, and the finding has to carry it.
        # Over `others`, the pages this finding is about, not over `shells`.
        # Every number printed below comes from that one set; see the evidence
        # line.
        measured = [p for p in others if p.get("rendered_text_len") is not None]
        recovered_sample = [p for p in measured
                            if p["rendered_text_len"] >= MIN_QUOTABLE_TEXT
                            and p["rendered_text_len"] > _static_text_len(p) * 3]
        if measured and len(recovered_sample) == len(measured):
            severity, confidence = "medium", "medium"
            sample_note = (" A browser pass rendered {} of these and JavaScript supplied "
                           "readable text on all {}, so the content exists for consumers "
                           "that execute JavaScript. The remaining {} were not rendered, "
                           "and this finding assumes they behave the same way."
                           .format(len(measured), len(measured),
                                   len(others) - len(measured)))
        else:
            severity = "high" if rate > SHELL_SHARE_HIGH else "medium"
            confidence = "high" if measured else "medium"
            sample_note = (
                " {} of these were rendered in a browser and the text did not appear."
                .format(len(measured)) if measured else
                " None of these were rendered in a browser, so whether JavaScript recovers "
                "the text is inferred from the static HTML rather than measured.")
        result.add(
            id_hint="content-pages-are-javascript-shells",
            # One set behind the title, the percentage, the examples and the
            # affected list, and it is `others` - the pages this finding is
            # about. The title and the percentage used to be computed from
            # `shells`, which includes the homepage, while the list underneath
            # was `others`, which does not: a site with seven shells printed
            # "7 of 33 content pages" above six URLs, and a reader who counts
            # them finds the finding disagreeing with itself. The homepage has
            # its own finding directly above, so it is named here rather than
            # counted here.
            title="{} of {} content pages are delivered as JavaScript shells".format(
                len(others), len(content_pages)),
            severity=severity,
            confidence=confidence,
            evidence="{}% of crawled content pages carry two or more shell signals{}. "
                     "Examples: {}.{}".format(
                         pct(len(others), len(content_pages)),
                         ", besides the homepage reported separately" if home_is_shell else "",
                         "; ".join("{} ({})".format(p["url"], verdicts[p["url"]][1][0])
                                   for p in sorted(others, key=lambda x: x["url"])[:3]),
                         sample_note),
            mechanism="C", root_cause="js-shell",
            summary="Server-render or pre-render the page templates that currently ship empty.",
            how_to_fix=[
                "Identify the templates behind the pages listed (they are usually one or two "
                "templates, not one problem per page).",
                "Enable server-side rendering or static generation for those routes.",
                "Re-check with View Source that the main heading and body copy are present in "
                "the delivered HTML.",
            ],
            effort="high", owner="developer",
            rationale="These pages are invisible to any consumer that does not run "
                      "JavaScript, which includes a large share of the crawlers that feed "
                      "AI answers.",
            affected_pages=[p["url"] for p in others],
        )

    if not shells:
        # Scoped to what was actually established, and it names the pages that
        # carry a signal without reaching the bar. The old sentence claimed
        # every content page delivered readable text, in a report whose other
        # half was about a page holding twelve characters.
        #
        # And it names the pages whose chrome is missing, because the same
        # sentence was printed unqualified about a storefront that delivers its
        # product copy and none of its header, menu or footer. Readable text
        # arriving is what this check measured and all it may report; the page
        # a machine got was still not the page a visitor sees, and a reader who
        # stops at this line would never learn that.
        also_chrome = [p for p in content_pages
                       if (chrome_pages or {}).get(p["url"])]
        result.skip("spa-shell-detection",
                    "every crawled content page delivered readable text in the initial HTML "
                    "response" + (
                        "" if not unsettled else
                        ", except {}, which is not enough to say the template ships empty and "
                        "is enough that no check here treats such a page as one written "
                        "short ({})".format(
                            plural(len(unsettled), "page carrying a single shell signal",
                                   "pages carrying one shell signal apiece"),
                            ", ".join(example_urls([p["url"] for p in unsettled], 3)))) + (
                        "" if not also_chrome else
                        ". This is a statement about the body copy only: {} of them deliver "
                        "no navigation, footer or heading of their own, which "
                        "`client-rendered-chrome` reports on".format(len(also_chrome))) + (
                        # Named, because a gateway delivers little text of its
                        # own and a reader who re-measures it will find that.
                        "" if not gateways else
                        ". {} little text of {} own and is read as a way into the site, "
                        "not a container: {}".format(
                            plural(len(gateways), "page carries", "pages carry"),
                            "its" if len(gateways) == 1 else "their",
                            "; ".join("{} links {} this crawl read, each of which delivered "
                                      "its own content".format(
                                          url, plural(len(dests), "page", "pages"))
                                      for url, dests in sorted(gateways.items())[:3]))))

    # One answer to "did this page's copy arrive in the HTML", published for
    # every page the crawl read and not only the ones typed as content, because
    # the other five skills grade every page. See
    # `pages_whose_copy_did_not_arrive`.
    copy_by_script = pages_whose_copy_did_not_arrive(readable, shells, gateways)
    if copy_by_script:
        result.signal("pages_whose_copy_did_not_arrive", sorted(copy_by_script))

    _check_thin_pages(result, content_pages, shells, snapshot, unsettled,
                      artefacts=artefacts, gateways=gateways,
                      copy_by_script=copy_by_script)
    return shells


def pages_whose_copy_did_not_arrive(pages, shells, gateways=()):
    """URLs whose own copy is not in the delivered HTML, on a site with shells.

    A jewellery and textiles shop delivered its homepage and four content
    pages as shells, and its thirteen category pages the same way - 37 to 69
    visible characters beside 200 KB of script - but those were typed `other`,
    so no check here read them. Its store-locator pages delivered a 40-link
    menu and 71 characters of their own. The report then issued "32 of 34
    pages have no H1", "no structured data at all", "no next step" and, from
    this skill, "write two or three sentences of body copy" for the store
    pages, each as its own job, while the one cause behind all of them was
    already reported here.

    Empty unless this run found at least one shell, because the claim is
    about a site shown to build its pages in the browser. On such a site a
    page is listed when:

      its delivered document is itself a shell by `shell_verdict`, whatever
        its type; or
      it carries a framework marker - a mount point or a hydration payload of
        the same name - that the shells carry, its body copy is under the
        quotable floor, and the rest of its visible text is no more than its
        template's header, menu and footer (`_short_page_verdict` says
        "empty", not "extraction").

    The marker has to match the shells'. A hydration payload alone is on every
    page of some frameworks, fully server-rendered ones included, so it is
    evidence here only beside pages that were measured arriving empty.
    """
    if not shells:
        return set()
    markers = set()
    for page in shells:
        spa = static_view(page).get("spa_shell") or {}
        markers.update(spa.get("state_blobs") or [])
        if spa.get("root_selector"):
            markers.add(spa["root_selector"])
    out = {page["url"] for page in shells}
    by_template, site_wide = _furniture_size(
        [page for page in pages if not page.get("render_skipped")])
    for page in pages:
        url = page.get("url")
        if not url or url in out or url in gateways:
            continue
        if shell_verdict(page)[0] == SHELL:
            out.add(url)
            continue
        view = static_view(page)
        spa = view.get("spa_shell") or {}
        own = set(spa.get("state_blobs") or [])
        if spa.get("root_selector"):
            own.add(spa["root_selector"])
        if not own & markers:
            continue
        if (view.get("body_text_len") or 0) >= quotable_floor(view):
            continue
        if _short_page_verdict(view, by_template, site_wide) != "empty":
            continue
        out.add(url)
    return out


def _children_left_unlinked(page, pages):
    """Crawled pages beneath this page's address that it never links, or [].

    A furniture retailer's blog index delivered a megabyte - 759,836 bytes of
    inline script and a hydration payload - with 289 characters of its own and
    not one link to a post, while this crawl read two posts at addresses under
    it. `thin-html` told the owner to "write two or three sentences of body
    copy". The index is not short of copy; its list of posts is filled in by
    the script, and no sentence written above it would put the list in the
    HTML.

    Returned only where the page also carries a hydration payload or more than
    `HEAVY_SCRIPT_BYTES` of inline script, so an index that links its
    children, or one with nothing that could be filling the list in, is judged
    by the usual rule.
    """
    view = static_view(page)
    spa = view.get("spa_shell") or {}
    if not (spa.get("state_blobs") or _script_bytes(view) > HEAVY_SCRIPT_BYTES):
        return None
    here = _same_page_key(page.get("url"))
    if here.endswith("/") and here.count("/") == 1:
        return None
    linked = {_same_page_key((link or {}).get("url"))
              for link in (view.get("links") or {}).get("internal") or []
              if isinstance(link, dict)}
    below = here.rstrip("/") + "/"
    children = sorted(other["url"] for other in pages
                      if other.get("url") and _same_page_key(other["url"]).startswith(below))
    if not children:
        # The same retailer's other two regional blog indexes: the same
        # megabyte of script, no link to any post, and no post read beneath
        # either, so there was nothing crawled to show missing from the list.
        # An address that names itself as an index of posts, linking nothing
        # at all beneath itself while carrying the payload that fills lists in,
        # is the same page; an empty list is `[]`, set aside with that said.
        if (_INDEX_SEGMENT_RE.search(here.rstrip("/").rsplit("/", 1)[-1])
                and not any(key.startswith(below) for key in linked)):
            return []
        return None
    if any(_same_page_key(child) in linked for child in children):
        return None
    return children


# The last path segment of an index of posts: a blog, a news or press section,
# a journal. Named shapes only - `/sg/blog`, `/news/`, `/journal` - because
# this is what licenses setting a short page aside without a crawled child to
# point at.
_INDEX_SEGMENT_RE = re.compile(
    r"^(?:blogs?|news|newsroom|press|journal|articles|stories|insights|"
    r"magazine|posts|updates|editorial)$", re.I)


def _script_redirect(page):
    """`(via, target)` where the page's content is a script sending the browser on.

    Read from `script_redirect`, which the extractor records when an inline
    script assigns `window.location`, `location.href` or calls
    `location.replace` / `location.assign`. A one-page site's root address is
    eight characters of text and a script sending the browser to `/en/` or one
    of seventeen other language paths; this skill told it, first in "Start
    here" and at high confidence, to write a paragraph of plain text on it.
    """
    record = page.get("script_redirect")
    if not record:
        return None
    if isinstance(record, str):
        return "window.location", record
    # The extractor writes the destination under `url`; `target` is read too so
    # a record written either way names where the script sends the browser.
    return ((record.get("via") or "window.location"),
            (record.get("target") or record.get("url") or ""))


def _site_part(host):
    """The part of a hostname one organisation holds: `streaming.x.go.kr` -> `x.go.kr`.

    The last two labels, or the last three where the second-last is a short
    registry label under a two-letter country (`go.kr`, `co.uk`, `or.th`).
    Close enough to tell a frame of the site's own from a tag manager's.
    """
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _frame_is_the_page(page):
    """The frame a short page consists of, or None.

    A museum's two virtual-tour pages deliver a title, 41 and 26 characters,
    no link of any kind, and one frame holding the tour from the museum's own
    streaming host. The extractor marks that frame invisible - it is sized by
    the tour's script - so the iframe check passed over it, and five findings
    from three skills then asked those two pages for a paragraph, a
    breadcrumb, a next step and links of their own. The page is the frame.

    All of: under the quotable floor, no link at all on the page, and a frame
    that is not a video or a map and is served from the site's own part of the
    domain - a tag manager's hidden frame is on somebody else's.
    """
    if page.get("body_text_len", 0) >= quotable_floor(page):
        return None
    links = page.get("links") or {}
    if links.get("internal_count") or links.get("external_count") \
            or links.get("internal") or links.get("external"):
        return None
    here = _site_part(urlparse(page.get("final_url") or page.get("url") or "").hostname)
    for frame in page.get("iframes") or []:
        if frame.get("is_video") or frame.get("is_map") or frame.get("is_audio"):
            continue
        if here and _site_part(urlparse(frame.get("src") or "").hostname) == here:
            return frame
    return None


# A cover page: the homepage delivering at most this many visible characters
# and one to three links into the site. The engagement skill reports it, once,
# with everything it costs; the same measurement is restated here because each
# skill runs on its own, and it is what stops this skill describing the same
# page a second time as "main content in images".
COVER_PAGE_TEXT = 200
COVER_PAGE_MAX_LINKS = 3


def _is_a_cover_page(page):
    if page.get("page_type") != "home" or page.get("script_redirect"):
        return False
    if (page.get("text_len") or 0) > COVER_PAGE_TEXT:
        return False
    here = {_same_page_key(page.get(field)) for field in ("url", "final_url") if page.get(field)}
    targets = {_same_page_key(link["url"])
               for link in (page.get("links") or {}).get("internal") or []
               if isinstance(link, dict) and link.get("url") and not link.get("fragment")}
    return 1 <= len(targets - here) <= COVER_PAGE_MAX_LINKS


def _unassessed_note(snapshot, content_pages):
    """How many 200 pages this check did not judge, or "" when it judged them all.

    Without it, "every crawled content page carries at least 300 characters"
    reads as a statement about the crawl, and on one site it stood beside
    findings that counted seven crawled pages of 45 to 65 characters.
    """
    if snapshot is None:
        return ""
    judged = {p["url"] for p in content_pages}
    others = [p for p in pages_of(snapshot) if p["url"] not in judged]
    if not others:
        return ""
    return (". {} of the {} pages that answered 200 were not assessed here - either this "
            "audit could not tell what kind of page they are, or they delivered a stub that "
            "no browser read - and nothing above is a claim about them".format(
                len(others), len(others) + len(judged)))


def _set_aside_note(extraction_failed, could_not_tell, assembled_elsewhere=(), shells=(),
                    artefact_pages=(), gateways=(), listings=(), redirects=(), frames=()):
    """The short pages this check declined to judge, and why, or "".

    A short page dropped without being named is a page the reader believes was
    counted. Each reason is stated separately because they mean different
    things to whoever reads the report: the first is a defect in this audit's
    extraction, the second a question this audit could not answer at all, and
    the last three are pages whose emptiness has a different cause and a
    different fix - "write two or three sentences of body copy" is the wrong
    instruction for a document whose copy arrives when the script runs, and it
    is the wrong instruction again for a page whose copy exists in a template
    that never ran.

    `artefact_pages` is `[(url, string)]`. A real report said "1 page carries
    too little text to be quoted" about a store-locator page whose whole
    content is `[wpsl]`, and never named the broken plugin, which is the
    entire fix. The sentence was true and useless: the cause was
    measured on the same page by the same run and never reached the reader.
    """
    parts = []
    # `redirects` is `[(url, via, target)]`, `gateways` `[(url, [destination])]`
    # and `listings` `[(url, [child])]`: three shapes of short page whose fix is
    # not copy, each named with what was measured on it.
    if redirects:
        parts.append("{} the browser to another address by script and {} no content of "
                     "{} own ({}); a crawler that does not run the script stays on the empty "
                     "page, so the fix is a server redirect (HTTP 301 or 302) to that address, "
                     "or serving the content at this one, and not copy written on a page that "
                     "exists to redirect".format(
                         plural(len(redirects), "page sends", "pages send"),
                         "delivers" if len(redirects) == 1 else "deliver",
                         "its" if len(redirects) == 1 else "their",
                         "; ".join("{} - `{}` to {}".format(url, via, target or "an address "
                                                           "the script computes")
                                   for url, via, target in sorted(redirects)[:3])))
    if frames:
        parts.append("{} nothing but a frame holding another document of this site's ({}), "
                     "and no link of any kind; the copy is in the framed document, so that is "
                     "reported as a page whose main content is in a frame rather than as a "
                     "page written short".format(
                         plural(len(frames), "page carries", "pages carry"),
                         "; ".join("{} - {}".format(url, src)
                                   for url, src in sorted(frames)[:3])))
    if gateways:
        parts.append("{} a way into the site rather than a page of copy: {} - its content is "
                     "its links, every one of those pages delivered its own content, and "
                     "writing copy on it is a choice rather than a fix".format(
                         plural(len(gateways), "page is", "pages are"),
                         "; ".join("{} links {} this crawl read ({})".format(
                             url, plural(len(dests), "page", "pages"),
                             ", ".join(example_urls(dests, 3)))
                             for url, dests in sorted(gateways)[:3])))
    if listings:
        parts.append("{} to none of the pages beneath {} own address that this crawl read, "
                     "while carrying a hydration payload or a heavy inline script, so {} list "
                     "of them is filled in by the script: rendering that list on the server "
                     "is the fix, and writing copy above it is not ({})".format(
                         plural(len(listings), "page links", "pages link"),
                         "its" if len(listings) == 1 else "their",
                         "its" if len(listings) == 1 else "each",
                         "; ".join("{}, with {} beneath it unlinked".format(
                             url, ", ".join(example_urls(children, 2)))
                             if children else
                             "{}, which links no page beneath it at all".format(url)
                             for url, children in sorted(listings)[:3])))
    if artefact_pages:
        parts.append("{} an unsubstituted template placeholder as its whole content "
                     "({}), reported above: the copy exists in a template that did not "
                     "run, so naming the placeholder is the fix and writing more copy is "
                     "not".format(
                         plural(len(artefact_pages), "page publishes", "pages publish"),
                         "; ".join("{} (`{}`)".format(url, truncate(key, 40))
                                   for url, key in sorted(artefact_pages)[:3])))
    if shells:
        parts.append("{}, which is a different defect with a different fix ({})".format(
            plural(len(shells),
                   "page is reported above as a JavaScript shell",
                   "pages are reported above as JavaScript shells"),
            ", ".join(example_urls([p["url"] for p in shells], 3))))
    if assembled_elsewhere:
        parts.append("{} - an empty framework mount point, a state blob, a <noscript> "
                     "demanding JavaScript, or script bytes dwarfing the visible text of the "
                     "whole delivered document - so what arrived over the wire is not the "
                     "document a visitor sees and this check cannot say the copy was never "
                     "written ({})".format(
                         plural(len(assembled_elsewhere),
                                "page is short and carries a shell signal",
                                "pages are short and carry a shell signal"),
                         ", ".join(example_urls(
                             [p["url"] for p in assembled_elsewhere], 3))))
    if extraction_failed:
        # The count and the clause after it are one phrase in `plural`, not a
        # number glued to a sentence written for several. "1 page is short but
        # carry more text" is what the split version printed, and a reader of
        # the published report counts that as an error in the report rather
        # than in the site.
        parts.append("{} more text outside the extracted body than this template's header, "
                     "menu and footer account for, so the missing text is this audit's "
                     "extraction rather than the page and is not counted either way "
                     "({})".format(
                         plural(len(extraction_failed),
                                "page is short but carries", "pages are short but carry"),
                         ", ".join(example_urls([p["url"] for p in extraction_failed], 3))))
    if could_not_tell:
        parts.append("{} and no page on this site extracted well enough to measure how much "
                     "text its template's chrome holds, so it could not be decided whether "
                     "the text is missing from the page or from this audit's reading of it "
                     "({})".format(
                         plural(len(could_not_tell), "page is short", "pages are short"),
                         ", ".join(example_urls([p["url"] for p in could_not_tell], 3))))
    if not parts:
        return ""
    return ". Set aside: {}".format("; and ".join(parts))


def _shell_signals_counted(pages):
    """What the shell test measured on these pages, in numbers.

    The clause this replaces read: "the response carries no framework mount
    point, no state blob and no bulk of script that could be holding the copy
    back until it runs." On a candle shop's homepage the delivered document
    held `viewerModel` fifteen times, `wixBiSession` nine times, `warmupData`
    and `siteAssets`, and 558,468 characters of inline script - 42.9% of a
    1,300,857-character document. Two of the three clauses were false of the
    page they were about, and the same report named the platform in its own fix
    steps two sections later.

    They were false because they were not statements about the document. "No
    state blob" means "no name from `STATE_BLOB_PATTERNS`", and that list is
    seven names long; "no bulk of script" means "the ratio in `shell_verdict`
    did not fire", which is three conditions and not one. A negative in
    evidence has to be as checkable as a positive, so each clause below prints
    the number that was counted and the bar it fell short of, and the two
    vocabulary-limited clauses name the vocabulary.
    """
    views = [static_view(page) for page in pages]

    # The heaviest page, because if the page with the most script does not
    # reach the ratio then none of them does. Naming it also lets a reader
    # re-measure the claim on one URL rather than on the whole list.
    heaviest = max(views, key=_script_bytes)
    # The share as well as the two figures. A reader who sees "558,468 bytes of
    # inline script" beside a visible-text count still has to divide to see
    # what kind of document this is, and the division is the thing being
    # claimed: 42.9% of the bytes delivered were script.
    # Same precedence the crawl uses when it asks how big a page was:
    # `html_bytes` is what the server sent and `html_len` is what it decoded to.
    delivered_bytes = heaviest.get("html_bytes") or heaviest.get("html_len") or 0
    share = (" - {}% of the {:,} bytes delivered".format(
        pct(_script_bytes(heaviest), delivered_bytes), delivered_bytes)
        if delivered_bytes else "")
    script = ("the heaviest of them carries {:,} bytes of inline script{} beside {:,} "
              "characters of visible text in the whole delivered document, its own "
              "header, menu and footer included; a document is read here as code "
              "around a container only under {} characters of visible text, alongside "
              "more than {:,} bytes of script, at {} bytes or more per character"
              .format(_script_bytes(heaviest), share, _delivered_text_len(heaviest),
                      MIN_QUOTABLE_TEXT, HEAVY_SCRIPT_BYTES, SCRIPT_BYTES_PER_CHARACTER))

    roots = sorted({(v.get("spa_shell") or {}).get("root_selector") for v in views
                    if (v.get("spa_shell") or {}).get("root_selector")})
    if roots:
        biggest = max((v.get("spa_shell") or {}).get("root_text_len") or 0
                      for v in views if (v.get("spa_shell") or {}).get("root_selector"))
        mount = ("the framework mount {} they do carry ({}) holds up to {:,} characters, "
                 "above the {} below which a mount point counts as one nothing has "
                 "rendered into".format(plural(len(roots), "point", "points"),
                                        ", ".join("`{}`".format(r) for r in roots),
                                        biggest, EMPTY_ROOT_TEXT))
    else:
        mount = ("none carries a mount point from the {} this audit matches "
                 "exactly ({}), a list that cannot see a build naming its mount "
                 "anything else".format(
                     plural(len(SPA_ROOT_SELECTORS), "id and attribute",
                            "ids and attributes"),
                     ", ".join("`{}`".format(s) for s in SPA_ROOT_SELECTORS[:4])
                     + " and {} more".format(len(SPA_ROOT_SELECTORS) - 4)))

    blobs = sorted({name for v in views
                    for name in (v.get("spa_shell") or {}).get("state_blobs") or []})
    if blobs:
        blob = "the state {} {} {} present".format(
            plural(len(blobs), "blob", "blobs"),
            ", ".join("`{}`".format(b) for b in blobs), "is" if len(blobs) == 1 else "are")
    else:
        blob = ("none holds a state blob under any of the {} hydration payload names "
                "this audit searches the HTML for ({}), which are the formats it can "
                "name and not every platform's".format(
                    len(STATE_BLOB_PATTERNS),
                    ", ".join("`{}`".format(b) for b in STATE_BLOB_PATTERNS)))

    return "{}; {}; and {}".format(script, mount, blob)


def _one_page_per_document(pages):
    """One entry per document, where two hostnames serve the same one.

    A project's report said "2 of 17 page(s) share a `<title>`... the most
    repeated is 'Example' on http://www.example.org/, https://example.org/".
    Those are the same page: the first answers 301 to the www form, which
    serves the same bytes as the bare form. The site declares no canonical, so
    folding a URL into its canonical target - the method the report described -
    could not fold anything.

    A canonical is not required to see this. Two addresses that differ only in
    hostname and answered with the same title and the same body text are one
    document however the site labels them, and counting both inflates every
    denominator built on this list. The hostname that survives is chosen by
    sort order so two runs fold the same way.

    That a site answers on two hostnames with no canonical is itself a finding,
    and `crawl-access-audit` raises it; this only stops the duplicate being
    counted twice here.
    """
    folded, out = {}, []
    for page in sorted(pages, key=lambda p: p.get("url") or ""):
        landed = page.get("final_url") or page.get("url") or ""
        parts = urlparse(landed)
        key = ((parts.path.rstrip("/") or "/"), parts.query,
               page.get("title") or "", page.get("body_text_len") or 0)
        if key in folded:
            continue
        folded[key] = page["url"]
        out.append(page)
    return out


# How much more than its template's usual furniture a page may carry before
# the surplus is text this audit lost rather than one template differing from
# another.
#
# Absolute floor first, because one quotable paragraph is the smallest surplus
# worth calling content at all; a share of the furniture on top, because a site
# with a 6,000-character mega-footer varies more between its templates than one
# with a 400-character footer, and a fixed number would call that variation
# lost text on the big site and lost text nothing on the small one.
CHROME_VARIATION_CHARS = MIN_QUOTABLE_TEXT
CHROME_VARIATION_SHARE = 0.25

# How many well-extracted pages a template needs before its own furniture size
# is measured from it rather than from the site as a whole. Three: with two,
# the median is an average of two pages and one outlier moves it.
CHROME_SAMPLE_MIN_PAGES = 3


def _chrome_key(page):
    """The nav fingerprint that says which template a page was built from."""
    signature = page.get("chrome_signature") or {}
    return tuple(signature.get("nav_paths") or ())


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _furniture_size(pages):
    """How many characters this site's repeated chrome holds, per template.

    Measured only on the pages whose extraction plainly worked - anything with
    a quotable amount of body text - because on those the difference between
    the whole document's visible text and the extracted body text is exactly
    the header, menu and footer that were stripped out.

    Returns `({chrome key: characters}, site-wide median or None)`.
    """
    by_template, everywhere = {}, []
    for page in pages:
        body = page.get("body_text_len") or 0
        whole = page.get("text_len") or 0
        if body < MIN_QUOTABLE_TEXT or whole <= body:
            continue
        by_template.setdefault(_chrome_key(page), []).append(whole - body)
        everywhere.append(whole - body)
    sized = {key: _median(sizes) for key, sizes in by_template.items()
             if len(sizes) >= CHROME_SAMPLE_MIN_PAGES}
    return sized, _median(everywhere)


def _short_page_verdict(page, by_template, site_wide):
    """For a page with almost no body text: "empty", "extraction", or "".

    `extraction_looks_incomplete` drops any page whose extracted body text is
    under a quarter of the whole document's visible text, on the theory that
    such a page is one this audit read badly. On a page that is genuinely all
    chrome and no content that ratio is always tiny - the chrome is the whole
    document - so the guard written to stop false positives guaranteed a false
    negative on exactly the pages this check exists to find. A retailer's
    `/about-us` carries 59 characters of body text inside a 689-byte `<main>`
    holding one `<h1>` and nothing else, and the report passed the check with
    "each of the 56 pages this check assessed carries at least 300 characters".

    The ratio cannot separate the two cases because both produce the same
    ratio. What separates them is where the rest of the document's text went.
    Every page carries its template's furniture, and the size of that furniture
    is measurable on the pages of the same template whose extraction plainly
    worked. If the whole document holds no more than that furniture, there was
    never any content to lose and the page really is empty. If it holds
    substantially more, that surplus is text this audit failed to keep, and the
    page is one we could not read rather than one the site left blank.

    "" is returned when the site offers nothing to measure the furniture with -
    no page of any template extracted well - and the caller reports those pages
    as undecided rather than counting them either way.
    """
    if not extraction_looks_incomplete(page):
        # The two readings of the page agree that it is short. Nothing to tell
        # apart.
        return "empty"
    furniture = by_template.get(_chrome_key(page))
    if furniture is None:
        furniture = site_wide
    if furniture is None:
        return ""
    outside_the_body = (page.get("text_len") or 0) - (page.get("body_text_len") or 0)
    allowance = furniture + max(CHROME_VARIATION_CHARS, CHROME_VARIATION_SHARE * furniture)
    return "empty" if outside_the_body <= allowance else "extraction"


def _text_this_audit_missed(pages):
    """URLs whose delivered document plainly holds text this audit did not keep.

    Four checks below say some version of "this page has almost no text, so its
    content must be in the picture / the video / the frame / the PDF". Each of
    them measured "almost no text" with `body_text_len`, the prose left after
    the site furniture is stripped, and that is the field that produced the
    defect this file's `shell_verdict` was rewritten for: a storefront's
    homepage recorded 3,517 characters of visible text and 171 of extracted
    prose, because the extractor kept the copy and dropped the product grid.
    Four more accusations of the same shape were one such page away.

    `_short_page_verdict` already separates the two cases and is the only thing
    here that can: a page that is genuinely all furniture carries no more than
    its template's furniture, and a page whose text was lost carries
    substantially more. Only its "extraction" answer is used, so a check goes
    quiet exactly where this audit has established it read the page badly, and
    is unchanged everywhere else.
    """
    judged = [p for p in pages if not p.get("render_skipped")]
    by_template, site_wide = _furniture_size(judged)
    return {p["url"] for p in judged
            if _short_page_verdict(p, by_template, site_wide) == "extraction"}


def _missed_text_note(pages, subject):
    """The set-aside sentence naming the pages a check declined to grade."""
    if not pages:
        return ""
    return (". Set aside: on {} the delivered document holds more visible text than this "
            "audit managed to extract, so how much this page says in writing is not "
            "established and {} is not attributed to it here ({})".format(
                plural(len(pages), "page", "pages"), subject,
                ", ".join(example_urls([p["url"] for p in pages], 3))))


def _check_thin_pages(result, content_pages, shells, snapshot=None, unsettled=(),
                      artefacts=None, gateways=None, copy_by_script=()):
    """Pages that are simply too short to quote, with no shell to explain it.

    Distinct from `js-shell`, where the text exists but arrives after
    JavaScript, and from `image-locked-facts`, where it exists but as pixels.
    This is the plain case: the page really does say almost nothing, so there
    is no sentence for an assistant to lift and nothing for a visitor to read.

    `shells` and `unsettled` come from `shell_verdict`, which both this check
    and `spa-shell-detection` now read. This check never decides for itself
    whether a page is a container: it could reach the opposite answer, and on
    one education site's homepage it did.

    `artefacts` is the same rule one step further out.
    `_template_artefacts_by_page` has already measured that a page's whole
    content is `[wpsl]` or `{{getCtrlKey()}}`, and a page like that is short
    for a reason this check cannot see and must not overwrite: a real report
    said "1 page carries too little text to be quoted" where the answer was the name of
    a plugin that had stopped running.
    """
    result.check("thin-html")
    # `{url: string}` for the pages whose content is a placeholder. Only the
    # `stands_alone` records, because that is the reading that explains a short
    # page; a stray field in a page's footer explains nothing about its length.
    artefact_urls = {
        url: record["artefacts"][0][1]
        for url, record in (artefacts or {}).items()
        if url != "_declined" and record.get("stands_alone") and record.get("artefacts")}
    shell_urls = {p["url"] for p in shells}
    # A page carrying even one shell signal is a page whose delivered document
    # is not the document a visitor sees, and nothing below may describe it as
    # written short.
    unsettled_urls = {p["url"] for p in unsettled}
    # The same rule one step wider: on a site this run showed builds its pages
    # in the browser, a short page carrying the shells' own framework marker is
    # one whose copy did not arrive either. See `pages_whose_copy_did_not_arrive`.
    unsettled_urls |= set(copy_by_script or ()) - shell_urls
    gateways = gateways or {}
    crawled = pages_of(snapshot) if snapshot is not None else content_pages
    # `render_skipped` is set by the crawl on a page that delivered almost
    # nothing and that the browser pass never reached, and such a page cannot
    # support a claim about how much text it carries: the delivered stub and
    # the document a visitor sees are two different pages, and this check only
    # ever read the first.
    judged = [p for p in content_pages if not p.get("render_skipped")]
    # A URL and the same URL on the site's other hostname are one page. See
    # `_one_page_per_document`: without this the denominator of every sentence
    # below counts the same document twice.
    judged = _one_page_per_document(judged)
    by_template, site_wide = _furniture_size(judged)

    thin, could_not_tell, extraction_failed = [], [], []
    set_aside_shells, assembled_elsewhere, publishing_a_placeholder = [], [], []
    redirecting, gateway_pages, listings, framed = [], [], [], []
    for page in judged:
        if page["url"] in shell_urls:
            set_aside_shells.append(page)
            continue
        if page.get("body_text_len", 0) >= quotable_floor(page):
            continue
        # Before the shell-signal test and before the furniture accounting,
        # because this page's shortness has a cause that was measured rather
        # than inferred, and the reader needs the cause and not the symptom.
        if page["url"] in artefact_urls:
            publishing_a_placeholder.append((page["url"], artefact_urls[page["url"]]))
            continue
        redirect = _script_redirect(page)
        if redirect:
            redirecting.append((page["url"], redirect[0], redirect[1]))
            continue
        # A page that is nothing but a frame of the site's own: its copy is
        # in the framed document, and `iframed-main-content` reports it with
        # that fix. See `_frame_is_the_page`.
        frame = _frame_is_the_page(page)
        if frame is not None:
            framed.append((page["url"], frame.get("src") or ""))
            continue
        if page["url"] in gateways:
            gateway_pages.append((page["url"], gateways[page["url"]]))
            continue
        children = _children_left_unlinked(page, crawled)
        if children is not None:
            listings.append((page["url"], children))
            continue
        if page["url"] in unsettled_urls:
            assembled_elsewhere.append(page)
            continue
        verdict = _short_page_verdict(page, by_template, site_wide)
        if verdict == "empty":
            thin.append(page)
        elif verdict == "extraction":
            extraction_failed.append(page)
        else:
            could_not_tell.append(page)

    set_aside = _set_aside_note(extraction_failed, could_not_tell,
                                assembled_elsewhere, set_aside_shells,
                                publishing_a_placeholder, gateways=gateway_pages,
                                listings=listings, redirects=redirecting, frames=framed)
    if framed:
        result.signal("short_pages_that_are_a_frame", sorted(url for url, _ in framed)[:10])
    if redirecting:
        result.signal("short_pages_that_redirect_by_script",
                      sorted(url for url, _, _ in redirecting)[:10])
    if listings:
        result.signal("listing_pages_filled_in_by_script",
                      sorted(url for url, _ in listings)[:10])
    if publishing_a_placeholder:
        result.signal("short_pages_whose_content_is_a_template_placeholder",
                      sorted(url for url, _ in publishing_a_placeholder)[:10])
    if assembled_elsewhere:
        result.signal("short_pages_whose_text_arrives_after_the_script",
                      sorted(p["url"] for p in assembled_elsewhere)[:10])
    if extraction_failed:
        result.signal("short_pages_whose_text_this_audit_probably_missed",
                      sorted(p["url"] for p in extraction_failed)[:10])
    if could_not_tell:
        result.signal("short_pages_this_audit_could_not_judge",
                      sorted(p["url"] for p in could_not_tell)[:10])

    if not thin:
        # Scoped to the pages this check judged, and it says how many it did
        # not. "Every crawled content page carries at least 300 characters"
        # appeared in a report whose findings section counted seven crawled
        # pages holding 45 to 65 characters: those seven were typed `other`,
        # so they were outside `content_pages` for this sentence while other
        # checks still graded them. One classification for the clean sentence
        # and another for the accusation is how a report contradicts itself.
        #
        # The set-aside pages are named for the same reason. A page this audit
        # could not read is not a page carrying 300 characters, and a sentence
        # that says every page assessed carries 300 characters while three of
        # them were quietly dropped is the same contradiction one step further
        # back.
        assessed = (len(judged) - len(set_aside_shells) - len(assembled_elsewhere)
                    - len(extraction_failed) - len(could_not_tell)
                    - len(publishing_a_placeholder) - len(redirecting)
                    - len(gateway_pages) - len(listings))
        if assessed:
            result.skip("thin-html",
                        "each of the {} pages this check could assess carries at least {} "
                        "characters of body text, which is enough to hold a quotable fact{}{}"
                        .format(assessed,
                                min([quotable_floor(page) for page in judged]
                                    or [MIN_QUOTABLE_TEXT]),
                                set_aside,
                                _unassessed_note(snapshot, judged)))
        else:
            # "each of the 0 pages this check could assess carries at least 300
            # characters" is what the sentence above printed on a one-page
            # crawl whose one page was a shell: a pass, phrased as a
            # measurement, over nothing.
            result.skip("thin-html",
                        "no crawled content page reached this check{}{}".format(
                            set_aside or ". Every one of them was set aside",
                            _unassessed_note(snapshot, judged)))
        return

    rate = len(thin) / float(len(judged))
    result.add(
        id_hint="pages-too-thin-to-quote",
        title="{} too little text to be quoted".format(
            plural(len(thin), "page carries", "pages carry")),
        severity="high" if rate >= THIN_SHARE else "medium",
        confidence="high",
        # Three readings of the same page, and the finding needs all three to
        # agree that the page is short. Counting the main region alone reported
        # a news liveblog as almost empty when the extractor had kept 89 of its
        # 1,660 visible characters; counting characters alone reported a
        # JavaScript shell as a page nobody had written.
        checked=("the text of the page's main content region, after the header, "
                 "footer and navigation are removed",
                 "the rest of the delivered document's visible text, measured "
                 "against the size of this template's own header, menu and footer "
                 "on the pages of the site whose extraction plainly worked - where "
                 "the page holds more than its furniture, the shortfall is this "
                 "audit's extraction and the page is not listed here",
                 "the shell signals that would explain a short page differently: an "
                 "empty framework mount point, a state blob, a <noscript> demanding "
                 "JavaScript, and script bytes dwarfing the visible text of the "
                 "whole delivered document. A page carrying any of them is set "
                 "aside here and handled as a shell instead, because writing more "
                 "copy is the wrong fix for a page whose copy arrives after the "
                 "script runs. Two of those four are limited by a list of names "
                 "rather than by the document - {} mount-point selectors matched "
                 "exactly and {} hydration payload names - so the evidence line "
                 "prints what was counted on these pages rather than asserting "
                 "the site has neither".format(len(SPA_ROOT_SELECTORS),
                                               len(STATE_BLOB_PATTERNS))),
        # Three claims, each of which this check actually made, and no fourth.
        # The sentence used to end "so there is no content this audit failed to
        # find" - a claim about the whole delivered document, from an
        # accounting that only ever compared visible text against visible text.
        # On a homepage of 227,011 bytes, 224,637 of them inline script, it was
        # printed at high severity in the confident tier.
        #
        # The shell half of the sentence used to read "the response carries no
        # framework mount point, no state blob and no bulk of script that could
        # be holding the copy back until it runs". Two of those three clauses
        # were false of the page they described - see `_shell_signals_counted`,
        # which now supplies the numbers instead.
        evidence="{} of {} content pages ({}%) hold under {} characters of body text. On each "
                 "of them the rest of the delivered document's visible text is accounted for "
                 "by the site's own repeated header, menu and footer. The shell signals that "
                 "would explain the missing copy were counted on these pages and none of them "
                 "reached its bar: {}. Examples: {}.{}".format(
                     len(thin), len(judged), pct(len(thin), len(judged)),
                     # The bar that was actually applied, not the constant. On a
                     # Han-script page it is 100, and "under 300 characters" over
                     # a page holding 150 is a sentence the reader checks and
                     # finds wrong.
                     max(quotable_floor(page) for page in thin),
                     _shell_signals_counted(thin),
                     "; ".join("{} ({} chars)".format(p["url"], p.get("body_text_len", 0))
                               for p in sorted(thin, key=lambda x: x["url"])[:3]),
                     set_aside),
        mechanism="C", root_cause="thin-html",
        summary="Give each of these pages at least a paragraph of plain text stating what it "
                "is and one concrete fact.",
        how_to_fix=[
            "For each page listed, write two or three sentences of body copy: what this page "
            "covers, who it is for, and one specific number, name or place.",
            "If the page is a gallery or a listing, add a short introduction above the grid "
            "and a one-line description under each item.",
            "If the page exists only to redirect attention elsewhere, remove it from the "
            "sitemap rather than leaving an empty URL in the index.",
        ],
        effort="medium", owner="content owner",
        rationale="Assistants quote what is easy to lift. A page with nothing to "
                  "lift is crawled, indexed and then never cited, and it dilutes the site's "
                  "average quality in the process.",
        affected_pages=[p["url"] for p in thin],
    )


# What the rendered pass measured, and the static number that means the same
# thing.
#
# `rendered_text_len` is `document.body.innerText` - the whole page, chrome
# included. `body_text_len` is the static page after navigation, header and
# footer have been taken out. Dividing one by the other measures our own
# extractor, not JavaScript, and it can only ever overstate the gap.
#
# On a denim shop whose framework wraps the product grid in a `<nav>`, the
# homepage was reported as "461 chars static -> 2270 rendered", a high-severity
# claim that JavaScript supplies most of the text. The same snapshot recorded
# 2,643 characters of static text on that page: the browser rendered *less*
# than the server sent. `text_len` is the whole static page, which is what the
# browser number is, so that is the one to compare against.
def static_view(page):
    """The page as the server delivered it, before any browser ran.

    The crawl re-reads a JavaScript shell from the rendered DOM so the other
    five skills judge the page a visitor sees. This skill is the one that must
    still see what arrived over the wire, because the whole finding is the
    difference between the two.
    """
    view = page.get("static_view")
    return dict(page, **{k: v for k, v in view.items() if v is not None}) if view else page


def _static_text_len(page):
    """The static length that is the same measurement as `rendered_text_len`.

    `static_text_len` is set by the crawl on a page it re-read from the
    rendered DOM. On those pages `text_len` now describes the rendered
    document, so reading it here would compare the rendered length against
    itself and report a gap of zero on the very pages that have the largest
    one.
    """
    if page.get("static_text_len") is not None:
        return max(page["static_text_len"], 1)
    return max(page.get("text_len") or page.get("body_text_len") or 0, 1)


def _check_render_gap(result, content_pages, render_mode):
    result.check("static-vs-rendered-text-gap")
    if render_mode != "rendered":
        return
    measured = [p for p in content_pages if "rendered_text_len" in p]
    if not measured:
        result.skip("static-vs-rendered-text-gap",
                    "the rendered pass produced no measurements for the sampled pages")
        return

    gaps = []
    for page in measured:
        static_len = _static_text_len(page)
        rendered_len = page["rendered_text_len"]
        gap = (rendered_len - static_len) / float(max(rendered_len, 1))
        if gap >= RENDER_GAP_SIGNIFICANT and rendered_len > MIN_QUOTABLE_TEXT:
            gaps.append((page, gap))

    if not gaps:
        result.skip("static-vs-rendered-text-gap",
                    "on {} rendered page(s) JavaScript added less than {}% of the text, so the "
                    "delivered HTML already carries the content".format(
                        len(measured), int(RENDER_GAP_SIGNIFICANT * 100)))
        return

    result.add(
        id_hint="javascript-supplies-most-page-text",
        title="JavaScript supplies most of the readable text on sampled pages",
        severity="high", confidence="high",
        evidence="On {} of {} rendered pages, JavaScript added {}% or more of the final text. "
                 "Examples: {}.".format(
                     len(gaps), len(measured), int(RENDER_GAP_SIGNIFICANT * 100),
                     "; ".join("{} ({} chars static -> {} rendered)".format(
                         p["url"], _static_text_len(p), p["rendered_text_len"])
                         for p, _ in sorted(gaps, key=lambda x: x[0]["url"])[:3])),
        mechanism="C", root_cause="js-shell",
        summary="Move the primary copy into the server response so it does not depend on the "
                "consumer running JavaScript.",
        how_to_fix=[
            "For each template listed, render the main content on the server.",
            # "Price" named a fact a library, a council and a manual do not
            # have. The instruction is to inline whatever the page exists to
            # state, and naming that in general costs nothing on a shop.
            "Where full SSR is not practical, inline the key facts (the heading, the "
            "description, and whatever the page exists to state) as static HTML and let the "
            "interactive parts hydrate afterwards.",
            "Re-measure by comparing View Source against the rendered page.",
        ],
        effort="high", owner="developer",
        rationale="The gap is the exact amount of content that disappears for any "
                  "consumer that does not execute JavaScript.",
        affected_pages=[p["url"] for p, _ in gaps],
    )


# How many pictures a template puts on every page it builds, so the ones left
# over are the page's own.
#
# A Japanese tea grower's news index was reported as carrying "388 chars of
# text beside 41 image(s), 24 of which carry no alt text describing them". All
# 41 are the site-wide drawer menu's product-category thumbnails, repeated
# identically on every page including the homepage; what the page itself holds
# is post titles and dates, which are text. The sibling check `thin-html`
# already subtracts the template's furniture from the text before it calls a
# page short, and this one subtracted nothing from the picture count before it
# called a page image-heavy. The two now measure the same way, from the same
# nav fingerprint and by the same median.
def _template_image_count(pages):
    """How many images each template carries on a typical page of its own.

    Returns `({chrome key: images}, site-wide median or None)`: the same shape
    `_furniture_size` returns for text, so a reader can put the two side by
    side.

    The median rather than the minimum, again as `_furniture_size` does it. A
    template's drawer menu is on every page of that template, so the median
    page of the template carries the drawer and whatever that page adds; taking
    the median means a page at or below it is one that added nothing.
    """
    by_template, everywhere = {}, []
    for page in pages:
        count = (page.get("images") or {}).get("count")
        if count is None:
            continue
        by_template.setdefault(_chrome_key(page), []).append(count)
        everywhere.append(count)
    sized = {key: _median(counts) for key, counts in by_template.items()
             if len(counts) >= CHROME_SAMPLE_MIN_PAGES}
    # The same floor on the site-wide fallback, and it is load-bearing here in
    # a way it is not for text. `_furniture_size` measures one page's chrome as
    # the difference between two numbers on that page, so it means something on
    # a crawl of one. This measures repetition across pages, and the median of
    # a single page is that page's own picture count - which would say every
    # page on a one-page crawl carries nothing of its own.
    site_wide = _median(everywhere) if len(everywhere) >= CHROME_SAMPLE_MIN_PAGES else None
    return sized, site_wide


def _images_of_its_own(page, by_template, site_wide):
    """Pictures on this page that its template does not put on every page.

    Falls back to the site-wide median where one template has too few pages to
    measure from, and to the raw count where the crawl saw too little to
    measure at all: a three-page crawl has no repetition to read, and guessing
    there would be worse than counting.
    """
    count = (page.get("images") or {}).get("count", 0)
    furniture = by_template.get(_chrome_key(page))
    if furniture is None:
        furniture = site_wide
    if furniture is None:
        return count
    return max(0, int(count - furniture))


def _check_image_locked(result, content_pages, shells):
    result.check("facts-locked-in-images")
    shell_urls = {p["url"] for p in shells}
    by_template, site_wide = _template_image_count(content_pages)
    missed = _text_this_audit_missed(content_pages)
    locked, template_only, unread, covers = [], [], [], []
    for page in content_pages:
        if page["url"] in shell_urls:
            continue  # already reported as a shell; do not double-count
        images = page.get("images") or {}
        text_len = page.get("body_text_len", 0)
        if text_len >= IMAGE_DOMINANT_TEXT:
            continue
        # A cover page's pictures are its way into the site, and the page is
        # reported once as a cover page with that cost named. See
        # `_is_a_cover_page`.
        if _is_a_cover_page(page):
            covers.append(page)
            continue
        # Checked after the text bar, so only a page this check would otherwise
        # have accused is named as set aside.
        if page["url"] in missed:
            unread.append(page)
            continue
        own = _images_of_its_own(page, by_template, site_wide)
        large = images.get("large_image_count", 0)
        # Two branches, and the template subtraction belongs to the second one
        # only.
        #
        # A large picture - 900x700 or bigger, or the page's hero - is the
        # shape this check was written for: a price table or a specification
        # rendered as a PNG. It is judged on its own, unsubtracted, because a
        # site that puts the same price table on every page has put the same
        # locked fact on every page, and subtracting repetition there would
        # delete the finding rather than sharpen it.
        #
        # "Five or more pictures" is the weaker branch and it is the one that
        # misfired: the tea grower's 41 drawer thumbnails are small, so they
        # tripped only this branch, and the page's own pictures were none.
        if large >= 1 or own >= 5:
            # A described image is not a locked fact. `alt` text is the
            # machine-readable statement of what a picture shows, so a gallery
            # or a portfolio that captions every image has already done what
            # this finding asks for - and was reported anyway, because the
            # check counted pictures and never looked at whether they carried
            # text.
            #
            # `described_count`, not the absence of `missing_alt_count`.
            # `alt=""` declares an image decorative, which is correct for a
            # divider and a false statement on a page whose only content is a
            # price table rendered as a PNG. That page is exactly the defect.
            if images.get("count") and images["described_count"] >= images["count"]:
                continue
            # The pictures the sentence is about, named by whichever branch
            # fired. A number counted over one set printed beside a noun that
            # names another is how a finding comes to disagree with itself.
            counted = ("{} big enough to be carrying the page's message".format(
                plural(large, "image", "images")) if large >= 1
                else "{} of its own".format(plural(own, "image", "images")))
            # `_undescribed`, not `count - described_count`. The second counts
            # an `alt=""` image as one nobody described, and the tea grower's
            # report said 24 of its drawer thumbnails "carry no alt text
            # describing them" about 24 elements marked `alt=""` - the correct
            # marking for a decorative picture, and a decision its author made
            # rather than an omission. Capped at the pictures the sentence just
            # counted, because the crawl records undescribed images per page
            # and cannot say which of them the template supplied.
            undescribed = min(_undescribed(page), large if large >= 1 else own)
            locked.append((page, "{} chars of text beside {}, {}".format(
                text_len, counted,
                "{} of which carry no text alternative at all".format(undescribed)
                if undescribed else
                "every one of them marked alt=\"\", which declares a picture decorative "
                "rather than stating what it shows")))
        elif images.get("svg_text_nodes", 0) >= 15 or images.get("canvas_count", 0) >= 1:
            locked.append((page, "{} chars of text; text rendered in SVG or canvas".format(text_len)))
        elif images.get("count", 0) and not own:
            template_only.append(page)

    set_aside = ""
    if template_only:
        set_aside = (". Set aside: on {} every picture is one this site's template puts on "
                     "every page it builds - a drawer menu's category thumbnails, a header "
                     "logo, a footer badge - so there is no picture here that could be "
                     "holding this page's own facts ({})".format(
                         plural(len(template_only), "page", "pages"),
                         ", ".join(example_urls([p["url"] for p in template_only], 3))))
        result.signal("pages_whose_only_pictures_are_the_site_template",
                      sorted(p["url"] for p in template_only)[:10])
    if unread:
        set_aside += _missed_text_note(unread, "what its pictures carry")
        result.signal("pages_whose_text_this_audit_could_not_read_for_images",
                      sorted(p["url"] for p in unread)[:10])
    if covers:
        set_aside += (". Set aside: the homepage at {} is a cover page - at most {} "
                      "characters of text and a few links into the site - so its pictures "
                      "are its way in rather than facts locked in pixels, and the cover page "
                      "is reported once, as a cover page, by the check of what the homepage "
                      "tells a visitor.".format(covers[0]["url"], COVER_PAGE_TEXT))

    if not locked:
        result.skip("facts-locked-in-images",
                    "no content page relies on imagery of its own to carry its main "
                    "message" + set_aside)
        return

    result.add(
        id_hint="facts-locked-in-images",
        title="{} in images rather than text".format(
            plural(len(locked), "page carries its main content",
                   "pages carry their main content")),
        severity="medium", confidence="medium",
        # Two readings, and the finding needs both: how much readable text the
        # page carries, and how many pictures it carries that its own template
        # does not put on every page it builds. Counting pictures alone
        # reported a news index carrying 41 drawer-menu thumbnails and no
        # picture of its own. Said in the evidence rather than in `checked`,
        # which this finding may not carry: it reports something observed.
        evidence="Pages with under {} chars of readable text but substantial imagery of "
                 "their own: {}.{}".format(
            IMAGE_DOMINANT_TEXT,
            "; ".join("{} ({})".format(p["url"], why) for p, why in sorted(locked, key=lambda x: x[0]["url"])[:5]),
            set_aside),
        mechanism="C", root_cause="image-locked-facts",
        summary="Restate the facts shown in the images as HTML text on the same page.",
        how_to_fix=[
            # The list was "the price, the specification, the opening hours,
            # the offer" - three of the four naming something only a seller
            # has. What is being asked for is whatever the picture is carrying,
            # and the examples now span the sites this marketplace audits
            # rather than the shops it started on.
            "For each page, write out in HTML what the image says: the figures, the "
            "specification, the dates, the opening hours, the terms.",
            "Keep the image; it is the text beside it that is missing, not the design.",
            "Give every content image a descriptive alt attribute that states its content, "
            "not its filename.",
            "A short paragraph above or below the image is enough; it does not need to be visible "
            "at large size to be quotable.",
        ],
        effort="medium", owner="content owner",
        rationale="Text baked into an image is not text. A machine that fetches the "
                  "page sees markup around a picture and finds no fact it can lift.",
        affected_pages=[p["url"] for p, _ in locked],
    )


def _reads_as_a_citation(page, pdf):
    """Is this PDF somebody else's document, cited rather than published here?

    A statistics charity's energy page links one PDF: a climate-science report
    chapter whose title contains the word "cost", listed among its sources. The
    finding told the charity to "publish an HTML version of the numbers that
    currently live only in the PDF" - numbers in a document another
    organisation wrote, which this one has no right to republish and no reason
    to. Nothing was for sale on that site either.

    Two places carry the markers, and either one settles it: the link text
    itself, and the sentence around it in the page's own words.
    """
    text = " ".join((pdf.get("text") or "").split())
    if CITATION_RE.search(text):
        return True
    body = " ".join((page.get("body_text") or "").split())
    if not text:
        return False
    # The anchor text as it reads in the running text. Matched on a prefix
    # because `pdf_links` truncates the anchor at 140 characters and a long
    # citation is longer than that.
    needle = text[:60]
    at = body.find(needle)
    if at < 0:
        return False
    window = body[max(0, at - CITATION_CONTEXT_CHARS):
                  at + len(needle) + CITATION_CONTEXT_CHARS]
    if CITATION_RE.search(window):
        return True
    # The heading the link sits under. A reference list is usually the last
    # section of a page and says so in its own heading.
    headings = [h for level in ("h2", "h3") for h in (page.get("headings") or {}).get(level, [])]
    return any(CITATION_HEADING_RE.search(h or "") for h in headings[-3:])


def _prose_beside_the_links(page):
    """Characters the page says in its own words, with the link labels removed.

    A document index is a page whose text *is* its list of links: forty lines
    reading "Minutes, 14 March 2026 (PDF)". `body_text_len` counts every one of
    those labels, so a register that says nothing at all about what it holds
    measures as a page with plenty of text on it. Subtracting the label
    characters leaves what the page says *about* the documents, which is the
    only thing a reader who cannot open them has to go on.

    `pdf_links` is capped at 20 entries by the extractor, so on a longer
    register the subtraction is short and this number comes out too high. That
    direction is the safe one: it makes this check quieter, never louder.
    """
    labels = sum(len(" ".join((pdf.get("text") or "").split()))
                 for pdf in (page.get("pdf_links") or []))
    return max(0, (page.get("body_text_len") or 0) - labels)


def _price_in_the_html(page):
    """True when the delivered page states a price a reader can see.

    Read from the page's own copy first and from the whole visible document
    when that copy states none. Both, rather than the copy alone, because the
    claim this gates is that the figure exists nowhere in the HTML: a shop
    whose grid of names and prices the extractor drops records 171 characters
    of prose beside 3,517 of visible text, and every price it publishes is in
    the 3,346 that were not kept.

    The whole document is only ever consulted to *withdraw* the accusation, so
    the widest reading cannot add a finding. A footer reading "from $5" silences
    this check on that page, and silence is the right direction: the finding
    tells an owner their prices are unreadable, and this one would be wrong.
    """
    return (has_price(page.get("body_text") or "")
            or has_price(page.get("text") or ""))


def _check_pdf_locked(result, snapshot, content_pages):
    result.check("facts-locked-in-pdfs")
    kind = site_kind(snapshot)
    # Two claims, and only the first one is about money.
    #
    # `sells_something` still gates the price claim and has to. The only way
    # out of that claim is "the page states a price", and a charity, a museum,
    # a government department and a documentation site have no price anywhere,
    # so telling any of them that its pricing facts are locked inside a
    # brochure is a sentence with no way to be right. A charity whose homepage
    # asks for a $500 donation was once read as a shop, that gate opened, and
    # its reference list was reported as a hidden price list.
    #
    # What was wrong is that this was the *only* branch, so the whole check
    # switched off on every site that sells nothing - including a town whose
    # agendas, minutes, ordinances and ballot are PDF-only, which is the
    # strongest case of a fact an assistant cannot read this marketplace has
    # met. The second branch asks the question the check is actually for, and
    # asks it of every site.
    sells = sells_something(snapshot)
    missed = _text_this_audit_missed(content_pages)
    priced, registers, cited, unread = [], [], [], []
    for page in content_pages:
        links = page.get("pdf_links") or []
        if not links:
            continue
        # The link text, not the URL. A URL is a filename somebody chose, and
        # matching `spec` or `menu` inside one turned `/menu-2026.pdf` on a
        # navigation page into a pricing document.
        if sells:
            pdfs = [pdf for pdf in links
                    if FACT_BEARING_PDF_RE.search(pdf.get("text") or "")]
            # Only a problem when the same facts are absent from the HTML.
            #
            # `_price_in_the_html` and not `has_price(body_text)`: the claim is
            # that the number is nowhere on the page, and a price sitting in a
            # product grid the extractor dropped is still a price the reader
            # sees. See `_text_this_audit_missed`.
            if pdfs and not _price_in_the_html(page):
                # A document the site did not write is not a document it can
                # move into HTML, whatever its title says.
                own = [pdf for pdf in pdfs if not _reads_as_a_citation(page, pdf)]
                if own:
                    priced.append((page, own[0]))
                else:
                    cited.append(page)
                continue

        records = [pdf for pdf in links
                   if PUBLIC_RECORD_PDF_RE.search(pdf.get("text") or "")]
        if not records:
            continue
        # The page restates it. A minutes page carrying three paragraphs of
        # what the meeting decided is a page an assistant can read and quote,
        # whatever the signed PDF underneath it holds - so the document being a
        # PDF costs nothing there and nothing is reported.
        if _prose_beside_the_links(page) >= PDF_INDEX_PROSE_FLOOR:
            continue
        # This branch counts the page's own words and has no wider reading to
        # fall back on - the whole document's visible text is the menu and the
        # footer as well, which say nothing about the minutes. So a page whose
        # words this audit did not keep is declined rather than accused. See
        # `_text_this_audit_missed`.
        if page["url"] in missed:
            unread.append(page)
            continue
        own = [pdf for pdf in records if not _reads_as_a_citation(page, pdf)]
        if not own:
            cited.append(page)
            continue
        registers.append((page, own[0]))

    unread_note = _missed_text_note(unread, "the documents it links to")
    if unread:
        result.signal("pages_whose_text_this_audit_could_not_read_for_pdfs",
                      sorted(p["url"] for p in unread)[:10])

    if not priced and not registers:
        result.skip("facts-locked-in-pdfs", ". ".join(part for part in (
            "no page links to a pricing or specification PDF whose numbers are missing from "
            "the page text" if sells else
            "nothing on this site is for sale, so no linked PDF was tested for a price or a "
            "specification a buyer needs",
            "no page links to a document of record - an agenda, minutes, an ordinance, a "
            "budget, a plan, a ballot - while saying under {} characters about it in its own "
            "words beside the link labels".format(PDF_INDEX_PROSE_FLOOR),
            "{} to a PDF whose title matched, in a reference list or with a citation around "
            "it: that is somebody else's document, and this site cannot republish it".format(
                plural(len(cited), "page links", "pages link")) if cited else "",
        ) if part) + unread_note)
        return

    if priced:
        result.add(
            id_hint="facts-locked-in-pdfs",
            title="Pricing or specification facts are available only inside PDFs",
            severity="medium", confidence="medium",
            evidence="{} to a fact-bearing PDF while stating no equivalent figure "
                     "anywhere in the delivered HTML, its own copy and its chrome both "
                     "searched. Examples: {}.{}".format(
                         plural(len(priced), "page links", "pages link"),
                         "; ".join('{} -> "{}"'.format(p["url"], pdf["text"] or pdf["url"])
                                   for p, pdf in sorted(priced, key=lambda x: x[0]["url"])[:5]),
                         unread_note),
            mechanism="C", root_cause="pdf-locked-facts",
            summary="Publish an HTML version of the numbers that currently live only in the PDF.",
            how_to_fix=[
                "Create an HTML page holding the same table or figures as the PDF.",
                "Link the PDF from that page as a download rather than as the only source.",
                "Keep both in sync by generating the PDF from the HTML, not the other way round.",
            ],
            effort="medium", owner="content owner",
            rationale="PDFs are fetched inconsistently, parsed unevenly, and rarely "
                      "quoted with confidence. A number that exists only in a PDF is a number "
                      "the assistant will not state.",
            affected_pages=[p["url"] for p, _ in priced],
        )

    if not registers:
        return

    # What this audit read, said in the finding rather than implied by it. The
    # crawl fetches a PDF's headers when it checks the link; it does not parse
    # the file. So the supportable claim is about the HTML - this page does not
    # state what the document states - and never about the document's contents,
    # which nothing here has seen.
    rate = len(registers) / float(len(content_pages))
    public = kind.is_certainly(PUBLIC_BODY)
    if public:
        summary = ("Publish each decision as HTML on the page that links its PDF, and keep the "
                   "PDF beneath it as the signed copy of record.")
        steps = [
            "On each index page, give every entry a line of HTML: the date, the body that met "
            "or issued the document, and what it decided or requires.",
            "For an ordinance, a plan or a notice, publish the operative text as an HTML page "
            "and link the PDF from it as the signed original.",
            "Keep the PDF. It is the authoritative document; the HTML beside it is the copy a "
            "resident, a search engine and an assistant can read and quote.",
            "Where the document comes out of a word processor, export HTML at the same time so "
            "the two versions cannot drift apart.",
        ]
        rationale = ("A resident asking an assistant when the next hearing is, what an "
                     "ordinance requires, or what was on the ballot is asking about words that "
                     "exist only inside a file the assistant is unlikely to open. The page "
                     "that links the file says nothing an answer could be built from.")
    else:
        summary = ("Say on the page what each linked document says, and keep the PDF as the "
                   "download rather than as the only copy.")
        steps = [
            "On each page listed, write a line of HTML per document: its date, what it covers, "
            "and the one thing a reader came for.",
            "Where a document carries text people quote - a policy, a standard, a set of "
            "conditions - publish that text as an HTML page and link the PDF from it.",
            "Keep the PDF as the download; the HTML beside it is what a reader and an "
            "assistant can search, link to and quote.",
            "Generate the PDF from the HTML where you can, so the two cannot diverge.",
        ]
        rationale = ("A document nobody restates in HTML is a document only a reader who "
                     "downloads and opens it can use. PDFs are fetched inconsistently and "
                     "quoted reluctantly, so the page that links one is, to an assistant, a "
                     "page with a filename on it.")
    result.add(
        id_hint="documents-of-record-only-as-pdfs",
        # One set behind the title, the evidence and the affected list, and it
        # is `registers`.
        title="{}".format(
            plural(len(registers),
                   "page links to documents of record and carries almost no text of its own",
                   "pages link to documents of record and carry almost no text of their own")),
        severity="high" if rate >= PDF_INDEX_SHARE_HIGH else "medium",
        # Medium, and it stays medium however many pages match: this audit read
        # the anchor and the page, never the file.
        confidence="medium",
        evidence="{} of {} content pages link a PDF whose title names a document of record - "
                 "an agenda, minutes, an ordinance, a budget, a plan, a ballot - and hold under "
                 "{} characters of their own words once the link labels are subtracted, so "
                 "nothing on the page states what the document states. This audit read the "
                 "anchor text and the page's HTML; it did not open the file, so this is a "
                 "claim about the page and not about the document's contents. Examples: "
                 "{}.{}".format(
                     len(registers), len(content_pages), PDF_INDEX_PROSE_FLOOR,
                     "; ".join('{} -> "{}" ({} chars of the page\'s own words)'.format(
                         p["url"], pdf["text"] or pdf["url"], _prose_beside_the_links(p))
                         for p, pdf in sorted(registers, key=lambda x: x[0]["url"])[:5]),
                     unread_note),
        mechanism="C", root_cause="pdf-locked-facts",
        summary=summary,
        how_to_fix=steps,
        effort="medium", owner="content owner",
        rationale=rationale,
        affected_pages=[p["url"] for p, _ in registers],
    )


def _background_loop_count(video):
    """How many `<video>` elements on this page are silent background loops.

    `autoplay`, `muted` and `loop` on one element is a video that starts by
    itself, says nothing, and never ends: wallpaper, the modern replacement for
    an animated GIF sitting behind a hero heading. A dental clinic was told
    that 12 of its 21 pages "lead with audio or video and carry little readable
    text" and advised to "generate a transcript and paste it into the page",
    about one silent looping clip that sits in its site-wide template. There is
    nothing in it to transcribe and no summary of it to write.

    All three attributes, never one or two of them. `muted` alone is ordinary
    on real content video that starts silent and unmutes on a click, and
    `autoplay` alone says only that the site is impolite. What no explainer,
    interview or product demo ever carries is `loop`: a viewer who reaches the
    end of something with a running time is not returned to its start.
    Requiring the conjunction is what keeps this from deleting the finding on
    the pages it was written for.

    Derived from the two counts the crawl already records: `autoplay_count` is
    every `<video autoplay>`, and `intrusive_autoplay_count` is those that are
    not also `muted` and `loop`. The difference is exactly the elements
    carrying all three. A snapshot taken before either count existed answers 0,
    which leaves this check behaving as it did rather than guessing.

    And a player the visitor can operate is content whatever its other
    attributes say. A `<video autoplay muted loop controls>` offers a play
    button, a pause button and a volume control, so somebody can sit through
    what is in it and there may well be something in it to write down;
    wallpaper is the clip with no controls at all. `controls_count` is the
    number of `<video>` elements on the page carrying a `controls` attribute,
    and background loops can only be among the elements that carry none - so
    it caps this count rather than replacing it. A snapshot with no
    `controls_count` leaves the cap off, and this returns what the three
    attributes alone say.
    """
    if "autoplay_count" not in video or "intrusive_autoplay_count" not in video:
        return 0
    loops = (video.get("autoplay_count") or 0) - (video.get("intrusive_autoplay_count") or 0)
    if "controls_count" in video:
        loops = min(loops, (video.get("native_count") or 0)
                    - (video.get("controls_count") or 0))
    return max(0, loops)


def _players_that_could_carry_content(video):
    """Players on this page that could be holding the page's substance.

    Native `<video>` elements that are background loops are subtracted: a
    silent clip restarting forever holds no statement, so a page whose only
    player is one has no spoken content missing from its text.

    Embeds and `<audio>` are counted in full. The crawl records no attributes
    for a third-party player, so this cannot tell a background clip served from
    a video platform apart from an interview, and it does not guess.
    """
    native = max(0, (video.get("native_count") or 0) - _background_loop_count(video))
    return native + (video.get("embed_count") or 0) + (video.get("audio_count") or 0)


def _check_video_transcripts(result, content_pages):
    result.check("video-transcripts")
    missed = _text_this_audit_missed(content_pages)
    thin_video, wallpaper_only, unread = [], [], []
    for page in content_pages:
        video = page.get("video") or {}
        if not (video.get("native_count") or video.get("embed_count")
                or video.get("audio_count")):
            continue
        if _players_that_could_carry_content(video) == 0:
            # Every player here is a silent looping background clip. Whether
            # the page is also short is a real question, and `thin-html`
            # answers it on its own 300-character bar; it is not this check's
            # to answer, because the fix offered here is a transcript and there
            # is no speech. Two claims used to be welded into one finding -
            # "leads with video" and "carries little readable text" - and only
            # the second survives, delivered by the check that owns it.
            wallpaper_only.append(page)
            continue
        if video.get("transcript_nearby") or video.get("track_count"):
            continue
        if page.get("body_text_len", 0) >= VIDEO_EXPLAINS_ITSELF_TEXT:
            continue  # the page explains itself in text regardless of the video
        # "Carries little readable text" is the half of this finding that
        # rests on a measurement, and on a page whose text this audit did not
        # keep there is no measurement. See `_text_this_audit_missed`.
        if page["url"] in missed:
            unread.append(page)
            continue
        thin_video.append(page)

    set_aside = ""
    if wallpaper_only:
        short = [p for p in wallpaper_only
                 if p.get("body_text_len", 0) < MIN_QUOTABLE_TEXT]
        set_aside = (
            ". Set aside: on {} the only player is a <video> carrying autoplay, muted and "
            "loop together - a silent clip with no beginning and no end, so there is nothing "
            "in it to transcribe ({}){}".format(
                plural(len(wallpaper_only), "page", "pages"),
                ", ".join(example_urls([p["url"] for p in wallpaper_only], 3)),
                "" if not short else
                ". {} under {} characters of body text, which is a real defect: "
                "it is judged by the thin-text check above on its own bar and is not "
                "attributed to the video here".format(
                    plural(len(short), "of them carries", "of them carry"),
                    MIN_QUOTABLE_TEXT)))
        result.signal("pages_whose_only_player_is_a_background_loop",
                      sorted(p["url"] for p in wallpaper_only)[:10])
    if unread:
        set_aside += _missed_text_note(unread, "what its player says")
        result.signal("pages_whose_text_this_audit_could_not_read_for_video",
                      sorted(p["url"] for p in unread)[:10])

    if not thin_video:
        result.skip("video-transcripts",
                    "no page relies on audio or video to carry content that is missing from "
                    "its text" + set_aside)
        return

    result.add(
        id_hint="video-without-transcript",
        # One phrase, counted by `plural`, rather than a count spliced into a
        # clause written for several pages. This title once printed as "1 page
        # leads with audio or video and carry little readable text": the verb
        # after the splice was still the plural one, and a
        # published report that cannot conjugate its own sentence is read as a
        # report that did not check its own numbers either.
        title="{}".format(
            plural(len(thin_video),
                   "page leads with audio or video and carries little readable text",
                   "pages lead with audio or video and carry little readable text")),
        severity="medium", confidence="medium",
        # Four separate reads. A transcript can be published in any of three
        # shapes and a detector that knows one of them reports the other two as
        # absent; the fourth asks whether there is anything to transcribe at
        # all. The loop above asks all four: `track_count` for caption files
        # attached to the player, `transcript_nearby` for the page's own words,
        # `body_text_len` for whether the page explains itself in writing
        # regardless of the video, and the autoplay attributes for whether the
        # player is a silent background loop.
        checked=("caption or subtitle files attached to the player, meaning a "
                 "<track> element or a caption file the markup names",
                 "the page's own words, searched for the phrases this audit "
                 "recognises as announcing a transcript - a transcript published "
                 "under a wording the list does not hold reads to it as none",
                 "how much readable text the page carries at all",
                 "whether each <video> is a silent background loop, which is "
                 "autoplay, muted and loop on the same element. A page whose only "
                 "player is one is set aside, because a clip that restarts forever "
                 "holds no statement to write down. A third-party embed carries no "
                 "attributes this audit can read, so a background clip served from "
                 "a video platform still counts as a player here"),
        evidence="Pages with an embedded or native video that is not a silent background "
                 "loop, no caption track, no nearby transcript, and under {} chars of body "
                 "text: {}.{}".format(
                     VIDEO_EXPLAINS_ITSELF_TEXT,
                     ", ".join(example_urls([p["url"] for p in thin_video])),
                     set_aside),
        mechanism="C", root_cause="no-transcript",
        summary="Publish a transcript or a written summary alongside each video.",
        how_to_fix=[
            "Generate a transcript (most video platforms produce one automatically) and paste "
            "it into the page inside a collapsible section.",
            "Add a 3 to 5 sentence written summary above the player stating what the video says.",
            "Attach a <track kind=\"captions\"> file to native <video> elements.",
        ],
        effort="low", owner="content owner",
        rationale="Nothing inside a video file is readable text. A page whose "
                  "substance is spoken aloud is, to a machine, a page with almost nothing on it.",
        affected_pages=[p["url"] for p in thin_video],
    )


def _check_iframed_content(result, content_pages, shells):
    result.check("iframed-main-content")
    shell_urls = {p["url"] for p in shells}
    missed = _text_this_audit_missed(content_pages)
    iframed, unread = [], []
    for page in content_pages:
        if page["url"] in shell_urls:
            continue
        frames = [f for f in (page.get("iframes") or [])
                  if not f["is_video"] and not f["is_map"] and not f.get("is_invisible")]
        # A frame marked invisible is still the content where it is the only
        # thing on the page: sized by a script, it reads as hidden in the
        # delivered HTML. See `_frame_is_the_page`.
        if not frames:
            whole = _frame_is_the_page(page)
            frames = [whole] if whole is not None else []
        if not (frames and page.get("body_text_len", 0) < MIN_QUOTABLE_TEXT):
            continue
        # "The page's own text is too short, so the frame must hold the
        # content" is an absence claim, and a page whose text this audit lost
        # cannot support one. See `_text_this_audit_missed`.
        if page["url"] in missed:
            unread.append(page)
            continue
        iframed.append((page, frames[0]))

    set_aside = _missed_text_note(unread, "its main content")
    if unread:
        result.signal("pages_whose_text_this_audit_could_not_read_for_iframes",
                      sorted(p["url"] for p in unread)[:10])

    if not iframed:
        result.skip("iframed-main-content",
                    "no page delegates its main content to an iframe" + set_aside)
        return

    result.add(
        id_hint="main-content-in-iframe",
        title="{}".format(
            plural(len(iframed),
                   "page holds its main content inside an iframe",
                   "pages hold their main content inside an iframe")),
        severity="medium", confidence="medium",
        evidence="Pages with under {} chars of own text plus a non-video, non-map iframe: {}.{}".format(
            MIN_QUOTABLE_TEXT,
            "; ".join("{} (iframe: {})".format(p["url"], f["src"][:80])
                      for p, f in sorted(iframed, key=lambda x: x[0]["url"])[:5]),
            set_aside),
        mechanism="C", root_cause="iframe-content",
        summary="Move the iframed content into the page itself, or duplicate its key facts in HTML.",
        how_to_fix=[
            "Where the iframe hosts your own content, render it directly in the page template.",
            "Where it is a third-party embed (a booking widget, a menu tool), write the same "
            "core facts as HTML text above or below it.",
            "Where the frame is an interactive viewer - a virtual tour, a 3D model, a "
            "floor plan - keep it, and write beside it on this page what it shows, with "
            "links to the pages about what is in it and to the section it belongs to.",
        ],
        effort="medium", owner="developer",
        rationale="An iframe is a separate document. Consumers that read the parent "
                  "page see the frame element, not the content inside it, so the page reads as empty.",
        affected_pages=[p["url"] for p, _ in iframed],
    )


def _undescribed(page):
    """Images on this page with no alt attribute and no other description either.

    `undescribed_count` is `missing_alt_count` minus the images an
    `aria-label`, an `aria-labelledby`, a `title`, `role="presentation"` or a
    `<figcaption>` in a parent `<figure>` already names. The extractor has
    written it since it learned those five shapes, and nothing read it: this
    check went on counting missing attributes, so a gallery that captions every
    picture was told its images carry no description, and the finding explained
    its own confidence cap with a limit that had already been lifted.

    A snapshot taken before the key existed has only the attribute count, which
    is then the honest answer to ask it for.
    """
    images = page.get("images") or {}
    if "undescribed_count" in images:
        return max(0, images["undescribed_count"])
    return images.get("missing_alt_count", 0)


def _check_alt_coverage(result, content_pages):
    result.check("image-alt-coverage")
    heavy = [p for p in content_pages
             if (p.get("images") or {}).get("count", 0) >= 5
             and _undescribed(p) > 0]
    if not heavy:
        result.skip("image-alt-coverage",
                    "no image-heavy content page has images that carry neither an alt "
                    "attribute nor any other description (an explicit alt=\"\" on a decorative "
                    "image is correct and was not counted, and neither was an image named by "
                    "an aria-label, a title or a caption)")
        return

    total_undescribed = sum(_undescribed(p) for p in heavy)
    # How many images, rather than how many times an image appears. A site's
    # two decorative header textures, present on all sixty crawled pages, were
    # reported as "189 images with no alt attribute" - a number that reads as a
    # content-photo problem across the library and is in fact two lines in one
    # shared template. Distinct source URLs is the count that matches the
    # number of edits the fix takes.
    #
    # Read from `undescribed_sample`, which is the URLs behind
    # `undescribed_count`. It used to read `missing_alt_sample`, which lists
    # every image with no alt attribute including the ones a caption or an
    # aria-label names - so this check could only list URLs for pages where
    # the two counts happened to agree, and carried a page-splitting apparatus
    # and two extra sentences of evidence to say why the rest went unlisted.
    distinct = sorted({url for page in heavy
                       for url in (page.get("images") or {}).get("undescribed_sample") or []})
    # The sample is capped, so its length is a floor and not a count. One
    # product page carried 232 undescribed images and a homepage 25, and the
    # report read "4 distinct image URL(s) with no alt attribute" - the sample had
    # ten entries and the crawl now records how many different pictures those
    # ten stand for. Where the record carries that number, it decides whether
    # the sample is the whole set; the URLs are still what gets listed, because
    # a count is not a list.
    distinct_total = sum((p.get("images") or {}).get("undescribed_distinct_count") or 0
                         for p in heavy)
    # One population behind every number this finding prints, and it is `heavy`
    # - the pages listed under `affected_pages`. The page count used to come
    # from the subset of `heavy` whose records carry an image sample, while the
    # occurrence count was summed over all of `heavy` and the affected list
    # named all of `heavy` too, so a finding could read "appearing 41 times
    # across 2 image-heavy pages" above a list of seven. A reader who adds them
    # up finds two of the three numbers describing a set the third does not.
    #
    # The distinct-URL wording is used only where every listed page carries a
    # sample. Where one does not, the distinct count is a floor rather than a
    # count, and a floor printed as a count is the same defect one step back.
    #
    # A page whose record says it holds more different undescribed pictures
    # than the sample lists has not been sampled everywhere, whatever the
    # sample's own length says.
    sampled_everywhere = all(
        (p.get("images") or {}).get("undescribed_sample")
        and (((p.get("images") or {}).get("undescribed_distinct_count") or 0)
             <= len((p.get("images") or {}).get("undescribed_sample") or []))
        for p in heavy)
    repeated = len(distinct) and total_undescribed >= len(distinct) * 2
    if distinct and sampled_everywhere:
        headline = len(distinct)
        evidence = ("{} distinct image URL(s) with no alt attribute and nothing else naming "
                    "them either, appearing {} times across {} image-heavy page(s).".format(
                        len(distinct), total_undescribed, len(heavy)))
        if repeated:
            evidence += (" Most are repeated on every page, so they live in a shared template "
                         "and are one edit each rather than one per page.")
    elif distinct_total:
        # More pictures than the sample can name. The count is the crawl's own
        # whole-page figure and the listed URLs are the sample, so the sentence
        # says which is which rather than letting a reader take the list for
        # the set.
        headline = distinct_total
        evidence = ("{} distinct image URL(s) with no alt attribute and nothing else naming "
                    "them either, appearing {} times across {} image-heavy page(s). More than "
                    "this audit lists below: the crawl counts every picture on a page and "
                    "keeps the addresses of the first few.".format(
                        distinct_total, total_undescribed, len(heavy)))
    else:
        headline = total_undescribed
        evidence = ("{} image(s) with no alt attribute and nothing else naming them either, "
                    "across {} image-heavy page(s).".format(total_undescribed, len(heavy)))
    evidence += " Examples: {}.".format(
        ", ".join(example_urls([p["url"] for p in heavy])))
    result.add(
        id_hint="images-missing-alt-text",
        # `headline` is whichever number the evidence sentence just printed
        # first. The title used to prefer the distinct count whenever there was
        # one, so on a snapshot where the two branches disagree it announced a
        # number the sentence under it never mentions.
        title="{} no description at all".format(
            plural(headline, "image has", "images have")),
        severity="low", confidence="high",
        # Two sources, both recorded by the crawl and both read here. The
        # second one was built, documented and never connected, and this
        # tuple used to explain the confidence cap by saying the crawl
        # records no other description. It does.
        checked=("the alt attribute on every image element on these pages",
                 "the five other ways HTML names a picture, recorded per image by "
                 "the crawl: aria-label, aria-labelledby, title, "
                 "role=\"presentation\", and a <figcaption> inside a parent "
                 "<figure>. An image named by any of them is not counted here. A "
                 "description written as ordinary prose beside the picture is not "
                 "one of these six and would still read as undescribed"),
        evidence=evidence,
        mechanism="C", root_cause="alt-missing",
        summary="Add descriptive alt text to content images and alt=\"\" to purely decorative ones.",
        how_to_fix=[
            "Write alt text that states what the image shows, not what the file is called.",
            "Use alt=\"\" for decorative images so assistive technology and crawlers skip them.",
            # "Product photos" was delivered to a documentation tree. What the
            # sentence is ranking is pictures that carry information over
            # pictures that are decoration, which is the same instruction on a
            # shop, a manual and a library - only the example was a claim about
            # what the site sells.
            "Prioritise the pictures that carry information: photographs of what the page is "
            "about, diagrams, screenshots, and any image containing words.",
        ],
        effort="low", owner="content owner",
        rationale="An image nothing describes is a blank to every reader that "
                  "does not see pixels. These carry no alt text and no caption, label or "
                  "title either, so there is no sentence anywhere saying what they show. "
                  "It matters for accessibility too, so this fix pays twice.",
        affected_pages=[p["url"] for p in heavy],
    )


def _check_pagination(result, snapshot, content_pages):
    result.check("crawlable-pagination")
    listings = [p for p in content_pages if p.get("page_type") == "category"]
    if not listings:
        result.skip("crawlable-pagination",
                    "no category or listing pages were crawled."
                    + unclassified_note(content_pages))
        return

    offenders = []
    for page in listings:
        text = page.get("body_text") or ""
        if not LOAD_MORE_RE.search(text):
            continue
        links = page.get("links") or {} or {}
        internal_links = links.get("internal") or []
        internal = [l["url"] for l in internal_links]
        # A "View more" that is an `<a href>` is not a button. A restaurant
        # group's journal index was reported as hiding its catalogue behind a
        # load-more control, with the rationale "a crawler does not press
        # buttons" - and the control is a link to `/journal/all/`, which
        # returns 200 and which a crawler follows like any other. The phrase in
        # the page text says nothing about what carries it.
        if any(LOAD_MORE_RE.search(l.get("text") or "") for l in internal_links):
            continue
        # `links.internal` is truncated at 300 entries, and numbered pagination
        # sits at the bottom of a listing page - so on a category page with
        # more than 300 links the pagination is exactly what gets cut, and the
        # page was reported as hiding its catalogue while paginating correctly.
        # The pre-truncation total is in the snapshot and was not consulted.
        if links.get("internal_count", len(internal)) > len(internal):
            continue
        if any(PAGINATION_RE.search(url) for url in internal):
            continue
        # A `<link rel="next">` in the head is crawlable pagination whatever
        # the body offers: a shop's bag category was told to "add <link
        # rel=\"next\">" while carrying `<link rel="next" href="?page=2">`.
        # Read from the extractor's record of the head's pagination links where
        # the snapshot has one.
        if page.get("rel_next") or (page.get("head_links") or {}).get("next"):
            continue
        offenders.append(page)

    if not offenders:
        result.skip("crawlable-pagination",
                    "listing pages either paginate with real links or do not use a load-more control")
        return

    result.add(
        id_hint="load-more-without-crawlable-pagination",
        title="{}".format(
            plural(len(offenders),
                   "listing page hides its catalogue behind a load-more button",
                   "listing pages hide their catalogue behind a load-more button")),
        severity="medium", confidence="medium",
        evidence="Listing pages containing a load-more control with no numbered pagination "
                 "links in the HTML: {}.".format(
                     ", ".join(example_urls([p["url"] for p in offenders]))),
        mechanism="A", root_cause="uncrawlable-pagination",
        summary="Add real paginated links alongside the load-more button.",
        how_to_fix=[
            "Render numbered page links (/category?page=2 and so on) in the HTML, even if the "
            "button is what most visitors use.",
            "Add <link rel=\"next\"> and <link rel=\"prev\"> to the page head.",
            "Make sure every item is reachable through those links without JavaScript.",
        ],
        effort="medium", owner="developer",
        rationale="A crawler does not press buttons. Items past the first batch have "
                  "no URL a crawler can follow, so most of the catalogue is never fetched.",
        affected_pages=[p["url"] for p in offenders],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-network", action="store_true", help="accepted for interface parity")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot))
    result.write(args.out)
    print("{}: {} finding(s)".format(SKILL, len(result.findings)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
