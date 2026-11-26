"""Delay attribution (PLAN_V2 §5, cut a; HANDOFF task 4.2): why was a factory late, for its record.

The AI only suggests (`agent.suggest_attribution`, task `attribution_v1`); a person on the exporter side decides
(`OrderBook.decide_attribution`) and the factory can dispute (`OrderBook.dispute_attribution`). The code rules
here hold for both the AI and the person:

- `outside_event` needs a recorded event (a news item recorded on this order) in the factory's district or
  nationwide, running at some point between the day the split was agreed and the day of the update
  (`matching_events`). Without one, the AI's suggestion is cut to `unclear`, and a person cannot record it.
- `factory` and `exporter_side` need a reason quoted word for word from the factory's update; without one, the
  AI's suggestion is cut to `unclear` (a person may still decide, with their role recorded).
No number is produced here; dates are story days from the store.
"""
from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("factory", "outside_event", "exporter_side", "unclear")
LABELS = {"factory": "the factory's side", "outside_event": "an outside event", "exporter_side": "our side (exporter)",
          "unclear": "unclear"}


@dataclass(frozen=True)
class NewsEvent:
    event_id: str        # e.g. "<order>:news-1"; the AI may cite only these ids
    area: str            # a district or "national"
    kind: str            # disruption type, from the news reading
    start_day: int
    end_day: int
    quote: str           # the words of the news item that say what happened


@dataclass(frozen=True)
class Delay:
    delay_id: str
    factory_id: str
    district: str
    day: int             # day of the update that reported it
    delay_days: int
    update_text: str
    cause_quote: str | None


def matching_events(events: list[NewsEvent], district: str, from_day: int, to_day: int) -> list[NewsEvent]:
    """Recorded events that could explain a delay: same district or nationwide, and running at some point in
    [from_day, to_day] (production start to the update)."""
    return [e for e in events if (e.area == district or e.area == "national")
            and e.start_day <= to_day and e.end_day >= from_day]
