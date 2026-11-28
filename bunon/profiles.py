"""Factory profiles and the new-entrant path (SPEC.md D31).

A factory with no past jobs has no record for Bunon to forecast from. It may still get a trial style when someone
vouches for it and an exporter's site visit is recent (at most NEW_ENTRANT_VISIT_DAYS old, a PLACEHOLDER). It then
counts as unproven, so the existing unproven-factory cap limits how much it can take. Without both, a factory with
no history is not a candidate. Profiles are simulated and hand-written (`data/demo/factory_profiles.json`); an
exporter search across profiles is roadmap only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bunon import config as C

PATH = C.ROOT / "data" / "demo" / "factory_profiles.json"


@dataclass(frozen=True)
class Profile:
    factory_id: str
    owner_background: str
    vouches: tuple[dict, ...]
    site_visits: tuple[dict, ...]
    placeholder: bool = True

    def last_visit(self) -> date | None:
        days = [date.fromisoformat(v["date"]) for v in self.site_visits if v.get("date")]
        return max(days) if days else None


def load(path: Path = PATH) -> dict[str, Profile]:
    if not path.exists():
        return {}
    d = json.loads(path.read_text(encoding="utf-8"))
    return {p["factory_id"]: Profile(p["factory_id"], p.get("owner_background", ""), tuple(p.get("vouches", [])),
                                     tuple(p.get("site_visits", [])), bool(p.get("placeholder", True)))
            for p in d.get("profiles", [])}


def eligible_new_entrant(p: Profile | None, as_of: date = C.AS_OF) -> bool:
    """A vouch and a site visit no older than NEW_ENTRANT_VISIT_DAYS (and not in the future)."""
    if p is None or not p.vouches:
        return False
    v = p.last_visit()
    return v is not None and 0 <= (as_of - v).days <= C.NEW_ENTRANT_VISIT_DAYS
