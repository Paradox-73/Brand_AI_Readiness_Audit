# -*- coding: utf-8 -*-
"""A site's furniture is not the page, and one page is not "1 page ... carry".

Four defects found in one validation pass over eleven real sites.

  `image-locked-facts`   A tea grower's news index was reported as "388 chars
                         of text beside 41 image(s), 24 of which carry no alt
                         text describing them". All 41 are the site-wide drawer
                         menu's category thumbnails, on every page including
                         the homepage; the page's own content is post titles
                         and dates, which are text. The 24 are `alt=""`, the
                         correct marking for a decorative picture and a
                         decision rather than an omission. The sibling check
                         `thin-html` already subtracts the template's furniture
                         from the text before calling a page short; this one
                         subtracted nothing from the picture count.

  `readability`          The words-between-subheadings ratio was measured by
                         the extractor, before the crawl removed the blocks
                         that repeat site-wide, while the word count gating it
                         was measured after. So a page could be reported for a
                         density measured on its account menu and its category
                         nav.

  report grammar         "To reach 10: fix `1 page leads with audio or video
                         and carry little readable text`" - a singular subject
                         with a plural verb, in a published report, because the
                         count was spliced into a clause written for several
                         pages.

  wallpaper video        A `<video>` the visitor can play, pause or hear is
                         content whatever else its markup says. The background
                         loop rule read `autoplay`, `muted` and `loop` and
                         never asked whether the element offered controls.

The second half of each part is the guard in the other direction: a page with
pictures of its own is still reported, a genuinely long page with no
subheadings is still reported, and a plain content video still produces the
finding it was written for.

Every host in this file is invented and every brand is fictional.
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


RENDER = _module("render-readability-audit", "render_for_chrome_tests")
ENGAGEMENT = _module("engagement-audit", "engagement_for_chrome_tests")

GROWER = "https://a-fictional-tea-grower.example"
CHROME = ["/", "/shop", "/news", "/about", "/contact"]

# What the drawer menu puts on every page of this invented site: one thumbnail
# per product category, each marked `alt=""` because the category name is
# already the link text beside it.
DRAWER_IMAGES = 41


def _reason(result, check):
    return next(n["reason"] for n in result.not_applicable if n["check"] == check)


def _finding(result, id_hint):
    return next((f for f in result.findings if f["id_hint"] == id_hint), None)


def _images(count, described=0, undescribed=0):
    return {"count": count, "described_count": described,
            "missing_alt_count": undescribed, "undescribed_count": undescribed,
            "undescribed_sample": ["{}/img/{}.jpg".format(GROWER, n)
                                   for n in range(min(undescribed, 20))],
            "large_image_count": 0, "svg_text_nodes": 0, "canvas_count": 0}


def _page(slug, images, body_chars, page_type="content"):
    url = "{}/{}".format(GROWER, slug)
    return {
        "url": url, "final_url": url, "status": 200, "page_type": page_type,
        "title": slug.replace("/", " ").strip() or "Home",
        "body_text": "t" * body_chars, "body_text_len": body_chars,
        "text_len": body_chars + 900,
        "images": images,
        "chrome_signature": {"nav_paths": list(CHROME)},
    }


def _site(news_images, news_body=388):
    """Seven ordinary pages of one template, plus the news index under test."""
    pages = [_page("shop/item-{}".format(n), _images(DRAWER_IMAGES), 1200)
             for n in range(7)]
    pages.append(_page("category/news/page/2", news_images, news_body))
    return pages


# --------------------------------------------------------------------------
# The drawer menu is not this page's pictures
# --------------------------------------------------------------------------

def test_a_page_whose_only_images_are_the_template_is_not_image_heavy():
    """The tea grower's news index, at the check that reported it."""
    pages = _site(_images(DRAWER_IMAGES, described=17, undescribed=24))
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])

    assert not result.findings, [f["title"] for f in result.findings]
    reason = _reason(result, "facts-locked-in-images")
    assert "every page it builds" in reason
    assert pages[-1]["url"] in reason


def test_the_template_subtraction_matches_the_thin_text_check():
    """`thin-html` measures the template's furniture per nav fingerprint and by
    the median. So does this, or the two disagree about the same page."""
    pages = _site(_images(DRAWER_IMAGES))
    by_template, site_wide = RENDER._template_image_count(pages)
    assert site_wide == DRAWER_IMAGES
    assert by_template[RENDER._chrome_key(pages[0])] == DRAWER_IMAGES
    assert RENDER._images_of_its_own(pages[-1], by_template, site_wide) == 0


def test_a_page_with_pictures_of_its_own_is_still_reported():
    """The guard in the other direction: subtracting the template must not
    delete the finding on the page it was written for."""
    own = _images(DRAWER_IMAGES + 6, described=17, undescribed=30)
    pages = _site(own, news_body=120)
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])

    finding = _finding(result, "facts-locked-in-images")
    assert finding is not None, [n["reason"] for n in result.not_applicable]
    assert finding["affected_pages"] == [pages[-1]["url"]]
    assert "6 images of its own" in finding["evidence"], finding["evidence"]


def test_an_explicit_empty_alt_is_not_counted_as_a_missing_description():
    """`alt=""` is a text alternative deliberately left empty. Reporting it as
    an image that "carries no alt text describing it" tells an author to undo a
    decision they made correctly."""
    decorative = _images(DRAWER_IMAGES + 6, described=0, undescribed=0)
    pages = _site(decorative, news_body=120)
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])

    finding = _finding(result, "facts-locked-in-images")
    assert finding is not None
    assert "no alt text describing them" not in finding["evidence"]
    assert "marked alt=\"\"" in finding["evidence"], finding["evidence"]


def test_a_price_table_rendered_as_a_picture_is_reported_on_every_page():
    """The template subtraction belongs to the "five or more pictures" branch
    and not to the large-picture one. A site that renders its price table as a
    900x700 PNG and puts it on every page has put the same unreadable fact on
    every page, and subtracting repetition there would delete the finding
    rather than sharpen it."""
    priced = _images(DRAWER_IMAGES + 2)
    priced["large_image_count"] = 2
    pages = [_page("shop/item-{}".format(n), dict(priced), 120) for n in range(8)]
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])

    finding = _finding(result, "facts-locked-in-images")
    assert finding is not None, [n["reason"] for n in result.not_applicable]
    assert "2 images big enough" in finding["evidence"], finding["evidence"]


def test_a_crawl_too_small_to_measure_repetition_counts_what_it_sees():
    """Two pages have no repetition to read. Guessing there would be worse
    than counting, so the check behaves as it did."""
    pages = [_page("only", _images(9, described=1, undescribed=8), 100)]
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])
    assert _finding(result, "facts-locked-in-images") is not None


# --------------------------------------------------------------------------
# A player the visitor can operate is not wallpaper
# --------------------------------------------------------------------------

def _video(**kwargs):
    record = {"native_count": 0, "embed_count": 0, "audio_count": 0,
              "autoplay_count": 0, "intrusive_autoplay_count": 0,
              "track_count": 0, "transcript_nearby": False}
    record.update(kwargs)
    return record


def test_a_video_with_controls_is_content_however_it_autoplays():
    """`autoplay muted loop controls` offers a play button, a pause button and
    a volume control, so there may well be something in it to write down."""
    operable = _video(native_count=1, autoplay_count=1, intrusive_autoplay_count=0,
                      controls_count=1)
    assert RENDER._background_loop_count(operable) == 0
    assert RENDER._players_that_could_carry_content(operable) == 1


def test_the_same_clip_without_controls_is_still_wallpaper():
    """The earlier rule, kept: this is the shape that produced
    "12 of 21 pages lead with video" about one silent hero loop."""
    wallpaper = _video(native_count=1, autoplay_count=1, intrusive_autoplay_count=0,
                       controls_count=0)
    assert RENDER._background_loop_count(wallpaper) == 1
    assert RENDER._players_that_could_carry_content(wallpaper) == 0


def test_a_snapshot_with_no_controls_count_behaves_as_it_did():
    """The crawl may not record the attribute yet. A missing key leaves the cap
    off rather than guessing an answer."""
    older = _video(native_count=1, autoplay_count=1, intrusive_autoplay_count=0)
    assert "controls_count" not in older
    assert RENDER._background_loop_count(older) == 1


# --------------------------------------------------------------------------
# One page is "1 page ... carries"
# --------------------------------------------------------------------------

def _media_page(slug, video, body_chars=180):
    page = _page(slug, _images(0), body_chars)
    page["video"] = video
    return page


def test_the_video_title_reads_as_english_at_one_page():
    """The sentence a real report printed: "1 page leads with audio or video and
    carry little readable text"."""
    pages = [_media_page("tour", _video(native_count=1))]
    result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(result, pages)
    finding = _finding(result, "video-without-transcript")

    assert finding["title"] == (
        "1 page leads with audio or video and carries little readable text")


def test_the_video_title_still_reads_as_english_at_several_pages():
    pages = [_media_page("tour-{}".format(n), _video(native_count=1))
             for n in range(3)]
    result = SkillResult("render-readability-audit")
    RENDER._check_video_transcripts(result, pages)

    assert _finding(result, "video-without-transcript")["title"] == (
        "3 pages lead with audio or video and carry little readable text")


def test_the_image_title_reads_as_english_at_one_page():
    pages = [_page("poster", _images(9, described=1, undescribed=8), 100)]
    result = SkillResult("render-readability-audit")
    RENDER._check_image_locked(result, pages, [])

    assert _finding(result, "facts-locked-in-images")["title"] == (
        "1 page carries its main content in images rather than text")


def test_the_iframe_title_reads_as_english_at_one_page():
    page = _page("booking", _images(0), 40)
    page["iframes"] = [{"src": "https://a-made-up-booking-widget.test/embed",
                        "is_video": False, "is_map": False, "is_invisible": False}]
    result = SkillResult("render-readability-audit")
    RENDER._check_iframed_content(result, [page], [])

    assert _finding(result, "main-content-in-iframe")["title"] == (
        "1 page holds its main content inside an iframe")


def test_the_pagination_title_reads_as_english_at_one_listing():
    page = _page("shop/all-teas", _images(0), 900, page_type="category")
    page["body_text"] = "Every tea we grow. Load more"
    page["links"] = {"internal": [{"url": GROWER + "/shop/sencha", "text": "Sencha"}],
                     "external": [], "internal_count": 1}
    result = SkillResult("render-readability-audit")
    RENDER._check_pagination(result, {"pages": [page]}, [page])

    assert _finding(result, "load-more-without-crawlable-pagination")["title"] == (
        "1 listing page hides its catalogue behind a load-more button")


def test_the_viewport_title_reads_as_english_at_one_page():
    page = _page("contact", _images(0), 500)
    page["has_viewport"] = False
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_viewport(result, [page])

    assert _finding(result, "missing-mobile-viewport")["title"] == (
        "1 page has no mobile viewport tag in the HTML it delivers")


# --------------------------------------------------------------------------
# Readability is measured on the page's own prose
# --------------------------------------------------------------------------

def _prose_page(slug, words, subheadings, pre_strip_words, walls=0, paragraphs=None):
    """A page as the snapshot carries it after the site-furniture pass.

    `word_count` is what the crawl recomputed once the repeated blocks came
    out; `readability` is what the extractor measured before they did, which is
    the whole point - the two describe different text.
    """
    url = "{}/{}".format(GROWER, slug)
    page = {
        "url": url, "final_url": url, "status": 200, "page_type": "article",
        "title": slug, "body_text": "word " * words, "body_text_len": words * 5,
        "word_count": words,
        "headings": {"h1": ["A heading"], "h2": ["Another"]},
        "links": {"internal": [], "external": []},
        "readability": {
            "wall_of_text_count": walls,
            "subheading_count": subheadings,
            "words_per_subheading": (round(pre_strip_words / subheadings, 1)
                                     if subheadings else float(pre_strip_words)),
            "list_text_share": 0.0, "avg_sentence_words": 18.0,
        },
    }
    if paragraphs is not None:
        page["paragraphs"] = paragraphs
    return page


def test_the_subheading_ratio_is_taken_after_the_menus_come_out():
    """3,600 words of account menu and category nav divided by two subheadings
    is 1,800; the page's own 800 words over the same two is 400."""
    page = _prose_page("blog", words=800, subheadings=2, pre_strip_words=3600)
    assert page["readability"]["words_per_subheading"] == 1800.0
    assert ENGAGEMENT._words_between_subheadings(page) == 400.0


def test_a_page_whose_density_was_the_navigation_is_not_reported():
    page = _prose_page("about/blog", words=800, subheadings=4, pre_strip_words=3600)
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_readability(result, [page])
    assert not result.findings, [f["evidence"] for f in result.findings]


def test_a_page_that_really_runs_without_subheadings_is_still_reported():
    """The guard in the other direction, which is the half that matters."""
    page = _prose_page("essay", words=1400, subheadings=1, pre_strip_words=1400)
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_readability(result, [page])

    finding = _finding(result, "pages-are-hard-to-skim")
    assert finding is not None
    assert "1400 words with a subheading only every 1400 words" in finding["evidence"]


def test_walls_of_text_the_furniture_pass_removed_are_not_this_page_s():
    """A long footer blurb on every page of a site is one edit to a template,
    not two walls of text on each page it appears on."""
    stripped = _prose_page("news", words=200, subheadings=2, pre_strip_words=200,
                           walls=3, paragraphs=["a short line of its own"])
    assert ENGAGEMENT._walls_that_survived_the_furniture_strip(stripped) == 0

    survived = _prose_page("essay", words=900, subheadings=2, pre_strip_words=900,
                           walls=2, paragraphs=["p" * 600, "q" * 600, "r" * 50])
    assert ENGAGEMENT._walls_that_survived_the_furniture_strip(survived) == 2


def test_a_snapshot_with_no_paragraph_list_keeps_the_extractor_s_count():
    """Nothing to compare against is not evidence that the walls were chrome."""
    page = _prose_page("essay", words=900, subheadings=2, pre_strip_words=900, walls=2)
    assert "paragraphs" not in page
    assert ENGAGEMENT._walls_that_survived_the_furniture_strip(page) == 2
