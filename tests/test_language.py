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
