"""The anonymiser (DOCS_PROPOSAL.md §a). The synthetic sample below is made up; the real documents never enter a test."""
from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from bunon import anonymise as an
from bunon import config as C

DOCS = C.ROOT / "data" / "demo" / "docs"

SAMPLE = """01/02/2026, 09:00                                   Instant Auto DO Generator | ACME TRADING HOUSE

                                   ACME TRADING HOUSE

                                       DELIVERY ORDER

DO                                                    Buyer
DO-ZZ-100                                             GLOBEX KIDS

PO                                                    Style
1234567890, 1234567891                                ZZ-100

Item                                                  Qty (selling units)
SS BOYF STARLIGHT SCRN TEE WHITE                      720

Factory                                               Address / Attn
INITECH                                               12 Lake Road, contact jane.doe@globex.test

PO / PACK / SIZE / COLOR BREAKDOWN

PO                PACK        RANGE  COLOR      SIZE        SKU         QTY
1234567890        PACK 1      4/6X   WHITE      4           5550001111  120
1234567890        PACK 1      4/6X   WHITE      5/6         5550001111  120
1234567890        PACK 1      4/6X   WHITE      6X          5550001111  120
1234567891        PACK 2      7/16   WHITE      7/8         5550002222  120
1234567891        PACK 2      7/16   WHITE      10/12       5550002222  120
1234567891        PACK 2      7/16   WHITE      14/16       5550002222  120

TOP FABRIC         WHITE         4/6X          .1 KG       2%       3 KG   3.06 KG
SEWING THREAD      1 PCS/unit    2%            720 PCS     734 PCS  326 PCS

file:///Users/jdoe/Desktop/Acme_DO.html
                    7.ZZ-100.png
"""
SECOND = SAMPLE.replace("ZZ-100", "ZZ-200").replace("1234567891", "1234567892").replace("STARLIGHT", "MOON")


def _numbers(text: str) -> Counter:
    """Every number that is not an identifier (10-digit PO or SKU, style code, graphic, image name, address)."""
    text = text.replace(an.header(text)["Address / Attn"], " ")
    text = re.sub(r"\b\d{10}\b|ZZ-\d+|S\d{2}|GFX\d{2}|\d+\.(?:ZZ-\d+|S\d{2})\.png|do_generator_\d+", " ", text)
    return Counter(re.findall(r"\d+(?:[.,/]\d+)*", text))


def test_sensitive_values_are_found_by_layout():
    kinds = {}
    for kind, real in an.find_sensitive(SAMPLE):
        kinds.setdefault(kind, set()).add(real)
    assert kinds["company"] == {"ACME TRADING HOUSE"}
    assert kinds["buyer"] == {"GLOBEX KIDS"} and kinds["factory"] == {"INITECH"}
    assert kinds["po"] == {"1234567890", "1234567891"} and kinds["sku"] == {"5550001111", "5550002222"}
    assert kinds["style"] == {"ZZ-100"} and kinds["graphic"] == {"STARLIGHT"}
    assert kinds["email"] == {"jane.doe@globex.test"} and len(kinds["path"]) == 1
    assert kinds["address"] == {"12 Lake Road, contact jane.doe@globex.test"}


def test_fakes_are_consistent_across_documents_and_keep_numbers_and_errors():
    m = an.Mapping()
    a, b = an.anonymise(SAMPLE, m), an.anonymise(SECOND, m)
    # The shared PO, buyer and factory get the same fake in both documents; the new style a new one.
    assert m.kinds["po"]["1234567890"] in a and m.kinds["po"]["1234567890"] in b
    assert an.header(a)["Buyer"] == an.header(b)["Buyer"] == "BUYER A"
    assert an.header(a)["Style"] == "S01" and an.header(b)["Style"] == "S02"
    assert re.fullmatch(r"7\d{9}", m.kinds["po"]["1234567890"])  # a 10-digit PO stays a 10-digit PO
    # Quantities, sizes, rates, waste and the errors (.1 KG, "326 PCS" against thread) are untouched.
    assert _numbers(a) == _numbers(SAMPLE)
    assert ".1 KG" in a and "326 PCS" in a and "4/6X" in a and "10/12" in a
    assert "S01.png" in a and "7.S01" not in a
    assert an.privacy_scan({"a": a, "b": b}, m) == []


def test_privacy_scan_catches_a_left_value_and_a_name_word():
    m = an.Mapping()
    a = an.anonymise(SAMPLE, m)
    assert an.privacy_scan({"x": a + "\nGLOBEX"}, m)  # a distinctive word of a real name
    assert an.privacy_scan({"x": a + "\n5550001111"}, m)
    assert an.privacy_scan({"x": a + "\n/Users/someone/"}, m)
    assert not an.privacy_scan({"x": a + "\nTRADING"}, m)  # generic words are not traces


def test_parse_reads_the_breakdown():
    p = an.parse(an.anonymise(SAMPLE, an.Mapping()))
    assert p["style"] == "S01" and p["qty"] == 720 and p["ship_date"] is None
    assert [r["size"] for r in p["rows"]] == ["4", "5/6", "6X", "7/8", "10/12", "14/16"]
    assert sum(r["qty"] for r in p["rows"]) == 720


def test_published_documents_hold_only_fakes():
    files = sorted(DOCS.glob("S*.txt"))
    assert len(files) == 10
    for f in files:
        t = f.read_text(encoding="utf-8")
        assert re.fullmatch(r"S\d{2}", an.header(t)["Style"]), f.name
        assert all(re.fullmatch(r"[78]0{3,}\d+", n) for n in an.TEN_DIGITS.findall(t)), f.name
        assert "/Users/" not in t and not an.EMAIL.search(t) and "http" not in t, f.name
        assert re.findall(r"file://\S+", t) and all(u.startswith("file:///local/") for u in re.findall(r"file://\S+", t))
        assert all(w in an.ITEM_WORDS or re.fullmatch(r"GFX\d{2}", w) for w in an.header(t)["Item"].split()), f.name
        assert an.header(t)["Buyer"] == "BUYER A" and an.header(t)["Factory"] == "FACTORY A", f.name
    programme = json.loads((DOCS / "programme.json").read_text(encoding="utf-8"))
    assert [p["style"] for p in programme] == [f.stem for f in files]
    assert all(p["qty"] == sum(r["qty"] for r in p["rows"]) for p in programme)


def test_published_documents_pass_the_privacy_scan_against_the_private_mapping():
    if not an.MAPPING_PATH.exists():
        pytest.skip("private document mapping not present (clean clone)")
    m = an.Mapping.load()
    texts = {f.name: f.read_text(encoding="utf-8") for f in DOCS.glob("*")}
    assert an.privacy_scan(texts, m) == []


def test_the_app_never_imports_the_anonymiser():
    for f in [C.ROOT / "app.py", *sorted((C.ROOT / "bunon").glob("*.py"))]:
        if f.name != "anonymise.py":
            assert not re.search(r"import\s+anonymise|anonymise\s+import|import\s+bunon\.anonymise",
                                 f.read_text(encoding="utf-8")), f.name
    assert "llm" not in an.__dict__ and "import" not in [l for l in an.__doc__.splitlines() if "llm" in l]
