"""Three failures from real reports, all of them about who a site is.

Two are about the name the audit adopts and print into a paste-ready snippet,
and one is about the class it puts the site in and the advice that follows from
that. All three are decided in `audit_common.py`, before any skill reads the
snapshot, which is why they are tested against that module rather than against
a report.

The sites are invented. `truefarm.test` stands for the vegetable farm whose
theme published an empty brand slot, `tanloom.test` for the handbag brand whose
theme escaped its own name twice, and `ashcombelibrary.test` for the town
library that was classified as a researcher.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    PERSONAL_OR_ACADEMIC, PUBLIC_BODY, UNDETERMINED,
    as_published, is_a_broken_title_template, load_snapshot,
    names_a_civic_institution, resolve_brand, site_kind,
    the_brand_slot_is_empty, the_site_spells, why_this_is_not_a_name,
)


def _page(url, page_type="other", title="", h1=(), h2=(), text="", og=None,
          jsonld=(), jsonld_types=(), internal=()):
    return {
        "url": url,
        "page_type": page_type,
        "status": 200,
        "title": title,
        "headings": {"h1": list(h1), "h2": list(h2), "h3": []},
        "body_text": text,
        "og": og or {},
        "jsonld": list(jsonld),
        "jsonld_types": list(jsonld_types),
        "links": {"internal": [{"url": u} for u in internal], "external": []},
    }


# --------------------------------------------------------------------------
# 1. A template with an empty brand slot is not a name
# --------------------------------------------------------------------------

def _farm():
    """The vegetable farm. Its theme means `<brand> - <tagline>` and its brand
    slot is empty, so `og:site_name` is "- Empowering communities" on every
    page. Everything needed to reject that is on the page."""
    site_name = "- Empowering communities"
    pages = [
        _page("https://truefarm.test/", "home",
              title="Truefarm - Empowering communities",
              h1=["Fresh from the field"],
              h2=["Welcome to True farm"],
              text="At Truefarm.in, we bring you fresh, chemical-free vegetables "
                   "directly from our farm to your table. Empowering communities "
                   "across the district.",
              og={"og:site_name": site_name}),
        _page("https://truefarm.test/about", "about",
              title="About - Empowering communities",
              og={"og:site_name": site_name}),
    ]
    return {
        "origin": "https://truefarm.test",
        "site": "truefarm.test",
        "pages": pages,
        "brand": {
            # What `crawl.py` records: it strips the leading separator as stray
            # punctuation, so the tagline arrives already looking like a name.
            "name": "Empowering communities",
            "source": "og:site_name",
            "authoritative_variants": ["Empowering communities"],
            "authoritative_sources": ["og:site_name"],
            "alternate_names": [],
            "fallback_candidates": [],
            "name_candidates": [],
            "declared_on": {"Empowering communities": ["https://truefarm.test/"]},
            "declared_by": {"Empowering communities": ["og:site_name"]},
            "host": "truefarm.test",
            "domain_token": "truefarm",
            "pages_seen": 2,
        },
    }


def test_a_leading_separator_is_an_empty_slot_and_not_punctuation():
    assert is_a_broken_title_template("- Empowering communities")
    assert is_a_broken_title_template("| Empowering communities")
    assert is_a_broken_title_template("Empowering communities -")
    assert is_a_broken_title_template("Acme |  | Home")
    assert is_a_broken_title_template(": Empowering communities")


def test_ordinary_names_and_titles_are_not_empty_slots():
    """The rule must not fire on punctuation a name actually contains. Two of
    these have cost this repository a finding before: splitting "charity:
    shelter" cut a charity's name to one word, and splitting "Coca-Cola" on its
    hyphen invented two names."""
    for value in ("charity: shelter", "Ben & Jerry's", "Coca-Cola",
                  "Acme | Home", "e-commerce for all", "Zingerman's"):
        assert not is_a_broken_title_template(value), value


def test_only_the_leading_separator_says_the_brand_slot_is_the_empty_one():
    """Which slot is empty decides whether the name is wrong. `"- Tagline"` has
    lost its brand; `"Acme -"` has lost its tagline and the name it leaves is
    correct, so rejecting it would throw a good name away for its neighbour's
    fault."""
    assert the_brand_slot_is_empty("- Empowering communities")
    assert not the_brand_slot_is_empty("Empowering communities -")
    assert not the_brand_slot_is_empty("Truefarm Organics")


def test_a_name_from_a_template_that_lost_its_tagline_is_kept():
    snapshot = _farm()
    snapshot["brand"]["name"] = "Truefarm Organics"
    snapshot["brand"]["authoritative_variants"] = ["Truefarm Organics"]
    snapshot["brand"]["declared_on"] = {}
    snapshot["brand"]["declared_by"] = {}
    for page in snapshot["pages"]:
        page["og"]["og:site_name"] = "Truefarm Organics -"
    assert resolve_brand(snapshot)["name"] == "Truefarm Organics"


def test_the_tagline_is_rejected_even_though_the_page_prints_it():
    """The whole point of reading the raw declaration. "Empowering communities"
    appears in the farm's body text, so a test that only asked whether the site
    spells the string would have adopted it."""
    farm = _farm()
    why = why_this_is_not_a_name("Empowering communities", farm)
    assert "empty brand slot" in why
    assert the_site_spells("Empowering communities",
                           [p["body_text"] for p in farm["pages"]])


def test_the_farm_is_named_for_what_its_own_pages_spell():
    """The failure in full: the tagline was the company's name in the verdict
    paragraph, in four fix templates, in a knowledge-base lookup that returned
    two unrelated entities for the phrase, and in the `name` and `description`
    of a paste-ready Organization block."""
    brand = resolve_brand(_farm())
    assert brand["name"] != "Empowering communities"
    # `truefarm` is what the address spells and "True farm" is how the H2 spells
    # it, so the name the report prints is one the site itself writes.
    assert brand["name"] in ("True farm", "Truefarm")
    assert brand["rejected_names"][0]["name"] == "Empowering communities"
    assert "empty brand slot" in brand["rejected_names"][0]["why"]


def test_the_rejected_string_is_gone_from_every_list_that_names_the_site():
    """Left in `authoritative_variants` it is still a second name asserted as
    the site's identity, and the naming-consistency check would report the
    disagreement between a name and the thing just ruled not to be one."""
    brand = resolve_brand(_farm())
    assert "Empowering communities" not in brand["authoritative_variants"]
    assert "Empowering communities" not in brand["declared_on"]
    assert "Empowering communities" not in brand["declared_by"]


def test_a_name_no_part_of_the_site_spells_is_rejected():
    """The second rule, on its own: a name declared nowhere the site writes."""
    snapshot = _farm()
    snapshot["brand"]["name"] = "Coastal Freight Holdings"
    snapshot["brand"]["authoritative_variants"] = ["Coastal Freight Holdings"]
    snapshot["brand"]["declared_by"] = {
        "Coastal Freight Holdings": ["jsonld:Organization-off-home"]}
    snapshot["brand"]["declared_on"] = {}
    why = why_this_is_not_a_name("Coastal Freight Holdings", snapshot)
    assert "spells it" in why
    assert resolve_brand(snapshot)["name"] != "Coastal Freight Holdings"


def test_a_good_name_is_left_exactly_as_the_site_declared_it():
    """The safety property. A site that declares a real name must come through
    this untouched, including the punctuation inside it."""
    snapshot = _farm()
    snapshot["brand"]["name"] = "Truefarm Organics"
    snapshot["brand"]["authoritative_variants"] = ["Truefarm Organics"]
    snapshot["brand"]["declared_on"] = {
        "Truefarm Organics": ["https://truefarm.test/"]}
    snapshot["brand"]["declared_by"] = {"Truefarm Organics": ["og:site_name"]}
    snapshot["pages"][0]["og"]["og:site_name"] = "Truefarm Organics"
    snapshot["pages"][1]["og"]["og:site_name"] = "Truefarm Organics"
    brand = resolve_brand(snapshot)
    assert brand["name"] == "Truefarm Organics"
    assert brand["source"] == "og:site_name"
    assert brand["rejected_names"] == []


# --------------------------------------------------------------------------
# 2. An HTML-escaped name is a defect to report, not an identity to adopt
# --------------------------------------------------------------------------

def _handbags():
    """The handbag brand. Its theme escapes its own values twice, so
    `og:site_name` is `Tan&amp;amp;Loom` and its JSON-LD `name` is
    `Tan\\u0026amp;Loom`. Both are the string `Tan&Loom`."""
    escaped = "Tan&amp;Loom"        # what the record holds after HTML parsing
    return {
        "origin": "https://tanloom.test",
        "site": "tanloom.test",
        "pages": [
            _page("https://tanloom.test/", "home", title=escaped, h1=[escaped],
                  text="Handmade leather bags, cut and stitched to order.",
                  og={"og:site_name": escaped},
                  jsonld=[{"@type": "Organization", "name": escaped}]),
            _page("https://tanloom.test/about", "about",
                  jsonld=[{"@type": "Organization", "name": "Tan&Loom"}]),
        ],
        "brand": {
            "name": escaped,
            "source": "og:site_name",
            "authoritative_variants": ["Tan&Loom", escaped],
            "authoritative_sources": ["jsonld:Organization", "og:site_name"],
            "alternate_names": [],
            "fallback_candidates": [],
            "name_candidates": [{"name": escaped, "confidence": "high"}],
            "declared_on": {"Tan&Loom": ["https://tanloom.test/about"],
                            escaped: ["https://tanloom.test/"]},
            "declared_by": {"Tan&Loom": ["jsonld:Organization"],
                            escaped: ["og:site_name"]},
            "host": "tanloom.test",
            "domain_token": "tanloom",
            "pages_seen": 2,
        },
    }


def test_a_double_escaped_name_decodes_to_the_name_the_site_meant():
    assert as_published("Tan&amp;amp;Loom") == "Tan&Loom"
    assert as_published("Tan&amp;Loom") == "Tan&Loom"
    assert as_published("<brand>&#39;s Wholefoods") == "<brand>'s Wholefoods"


def test_a_reference_without_its_semicolon_is_left_alone():
    """`html.unescape` decodes semicolon-less legacy references, which turns
    the name "Salt&notes" into "Salt¬tes" - a corruption the decoder
    invented."""
    assert as_published("Salt&notes") == "Salt&notes"
    assert as_published("Bed&Breakfast") == "Bed&Breakfast"


def test_the_escaped_spelling_never_becomes_the_brand():
    """The failure in full: the verdict read "A machine can reach
    Tan&amp;amp;Loom and read it", and `<h1>Tan&amp;amp;Loom</h1>` was the
    report's only paste-ready code block."""
    brand = resolve_brand(_handbags())
    assert brand["name"] == "Tan&Loom"
    assert "Tan&amp;Loom" not in json.dumps(brand)


def test_one_name_written_twice_stops_being_two_names():
    """The medium finding a real report printed: "2 distinct names are asserted as the
    site's identity: "Tan&Loom", "Tan&amp;Loom"" - against a site with one
    name, whose fix would have published the escaping as an alternateName."""
    brand = resolve_brand(_handbags())
    assert brand["authoritative_variants"] == ["Tan&Loom"]
    assert sorted(brand["declared_on"]["Tan&Loom"]) == [
        "https://tanloom.test/", "https://tanloom.test/about"]
    assert brand["declared_by"]["Tan&Loom"] == ["jsonld:Organization",
                                                "og:site_name"]


def test_the_escaping_is_still_there_to_be_reported():
    """Decoding the name must not delete the evidence of the template bug.
    `structured-data-audit` reads the page records undecoded, so those have to
    come through untouched."""
    snapshot = _handbags()
    resolve_brand(snapshot)
    assert snapshot["pages"][0]["jsonld"][0]["name"] == "Tan&amp;Loom"
    assert snapshot["pages"][0]["og"]["og:site_name"] == "Tan&amp;Loom"


def test_the_check_runs_where_every_skill_reads_the_crawl():
    """Six skills and the report reader all reach a snapshot through
    `load_snapshot`, and each of the two failures reached the report through
    four separate checks. One name, decided once."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "snapshot.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_handbags(), handle)
        loaded = load_snapshot(path)
        # The file on disk is what the crawl observed and stays that way: only
        # what the audit adopts changes.
        with open(path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
    assert loaded["brand"]["name"] == "Tan&Loom"
    assert on_disk["brand"]["name"] == "Tan&amp;Loom"


def test_a_snapshot_with_no_brand_block_still_loads():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "snapshot.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"origin": "https://quiet.test", "pages": []}, handle)
        assert load_snapshot(path)["pages"] == []


# --------------------------------------------------------------------------
# 3. A town library is a public body, whatever its address ends in
# --------------------------------------------------------------------------

def _library(host="ashcombelibrary.test", name="Ashcombe Public Library",
             pages=None):
    return {
        "origin": "https://" + host,
        "site": host,
        "brand": {"name": name, "host": host,
                  "domain_token": host.split(".")[0],
                  "authoritative_variants": [name]},
        "pages": pages if pages is not None else [
            _page("https://" + host + "/", "home",
                  title=name + " | Hours, catalogue and events",
                  h1=[name],
                  text="Renew a book, reserve a museum pass, or join the "
                       "summer reading programme."),
            _page("https://" + host + "/hours", "other", title="Hours"),
        ],
    }


def test_a_town_library_is_not_a_researcher():
    """The failure in full: a New Hampshire public library was told to "claim
    or update the profiles that apply to you: your researcher identifier, your
    institution's staff directory, the repository host your code or data lives
    on", and to repeat its boilerplate in "your researcher profile, and every
    conference or speaker biography"."""
    kind = site_kind(_library())
    assert kind.kind == PUBLIC_BODY
    assert kind.determined
    assert kind.kind != PERSONAL_OR_ACADEMIC
    assert "Ashcombe Public Library" in kind.why()


def test_the_markup_is_read_before_the_name_is():
    """A site declaring its own schema.org type has said what it is, in the
    same way `GovernmentOrganization` says it."""
    snapshot = _library(name="Reading Room")
    snapshot["pages"][0]["jsonld_types"] = ["Library"]
    kind = site_kind(snapshot)
    assert kind.kind == PUBLIC_BODY
    assert kind.confidence == "high"


def test_a_company_selling_library_software_is_not_a_library():
    """The other direction, and the one the rule has to be careful about. The
    words "library" and "school" appear on plenty of commercial sites."""
    vendor = {
        "origin": "https://shelfmark.test",
        "site": "shelfmark.test",
        "brand": {"name": "Shelfmark Library Systems", "host": "shelfmark.test",
                  "domain_token": "shelfmark",
                  "authoritative_variants": ["Shelfmark Library Systems"]},
        "pages": [
            _page("https://shelfmark.test/", "home",
                  title="Shelfmark Library Systems | Software for public libraries",
                  h1=["Library software that works"],
                  text="We serve 400 public libraries, school districts and "
                       "museums across the country.",
                  internal=["https://shelfmark.test/pricing"]),
        ],
    }
    assert site_kind(vendor).kind != PUBLIC_BODY


def test_prose_is_not_read_as_a_self_description():
    """Only the name the site calls itself by. A consultancy whose case study
    names a library client is not a library."""
    consultancy = {
        "origin": "https://kelvinbrand.test",
        "site": "kelvinbrand.test",
        "brand": {"name": "Kelvin Brand Studio", "host": "kelvinbrand.test",
                  "domain_token": "kelvinbrand"},
        "pages": [
            _page("https://kelvinbrand.test/", "home", title="Kelvin Brand Studio",
                  h1=["Identity for institutions"],
                  text="We rebranded the Ashcombe Public Library and the "
                       "Kelvin Historical Society."),
        ],
    }
    assert site_kind(consultancy).kind != PUBLIC_BODY


def test_a_business_named_after_an_institution_is_not_one():
    """The corroboration the name rule needs: nothing for sale."""
    shop = _library(host="oldschoolbooks.test", name="The Old School Bookshop")
    shop["pages"][0]["links"]["internal"] = [
        {"url": "https://oldschoolbooks.test/cart"}]
    assert site_kind(shop).kind != PUBLIC_BODY


def test_the_name_has_to_read_as_a_name_and_not_as_copy():
    assert names_a_civic_institution("Ashcombe Public Library")
    assert names_a_civic_institution("Museum of Invented Craft")
    assert names_a_civic_institution("Town of Ashcombe")
    assert names_a_civic_institution("Ashcombe School District")
    assert names_a_civic_institution("St Aidan's Church")
    assert not names_a_civic_institution("Software for public libraries")
    assert not names_a_civic_institution("Library management made simple")
    assert not names_a_civic_institution("Ashcombe Library Solutions")
    assert not names_a_civic_institution("Church Street Cafe")


def test_a_university_is_still_academic():
    """The vocabulary keeps a person, a university and a lab in one class, and
    this must not empty it."""
    kind = site_kind({"origin": "https://physics.ac.zz",
                      "pages": [_page("https://physics.ac.zz/", "home")]})
    assert kind.kind == PERSONAL_OR_ACADEMIC


def test_an_ordinary_site_is_still_undecided():
    assert site_kind({"origin": "https://quiet.example",
                      "pages": [_page("https://quiet.example/", "home")]}
                     ).kind == UNDETERMINED
