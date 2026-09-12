# -*- coding: utf-8 -*-
"""The robots.txt a publishing platform ships is read as that file.

A museum on Drupal serves Drupal's default robots.txt word for word, and the
report's confident first item was "robots.txt disallows 4 paths that look like
real content ... /includes/, /misc/, /modules/, /themes/" - the platform's code
directories. The names alone do not say "platform" the way `wp-` does, so they
are excused only where the file carries that platform's stock set.

Every host here is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from robots_parser import (  # noqa: E402
    benign_disallow, parse_robots, paths_under_route_words, stock_robots_platform,
    substantive_disallows,
)


def _module(name, alias):
    path = os.path.join(ROOT, "skills", name, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module("crawl-access-audit", "ca_for_stock_robots_tests")

# The disallow half of the default file the museum serves.
DRUPAL_DEFAULT = """User-agent: *
Crawl-delay: 10
Allow: /misc/*.css$
Allow: /modules/*.js$
Allow: /themes/*.png
Disallow: /includes/
Disallow: /misc/
Disallow: /modules/
Disallow: /profiles/
Disallow: /scripts/
Disallow: /themes/
Disallow: /CHANGELOG.txt
Disallow: /cron.php
Disallow: /INSTALL.txt
Disallow: /install.php
Disallow: /LICENSE.txt
Disallow: /update.php
Disallow: /xmlrpc.php
Disallow: /admin/
Disallow: /comment/reply/
Disallow: /filter/tips/
Disallow: /node/add/
Disallow: /search/
Disallow: /user/register/
Disallow: /user/password/
Disallow: /user/login/
Disallow: /?q=admin/
"""

JOOMLA_DEFAULT = "User-agent: *\n" + "".join(
    "Disallow: /{}/\n".format(d) for d in (
        "administrator", "bin", "cache", "cli", "components", "includes", "installation",
        "language", "layouts", "libraries", "logs", "modules", "plugins", "tmp"))


def _robots(text):
    parsed = parse_robots(text)
    parsed["status"] = 200
    return parsed


def test_the_drupal_default_file_is_recognised_and_closes_no_content():
    robots = _robots(DRUPAL_DEFAULT)
    rules = robots["groups"][0]["disallow"]
    assert stock_robots_platform(rules) == "Drupal"
    assert substantive_disallows(robots, "*") == []
    result = SkillResult("crawl-access-audit")
    CA._check_robots_blocks(result, robots, "https://museum.test", [])
    assert not [f for f in result.findings if f["id_hint"] == "robots-blocks-content-paths"]


def test_the_joomla_default_file_is_recognised_and_closes_no_content():
    robots = _robots(JOOMLA_DEFAULT)
    assert stock_robots_platform(robots["groups"][0]["disallow"]) == "Joomla"
    assert substantive_disallows(robots, "*") == []


def test_a_site_that_named_its_own_sections_like_that_is_still_reported():
    """A training company's `/modules/` and an electronics shop's `/components/`
    are their catalogues; without the rest of the stock file they are content."""
    robots = _robots("User-agent: *\nDisallow: /modules/\nDisallow: /components/\n"
                     "Disallow: /themes/\n")
    assert stock_robots_platform(robots["groups"][0]["disallow"]) == ""
    assert substantive_disallows(robots, "*") == ["/components/", "/modules/", "/themes/"]


@pytest.mark.parametrize("rule", ["/sites/", "/sites/default/files/"])
def test_a_drupal_sites_uploaded_files_are_never_excused(rule):
    siblings = parse_robots(DRUPAL_DEFAULT)["groups"][0]["disallow"] + [rule]
    assert benign_disallow(rule, siblings) is False


@pytest.mark.parametrize("rule,seen,benign", [
    # A rule is a prefix, so `/home` closes a homeware shop's `/home-decor`.
    ("/home", ["/home-decor/rugs"], False),
    ("/home/", ["/home/garden"], False),
    ("/daftar", ["/daftar-harga"], False),
    # Anchored, the rule closes its own path and nothing longer.
    ("/home$", ["/home-decor/rugs"], True),
    # The front page's other addresses, and an account's own sub-pages, are
    # the same route.
    ("/home", ["/home", "/home/", "/homepage"], True),
    ("/cuenta", ["/cuenta/pedidos"], True),
    # Nothing seen behind it: the word is all there is to read.
    ("/home", [], True),
])
def test_a_route_word_is_excused_only_where_the_rule_stops_at_it(rule, seen, benign):
    assert benign_disallow(rule, seen_paths=seen) is benign


def test_a_home_rule_over_a_real_section_is_reported_from_the_crawl():
    robots = _robots("User-agent: *\nDisallow: /home\nDisallow: /cuenta\n")
    robots["paths_under_route_words"] = paths_under_route_words(
        robots, ["https://homeware.test/home-decor/rugs",
                 "https://homeware.test/cuenta/pedidos",
                 "https://homeware.test/homepage"])
    assert robots["paths_under_route_words"] == ["/home-decor/rugs"]
    assert substantive_disallows(robots, "*") == ["/home"]
