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
        out.append(StyleOrder(style_id=p["style"], qty=p["qty"], item=p["item"] or "",
                              ranges=sorted({r["range"] for r in p["rows"]}),
                              colours=sorted({r["colour"] for r in p["rows"]})))
    return out


def as_programme(order: OrderConstraints, styles: list[StyleOrder] | None = None) -> OrderConstraints:
    """The order with the programme's styles as its subcontracted part (own capacity 0: every style goes out)."""
    styles = styles if styles is not None else load_styles()
    return order.model_copy(update={"styles": styles, "total_qty": sum(s.qty for s in styles),
                                    "own_capacity_qty": 0})
