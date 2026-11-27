"""The real order documents (data/documents, data/Documents) hold real company names, PO numbers, SKUs, an email
and an address. They are never committed, never sent to the LLM and never used raw in the demo or tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def test_no_document_or_pdf_is_tracked():
    bad = [f for f in _tracked() if f.lower().startswith("data/documents/") or f.lower().endswith(".pdf")]
    assert not bad, bad


def test_no_code_reads_the_documents_folder():
    hits = []
    for f in _tracked():
        if f.endswith(".py") and f != "tests/test_no_documents.py":
            text = (ROOT / f).read_text(encoding="utf-8", errors="ignore").lower()
            if "data/documents" in text or "data\\documents" in text or '"documents"' in text:
                hits.append(f)
    assert not hits, hits
