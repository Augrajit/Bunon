"""What-if analyst: the LLM chooses which tools to run; the tools do all the numbers (SPEC.md D8).

- Tools: run_simulation (current plan under a disruption), solve_plan (best plan under given
  exclusions/caps, optionally knowing about a disruption), compare_plans (two earlier results).
- Every tool result is registered as named facts (c1.p_meet, c2.cost_delta, ...). The final answer
  may cite numbers only as {{key}} placeholders; agent.check_text enforces it. One retry, then a
  deterministic template built from the same facts.
- At most MAX_TOOL_CALLS tool calls per question; after that, tools are switched off.
- Every model step is cached under a key of the conversation so far, so a cached question
  replays offline with no provider at all.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from bunon import agent as ag
from bunon import config as C
from bunon import optimizer as op
from bunon.llm import BudgetExceeded, CacheMiss, LLMClient, LLMError

ANALYST_VERSION = "analyst_v1"
MAX_TOOL_CALLS = 6
MAX_MODEL_STEPS = 10
AREAS = ["national", "ashulia_savar", "gazipur", "narayanganj", "chattogram", "dhaka_city", "mymensingh"]

DECLARATIONS = [
    {
        "name": "run_simulation",
        "description": ("Simulate the CURRENT recommended plan with a disruption applied (severity maps to "
                        "fixed parameters). Returns the probability of on-time full delivery and which "
                        "planned factories are hit."),
        "parameters": {"type": "object", "properties": {
            "area": {"type": "string", "enum": AREAS, "description": "Affected district, or national"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "duration_days": {"type": "integer", "description": "Only if the question states a number of days"},
        }, "required": ["area", "severity"]},
    },
    {
        "name": "solve_plan",
        "description": ("Find the best new subcontracting plan under the given settings, optionally knowing "
                        "about a disruption. Returns probability, cost, cost change versus the current plan, "
                        "buffer, factories and whether the target is met."),
        "parameters": {"type": "object", "properties": {
            "exclude_factories": {"type": "array", "items": {"type": "string"},
                                  "description": "Factory IDs like F0123 that must not be used"},
            "max_factories": {"type": "integer", "description": "Between 1 and 5"},
            "max_district_share": {"type": "number", "description": "Fraction between 0.3 and 1.0"},
            "unproven_cap": {"type": "number", "description": "Fraction between 0 and 0.3"},
            "shock_area": {"type": "string", "enum": AREAS},
            "shock_severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "shock_duration_days": {"type": "integer"},
        }},
    },
    {
        "name": "compare_plans",
        "description": ("Compare two earlier results by their call ids (e.g. 'c2') or 'current'. Returns the "
                        "difference in probability (second minus first) and in cost."),
        "parameters": {"type": "object", "properties": {
            "first": {"type": "string"}, "second": {"type": "string"}}, "required": ["first", "second"]},
    },
]

SYSTEM = """You are Bunon's procurement analyst for a garment exporter in Bangladesh. A manager asks a what-if
question about the current subcontracting plan. Answer it by calling tools; the tools do all calculations.

Rules:
- Call only the tools you need (at most {max_calls} calls). Do not repeat a call with the same arguments.
- Never compute, estimate or invent a number yourself.
- To repeat a setting from the question (a cap, an exclusion, a number of days), cite the fact that the tool
  echoes back (for example the cap used); never restate it in words.
- FINAL ANSWER RULES (an automatic checker enforces them; violating answers are discarded):
  1. No digits and no number words (two, five, hundred, percent, half, double ...) in the text.
  2. Every number is a placeholder with a key from the facts, wrapped in double braces, e.g. {{{{c2.p_meet}}}}.
     Never write a key without braces.
  3. Refer to factories as {{{{fac:F0123}}}}.
  4. Two to five short sentences: what you checked, what it shows, and a recommendation. Say that a human
     decides and nothing is sent automatically. Mention that the data is synthetic.

FACTS ABOUT THE CURRENT PLAN (key = value: meaning):
{facts}
"""


@dataclass
class ToolCallLog:
    step: int
    call_id: str
    name: str
    args: dict
    ok: bool
    facts: dict[str, str]          # key -> rendered value
    error: str = ""


@dataclass
class AnalystRun:
    question: str
    calls: list[ToolCallLog]
    answer_md: str
    source: str                     # "cache" | "live" | "template" | "unavailable"
    errors: list[str] = field(default_factory=list)
    facts: ag.FactSheet = field(default_factory=dict)
    plans: dict = field(default_factory=dict)


class Toolbox:
    """Runs tools against the current plan and records every result as facts."""

    def __init__(self, world: op.World, order, params, comp: dict, base_facts: ag.FactSheet,
                 target: float, shocks: bool = True, partial: bool = C.PARTIAL_LOTS_DEFAULT):
        self.w, self.order, self.params, self.target = world, order, params, target
        self.shocks, self.partial = shocks, partial
        self.rec = comp["ours"].recommended
        self.cands = op.build_candidates(world, order, params)
        self.ids = {c.factory_id for c in self.cands}
        self.facts: ag.FactSheet = dict(base_facts)
        self.results: dict[str, dict] = {"current": {"p": self.rec.risk.p_meet_qty, "cost": self.rec.plan.cost_total}}
        self.plans: dict = {}
        self.n = 0

    def _fact(self, key, value, kind, label) -> tuple[str, str]:
        self.facts[key] = ag.Fact(value, kind, label)
        return key, self.facts[key].render()

    def call(self, name: str, args: dict) -> tuple[str, bool, dict[str, str], str]:
        self.n += 1
        cid = f"c{self.n}"
        if name not in ("run_simulation", "solve_plan", "compare_plans"):
            return cid, False, {}, f"unknown tool '{name}'; available: run_simulation, solve_plan, compare_plans"
        try:
            facts = getattr(self, f"_t_{name}")(cid, args)
            return cid, True, facts, ""
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            return cid, False, {}, str(e)

    @staticmethod
    def _event_days(severity, days) -> int:
        """Event length the simulation used: the stated duration, or the fixed table's default."""
        return int(days) if days is not None else C.SEVERITY_TABLE[severity]["default_days"]

    def _event(self, area, severity, days):
        if area is None and severity is None:
            return None
        if area not in AREAS or severity not in ("low", "medium", "high"):
            raise ValueError("a disruption needs both area (from the list) and severity (low/medium/high)")
        return op.event_from_severity(area, severity, int(days) if days is not None else None)

    def _t_run_simulation(self, cid, a):
        ev = self._event(a.get("area"), a.get("severity"), a.get("duration_days"))
        if ev is None:
            raise ValueError("run_simulation needs area and severity")
        risk = op.evaluate(self.rec.plan, self.cands, self.w, self.shocks, partial_lots=self.partial, events=[ev])
        dist = {c.factory_id: c.district for c in self.cands}
        hit = [ln.factory_id for ln in self.rec.plan.lines if ev.area == "national" or dist[ln.factory_id] == ev.area]
        self.results[cid] = {"p": risk.p_meet_qty, "cost": self.rec.plan.cost_total}
        return dict([
            self._fact(f"{cid}.p_meet", risk.p_meet_qty, "prob", f"current plan under {ev.area} {a['severity']} disruption: probability"),
            self._fact(f"{cid}.p_change", risk.p_meet_qty - self.rec.risk.p_meet_qty, "pp", "change versus no disruption"),
            self._fact(f"{cid}.event_days", self._event_days(a["severity"], a.get("duration_days")), "days",
                       "length of the disruption simulated (as asked, or the fixed table's default)"),
            self._fact(f"{cid}.delay_days", ev.extra_delay_days, "days",
                       "resulting delivery delay for affected factories (fixed table; not the disruption's length)"),
            self._fact(f"{cid}.output_share", ev.fill_mult, "pct", "share of output still delivered by affected factories (fixed table)"),
            self._fact(f"{cid}.affected", ", ".join(hit) or "none", "text", "planned factories in the affected area"),
            self._fact(f"{cid}.n_affected", len(hit), "int", "number of planned factories in the affected area"),
        ])

    def _t_solve_plan(self, cid, a):
        upd = {}
        excl = [str(x).upper() for x in a.get("exclude_factories") or []]
        bad = [x for x in excl if x not in self.ids]
        if bad:
            raise ValueError(f"unknown factory IDs {bad}")
        if excl:
            upd["excluded_ids"] = excl
        if a.get("max_factories") is not None:
            v = int(a["max_factories"])
            if not 1 <= v <= 5:
                raise ValueError("max_factories must be between 1 and 5")
            upd["max_factories"] = v
        if a.get("max_district_share") is not None:
            v = float(a["max_district_share"])
            if not 0.3 <= v <= 1.0:
                raise ValueError("max_district_share must be between 0.3 and 1.0")
            upd["max_district_share"] = None if v >= 1.0 else v
        if a.get("unproven_cap") is not None:
            v = float(a["unproven_cap"])
            if not 0.0 <= v <= 0.3:
                raise ValueError("unproven_cap must be between 0 and 0.3")
            upd["unproven_cap_pct"] = v
        p2 = self.params.model_copy(update=upd)
        ev = self._event(a.get("shock_area"), a.get("shock_severity"), a.get("shock_duration_days"))
        cands = op.build_candidates(self.w, self.order, p2)
        if ev:
            cands = op.adjust_for_events(cands, self.w, [ev])
        res = op.search_buffer(self.w, self.order, p2, self.target, shocks=self.shocks, partial_lots=self.partial,
                               cands=cands, events=[ev] if ev else None)
        new = res.recommended
        if not new.risk:
            raise ValueError("no feasible plan under these settings")
        self.plans[cid] = new
        self.results[cid] = {"p": new.risk.p_meet_qty, "cost": new.plan.cost_total}
        buf = max(0, new.plan.total_allocated - new.plan.target_qty) / new.plan.target_qty
        settings = []  # echo the settings used, so the answer can cite them instead of restating numbers
        if excl:
            settings.append(self._fact(f"{cid}.excluded", ", ".join(excl), "text", "factories excluded"))
        if "max_factories" in upd:
            settings.append(self._fact(f"{cid}.max_factories", upd["max_factories"], "int", "maximum factories used"))
        if "max_district_share" in upd and upd["max_district_share"] is not None:
            settings.append(self._fact(f"{cid}.max_district_share", upd["max_district_share"], "pct",
                                       "maximum share of pieces per district used"))
        if "unproven_cap_pct" in upd:
            settings.append(self._fact(f"{cid}.unproven_cap", upd["unproven_cap_pct"], "pct", "unproven-factory cap used"))
        if ev:
            settings.append(self._fact(f"{cid}.shock", f"{ev.area} ({a['shock_severity']})", "text", "disruption assumed"))
            settings.append(self._fact(f"{cid}.event_days", self._event_days(a["shock_severity"], a.get("shock_duration_days")),
                                       "days", "length of the disruption assumed"))
        return dict(settings + [
            self._fact(f"{cid}.p_meet", new.risk.p_meet_qty, "prob", "new plan: probability of on-time full delivery"),
            self._fact(f"{cid}.cost_total", new.plan.cost_total, "bdt", "new plan: total cost"),
            self._fact(f"{cid}.cost_delta", new.plan.cost_total - self.rec.plan.cost_total, "bdt_delta", "cost versus the current plan"),
            self._fact(f"{cid}.buffer_pct", buf, "pct", "new plan: buffer"),
            self._fact(f"{cid}.n_factories", len(new.plan.lines), "int", "new plan: number of factories"),
            self._fact(f"{cid}.factories", ", ".join(ln.factory_id for ln in new.plan.lines), "text", "new plan: factories"),
            self._fact(f"{cid}.target_met", "yes" if res.target_met else "NO, below target", "text", "does it reach the target"),
        ])

    def _t_compare_plans(self, cid, a):
        f, s = a.get("first"), a.get("second")
        if f not in self.results or s not in self.results:
            raise ValueError(f"unknown result ids; available: {sorted(self.results)}")
        r1, r2 = self.results[f], self.results[s]
        return dict([
            self._fact(f"{cid}.p_diff", r2["p"] - r1["p"], "pp", f"probability, {s} minus {f}"),
            self._fact(f"{cid}.cost_diff", r2["cost"] - r1["cost"], "bdt_delta", f"cost, {s} minus {f}"),
        ])


def _template(calls: list[ToolCallLog]) -> str:
    """Deterministic fallback answer from the same facts."""
    parts = []
    for c in calls:
        if not c.ok:
            continue
        if c.name == "run_simulation":
            parts.append(f"With that disruption the current plan's probability is {{{{{c.call_id}.p_meet}}}} "
                         f"({{{{{c.call_id}.p_change}}}}).")
        elif c.name == "solve_plan":
            parts.append(f"A re-plan reaches {{{{{c.call_id}.p_meet}}}} at {{{{{c.call_id}.cost_total}}}} "
                         f"({{{{{c.call_id}.cost_delta}}}}); target met: {{{{{c.call_id}.target_met}}}}.")
        elif c.name == "compare_plans":
            parts.append(f"Difference: {{{{{c.call_id}.p_diff}}}}, {{{{{c.call_id}.cost_diff}}}}.")
    parts.append("A human decides; nothing is sent automatically. All performance data is synthetic.")
    return " ".join(parts)


def answer(question: str, toolbox: Toolbox, client: LLMClient | None = None) -> AnalystRun:
    client = client or LLMClient()
    system = SYSTEM.format(max_calls=MAX_TOOL_CALLS,
                           facts="\n".join(f"{k} = {f.render()}: {f.label}" for k, f in toolbox.facts.items()))
    ag.assert_pseudonymous(system + question)
    convo: list[dict] = [{"system": system, "question": question}]
    session = None
    calls: list[ToolCallLog] = []
    errors: list[str] = []
    source = "cache"
    pending_results: list[dict] | None = None
    user_text: str | None = None
    force_text = False
    final_text = None
    retried = False

    for step in range(1, MAX_MODEL_STEPS + 1):
        key = client.step_key("analyst", ANALYST_VERSION, convo)
        out = client.cache_get(key)
        if out is None:
            if client.mode == "offline":
                return AnalystRun(question, calls, "", "unavailable",
                                  ["This question is not in the cache and the app is offline."], toolbox.facts)
            try:
                if session is None:
                    if len(convo) > 1:  # cached prefix but a later step is missing: re-run everything live
                        return _live_from_scratch(question, toolbox, client)
                    session = client.tool_session(system, question, DECLARATIONS)
                client.spend()
                out = session.step(pending_results, user_text, force_text)
            except BudgetExceeded as e:
                if not calls:  # nothing ran yet: same as offline
                    return AnalystRun(question, calls, "", "unavailable", [f"{e}."], toolbox.facts)
                errors.append(str(e))
                break
            except LLMError as e:
                errors.append(str(e))
                break
            client.cache_put(key, "analyst", ANALYST_VERSION, out, convo)
            client.calls += 1
            source = "live"
        elif session is not None:  # keep a live session in step with cached answers (rare)
            out = session.step(pending_results, user_text, force_text)
        convo.append({"model": out})
        pending_results, user_text, force_text = None, None, False

        if out.get("calls"):
            results = []
            for c in out["calls"]:
                if len(calls) >= MAX_TOOL_CALLS:
                    results.append({"id": c["id"], "name": c["name"],
                                    "response": {"ok": False, "error": "tool limit reached; answer now"}})
                    continue
                cid, ok, facts, err = toolbox.call(c["name"], c["args"])
                calls.append(ToolCallLog(step, cid, c["name"], c["args"], ok, facts, err))
                resp = ({"ok": True, "call_id": cid, "facts": facts,
                         "cite_as": "write each value as {{key}}, e.g. {{" + next(iter(facts), "key") + "}}"}
                        if ok else {"ok": False, "call_id": cid, "error": err})
                results.append({"id": c["id"], "name": c["name"], "response": resp})
            pending_results = results
            force_text = len(calls) >= MAX_TOOL_CALLS
            convo.append({"tools": [{"name": r["name"], "response": r["response"]} for r in results]})
            continue

        text = (out.get("text") or "").strip()
        errs = ag.check_text("answer", text, toolbox.facts) if text else ["empty answer"]
        if not errs:
            final_text = text
            break
        errors += errs
        if retried:
            break
        retried = True
        user_text = ("Your answer was rejected for these reasons; rewrite it following the FINAL ANSWER RULES:\n- "
                     + "\n- ".join(errs))
        force_text = True
        convo.append({"user": user_text})

    if final_text is None:
        final_text, source = _template(calls), "template"
    md = ag.render_text(final_text, toolbox.facts)
    return AnalystRun(question, calls, md, source, errors, toolbox.facts, toolbox.plans)


def _live_from_scratch(question: str, toolbox: Toolbox, client: LLMClient) -> AnalystRun:
    """A cached prefix exists but a later step is missing: re-run the whole conversation live so the
    provider session and the cache agree (thought signatures cannot be rebuilt from the cache)."""
    fresh = toolbox_reset(toolbox)
    shadow = _NoCacheClient(client)
    return answer(question, fresh, shadow)


class _NoCacheClient(LLMClient):
    """Live client that ignores existing step-cache entries (but still writes new ones)."""

    def __init__(self, base: LLMClient):
        super().__init__(provider=base._provider, mode="live", cache_dir=base.cache_dir,
                         provider_name=base.provider_name, model=base.model)
        self.budget_path = base.budget_path

    def cache_get(self, key):
        return None


def toolbox_reset(tb: Toolbox) -> Toolbox:
    base = {k: v for k, v in tb.facts.items() if not k.startswith("c") or "." not in k or not k.split(".")[0][1:].isdigit()}
    return Toolbox(tb.w, tb.order, tb.params, {"ours": type("R", (), {"recommended": tb.rec})()}, base, tb.target,
                   tb.shocks, tb.partial)
