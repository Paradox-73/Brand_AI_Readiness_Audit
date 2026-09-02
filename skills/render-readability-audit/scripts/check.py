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

from audit_common import (  # noqa: E402
    CONTENT_TYPES, example_urls, has_price, load_snapshot, pages_of, pct,
    plural, sample, SkillResult
)

SKILL = "render-readability-audit"

# Thresholds, each with the reason it sits where it does.
MIN_QUOTABLE_TEXT = 300      # below ~300 chars a page carries no self-contained fact
HEAVY_SCRIPT_BYTES = 50000   # 50 KB of script alongside no prose means the page is assembled client-side
EMPTY_ROOT_TEXT = 200        # a framework root with under 200 chars has not rendered content
SHELL_SHARE_HIGH = 0.5       # more than half of content pages being shells is a site-wide failure
RENDER_GAP_SIGNIFICANT = 0.6 # JavaScript adding 60%+ of the text means the static HTML is a stub
IMAGE_DOMINANT_TEXT = 400    # a page with a hero image and under 400 chars is carrying facts in pixels
EXTERNAL_BUNDLE_COUNT = 3    # modern builds ship code-split external chunks and no inline script at all
THIN_SHARE = 0.3             # a third of content pages too short to quote is a site-wide problem

# Link text that promises the numbers a buyer needs.
FACT_BEARING_PDF_RE = re.compile(
    r"\b(price|pricing|rate card|tariff|fee|cost|quote|spec|specification|"
    r"datasheet|data sheet|brochure|catalog(ue)?|menu|product sheet|"
    r"technical detail|dimensions|sizing|price list)\b", re.I)

LOAD_MORE_RE = re.compile(r"\b(load more|show more|view more|see more|load additional)\b", re.I)
PAGINATION_RE = re.compile(r"(?:[?&](?:page|p|offset|start)=\d+|/page/\d+|/p/\d+/?$)", re.I)


def run(snapshot):
    result = SkillResult(SKILL)
    content_pages = [p for p in pages_of(snapshot, content_only=True)]
    render_mode = (snapshot.get("crawl") or {}).get("render_mode", "static")

    result.check("render-mode")
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

    if not content_pages:
        for name in ("thin-html", "spa-shell-detection", "facts-locked-in-images",
                     "facts-locked-in-pdfs", "video-transcripts", "iframed-main-content",
                     "image-alt-coverage", "crawlable-pagination"):
            result.skip(name, "no content pages returned HTTP 200, so there is no delivered "
                              "HTML to assess")
        return result

    shells = _check_shells(result, snapshot, content_pages, render_mode)
    _check_render_gap(result, content_pages, render_mode)
    _check_image_locked(result, content_pages, shells)
    _check_pdf_locked(result, content_pages)
    _check_video_transcripts(result, content_pages)
    _check_iframed_content(result, content_pages, shells)
    _check_alt_coverage(result, content_pages)
    _check_pagination(result, snapshot, content_pages)

    result.signal("shell_page_count", len(shells))
    result.signal("render_mode", render_mode)
    return result


# --------------------------------------------------------------------------

def _shell_reasons(page):
    """Why this page looks like a container rather than content.

    Normally two independent signals are required, so a genuinely short page (a
    thin contact page, say) is not misread as broken. The exception is an empty
    framework mount point on a page with almost no text: nothing but a shell
    produces that combination, and requiring a second signal missed the single
    most common shape on the web - a React or Vue build whose scripts are all
    external, so the "heavy inline script" signal never fires.
    """
    spa = page.get("spa_shell") or {}
    scripts = page.get("scripts") or {}
    reasons = []

    text_len = page.get("body_text_len", 0)
    script_bytes = scripts.get("inline_bytes", 0) + spa.get("state_blob_bytes", 0)
    root_len = spa.get("root_text_len")
    empty_root = bool(spa.get("root_selector")) and root_len is not None         and root_len < EMPTY_ROOT_TEXT

    if text_len < MIN_QUOTABLE_TEXT and (
            script_bytes > HEAVY_SCRIPT_BYTES
            or scripts.get("external_count", 0) >= EXTERNAL_BUNDLE_COUNT):
        reasons.append("{} chars of visible text against {:,} bytes of inline script "
                       "and {} external script(s)".format(
                           text_len, script_bytes, scripts.get("external_count", 0)))
    if empty_root:
        reasons.append("framework root `{}` contains {} chars".format(
            spa["root_selector"], root_len))
    if spa.get("state_blobs") and text_len < MIN_QUOTABLE_TEXT * 2:
        reasons.append("content held in {} rather than in HTML".format(
            ", ".join(spa["state_blobs"][:2])))
    if spa.get("noscript_demands_js"):
        reasons.append("<noscript> tells the visitor to enable JavaScript")

    if empty_root and text_len < MIN_QUOTABLE_TEXT:
        return reasons
    return reasons if len(reasons) >= 2 else []


def _check_shells(result, snapshot, content_pages, render_mode):
    result.check("thin-html")
    result.check("spa-shell-detection")

    shells = [p for p in content_pages if _shell_reasons(p)]
    home = next((p for p in content_pages if p.get("page_type") == "home"), None)
    home_is_shell = home is not None and bool(_shell_reasons(home))
    recovered = None
    if home_is_shell and "rendered_text_len" in (home or {}):
        # "Recovered" means JavaScript supplied the bulk of the text and there
        # is now enough of it to quote. The bar used to be a flat 900 chars,
        # which called a homepage that went from 13 characters to 538 "not
        # reachable even with JavaScript enabled" - the opposite of what had
        # just been measured, and a `critical` verdict on a site whose real
        # problem is `high`.
        rendered_len = home["rendered_text_len"]
        recovered = (rendered_len >= MIN_QUOTABLE_TEXT
                     and rendered_len >= max(home.get("body_text_len", 0), 1) * 3)

    if home_is_shell:
        if recovered is True:
            severity, confidence = "high", "high"
            extra = ("A Playwright pass recovered {} chars after JavaScript ran, so the content "
                     "exists but only for consumers that execute JavaScript.".format(
                         home.get("rendered_text_len")))
        elif recovered is False:
            severity, confidence = "critical", "high"
            extra = "A Playwright pass recovered only {} chars, so the content is not reachable " \
                    "even with JavaScript enabled.".format(home.get("rendered_text_len"))
        else:
            severity, confidence = "critical", "medium"
            extra = ("No rendered pass was available on this machine, so it could not be "
                     "confirmed whether JavaScript recovers the text.")
        result.add(
            id_hint="homepage-is-javascript-shell",
            title="The homepage is delivered as an empty JavaScript shell",
            severity=severity, confidence=confidence,
            evidence="{}: {}. {}".format(home["url"], "; ".join(_shell_reasons(home)), extra),
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
        measured = [p for p in shells if p.get("rendered_text_len") is not None]
        recovered_sample = [p for p in measured
                            if p["rendered_text_len"] >= MIN_QUOTABLE_TEXT
                            and p["rendered_text_len"] > max(p.get("body_text_len", 0), 1) * 3]
        if measured and len(recovered_sample) == len(measured):
            severity, confidence = "medium", "medium"
            sample_note = (" A browser pass rendered {} of these and JavaScript supplied "
                           "readable text on all {}, so the content exists for consumers "
                           "that execute JavaScript. The remaining {} were not rendered, "
                           "and this finding assumes they behave the same way."
                           .format(len(measured), len(measured),
                                   len(shells) - len(measured)))
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
            title="{} of {} content pages are delivered as JavaScript shells".format(
                len(shells), len(content_pages)),
            severity=severity,
            confidence=confidence,
            evidence="{}% of crawled content pages carry two or more shell signals. "
                     "Examples: {}.{}".format(
                         pct(len(shells), len(content_pages)),
                         "; ".join("{} ({})".format(p["url"], _shell_reasons(p)[0])
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
        result.skip("spa-shell-detection",
                    "every crawled content page delivered readable text in the initial HTML "
                    "response")

    _check_thin_pages(result, content_pages, shells)
    return shells


def _check_thin_pages(result, content_pages, shells):
    """Pages that are simply too short to quote, with no shell to explain it.

    Distinct from `js-shell`, where the text exists but arrives after
    JavaScript, and from `image-locked-facts`, where it exists but as pixels.
    This is the plain case: the page really does say almost nothing, so there
    is no sentence for an assistant to lift and nothing for a visitor to read.
    """
    result.check("thin-html")
    shell_urls = {p["url"] for p in shells}
    thin = [p for p in content_pages
            if p["url"] not in shell_urls
            and p.get("body_text_len", 0) < MIN_QUOTABLE_TEXT]

    if not thin:
        result.skip("thin-html",
                    "every crawled content page carries at least {} characters of body text, "
                    "which is enough to hold a quotable fact".format(MIN_QUOTABLE_TEXT))
        return

    rate = len(thin) / float(len(content_pages))
    result.add(
        id_hint="pages-too-thin-to-quote",
        title="{} too little text to be quoted".format(
            plural(len(thin), "page carries", "pages carry")),
        severity="high" if rate >= THIN_SHARE else "medium",
        confidence="high",
        evidence="{} of {} content pages ({}%) hold under {} characters of body text. "
                 "Examples: {}.".format(
                     len(thin), len(content_pages), pct(len(thin), len(content_pages)),
                     MIN_QUOTABLE_TEXT,
                     "; ".join("{} ({} chars)".format(p["url"], p.get("body_text_len", 0))
                               for p in sorted(thin, key=lambda x: x["url"])[:3])),
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
        static_len = max(page.get("body_text_len", 0), 1)
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
                         p["url"], p.get("body_text_len"), p["rendered_text_len"])
                         for p, _ in sorted(gaps, key=lambda x: x[0]["url"])[:3])),
        mechanism="C", root_cause="js-shell",
        summary="Move the primary copy into the server response so it does not depend on the "
                "consumer running JavaScript.",
        how_to_fix=[
            "For each template listed, render the main content on the server.",
            "Where full SSR is not practical, inline the key facts (heading, description, "
            "price, location) as static HTML and let the interactive parts hydrate afterwards.",
            "Re-measure by comparing View Source against the rendered page.",
        ],
        effort="high", owner="developer",
        rationale="The gap is the exact amount of content that disappears for any "
                  "consumer that does not execute JavaScript.",
        affected_pages=[p["url"] for p, _ in gaps],
    )


def _check_image_locked(result, content_pages, shells):
    result.check("facts-locked-in-images")
    shell_urls = {p["url"] for p in shells}
    locked = []
    for page in content_pages:
        if page["url"] in shell_urls:
            continue  # already reported as a shell; do not double-count
        images = page.get("images") or {}
        text_len = page.get("body_text_len", 0)
        if text_len >= IMAGE_DOMINANT_TEXT:
            continue
        if images.get("large_image_count", 0) >= 1 or images.get("count", 0) >= 5:
            locked.append((page, "{} chars of text beside {} image(s)".format(
                text_len, images.get("count", 0))))
        elif images.get("svg_text_nodes", 0) >= 15 or images.get("canvas_count", 0) >= 1:
            locked.append((page, "{} chars of text; text rendered in SVG or canvas".format(text_len)))

    if not locked:
        result.skip("facts-locked-in-images",
                    "no content page relies on imagery to carry its main message")
        return

    result.add(
        id_hint="facts-locked-in-images",
        title="{} their main content in images rather than text".format(
            plural(len(locked), "page carries", "pages carry")),
        severity="medium", confidence="medium",
        evidence="Pages with under {} chars of readable text but substantial imagery: {}.".format(
            IMAGE_DOMINANT_TEXT,
            "; ".join("{} ({})".format(p["url"], why) for p, why in sorted(locked, key=lambda x: x[0]["url"])[:5])),
        mechanism="C", root_cause="image-locked-facts",
        summary="Restate the facts shown in the images as HTML text on the same page.",
        how_to_fix=[
            "For each page, write out in HTML what the image says: the price, the specification, "
            "the opening hours, the offer.",
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


def _check_pdf_locked(result, content_pages):
    result.check("facts-locked-in-pdfs")
    locked = []
    for page in content_pages:
        pdfs = [pdf for pdf in (page.get("pdf_links") or [])
                if FACT_BEARING_PDF_RE.search(pdf.get("text") or "")
                or FACT_BEARING_PDF_RE.search(pdf.get("url") or "")]
        if not pdfs:
            continue
        # Only a problem when the same facts are absent from the HTML.
        if has_price(page.get("body_text", "")):
            continue
        locked.append((page, pdfs[0]))

    if not locked:
        result.skip("facts-locked-in-pdfs",
                    "no page links to a pricing or specification PDF whose numbers are missing "
                    "from the page text")
        return

    result.add(
        id_hint="facts-locked-in-pdfs",
        title="Pricing or specification facts are available only inside PDFs",
        severity="medium", confidence="medium",
        evidence="{} page(s) link to a fact-bearing PDF while stating no equivalent figure in "
                 "HTML. Examples: {}.".format(
                     len(locked),
                     "; ".join('{} -> "{}"'.format(p["url"], pdf["text"] or pdf["url"])
                               for p, pdf in sorted(locked, key=lambda x: x[0]["url"])[:5])),
        mechanism="C", root_cause="pdf-locked-facts",
        summary="Publish an HTML version of the numbers that currently live only in the PDF.",
        how_to_fix=[
            "Create an HTML page holding the same table or figures as the PDF.",
            "Link the PDF from that page as a download rather than as the only source.",
            "Keep both in sync by generating the PDF from the HTML, not the other way round.",
        ],
        effort="medium", owner="content owner",
        rationale="PDFs are fetched inconsistently, parsed unevenly, and rarely "
                  "quoted with confidence. A number that exists only in a PDF is a number the "
                  "assistant will not state.",
        affected_pages=[p["url"] for p, _ in locked],
    )


def _check_video_transcripts(result, content_pages):
    result.check("video-transcripts")
    thin_video = []
    for page in content_pages:
        video = page.get("video") or {}
        if not (video.get("native_count") or video.get("embed_count")
                or video.get("audio_count")):
            continue
        if video.get("transcript_nearby") or video.get("track_count"):
            continue
        if page.get("body_text_len", 0) >= 800:
            continue  # the page explains itself in text regardless of the video
        thin_video.append(page)

    if not thin_video:
        result.skip("video-transcripts",
                    "no page relies on audio or video to carry content that is missing from "
                    "its text")
        return

    result.add(
        id_hint="video-without-transcript",
        title="{} with audio or video and carry little readable text".format(
            plural(len(thin_video), "page leads", "pages lead")),
        severity="medium", confidence="medium",
        evidence="Pages with an embedded or native video, no caption track, no nearby "
                 "transcript, and under 800 chars of body text: {}.".format(
                     ", ".join(example_urls([p["url"] for p in thin_video]))),
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
    iframed = []
    for page in content_pages:
        if page["url"] in shell_urls:
            continue
        frames = [f for f in (page.get("iframes") or []) if not f["is_video"] and not f["is_map"]]
        if frames and page.get("body_text_len", 0) < MIN_QUOTABLE_TEXT:
            iframed.append((page, frames[0]))

    if not iframed:
        result.skip("iframed-main-content",
                    "no page delegates its main content to an iframe")
        return

    result.add(
        id_hint="main-content-in-iframe",
        title="{} their main content inside an iframe".format(
            plural(len(iframed), "page holds", "pages hold")),
        severity="medium", confidence="medium",
        evidence="Pages with under {} chars of own text plus a non-video, non-map iframe: {}.".format(
            MIN_QUOTABLE_TEXT,
            "; ".join("{} (iframe: {})".format(p["url"], f["src"][:80])
                      for p, f in sorted(iframed, key=lambda x: x[0]["url"])[:5])),
        mechanism="C", root_cause="iframe-content",
        summary="Move the iframed content into the page itself, or duplicate its key facts in HTML.",
        how_to_fix=[
            "Where the iframe hosts your own content, render it directly in the page template.",
            "Where it is a third-party embed (a booking widget, a menu tool), write the same "
            "core facts as HTML text above or below it.",
        ],
        effort="medium", owner="developer",
        rationale="An iframe is a separate document. Consumers that read the parent "
                  "page see the frame element, not the content inside it, so the page reads as empty.",
        affected_pages=[p["url"] for p, _ in iframed],
    )


def _check_alt_coverage(result, content_pages):
    result.check("image-alt-coverage")
    heavy = [p for p in content_pages
             if (p.get("images") or {}).get("count", 0) >= 5
             and (p.get("images") or {}).get("missing_alt_count", 0) > 0]
    if not heavy:
        result.skip("image-alt-coverage",
                    "no image-heavy content page has images missing an alt attribute "
                    "(an explicit alt=\"\" on a decorative image is correct and was not counted)")
        return

    total_missing = sum((p.get("images") or {}).get("missing_alt_count", 0) for p in heavy)
    # How many images, rather than how many times an image appears. A site's
    # two decorative header textures, present on all sixty crawled pages, were
    # reported as "189 images with no alt attribute" - a number that reads as a
    # content-photo problem across the library and is in fact two lines in one
    # shared template. Distinct source URLs is the count that matches the
    # number of edits the fix takes.
    distinct = sorted({url for page in heavy
                       for url in (page.get("images") or {}).get("missing_alt_sample") or []})
    repeated = len(distinct) and total_missing >= len(distinct) * 2
    result.add(
        id_hint="images-missing-alt-text",
        title="{} no alt attribute".format(
            plural(len(distinct) or total_missing, "image has", "images have")),
        severity="low", confidence="high",
        evidence="{} distinct image URL(s) with no alt attribute at all, appearing {} times "
                 "across {} image-heavy page(s).{} Examples: {}.".format(
                     len(distinct) or total_missing, total_missing, len(heavy),
                     " Most are repeated on every page, so they live in a shared template and "
                     "are one edit each rather than one per page." if repeated else "",
                     ", ".join(example_urls([p["url"] for p in heavy]))),
        mechanism="C", root_cause="alt-missing",
        summary="Add descriptive alt text to content images and alt=\"\" to purely decorative ones.",
        how_to_fix=[
            "Write alt text that states what the image shows, not what the file is called.",
            "Use alt=\"\" for decorative images so assistive technology and crawlers skip them.",
            "Prioritise product photos, diagrams and any image containing words.",
        ],
        effort="low", owner="content owner",
        rationale="Alt text is the only readable description of an image. It also "
                  "matters for accessibility, so this fix pays twice.",
        affected_pages=[p["url"] for p in heavy],
    )


def _check_pagination(result, snapshot, content_pages):
    result.check("crawlable-pagination")
    listings = [p for p in content_pages if p.get("page_type") == "category"]
    if not listings:
        result.skip("crawlable-pagination", "no category or listing pages were crawled")
        return

    offenders = []
    for page in listings:
        text = page.get("body_text", "")
        if not LOAD_MORE_RE.search(text):
            continue
        internal = [l["url"] for l in (page.get("links", {}).get("internal") or [])]
        if any(PAGINATION_RE.search(url) for url in internal):
            continue
        offenders.append(page)

    if not offenders:
        result.skip("crawlable-pagination",
                    "listing pages either paginate with real links or do not use a load-more control")
        return

    result.add(
        id_hint="load-more-without-crawlable-pagination",
        title="{} their catalogue behind a load-more button".format(
            plural(len(offenders), "listing page hides", "listing pages hide")),
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
