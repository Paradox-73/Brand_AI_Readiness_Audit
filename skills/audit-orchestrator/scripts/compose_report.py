#!/usr/bin/env python3
"""Merge sub-skill findings into report.json and report.md.

Responsibilities that belong to the entrypoint and nowhere else:
  - deduplicate observations two skills both noticed
  - assign stable finding IDs
  - compute priority as severity x reach / effort
  - add proactive recommendations whose conditions are met
  - simulate which sentence an assistant would quote from each key page
  - render the human-readable report

Usage:
    python compose_report.py --snapshot snapshot.json \
        --findings a.json b.json ... --out-json report.json --out-md report.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_common import (  # noqa: E402
    EFFORT_DIVISOR, MECHANISMS, SEVERITY_RANK, SEVERITY_WEIGHT, VERSION,
    load_snapshot, pages_of, read_json, sentences, truncate, write_json,
)

# Sub-skill order. Earlier skills own overlapping observations, so when two
# skills see the same root cause on the same pages the earlier one's wording
# and fix survive and the later one's evidence is merged into it.
SKILL_ORDER = [
    "crawl-access-audit",
    "render-readability-audit",
    "structured-data-audit",
    "fact-extractability-audit",
    "freshness-corroboration-audit",
    "engagement-audit",
]

PRIORITY_BANDS = ((2.0, "critical"), (1.0, "high"), (0.45, "medium"), (0.0, "low"))

EFFORT_TIME = {
    "low": "under an hour",
    "medium": "half a day to a day",
    "high": "several days of development time",
}

NUMERIC_RE = re.compile(r"\d|[$€£¥₹]")
CONCRETE_RE = re.compile(
    r"\d|[$€£¥₹]|\b(?:is|are)\s+(?:an?|the)\b|\b(?:means|refers to|defined as)\b", re.I)


# --------------------------------------------------------------------------
# Merge and score
# --------------------------------------------------------------------------

def load_skill_results(paths):
    results = []
    for path in paths:
        data = read_json(path)
        results.append(data)
    order = {name: i for i, name in enumerate(SKILL_ORDER)}
    results.sort(key=lambda r: order.get(r.get("skill", ""), 99))
    return results


def dedupe(results):
    """Collapse findings that describe the same root cause on the same pages.

    Two skills legitimately notice one problem from different angles. Reporting
    it twice makes the report look padded and inflates the severity counts,
    so the earlier skill keeps the finding and the later one's evidence is
    appended to it.
    """
    merged = {}
    order = []
    duplicates = []
    for result in results:
        skill = result.get("skill", "unknown")
        for finding in result.get("findings", []):
            key = (finding["mechanism"], finding["root_cause"],
                   tuple(sorted(finding.get("affected_pages") or [])))
            existing = merged.get(key)
            # Merge only across skills. A single skill emitting several findings
            # that share a root cause is doing so deliberately - robots.txt can
            # block answer crawlers, training crawlers and content paths, and
            # those are three different decisions with three different fixes.
            if existing is not None and existing["detected_by"] != skill:
                existing.setdefault("also_detected_by", []).append(skill)
                existing["evidence"] = "{} Also observed by {}: {}".format(
                    existing["evidence"], skill, finding["evidence"])
                duplicates.append({"skill": skill, "id_hint": finding["id_hint"],
                                   "merged_into": existing["id_hint"]})
                continue
            record = dict(finding)
            record["detected_by"] = skill
            # Same skill, same key: keep both, but give the later one a distinct
            # slot so it is not silently overwritten.
            slot = key if existing is None else key + (finding["id_hint"],)
            merged[slot] = record
            order.append(slot)
    return [merged[k] for k in order], duplicates


def score(finding, pages_crawled):
    """priority = severity weight x reach / effort.

    Reach is the share of crawled pages the finding touches, floored at 0.25 so
    a critical problem on one page still outranks cosmetic issues everywhere.
    A finding with no attributed pages is site-wide by construction (robots,
    entity ambiguity, boilerplate) and gets full reach.
    """
    count = finding.get("affected_page_count", 0)
    if count == 0:
        reach = 1.0
    else:
        reach = max(0.25, min(1.0, count / float(max(pages_crawled, 1))))
    weight = SEVERITY_WEIGHT[finding["severity"]]
    effort = EFFORT_DIVISOR[finding["suggested_action"]["effort"]]
    return round(weight * reach / effort, 3), round(reach, 3)


def priority_label(value):
    for threshold, label in PRIORITY_BANDS:
        if value >= threshold:
            return label
    return "low"


def assign_ids(findings):
    """Stable IDs: sort by severity, mechanism, id_hint, then number upward.

    Deliberately not sorted by priority, so a finding keeps the same ID when a
    later crawl changes how many pages it touches.
    """
    ordered = sorted(findings, key=lambda f: (
        SEVERITY_RANK[f["severity"]], f["mechanism"], f["id_hint"]))
    for index, finding in enumerate(ordered, start=1):
        finding["id"] = "F-{:03d}".format(index)
    return ordered


# --------------------------------------------------------------------------
# Citation simulation
# --------------------------------------------------------------------------

def simulate_citations(snapshot, brand_name):
    """The sentence an assistant would most likely lift from each key page.

    Makes the extractability findings concrete: a judge can read "none found"
    beside a page and see immediately what the problem is.
    """
    out = []
    key_types = ("home", "about", "pricing", "faq", "product", "service", "contact", "location")
    pattern = r"\s+".join(re.escape(t) for t in brand_name.split()) if brand_name else None

    for page in pages_of(snapshot, content_only=True):
        if page["page_type"] not in key_types:
            continue
        # Quote from paragraphs, not from the flattened page text: a heading
        # and the sentence after it run together when the whole page is
        # flattened, and the result is not a sentence anyone would quote.
        candidates = []
        for paragraph in (page.get("paragraphs") or [])[:25]:
            candidates.extend(sentences(paragraph))
        if not candidates:
            candidates = sentences(page.get("body_text", ""))
        candidates = candidates[:40]
        best = None
        reason = ""
        # Priority 1: a definition naming the brand. That is what an assistant
        # needs before it can say anything else.
        if pattern:
            for sentence in candidates:
                if re.search(pattern, sentence, re.I) and re.search(
                        r"\b(?:is|are|was|provides|offers|helps|builds|makes)\b", sentence, re.I):
                    best, reason = sentence, "names the brand and says what it is"
                    break
        # Priority 2: a self-contained sentence carrying a number or a price.
        if best is None:
            for sentence in candidates:
                if 40 <= len(sentence) <= 300 and NUMERIC_RE.search(sentence):
                    best, reason = sentence, "states a number a reader could act on"
                    break
        # Priority 3: any self-contained sentence that defines something.
        if best is None:
            for sentence in candidates:
                if 40 <= len(sentence) <= 300 and CONCRETE_RE.search(sentence):
                    best, reason = sentence, "defines something, though it states no figure"
                    break
        out.append({
            "url": page["url"],
            "page_type": page["page_type"],
            "likely_citation": truncate(best, 260) if best else None,
            "why": reason if best else
                   "no sentence on this page both stands alone and states a fact, so an "
                   "assistant fetching it would have nothing to quote",
        })
        if len(out) >= 12:
            break
    return sorted(out, key=lambda c: c["url"])


# --------------------------------------------------------------------------
# Proactive recommendations
# --------------------------------------------------------------------------

def build_recommendations(snapshot, signals, findings):
    """Recommendations beyond the defects found, included only when relevant.

    Every entry states its condition, so nothing generic is ever appended just
    to make the report look longer.
    """
    root_causes = {f["root_cause"] for f in findings}
    page_types = {p["page_type"] for p in pages_of(snapshot, content_only=True)}
    origin = snapshot["origin"].rstrip("/")
    brand = (snapshot.get("brand") or {}).get("name") or snapshot["site"]
    out = []

    def add(rec_id, title, condition, summary, steps, mechanism, why, effort, owner, snippet=None):
        if not condition:
            return
        entry = {
            "id": rec_id, "type": "proactive", "title": title, "summary": summary,
            "how_to_do_it": steps, "mechanism": mechanism, "why_this_works": why,
            "effort": effort, "owner": owner,
        }
        if snippet:
            entry["snippet"] = snippet
        out.append(entry)

    add("R-LLMS-TXT", "Publish /llms.txt and /llms-full.txt",
        not signals.get("llms_txt_present"),
        "Add a short plain-text file at the site root summarising the brand, its key pages and "
        "its canonical facts.",
        ["Create /llms.txt with a one-line brand definition, the canonical boilerplate, and a "
         "linked list of your most important pages with one line of description each.",
         "Add /llms-full.txt containing the full text of those key pages if they are short.",
         "Keep it under 200 lines and update it whenever the boilerplate changes."],
        "B",
        "It hands a machine the exact summary you want quoted, instead of leaving it to "
        "assemble one from whichever page it happened to fetch.",
        "low", "marketing",
        "# {brand}\n\n> {brand} is a <category> that <does what> for <whom>.\n\n"
        "## Key pages\n\n- [Pricing]({origin}/pricing): <one line with the actual numbers>\n"
        "- [About]({origin}/about): founding facts, team, location\n".format(
            brand=brand, origin=origin))

    add("R-ANSWER-FIRST", "Add an answer-first summary block to every key page",
        signals.get("fluff_first"),
        "Open each key page with two or three sentences an assistant can quote without editing.",
        ["Write a 2-3 sentence block immediately under the H1 stating the page's core facts.",
         "Use plain declarative sentences, each carrying one fact.",
         "Do not hide it behind a tab, an accordion or a carousel."],
        "B",
        "Assistants lift short passages from near the top of a page. A block written to be "
        "lifted is the passage they find.",
        "low", "content owner")

    add("R-FAQ-SCHEMA", "Publish FAQ content with FAQPage schema",
        not signals.get("has_faq_schema"),
        "Add a page of real questions, phrased the way people ask assistants, marked up as FAQPage.",
        ["Collect the ten questions your sales or support team answers most often.",
         'Phrase each heading as the customer asks it ("How much does X cost?"), not as an '
         "internal topic name.",
         "Answer each in two or three sentences, leading with the actual value.",
         "Wrap the whole set in FAQPage JSON-LD with the answer text matching the page exactly."],
        "B",
        "Question-and-answer pairs are already the shape of the thing an assistant is trying "
        "to produce, so they are unusually cheap for it to reuse.",
        "medium", "content owner")

    add("R-SAMEAS-WIKIDATA", "Corroborate the brand across independent sources",
        signals.get("authoritative_profile_count", 0) < 3
        or not signals.get("has_wikidata_or_wikipedia"),
        "Claim the authoritative profiles, create a Wikidata item, and list them all in sameAs.",
        ["Claim or update: LinkedIn company page, Google Business Profile, Crunchbase, and one "
         "or two industry directories.",
         "Create a Wikidata item using the same one-sentence description you use on the site.",
         "List every profile URL in the Organization `sameAs` array.",
         "Make sure each profile links back to the site."],
        "D",
        "One site saying something is a claim. Several independent, verifiable sources saying "
        "the same thing is what makes it safe to repeat.",
        "medium", "marketing")

    add("R-BOILERPLATE", "Write one canonical boilerplate and reuse it verbatim",
        True,  # always: the cheapest corroboration win there is
        "Agree a single paragraph describing the brand and use it, unchanged, everywhere.",
        ["Write one paragraph: what you are, who you serve, where you are, when you started.",
         "Paste it, character for character, into the site, the Organization `description`, "
         "LinkedIn, the press kit, app store listings and every directory entry.",
         "Change it in one place and propagate; never let two versions coexist.",
         "Review it once a year rather than rewriting it per channel."],
        "D",
        "Identical wording across independent sources is the strongest agreement signal "
        "available, and it costs nothing but discipline.",
        "low", "marketing")

    add("R-PRESS-PAGE", "Publish a press page with reusable facts",
        "press" not in page_types,
        "Give journalists and analysts a single page of approved facts so third parties repeat "
        "the same ones.",
        ["Publish a page with the logo pack, the canonical boilerplate, founding facts and a "
         "named spokesperson.",
         "Include the exact figures you want repeated: headcount, founding year, locations, "
         "customer count.",
         "Link it from the footer."],
        "D",
        "Third-party coverage is where corroboration comes from. A press page decides what "
        "those third parties copy.",
        "low", "marketing")

    add("R-DATE-SIGNALS", "Show a visible last-updated date and review cadence",
        signals.get("no_date_signals"),
        "Add a visible updated date plus dateModified to evergreen pages, and review them on a schedule.",
        ["Add \"Last updated <date>\" to key pages, wrapped in <time datetime=\"YYYY-MM-DD\">.",
         "Mirror it in dateModified in structured data.",
         "Put a quarterly review of the top ten pages in someone's calendar.",
         "Only move the date when the content actually changed."],
        "D",
        "A dated page beats an undated one when two sources say the same thing, and freshness "
        "is one of the few quality signals a machine can check cheaply.",
        "low", "content owner")

    add("R-COMPARISON-PAGES", "Publish comparison and alternatives pages",
        "comparison" not in page_types,
        'Cover the framings people actually search: "X vs Y" and "X for <use case>".',
        ["List the three alternatives prospects mention most and write an honest comparison "
         "page for each.",
         "Say plainly who each option suits better; a comparison that never concedes anything "
         "is not treated as a source.",
         "Include a table of concrete differences: price, capability, minimum commitment."],
        "E",
        "Assistants answer the question as it was asked. A brand with content only for its own "
        "framing is absent from every comparison question, which is how most buyers ask.",
        "medium", "content owner")

    add("R-USE-CASE-PAGES", "Add use-case or location landing pages",
        bool({"location", "product", "service"} & page_types),
        "Give each distinct audience or place its own page, rather than one page for everyone.",
        ["List the three audiences or locations that matter commercially.",
         "Give each a page naming that audience or place in the H1 and first paragraph.",
         "Include facts specific to it: local address, sector-specific numbers, relevant "
         "customer examples."],
        "E",
        "Answers are shaped by the asker's context. Content that exists for only one framing "
        "is surfaced for only one framing.",
        "medium", "content owner")

    add("R-HTML-FOR-PDF", "Publish HTML versions of PDF-only content",
        "pdf-locked-facts" in root_causes,
        "Move the numbers out of the PDF and onto a page.",
        ["Create an HTML page carrying the same table or figures as the PDF.",
         "Keep the PDF as a download from that page, not as the only source.",
         "Generate the PDF from the HTML so the two cannot diverge."],
        "C",
        "PDFs are fetched inconsistently and quoted reluctantly. The same numbers in HTML are "
        "read every time.",
        "medium", "content owner")

    add("R-EMAIL-TEXT-FIRST", "Design newsletters and transactional email text-first",
        signals.get("newsletter_signup"),
        "Put the substance in the first two lines of readable text, never in an image.",
        ["Lead every email with two lines of plain text stating what it is and what changed.",
         "Never put the main message in an image; use images to support text, not replace it.",
         "Give every email a subject line that states the fact rather than teasing it.",
         "Include a plain-text alternative part in every send."],
        "F",
        "Inbox summaries are built from the text of a message. Substance carried in an image, "
        "or buried under filler, is simply absent from the summary the recipient reads.",
        "low", "marketing")

    add("R-WAYFINDING", "Add site search, breadcrumbs and related-content modules",
        bool({"dead-end", "orphan-pages", "no-breadcrumbs", "broken-links"} & root_causes),
        "Give every page a way onward and a way back.",
        ["Add on-site search, and describe it with WebSite + SearchAction structured data.",
         "Add breadcrumbs to every deep template, visible and in BreadcrumbList markup.",
         'Add a "Read next" module with 2-4 related internal links to article and product templates.'],
        "G",
        "A visitor sent by an answer lands deep with no journey behind them. Wayfinding is the "
        "difference between one page and a session.",
        "medium", "developer")

    add("R-AUTHOR-PAGES", "Give articles named authors with credentials",
        bool(signals.get("articles_without_author")) and "article" in page_types,
        "Attribute every article to a real, described person.",
        ["Create an author page per writer with their role, credentials and other articles.",
         "Link each article byline to that page and set Article `author` to the same Person.",
         "Include something verifiable: a professional qualification, years in the field, a "
         "public profile."],
        "D",
        "Attribution is a trust signal a machine can verify. Anonymous content competes badly "
        "with the same claim made by a named, credentialed person.",
        "medium", "content owner")

    return out


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------

def compose(snapshot, skill_results, audited_at=None):
    crawl = snapshot.get("crawl") or {}
    pages_crawled = max(crawl.get("pages_ok") or crawl.get("pages_crawled") or 0, 1)
    brand = snapshot.get("brand") or {}

    signals = {}
    checks_run, not_applicable = [], []
    extra_requests = 0
    for result in skill_results:
        signals.update(result.get("signals") or {})
        for check in result.get("checks_run", []):
            checks_run.append({"skill": result.get("skill"), "check": check})
        not_applicable.extend(result.get("not_applicable", []))
        extra_requests += result.get("extra_requests_made", 0)

    findings, duplicates = dedupe(skill_results)
    for finding in findings:
        value, reach = score(finding, pages_crawled)
        finding["suggested_action"]["priority"] = priority_label(value)
        finding["suggested_action"]["priority_score"] = value
        finding["reach"] = reach
    findings = assign_ids(findings)

    ranked = sorted(findings,
                    key=lambda f: (-f["suggested_action"]["priority_score"],
                                   SEVERITY_RANK[f["severity"]], f["id"]))
    actionable = [f for f in ranked if f["severity"] != "info"]
    start_here = [f["id"] for f in actionable[:3]]

    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("critical", "high", "medium", "low", "info")}

    report = {
        "site": snapshot["site"],
        "audited_at": audited_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_findings": len(findings),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "info": counts["info"],
            "verdict": _verdict(counts, findings),
        },
        "findings": [_public_finding(f) for f in findings],
        "start_here": start_here,
        "recommendations": build_recommendations(snapshot, signals, findings),
        "citation_simulation": simulate_citations(snapshot, brand.get("name") or ""),
        "crawl": {
            "origin": snapshot["origin"],
            "brand_name": brand.get("name"),
            "brand_name_source": brand.get("source"),
            "pages_crawled": crawl.get("pages_crawled", 0),
            "pages_ok": crawl.get("pages_ok", 0),
            "budget_exhausted": bool(crawl.get("budget_exhausted")),
            "render_mode": crawl.get("render_mode", "static"),
            "elapsed_s": crawl.get("elapsed_s"),
            "extra_requests_made": extra_requests,
            "user_agent": crawl.get("user_agent"),
            "notes": crawl.get("notes") or [],
        },
        "checks_run": sorted(checks_run, key=lambda c: (c["skill"], c["check"])),
        "not_applicable": sorted(not_applicable, key=lambda n: (n.get("skill", ""), n["check"])),
        "merged_duplicates": duplicates,
        "auditor": {"name": "brand-ai-readiness-audit", "version": VERSION},
    }
    return report


def _public_finding(finding):
    """Required keys first, extensions after. Ordering is for readers, not code."""
    action = finding["suggested_action"]
    out = {
        "id": finding["id"],
        "title": finding["title"],
        "severity": finding["severity"],
        "evidence": finding["evidence"],
        "suggested_action": {
            "summary": action["summary"],
            "priority": action["priority"],
            "priority_score": action["priority_score"],
            "effort": action["effort"],
            "owner": action["owner"],
            "how_to_fix": action["how_to_fix"],
            "rationale": action["rationale"],
        },
        "confidence": finding["confidence"],
        "mechanism": finding["mechanism"],
        "mechanism_description": MECHANISMS[finding["mechanism"]],
        "root_cause": finding["root_cause"],
        "affected_pages": finding["affected_pages"],
        "affected_page_count": finding["affected_page_count"],
        "reach": finding["reach"],
        "detected_by": finding["detected_by"],
    }
    if "snippet" in action:
        out["suggested_action"]["snippet"] = action["snippet"]
    if finding.get("also_detected_by"):
        out["also_detected_by"] = finding["also_detected_by"]
    return out


def _verdict(counts, findings):
    if counts["critical"]:
        return ("Machines are being shut out before they read anything. {} critical problem(s) "
                "block access or delivery, and nothing else on the site can compensate until "
                "they are fixed.".format(counts["critical"]))
    if counts["high"] >= 3:
        return ("The site is reachable, but {} high-severity problems mean a machine fetching "
                "it struggles to work out who this brand is or to find a fact worth "
                "quoting.".format(counts["high"]))
    if counts["high"]:
        return ("The foundations are sound. {} high-severity problem(s) are holding back how "
                "confidently the brand can be described and cited.".format(counts["high"]))
    if counts["medium"]:
        return ("No blocking problems. {} medium-severity items are worth fixing to improve how "
                "often and how accurately the brand gets quoted.".format(counts["medium"]))
    return ("No blocking or significant problems were found. The proactive recommendations below "
            "are where the remaining upside is.")


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

SEVERITY_HEADINGS = [
    ("critical", "Critical", "Machines cannot get in or cannot read the page. Fix these first."),
    ("high", "High", "Significant loss of visibility, accuracy or visitors."),
    ("medium", "Medium", "Real improvements, worth scheduling."),
    ("low", "Low", "Hygiene. Cheap to fix, small individual gain."),
    ("info", "For information", "Observations that are not defects, recorded so you know they were checked."),
]


def render_markdown(report):
    lines = []
    add = lines.append

    add("# AI readiness audit: {}".format(report["site"]))
    add("")
    add("Audited {} · {} pages crawled · {} findings · {} recommendations".format(
        report["audited_at"], report["crawl"]["pages_crawled"],
        report["summary"]["total_findings"], len(report["recommendations"])))
    add("")
    add("## The short version")
    add("")
    add(report["summary"]["verdict"])
    add("")
    add("| Severity | Count |")
    add("| --- | --- |")
    for key, label, _ in SEVERITY_HEADINGS:
        add("| {} | {} |".format(label, report["summary"].get(key, 0)))
    add("")

    by_id = {f["id"]: f for f in report["findings"]}
    if report["start_here"]:
        add("## Start here")
        add("")
        add("Three fixes, ranked by how much they change relative to the work involved.")
        add("")
        for position, finding_id in enumerate(report["start_here"], start=1):
            finding = by_id[finding_id]
            action = finding["suggested_action"]
            add("{}. **{}** ({}) — {}".format(position, finding["title"], finding["id"],
                                              action["summary"]))
            add("   Who does it: {}. Roughly: {}.".format(
                action["owner"], EFFORT_TIME[action["effort"]]))
        add("")

    for key, label, blurb in SEVERITY_HEADINGS:
        group = [f for f in report["findings"] if f["severity"] == key]
        if not group:
            continue
        group.sort(key=lambda f: (-f["suggested_action"]["priority_score"], f["id"]))
        add("## {} findings".format(label))
        add("")
        add("_{}_".format(blurb))
        add("")
        for finding in group:
            add(_render_finding(finding))
        add("")

    if report["recommendations"]:
        add("## Beyond the defects")
        add("")
        add("These are not problems with the site. They are things that would raise the chance "
            "of being found, quoted and trusted, and they are included only where the audit saw "
            "the condition that makes them relevant.")
        add("")
        for rec in report["recommendations"]:
            add("### {}".format(rec["title"]))
            add("")
            add(rec["summary"])
            add("")
            add("**Why this works** (mechanism {}): {}".format(rec["mechanism"], rec["why_this_works"]))
            add("")
            add("**How to do it** — {}, roughly {}:".format(rec["owner"], EFFORT_TIME[rec["effort"]]))
            for step in rec["how_to_do_it"]:
                add("- {}".format(step))
            if rec.get("snippet"):
                add("")
                add("```")
                add(rec["snippet"])
                add("```")
            add("")

    citations = report.get("citation_simulation") or []
    if citations:
        add("## What an assistant would quote")
        add("")
        add("For each key page, the sentence an assistant fetching it would most likely lift. "
            '"Nothing quotable" means the page contains no self-contained sentence stating a fact.')
        add("")
        add("| Page | Type | Likely quote |")
        add("| --- | --- | --- |")
        for citation in citations:
            quote = citation["likely_citation"]
            add("| {} | {} | {} |".format(
                citation["url"], citation["page_type"],
                _escape_cell(quote) if quote else "**Nothing quotable** — " + citation["why"]))
        add("")

    add("## Appendix: what was checked")
    add("")
    add("{} checks were run across {} skills. The audit made {} extra read-only requests beyond "
        "the crawl, used the user agent `{}`, and rendered in `{}` mode.".format(
            len(report["checks_run"]), len({c["skill"] for c in report["checks_run"]}),
            report["crawl"]["extra_requests_made"], report["crawl"]["user_agent"],
            report["crawl"]["render_mode"]))
    add("")
    if report["crawl"]["notes"]:
        for note in report["crawl"]["notes"]:
            add("- Note: {}".format(note))
        add("")

    if report["not_applicable"]:
        add("### Checks that did not apply, and why")
        add("")
        add("A check that stays quiet on purpose is as important as one that fires. These were "
            "run and found nothing to report:")
        add("")
        for item in report["not_applicable"]:
            add("- **{}** ({}): {}".format(item["check"], item.get("skill", ""), item["reason"]))
        add("")

    if report["merged_duplicates"]:
        add("### Observations merged")
        add("")
        for item in report["merged_duplicates"]:
            add("- `{}` from {} was merged into `{}`, which reports the same root cause.".format(
                item["id_hint"], item["skill"], item["merged_into"]))
        add("")

    add("---")
    add("")
    add("Generated by {} v{}. Read-only: this audit made no change to the site and submitted "
        "no forms.".format(report["auditor"]["name"], report["auditor"]["version"]))
    add("")
    return "\n".join(lines)


def _render_finding(finding):
    action = finding["suggested_action"]
    out = []
    out.append("### {} — {}".format(finding["id"], finding["title"]))
    out.append("")
    out.append("**Priority {}** · confidence {} · mechanism {} · found by {}{}".format(
        action["priority"], finding["confidence"], finding["mechanism"], finding["detected_by"],
        " and " + ", ".join(finding["also_detected_by"]) if finding.get("also_detected_by") else ""))
    out.append("")
    out.append("**What we found.** {}".format(finding["evidence"]))
    out.append("")
    out.append("**Why it matters.** {}".format(action["rationale"]))
    out.append("")
    out.append("**What to do.** {}".format(action["summary"]))
    out.append("")
    out.append("Who does it: {}. Roughly: {}.".format(action["owner"], EFFORT_TIME[action["effort"]]))
    out.append("")
    for step in action["how_to_fix"]:
        out.append("- {}".format(step))
    if action.get("snippet"):
        out.append("")
        out.append("```")
        out.append(action["snippet"])
        out.append("```")
    if finding["affected_pages"]:
        out.append("")
        out.append("Affected pages ({} total, showing up to 5):".format(finding["affected_page_count"]))
        for url in finding["affected_pages"]:
            out.append("- {}".format(url))
    out.append("")
    return "\n".join(out)


def _escape_cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# Optional HTML rendering
# --------------------------------------------------------------------------

SEVERITY_COLOURS = {"critical": "#b00020", "high": "#c2410c", "medium": "#a16207",
                    "low": "#3f6212", "info": "#334155"}


def render_html(report):
    def esc(value):
        return html.escape(str(value or ""))

    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<title>AI readiness audit: {}</title>".format(esc(report["site"])),
             "<style>",
             "body{font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;max-width:52rem;",
             "margin:2rem auto;padding:0 1rem;color:#111;background:#fff}",
             "h1,h2,h3{line-height:1.25} code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}",
             "pre{background:#f6f7f9;padding:.75rem;overflow-x:auto;border-radius:6px;font-size:.85em}",
             ".badge{display:inline-block;padding:.1rem .5rem;border-radius:999px;color:#fff;",
             "font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}",
             ".finding{border:1px solid #e5e7eb;border-radius:8px;padding:1rem 1.25rem;margin:1rem 0}",
             ".meta{color:#555;font-size:.85rem} table{border-collapse:collapse;width:100%}",
             "td,th{border-bottom:1px solid #e5e7eb;padding:.4rem .5rem;text-align:left;font-size:.9rem}",
             "@media (prefers-color-scheme:dark){body{background:#0b0e14;color:#e6e6e6}",
             "pre{background:#161b22}.finding{border-color:#2a2f3a}td,th{border-color:#2a2f3a}",
             ".meta{color:#9aa4b2}}",
             "</style></head><body>"]
    parts.append("<h1>AI readiness audit: {}</h1>".format(esc(report["site"])))
    parts.append('<p class="meta">Audited {} · {} pages crawled · {} findings</p>'.format(
        esc(report["audited_at"]), report["crawl"]["pages_crawled"],
        report["summary"]["total_findings"]))
    parts.append("<p>{}</p>".format(esc(report["summary"]["verdict"])))

    by_id = {f["id"]: f for f in report["findings"]}
    if report["start_here"]:
        parts.append("<h2>Start here</h2><ol>")
        for finding_id in report["start_here"]:
            finding = by_id[finding_id]
            parts.append("<li><strong>{}</strong> — {}</li>".format(
                esc(finding["title"]), esc(finding["suggested_action"]["summary"])))
        parts.append("</ol>")

    for key, label, _ in SEVERITY_HEADINGS:
        group = [f for f in report["findings"] if f["severity"] == key]
        if not group:
            continue
        parts.append("<h2>{} findings</h2>".format(esc(label)))
        for finding in sorted(group, key=lambda f: -f["suggested_action"]["priority_score"]):
            action = finding["suggested_action"]
            parts.append('<div class="finding">')
            parts.append('<h3><span class="badge" style="background:{}">{}</span> {} — {}</h3>'.format(
                SEVERITY_COLOURS[key], esc(key), esc(finding["id"]), esc(finding["title"])))
            parts.append('<p class="meta">Priority {} · confidence {} · mechanism {} · '
                         "{} · {}</p>".format(
                             esc(action["priority"]), esc(finding["confidence"]),
                             esc(finding["mechanism"]), esc(action["owner"]),
                             esc(EFFORT_TIME[action["effort"]])))
            parts.append("<p><strong>What we found.</strong> {}</p>".format(esc(finding["evidence"])))
            parts.append("<p><strong>Why it matters.</strong> {}</p>".format(esc(action["rationale"])))
            parts.append("<p><strong>What to do.</strong> {}</p><ul>".format(esc(action["summary"])))
            for step in action["how_to_fix"]:
                parts.append("<li>{}</li>".format(esc(step)))
            parts.append("</ul>")
            if action.get("snippet"):
                parts.append("<pre><code>{}</code></pre>".format(esc(action["snippet"])))
            parts.append("</div>")

    if report["recommendations"]:
        parts.append("<h2>Beyond the defects</h2>")
        for rec in report["recommendations"]:
            parts.append('<div class="finding"><h3>{}</h3><p>{}</p>'.format(
                esc(rec["title"]), esc(rec["summary"])))
            parts.append("<p><strong>Why this works</strong> (mechanism {}): {}</p><ul>".format(
                esc(rec["mechanism"]), esc(rec["why_this_works"])))
            for step in rec["how_to_do_it"]:
                parts.append("<li>{}</li>".format(esc(step)))
            parts.append("</ul></div>")

    parts.append("<h2>Appendix</h2><p class=\"meta\">{} checks run; {} did not apply.</p>".format(
        len(report["checks_run"]), len(report["not_applicable"])))
    parts.append("</body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--findings", nargs="+", required=True)
    parser.add_argument("--out-json", default="report.json")
    parser.add_argument("--out-md", default="report.md")
    parser.add_argument("--out-html", help="also write an HTML report to this path")
    parser.add_argument("--audited-at", help="override the report timestamp (used by tests)")
    args = parser.parse_args(argv)

    snapshot = load_snapshot(args.snapshot)
    results = load_skill_results(args.findings)
    report = compose(snapshot, results, audited_at=args.audited_at)

    write_json(args.out_json, report)
    with open(args.out_md, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    if args.out_html:
        with open(args.out_html, "w", encoding="utf-8") as handle:
            handle.write(render_html(report))

    summary = report["summary"]
    print("composed {} finding(s) [{} critical, {} high, {} medium, {} low, {} info] and {} "
          "recommendation(s) -> {}".format(
              summary["total_findings"], summary["critical"], summary["high"],
              summary["medium"], summary["low"], summary["info"],
              len(report["recommendations"]), args.out_json), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
