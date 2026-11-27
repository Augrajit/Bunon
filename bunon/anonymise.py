"""Anonymise the real delivery orders (DOCS_PROPOSAL.md §a). Local only: run by a person on the machine that holds
the documents, never imported by the app, never sent to an LLM.

    python -m bunon.anonymise <folder with the delivery-order PDFs>

What it does:
- `pdftotext -layout` gives each delivery order's text.
- Sensitive values are found by their place in the layout:
  - the issuing company, buyer, factory, address / attn, PO numbers and style code from the header
  - every 10-digit number that is not a PO is treated as a SKU
  - emails, file paths and URLs
  - the graphic words of the item description (anything not in `ITEM_WORDS`)
- Each value gets a consistent fake, the same in every document (a PO stays a 10-digit PO, a style becomes S01…).
  The mapping is kept in `data/private/doc_mapping.json` (gitignored), so re-running gives the same fakes.
- Everything else is kept as it is: layout, sizes, colours, packs, quantities, rates, waste and every error.
- `privacy_scan` refuses to write if any real value (or a distinctive word of a real name) is left in the output.

Output: `data/demo/docs/S01.txt …` and `data/demo/docs/programme.json` (header and size breakdown per style, parsed
in code from the anonymised text).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bunon import config as C

OUT_DIR = C.ROOT / "data" / "demo" / "docs"
MAPPING_PATH = C.ROOT / "data" / "private" / "doc_mapping.json"

# Words of an item description that describe the garment, not the graphic. Everything else is replaced.
ITEM_WORDS = {"SS", "LS", "OVRSZ", "BOYF", "BOYFD", "SCRN", "SCR", "SCN", "TE", "TEE", "CNH", "BRIB",
              "WHITE", "PINK", "BLACK", "WHT/BLK", "WHT", "BLK", "GREY", "NAVY", "RED", "BLUE"}
# Words too generic to count as a trace of a real name in the privacy scan.
GENERIC = {"TRADING", "APPAREL", "APPARELS", "LIMITED", "LTD", "GARMENTS", "FASHION", "FASHIONS", "THE", "AND",
           "ONE", "GROUP", "INTERNATIONAL", "INDUSTRIES", "COMPANY", "TEXTILE", "TEXTILES"}
EMPTY = {"—", "--", "-", ""}

TEN_DIGITS = re.compile(r"(?<!\d)\d{10}(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FILE_URL = re.compile(r"file://\S+")
WEB_URL = re.compile(r"https?://\S+")
TITLE = re.compile(r"Instant Auto DO Generator \| (.+?)(?:\s{2,}|\s*$)", re.M)


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True,
                          check=True).stdout.decode("utf-8")


def _cols(line: str) -> list[str]:
    return [c for c in re.split(r"\s{2,}", line.strip()) if c]


def header(text: str) -> dict[str, str]:
    """Label line / value line pairs at the top of a delivery order ("DO   Buyer" then "DO-…   <buyer>")."""
    lines = text.splitlines()
    out: dict[str, str] = {}
    labels = {"DO", "Buyer", "PO", "Style", "Item", "Qty (selling units)", "Factory", "Address / Attn",
              "Order date", "Ship date", "Ship on", "Cancel date"}
    for i, line in enumerate(lines[:-2]):
        cols = _cols(line)
        if len(cols) == 2 and set(cols) <= labels:
            nxt = next((l for l in lines[i + 1:i + 3] if l.strip()), "")
            vals = _cols(nxt)
            if len(vals) == 2:
                out[cols[0]], out[cols[1]] = vals
        if line.startswith("PO / PACK / SIZE"):
            break
    return out


@dataclass
class Mapping:
    """Real value → fake, per kind. Consistent across documents and runs."""
    kinds: dict[str, dict[str, str]] = field(default_factory=dict)

    FORMATS = {"company": "COMPANY {:s}", "buyer": "BUYER {:s}", "factory": "FACTORY {:s}",
               "address": "ADDRESS {:s}", "po": "7{:09d}", "sku": "8{:09d}", "style": "S{:02d}",
               "graphic": "GFX{:02d}", "email": "contact{:d}@example.com", "path": "file:///local/do_generator_{:d}.html",
               "url": "https://example.com/{:d}"}

    def fake(self, kind: str, real: str) -> str:
        table = self.kinds.setdefault(kind, {})
        if real not in table:
            n = len(table) + 1
            fmt = self.FORMATS[kind]
            table[real] = fmt.format(chr(ord("A") + n - 1)) if "{:s}" in fmt else fmt.format(n)
        return table[real]

    @classmethod
    def load(cls, path: Path = MAPPING_PATH) -> Mapping:
        return cls(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else cls()

    def save(self, path: Path = MAPPING_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.kinds, indent=1, ensure_ascii=False), encoding="utf-8")

    def real_values(self) -> list[str]:
        return [r for t in self.kinds.values() for r in t]


def find_sensitive(text: str) -> list[tuple[str, str]]:
    """(kind, real value) pairs in one delivery order."""
    h = header(text)
    found: list[tuple[str, str]] = []
    for m in TITLE.finditer(text):
        found.append(("company", m.group(1)))
    for label, kind in (("Buyer", "buyer"), ("Factory", "factory"), ("Address / Attn", "address")):
        if h.get(label, "") not in EMPTY:
            found.append((kind, h[label]))
    pos = [p.strip() for p in h.get("PO", "").split(",") if p.strip()]
    found += [("po", p) for p in pos]
    if h.get("Style", "") not in EMPTY:
        found.append(("style", h["Style"]))
    found += [("sku", n) for n in TEN_DIGITS.findall(text) if n not in pos]
    found += [("email", e) for e in EMAIL.findall(text)]
    found += [("path", u) for u in FILE_URL.findall(text)]
    found += [("url", u) for u in WEB_URL.findall(text)]
    found += [("graphic", w) for w in h.get("Item", "").split() if w not in ITEM_WORDS]
    return found


def anonymise(text: str, mapping: Mapping) -> str:
    """Replace every sensitive value (longest first, so a PO list or "DO-<style>" is caught whole)."""
    pairs = {real: mapping.fake(kind, real) for kind, real in find_sensitive(text)}
    # The graphic words are replaced only inside the item description line, so a colour or size is never touched.
    graphic = {r: f for r, f in pairs.items() if r in mapping.kinds.get("graphic", {})}
    item = header(text).get("Item", "")
    out_lines = []
    for line in text.splitlines():
        if item and item in line and graphic:
            new_item = " ".join(graphic.get(w, w) for w in item.split())
            line = line.replace(item, new_item)
        out_lines.append(line)
    out = "\n".join(out_lines)
    for real in sorted((r for r in pairs if r not in graphic), key=len, reverse=True):
        out = out.replace(real, pairs[real])
    # "<n>.<style>.png": the number in front is the real style code's number, so it goes too.
    return re.sub(r"\b\d+\.(S\d{2})\.png\b", r"\1.png", out)


def _traces(mapping: Mapping) -> list[str]:
    """Strings that must not appear in any output: every real value, and each distinctive word of a real name."""
    t = set(mapping.real_values())
    for kind in ("company", "buyer", "factory", "address"):
        for real in mapping.kinds.get(kind, {}):
            t |= {w for w in re.findall(r"[A-Za-z]{4,}", real) if w.upper() not in GENERIC}
    for real in mapping.kinds.get("path", {}):  # a user name or folder name inside a local path
        t |= {w for w in re.findall(r"[A-Za-z]{4,}", real) if w.lower() not in {"file", "users", "desktop", "html"}}
    return sorted(t, key=len, reverse=True)


def privacy_scan(texts: dict[str, str], mapping: Mapping) -> list[str]:
    """Problems found ("<file>: <kind of leak>"); never prints the real value itself."""
    traces = _traces(mapping)
    problems = []
    for name, text in texts.items():
        low = text.lower()
        n = sum(1 for t in traces if t.lower() in low)
        if n:
            problems.append(f"{name}: {n} real value(s) or name word(s) left")
        if re.search(r"/Users/|C:\\\\Users", text):
            problems.append(f"{name}: a local user path is left")
    return problems


ROW = re.compile(r"^(\d{10})\s+PACK (\d+)\s+(\S+)\s+(\S+(?: \S+)*?)\s{2,}(\S+)\s+(\d{10})\s+([\d,]+)\s*$")


def parse(text: str) -> dict:
    """Header and size breakdown of an anonymised delivery order (code, no LLM)."""
    h = header(text)
    rows = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if m:
            po, pack, rng, colour, size, sku, qty = m.groups()
            rows.append({"po": po, "pack": int(pack), "range": rng, "colour": colour, "size": size, "sku": sku,
                         "qty": int(qty.replace(",", ""))})
    qty = h.get("Qty (selling units)", "")
    return {"style": h.get("Style"), "item": h.get("Item"), "pos": [p.strip() for p in h.get("PO", "").split(",")],
            "qty": int(qty.replace(",", "")) if qty.replace(",", "").isdigit() else None,
            "ship_date": None if h.get("Ship date", "") in EMPTY else h.get("Ship date"),
            "factory": h.get("Factory"), "rows": rows}


def run(folder: Path, out_dir: Path = OUT_DIR, mapping_path: Path = MAPPING_PATH) -> list[str]:
    pdfs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")
    texts = {}
    for p in pdfs:
        t = pdf_text(p)
        if "DELIVERY ORDER" in t:  # the scanned style sheet has no text layer and stays out of scope
            texts[p.name] = t
    mapping = Mapping.load(mapping_path)
    # Styles numbered in sorted order of the real codes, so S01… are stable.
    for t in sorted(texts.values(), key=lambda s: header(s).get("Style", "")):
        mapping.fake("style", header(t)["Style"])
    outs = {}
    for t in texts.values():
        a = anonymise(t, mapping)
        outs[header(a)["Style"]] = a
    problems = privacy_scan(outs, mapping)
    if problems:
        raise SystemExit("Privacy scan failed, nothing written:\n" + "\n".join(problems))
    mapping.save(mapping_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    for style, a in sorted(outs.items()):
        (out_dir / f"{style}.txt").write_text(a + "\n", encoding="utf-8")
    programme = [parse(outs[s]) for s in sorted(outs)]
    (out_dir / "programme.json").write_text(json.dumps(programme, indent=1), encoding="utf-8")
    return sorted(outs)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m bunon.anonymise <folder with the delivery-order PDFs>")
    styles = run(Path(sys.argv[1]))
    print(f"wrote {len(styles)} anonymised delivery orders: {', '.join(styles)}")
