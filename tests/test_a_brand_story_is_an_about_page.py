# -*- coding: utf-8 -*-
"""A brand-story page is an about page, not an article waiting for a date.

A fashion retailer publishes its story at `/<region>/global/story`. It was typed
`article` from its address, so the freshness check asked it for a publication
date - a page about who the company is, graded as a post that had gone stale.

The slug has to be the whole segment. A blog post called
`story-behind-the-range` is a piece of writing, and it stays one.

Every host below is invented.
"""

from __future__ import annotations

import sys

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import detect_page_type  # noqa: E402

SHOP = "https://linen-and-thread.test"


def _typed(path):
    return detect_page_type(SHOP + path, {})


def test_a_regional_brand_story_is_an_about_page():
    assert _typed("/my/global/story") == "about"
    assert _typed("/brand-story") == "about"
    assert _typed("/our-brand/") == "about"


def test_a_blog_post_whose_slug_starts_with_story_is_not():
    assert _typed("/blogs/journal/story-behind-the-range") != "about"
