"""What the archive does to the bytes it carries.

An independent review unzipped a build and found CRLF and LF line endings mixed
together across the files. Nobody chose that: it is what a Windows checkout
holds - 33 of the Python files here are CRLF and 61 are LF - copied into the
zip unexamined. Anyone who unpacks the submission on a different platform
gets files that differ from each other for no reason, and the two that most
obviously should not differ are a shared module and the copy of it that
`package.py` vendors into each skill.

These tests build a zip into a temporary directory and read it. They never
touch `dist/`: the artefact there is what ships, and rebuilding it to test
the packaging would mix two builds into one submission.
"""

from __future__ import annotations

import os
import sys
import zipfile

import pytest

from conftest import ROOT

sys.path.insert(0, ROOT)

import package  # noqa: E402

PACKAGE = package.PACKAGE_NAME
VENDOR_MARKER = b"GENERATED COPY - do not edit"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A zip built the way `python package.py` builds one, somewhere harmless."""
    out = str(tmp_path_factory.mktemp("packaging") / "submission.zip")
    package.build(out)
    with zipfile.ZipFile(out) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_no_text_file_in_the_zip_carries_a_carriage_return(built):
    """One convention, chosen, rather than whatever each file happened to have."""
    offenders = sorted(
        name for name, payload in built.items()
        if name.endswith(package.NORMALISED_SUFFIXES) and b"\r" in payload)
    assert not offenders, (
        "these files ship with CRLF line endings: {}".format(offenders[:10]))


def test_the_zip_normalises_a_file_that_is_crlf_on_disk(built):
    """The check above passes trivially if nothing in the checkout is CRLF.

    It is not passing trivially today, and this says so: at least one packaged
    file really does hold CRLF on disk and LF in the archive.
    """
    converted = []
    for name in built:
        parts = name.split("/", 1)
        if len(parts) != 2 or not name.endswith(package.NORMALISED_SUFFIXES):
            continue
        path = os.path.join(ROOT, parts[1].replace("/", os.sep))
        if not os.path.isfile(path):
            continue                       # a vendored copy has no source path
        with open(path, "rb") as handle:
            if b"\r\n" in handle.read():
                converted.append(name)
    assert converted, (
        "no packaged file is CRLF on disk, so the normalisation is untested. "
        "Re-check it by hand before deleting this test")


def test_bytes_that_are_the_point_are_left_alone(built):
    """The fixture PDF is evidence for the "facts locked in a PDF" check, and a
    CR inside a compressed stream is data. It enters the zip untouched."""
    pdfs = [name for name in built if name.endswith(".pdf")]
    assert pdfs, "the PDF fixture is no longer packaged; this test is stale"
    for name in pdfs:
        with open(os.path.join(ROOT, name.split("/", 1)[1].replace("/", os.sep)),
                  "rb") as handle:
            assert built[name] == handle.read(), "{} was rewritten".format(name)


def test_each_vendored_copy_is_byte_identical_to_the_source_it_shipped_beside(built):
    """The promise `package.py` makes, checked in bytes rather than in text.

    `test_marketplace.py` makes the same comparison after Python's universal
    newline translation, which cannot see a CRLF source shipped beside its LF
    copies. A person comparing the two files by hand can, and one did.
    """
    sources = {}
    for name, payload in built.items():
        if name.endswith(".py") and VENDOR_MARKER not in payload[:800]:
            sources[os.path.basename(name)] = payload

    vendored = {name: payload for name, payload in built.items()
                if name.endswith(".py") and VENDOR_MARKER in payload[:800]}
    assert vendored, "the zip vendors no shared module; this test is stale"

    for name, payload in sorted(vendored.items()):
        origin = sources.get(os.path.basename(name))
        assert origin is not None, "{} has no source in the zip".format(name)
        assert payload.endswith(origin), (
            "{} is not the header followed by the source verbatim; the two "
            "have drifted, or one of them was rewritten on the way in".format(name))
