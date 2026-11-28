"""The demo programme (SPEC.md D30): the 10 anonymised delivery orders' styles at their real quantities, all
subcontracted. A style always goes whole to one factory (interview). Read from `data/demo/docs/programme.json`
(written by `bunon.anonymise`, code-parsed, no LLM)."""
from __future__ import annotations

import json
from pathlib import Path

from bunon import config as C
from bunon.schemas import OrderConstraints, StyleOrder

PROGRAMME_PATH = C.ROOT / "data" / "demo" / "docs" / "programme.json"


def load_styles(path: Path = PROGRAMME_PATH) -> list[StyleOrder]:
    out = []
    for p in json.loads(path.read_text(encoding="utf-8")):
        by_range: dict[str, int] = {}
        for r in p["rows"]:
            by_range[r["range"]] = by_range.get(r["range"], 0) + r["qty"]
        out.append(StyleOrder(style_id=p["style"], qty=p["qty"], item=p["item"] or "",
                              ranges=sorted(by_range), colours=sorted({r["colour"] for r in p["rows"]}),
                              qty_by_range=dict(sorted(by_range.items()))))
    return out


def fabric_kg_per_piece(style: StyleOrder) -> float:
    """D31: fabric per piece from the delivery orders' rates per dozen by size range, plus the fabric waste."""
    if not style.qty_by_range:
        return C.FABRIC_KG_PER_TEE
    kg = sum(q * C.FABRIC_KG_PER_PIECE_BY_RANGE.get(r, C.FABRIC_KG_PER_TEE) for r, q in style.qty_by_range.items())
    return kg / sum(style.qty_by_range.values()) * (1 + C.FABRIC_WASTE_PCT)


def material_bdt_per_piece(style: StyleOrder) -> float:
    """Exporter-supplied material per piece of a style: fabric (kg x price) plus trims (placeholders)."""
    return fabric_kg_per_piece(style) * C.FABRIC_BDT_PER_KG + C.TRIMS_BDT_PER_PIECE


def as_programme(order: OrderConstraints, styles: list[StyleOrder] | None = None) -> OrderConstraints:
    """The order with the programme's styles as its subcontracted part (own capacity 0: every style goes out)."""
    styles = styles if styles is not None else load_styles()
    return order.model_copy(update={"styles": styles, "total_qty": sum(s.qty for s in styles),
                                    "own_capacity_qty": 0})
