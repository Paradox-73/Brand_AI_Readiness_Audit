# -*- coding: utf-8 -*-
"""A silent loop has nothing to transcribe, and a town's minutes are still facts.

Two findings from `render-readability-audit`, one false and one that never
fired at all.

  `no-transcript`        A dental clinic was told that 12 of its 21 pages "lead
                         with audio or video and carry little readable text",
                         and advised to "generate a transcript and paste it
                         into the page" and to "add a 3 to 5 sentence written
                         summary above the player stating what the video says".
                         The video was one silent looping clip in the site-wide
                         template - `autoplay muted loop playsinline` - behind
                         a hero heading. It says nothing. There is nothing to
                         transcribe, and the second half of that finding, that
                         the pages carry little readable text, belongs to
                         `thin-html` and its own bar.

  `pdf-locked-facts`     A town government whose agendas, minutes, ordinances,
                         emergency management plan and primary ballot are every
                         one of them PDF-only declined with "nothing on this
                         site is for sale, so a linked PDF is not hiding a
                         price or a specification a buyer needs". The check was
                         gated on commerce, so it switched off precisely where
                         PDFs carry the most content.

The second half of each file is the guard in the other direction. `muted` alone
is ordinary on content video, a plain `<video>` with no autoplay at all is
content, and a page that restates its documents in prose is fine however many
PDFs hang off it - none of those may be silenced by the fixes above.

Every host in this file is invented and every document is fictional.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    scripts = os.path.join(ROOT, "skills", skill, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RENDER = _module("render-readability-audit", "render_for_record_tests")

CLINIC = "https://a-made-up-dental-practice.example"
TOWNSHIP = "https://millbrook-township.gov.zz"
MAKER = "https://an-invented-valve-works.example"
CHROME = ["/", "/about", "/services", "/contact"]


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _causes(result):
    return {f["root_cause"] for f in result.findings}


def _finding(result, id_hint):
    return next((f for f in result.findings if f["id_hint"] == id_hint), None)


# --------------------------------------------------------------------------
# Page shapes
# --------------------------------------------------------------------------

def _video(native=0, embeds=0, audio=0, autoplay=0, intrusive=0,
           tracks=0, transcript=False):
    """A `video` record in the shape the crawl writes it.

    `intrusive` is the crawl's count of autoplaying elements that are *not*
    also `muted` and `loop`, so `autoplay - intrusive` is the number carrying
    all three. Both numbers are set explicitly in every case below, because the
    whole rule under test is the difference between them.
    """
    return {"native_count": native, "embed_count": embeds, "audio_count": audio,
            "autoplay_count": autoplay, "intrusive_autoplay_count": intrusive,
            "track_count": tracks, "transcript_nearby": transcript}


def _media_page(slug, video, body_chars=180, chrome_chars=700):
    url = "{}/{}".format(CLINIC, slug)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": slug.replace("-", " ").title(),
        "body_text": "w" * body_chars, "body_text_len": body_chars,
        "text_len": body_chars + chrome_chars,
        "video": video,
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _written_page(index, body_chars=900, chrome_chars=700):
    """An ordinary page with real copy, so the chrome measurement has a base."""
    url = "{}/service-{}".format(CLINIC, index)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": "Service {}".format(index),
        "body_text": "y" * body_chars, "body_text_len": body_chars,
        "text_len": body_chars + chrome_chars,
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _register_page(origin, slug, title, labels, prose_chars, headings=None):
    """A page whose text is its list of document links and little else."""
    url = "{}/{}".format(origin, slug)
    body = " ".join(labels) + " " + ("q" * prose_chars)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": "content",
        "title": title,
        "body_text": body, "body_text_len": len(body),
        "text_len": len(body) + 700,
        "headings": {"h2": list(headings or ["Meeting records"]), "h3": []},
        "pdf_links": [{"url": "{}/files/{}.pdf".format(origin, n),
                       "text": label}
                      for n, label in enumerate(labels)],
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _snapshot(origin, pages):
    return {"origin": origin, "pages": pages,
            "crawl": {"render_mode": "static", "notes": []}}


# Long enough that the labels alone carry the page past the 300-character bar,
# which is the whole point: a register whose text *is* its list of links reads
# as a page with plenty on it until the labels are subtracted.
MINUTES_LABELS = [
    "Selectboard minutes, 14 March",
    "Selectboard minutes, 28 March",
    "Selectboard minutes, 11 April",
    "Selectboard agenda, 25 April",
    "Selectboard agenda, 9 May",
    "Highway ordinance, as amended",
    "Emergency management plan",
    "Town plan, adopted",
    "Annual report of the auditors",
]


# --------------------------------------------------------------------------
# The background loop is not a video with something in it
# --------------------------------------------------------------------------

def test_autoplay_muted_and_loop_together_read_as_wallpaper():
    """The three attributes the clinic's template carried, as one answer."""
    assert RENDER._background_loop_count(_video(native=1, autoplay=1, intrusive=0)) == 1
    assert RENDER._players_that_could_carry_content(
        _video(native=1, autoplay=1, intrusive=0)) == 0


def test_muted_alone_and_autoplay_alone_are_not_wallpaper():
    """The guard in the other direction, which is the harder half.

    Content video that starts silent and unmutes on a click carries `muted`;
    an impolite site carries `autoplay`. Neither is a background clip, and a
    rule keyed on either one would delete this finding on the pages it was
    written for. `loop` is what no explainer, interview or demo carries: a
    viewer who reaches the end of something with a running time is not sent
    back to its start.
    """
    # autoplay + muted, no loop: the crawl counts it as intrusive, so the
    # difference this check reads is zero.
    assert RENDER._players_that_could_carry_content(
        _video(native=1, autoplay=1, intrusive=1)) == 1
    # A plain content video, no autoplay at all.
    assert RENDER._players_that_could_carry_content(
        _video(native=1, autoplay=0, intrusive=0)) == 1
    # And an embed, whose attributes the crawl cannot see, is never assumed
    # decorative.
    assert RENDER._players_that_could_carry_content(
        _video(embeds=1, autoplay=0, intrusive=0)) == 1


def test_a_content_video_beside_a_background_loop_still_counts():
    """A hero loop does not buy the page an exemption for its real player."""
    both = _video(native=2, autoplay=1, intrusive=0)
    assert RENDER._players_that_could_carry_content(both) == 1


def test_a_template_background_loop_produces_no_transcript_finding():
    """The clinic's report, at the check that produced it."""
    pages = [_media_page("implants-{}".format(i),
                         _video(native=1, autoplay=1, intrusive=0))
             for i in range(12)]
    result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(result, pages)

    assert not result.findings, [f["title"] for f in result.findings]
    reason = _reason(result, "video-transcripts")
    assert "autoplay, muted and loop" in reason
    assert "nothing in it to transcribe" in reason
    assert pages[0]["url"] in reason


def test_the_thin_half_of_that_finding_survives_at_the_check_that_owns_it():
    """Two claims were welded together and only one of them was true.

    "Leads with video" goes; "carries little readable text" stays, delivered by
    `thin-html` on its own 300-character bar with the fix that belongs to it -
    write a paragraph, not transcribe a silent clip.
    """
    thin_wallpaper = _media_page("clinic-tour",
                                 _video(native=1, autoplay=1, intrusive=0),
                                 body_chars=59)
    pages = [_written_page(i) for i in range(6)] + [thin_wallpaper]

    video_result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(video_result, pages)
    assert "no-transcript" not in _causes(video_result)
    reason = _reason(video_result, "video-transcripts")
    assert "thin-text check above" in reason

    thin_result = SkillResult("render-readability-audit")
    RENDER._check_thin_pages(thin_result, pages, [])
    thin = next(f for f in thin_result.findings if f["root_cause"] == "thin-html")
    assert thin_wallpaper["url"] in thin["affected_pages"]
    assert "transcript" not in thin["suggested_action"]["summary"].lower()


def test_a_page_whose_video_says_something_is_still_reported():
    """The check must keep working, or the fix above is just a deletion."""
    pages = [_media_page("about-our-treatments",
                         _video(native=1, autoplay=0, intrusive=0))]
    result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(result, pages)
    finding = _finding(result, "video-without-transcript")
    assert finding is not None
    assert finding["affected_pages"] == [pages[0]["url"]]
    assert any("background loop" in source for source in finding["checked"])


# --------------------------------------------------------------------------
# A document of record is a fact an assistant cannot read
# --------------------------------------------------------------------------

def _township_snapshot(prose_chars=40):
    register = _register_page(TOWNSHIP, "minutes", "Meeting records",
                              MINUTES_LABELS, prose_chars)
    home = {
        "url": TOWNSHIP + "/", "final_url": TOWNSHIP + "/", "status": 200,
        "page_type": "home", "title": "Millbrook Township",
        "body_text": "z" * 900, "body_text_len": 900, "text_len": 1600,
        "chrome_signature": {"nav_paths": list(CHROME)},
    }
    return _snapshot(TOWNSHIP, [home, register]), register


def test_a_town_whose_records_are_pdf_only_is_no_longer_declined_on_commerce():
    """The generalization failure, in the sentence it was printed in."""
    snapshot, register = _township_snapshot()
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, snapshot["pages"])

    assert "pdf-locked-facts" in _causes(result), [
        n["reason"] for n in result.not_applicable]
    finding = _finding(result, "documents-of-record-only-as-pdfs")
    assert finding["affected_pages"] == [register["url"]]
    assert not result.not_applicable, "a check that fired must not also skip"


def test_the_record_finding_never_claims_to_know_what_the_file_holds():
    """This audit fetches a PDF's headers. It does not parse the document.

    So the supportable claim is about the HTML - the page does not state what
    the document states - and never about the document's contents, which
    nothing here has read.
    """
    snapshot, _ = _township_snapshot()
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, snapshot["pages"])
    evidence = _finding(result, "documents-of-record-only-as-pdfs")["evidence"]

    assert "did not open the file" in evidence
    assert "claim about the page and not about the document's contents" in evidence


def test_the_record_finding_counts_the_set_it_lists():
    """A title whose number disagrees with the list under it is a finding that
    contradicts itself, and a reader who counts the URLs finds it out."""
    snapshot, _ = _township_snapshot()
    pages = list(snapshot["pages"]) + [
        _register_page(TOWNSHIP, "ordinances", "Ordinances",
                       ["Zoning ordinance, as adopted",
                        "Dog control ordinance"], 30)]
    snapshot["pages"] = pages
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, pages)
    finding = _finding(result, "documents-of-record-only-as-pdfs")

    assert finding["title"].startswith("2 pages link")
    assert finding["affected_page_count"] == 2
    assert "2 of {} content pages".format(len(pages)) in finding["evidence"]


def test_a_town_is_told_something_different_from_a_manufacturer():
    """The tool should say something different to a town than to a company,
    rather than saying nothing to the town at all."""
    snapshot, _ = _township_snapshot()
    town_result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(town_result, snapshot, snapshot["pages"])
    town = _finding(town_result, "documents-of-record-only-as-pdfs")

    maker_register = _register_page(
        MAKER, "compliance", "Compliance",
        ["Annual report", "Audit report, previous year"], 30)
    product = {
        "url": MAKER + "/valves/gate-40", "final_url": MAKER + "/valves/gate-40",
        "status": 200, "page_type": "product", "title": "Gate valve 40",
        "body_text": "p" * 900, "body_text_len": 900, "text_len": 1600,
        "chrome_signature": {"nav_paths": list(CHROME)},
    }
    maker_snapshot = _snapshot(MAKER, [product, maker_register])
    maker_result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(maker_result, maker_snapshot, maker_snapshot["pages"])
    maker = _finding(maker_result, "documents-of-record-only-as-pdfs")

    assert maker is not None, "the second branch has to run on a seller too"
    town_steps = " ".join(town["suggested_action"]["how_to_fix"]).lower()
    maker_steps = " ".join(maker["suggested_action"]["how_to_fix"]).lower()
    assert "signed copy of record" in town["suggested_action"]["summary"].lower()
    assert "resident" in town["suggested_action"]["rationale"].lower()
    assert "ordinance" in town_steps
    assert "resident" not in maker["suggested_action"]["rationale"].lower()
    assert "ordinance" not in maker_steps


def test_a_seller_hiding_a_datasheet_is_still_reported():
    """The branch that already worked, kept. Removing the commerce gate from
    the price claim would tell a museum its pricing facts are locked in its
    brochure, which was the reason the gate was written."""
    product = {
        "url": MAKER + "/valves/gate-40", "final_url": MAKER + "/valves/gate-40",
        "status": 200, "page_type": "product", "title": "Gate valve 40",
        "body_text": "The 40 mm gate valve, in bronze and in steel.",
        "body_text_len": 45, "text_len": 700,
        "headings": {"h2": ["Specification"], "h3": []},
        "pdf_links": [{"url": MAKER + "/files/gate-40.pdf",
                       "text": "Datasheet and dimensions"}],
        "chrome_signature": {"nav_paths": list(CHROME)},
    }
    snapshot = _snapshot(MAKER, [product])
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, snapshot["pages"])

    finding = _finding(result, "facts-locked-in-pdfs")
    assert finding is not None
    assert finding["affected_pages"] == [product["url"]]


def test_a_page_that_restates_its_documents_is_not_reported():
    """The escape hatch a site with no prices can actually reach.

    "The page states a price" is unreachable for a town, which is why the old
    gate had no way to be right about one. "The page says what the document
    says" is reachable by anybody, and it is the thing this check is asking
    for.
    """
    snapshot, _ = _township_snapshot(prose_chars=900)
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, snapshot["pages"])

    assert not result.findings, [f["title"] for f in result.findings]
    reason = _reason(result, "facts-locked-in-pdfs")
    assert "document of record" in reason


def test_the_link_labels_do_not_count_as_the_page_saying_something():
    """A register whose text *is* its list of links measures as a page with
    plenty of text on it until the labels are taken out."""
    register = _register_page(TOWNSHIP, "minutes", "Meeting records",
                              MINUTES_LABELS, 80)
    assert register["body_text_len"] > RENDER.PDF_INDEX_PROSE_FLOOR
    assert RENDER._prose_beside_the_links(register) < RENDER.PDF_INDEX_PROSE_FLOOR


def test_the_decline_says_both_branches_ran():
    """A site with no prices and no register still has to be told what was
    looked for, or "nothing is for sale" reads as the whole answer again."""
    home = {
        "url": TOWNSHIP + "/", "final_url": TOWNSHIP + "/", "status": 200,
        "page_type": "home", "title": "Millbrook Township",
        "body_text": "z" * 900, "body_text_len": 900, "text_len": 1600,
        "pdf_links": [{"url": TOWNSHIP + "/files/newsletter.pdf",
                       "text": "Spring newsletter"}],
        "headings": {"h2": [], "h3": []},
        "chrome_signature": {"nav_paths": list(CHROME)},
    }
    snapshot = _snapshot(TOWNSHIP, [home])
    result = SkillResult("render-readability-audit")
    RENDER._check_pdf_locked(result, snapshot, snapshot["pages"])

    reason = _reason(result, "facts-locked-in-pdfs")
    assert "nothing on this site is for sale" in reason
    assert "document of record" in reason
