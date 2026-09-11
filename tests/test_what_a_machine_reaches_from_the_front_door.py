# -*- coding: utf-8 -*-
"""The front page led nowhere and no check said so.

A client-rendered storefront's delivered homepage carries two `href`s - both to
a font host - plus one `href=""`. The report named the shell and stopped there.
What it never said is the single most actionable sentence available about this
site: a machine that arrives at the front page and follows links reads one page
and stops. The nine pages that run read all came from the sitemap, against
5,000 URLs listed in it.

So this file pins the sentence and, just as hard, pins the four shapes it must
stay silent about - a site with links, a site that is one page, a menu built in
a way the anchor loop cannot read, and a link that only appears to lead
somewhere.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGAGEMENT = _module("engagement-audit", "engagement_for_front_door_tests")

SITE = "https://an-invented-host.test"
HOME = SITE + "/"
CHECK = "homepage-links-into-the-site"


def _home(internal=(), nav=(), footer=(), chrome=0, **extra):
    page = {
        "url": HOME, "final_url": HOME, "status": 200, "page_type": "home",
        "source": "homepage", "text_len": 4000,
        "links": {"internal": [dict(entry) for entry in internal],
                  "external": [], "nav": list(nav), "footer": list(footer)},
        "chrome_signature": {"nav_paths": [], "nav_path_count": chrome},
    }
    page.update(extra)
    return page


def _other(path, source="sitemap"):
    return {"url": SITE + path, "final_url": SITE + path, "status": 200,
            "page_type": "other", "source": source, "text_len": 900,
            "links": {"internal": []}}


def _snapshot(pages, sitemap_urls=(), sitemap_count=None):
    sitemaps = []
    if sitemap_urls:
        sitemaps = [{"url": SITE + "/sitemap.xml", "status": 200,
                     "url_count": sitemap_count or len(sitemap_urls),
                     "urls": [{"loc": SITE + path} for path in sitemap_urls]}]
    return {"origin": SITE, "pages": list(pages), "sitemaps": sitemaps,
            "crawl": {"render_mode": "static"}}


def _reason(result):
    return " ".join(entry["reason"] for entry in result.not_applicable
                    if entry["check"] == CHECK)


def _run(snapshot, home):
    result = SkillResult("engagement-audit")
    ENGAGEMENT._check_front_door(result, snapshot, home)
    return result


# --------------------------------------------------------------------------
# The shape seen in the wild
# --------------------------------------------------------------------------

def test_a_homepage_whose_only_hrefs_leave_the_site_reaches_nothing():
    """Two hrefs to a font host and one `href=""`. The font host is not a page
    of this site and the empty href resolves to the page the reader is already
    on, so the count of addresses a machine can reach from here is zero."""
    home = _home(internal=[{"url": HOME, "text": "", "fragment": ""}],
                 text_len=40, spa_shell={"root_selector": "#root"})
    home["links"]["external"] = [{"url": "https://fonts.invented-cdn.test/a.css"},
                                 {"url": "https://fonts.invented-cdn.test/b.css"}]
    pages = [home] + [_other("/p{}".format(n)) for n in range(8)]
    result = _run(_snapshot(pages, ["/p{}".format(n) for n in range(8)], 5000), home)

    finding = next(f for f in result.findings
                   if f["id_hint"] == "homepage-reaches-no-other-page")
    assert finding["severity"] == "high"
    assert "reads this one page and stops" in finding["evidence"]


def test_the_finding_says_how_the_rest_of_the_crawl_was_actually_found():
    """The consequence is measured, not predicted: `source` records how each
    URL entered the crawl, and on this site not one page was reached by
    following a link."""
    home = _home(internal=[{"url": HOME}])
    pages = [home] + [_other("/p{}".format(n)) for n in range(8)]
    result = _run(_snapshot(pages, ["/p{}".format(n) for n in range(8)], 5000), home)

    evidence = result.findings[0]["evidence"]
    assert "0 pages were reached by following a link from another page" in evidence
    assert "5,000 URLs" in evidence


def test_a_site_with_no_sitemap_either_is_told_so():
    """The worse case, and the one the reader has to be given: nothing on the
    site names another page of it to a machine at all."""
    home = _home(internal=[])
    result = _run(_snapshot([home, _other("/p1")]), home)
    assert "No sitemap answered either" in result.findings[0]["evidence"]


def test_the_shell_is_named_as_the_cause_rather_than_reported_twice():
    """A real report emitted one root cause as several unrelated work items. Where the front page delivered a framework mount
    point, the finding says so and points at the shell finding instead of
    prescribing a second, separate content fix."""
    home = _home(internal=[], text_len=40, spa_shell={"root_selector": "#root"})
    result = _run(_snapshot([home, _other("/p1")]), home)
    assert "this is what the shell costs" in result.findings[0]["evidence"]


def test_it_fires_on_a_site_that_is_not_a_shell_at_all():
    """The defect survives server-rendered HTML: a homepage whose navigation
    is assembled by JavaScript from a payload delivers the same zero links to
    the same crawler, and every other check in the skill passes on it."""
    home = _home(internal=[], text_len=6000)
    result = _run(_snapshot([home, _other("/p1")]), home)
    assert result.findings, "a delivered page with no links still reaches nothing"


def test_the_whole_skill_still_asks_when_every_page_is_a_stub():
    """Every other check here asks what a page lacks, so a stub is set aside
    from all of them and, where every page is a stub, the skill declines
    outright - which is the site this defect was found on. This check has to
    run before that, over the pages as crawled."""
    home = _home(internal=[], text_len=40, spa_shell={"root_selector": "#root"})
    stub = _other("/p1")
    stub.update(text_len=40, spa_shell={"root_selector": "#root"})
    result = ENGAGEMENT.run(_snapshot([home, stub]), allow_network=False)
    assert CHECK in result.checks_run
    assert any(f["id_hint"] == "homepage-reaches-no-other-page" for f in result.findings)


# --------------------------------------------------------------------------
# Where it must stay quiet
# --------------------------------------------------------------------------

def test_a_homepage_that_links_into_the_site_is_left_alone():
    home = _home(internal=[{"url": HOME}, {"url": SITE + "/about"},
                           {"url": SITE + "/shop"}], chrome=3)
    result = _run(_snapshot([home, _other("/about")]), home)
    assert not result.findings
    assert "somewhere to go" in _reason(result)


def test_a_one_page_site_is_not_told_its_front_door_leads_nowhere():
    """A one-pager has nowhere onward to link, and saying so tells it only that
    it is a one-pager. The count is over the crawl and the sitemap together, so
    a second page the crawl never reached still stops this."""
    home = _home(internal=[{"url": HOME}])
    result = _run(_snapshot([home]), home)
    assert not result.findings
    assert "no other address to name" in _reason(result)


def test_a_menu_this_audit_cannot_read_is_declined_rather_than_published():
    """The second reading is the guard. `chrome_signature` sweeps <nav>,
    <header>, <footer> and the ARIA roles through a different code path in the
    extractor; where that holds destinations and the anchor loop holds none,
    the disagreement is about this audit and not about the site."""
    home = _home(internal=[], nav=[{"url": SITE + "/a"}, {"url": SITE + "/b"}])
    result = _run(_snapshot([home, _other("/a")]), home)
    assert not result.findings
    assert "built in a way this check cannot read" in _reason(result)


def test_a_link_whose_destination_is_only_in_its_fragment_reaches_nothing():
    """The email-obfuscation shape: the anchor names a handler path and carries
    the real destination in its fragment, so following the path it appears to
    name reaches something else. The dead-end check already sets these aside
    and this one has to agree, or the same page is a dead end to one check and
    a working front door to the other."""
    home = _home(internal=[{"url": SITE + "/cdn-cgi/l/email-protection",
                            "fragment": "0e6f7d"}])
    result = _run(_snapshot([home, _other("/p1")]), home)
    assert result.findings, "an obfuscated-email anchor is not a route to a page"


def test_the_same_path_linked_plainly_somewhere_on_the_page_is_a_route():
    """A page that links `/docs` plainly and also links `/docs#install` has a
    route to `/docs`. Only the addresses nothing links plainly are set aside."""
    home = _home(internal=[{"url": SITE + "/docs", "fragment": ""},
                           {"url": SITE + "/docs", "fragment": "install"}])
    result = _run(_snapshot([home, _other("/docs")]), home)
    assert not result.findings


def test_a_crawl_that_never_reached_the_homepage_says_that_instead():
    result = _run(_snapshot([_other("/p1")]), None)
    assert not result.findings
    assert "was not reached by this crawl" in _reason(result)
