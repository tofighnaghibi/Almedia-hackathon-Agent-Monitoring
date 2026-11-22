"""Live marketplace: history first, then one simulated hour at a time (L1 live mode).

    live = LiveMarketplace(history_days=28, live_days=6)
    history = live.history()            # everything before the live start
    live.inject("payment_failure")      # takes effect from the next hour
    tick = live.step()                  # the next hour's rows (+ any late rows)

- The baseline for the whole period is generated up front (so the history
  and the live hours come from one consistent world), but rows are only
  handed out when their hour has passed. Nothing in the future is visible.
- Scenarios are Ayesha's ``ScenarioSpec`` templates, re-timed to start when
  they are injected. Their handlers are applied hour by hour to the rows as
  they are released, so a fault injected while the system runs changes only
  what comes after it.
- Late data is really late: the affected rows are held back and delivered
  ``release_delay_hours`` after the delay ends (a backfill), with an
  ``ingested_at`` time that shows when they arrived.
- Planned events (promo, holiday) are put on the calendar when injected and
  start ``planned_lead_hours`` later, as planned events would.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional

import pandas as pd

from .config import SimConfig
from .generator import build_world, realize
from .scenarios import ScenarioSpec, default_fault_scenarios, default_innocent_scenarios, inject_scenarios
from .schema import EVENTS_COLUMNS, TIMESTAMP_FORMAT

HOUR = pd.Timedelta(hours=1)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    kind: str  # "fault" or "innocent"
    template: ScenarioSpec


def presets() -> dict[str, Preset]:
    """Scenario templates by type, from the scenario injector's default suites."""
    anchor = pd.Timestamp("2026-01-05T00:00:00Z")
    out = {}
    labels = {
        "payment_failure": "Payment failure: Android in DE (after a checkout release)",
        "bad_search_release": "Search release breaks DE search results",
        "stock_out": "Supplier stock-out: electronics",
        "ad_budget_exhaustion": "Ad budget spent too fast: FR",
        "planned_promo": "Planned promo: UK (+25%)",
        "holiday": "Public holiday: US (-20%)",
        "late_missing_data": "Late data: DE iOS (4 hours, backfilled later)",
    }
    for spec in default_fault_scenarios(anchor) + default_innocent_scenarios(anchor):
        out[spec.scenario_type] = Preset(spec.scenario_type, labels.get(spec.scenario_type, spec.description),
                                         spec.kind, spec)
    return out


@dataclass
class Tick:
    hour: pd.Timestamp  # the hour that just passed
    rows: pd.DataFrame  # its rows plus any late rows released now
    late_rows: int = 0  # how many of ``rows`` are a backfill of earlier hours
    held_back: int = 0  # rows of this hour withheld (late data)
    events_changed: bool = False


@dataclass
class LiveMarketplace:
    history_days: int = 28
    live_days: int = 6
    seed: int = 42
    start: str = "2026-10-01"
    release_delay_hours: int = 3
    planned_lead_hours: int = 2

    events: pd.DataFrame = field(init=False)
    truth: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        cfg = SimConfig(start=self.start, days=self.history_days + self.live_days, seed=self.seed)
        self.config = cfg
        self._base = realize(build_world(cfg))
        self._base["ingested_at"] = self._base["timestamp"] + HOUR
        self.live_start = pd.Timestamp(self.start, tz="UTC") + pd.Timedelta(days=self.history_days)
        self.end = pd.Timestamp(self.start, tz="UTC") + pd.Timedelta(days=self.history_days + self.live_days)
        self.now = self.live_start  # the next hour to be released
        self._by_hour = {ts: g for ts, g in self._base.groupby("timestamp")}
        self._active: list[ScenarioSpec] = []
        self._late: list[ScenarioSpec] = []
        self._pending: list[tuple[pd.Timestamp, pd.DataFrame]] = []
        self.events = pd.DataFrame(columns=EVENTS_COLUMNS)
        self._n = 0
        self._presets = presets()

    # --- data ----------------------------------------------------------------
    def history(self) -> pd.DataFrame:
        """All rows before the live start (what the system learns from)."""
        return self._base[self._base["timestamp"] < self.live_start].reset_index(drop=True)

    @property
    def finished(self) -> bool:
        return self.now >= self.end

    def step(self) -> Tick:
        """Release the next hour."""
        if self.finished:
            raise StopIteration("the simulated period is over")
        hour = self.now
        rows = self._by_hour[hour].copy()
        specs = [s for s in self._active if s.start_time <= hour <= s.end_time]
        if specs:
            rows, _, _ = inject_scenarios(rows, specs, apply_stock_out_to_df=True)
        held = 0
        for spec in self._late:
            if spec.start_time <= hour <= spec.end_time:
                mask = pd.Series(True, index=rows.index)
                for dim, val in spec.affected_segment.items():
                    mask &= rows[dim] == val
                if mask.any():
                    release = spec.end_time + HOUR * (1 + self.release_delay_hours)
                    self._pending.append((release, rows[mask].copy()))
                    held += int(mask.sum())
                    rows = rows[~mask]
        late = [df.assign(ingested_at=hour + HOUR) for t, df in self._pending if t <= hour + HOUR]
        self._pending = [(t, df) for t, df in self._pending if t > hour + HOUR]
        n_late = sum(len(d) for d in late)
        out = pd.concat([rows, *late], ignore_index=True) if late else rows.reset_index(drop=True)
        self.now = hour + HOUR
        return Tick(hour, out, n_late, held)

    # --- scenarios -----------------------------------------------------------
    def preset_list(self) -> list[dict[str, str]]:
        return [{"key": p.key, "label": p.label, "kind": p.kind} for p in self._presets.values()]

    def inject(self, key: str, at: Optional[pd.Timestamp] = None, duration_hours: Optional[float] = None) -> dict:
        """Schedule a scenario from its preset. Returns its ground-truth entry."""
        if key not in self._presets:
            raise KeyError(f"unknown scenario {key!r}; choose from {sorted(self._presets)}")
        tpl = self._presets[key].template
        planned = bool(tpl.planned)
        start = pd.Timestamp(at) if at is not None else self.now + (HOUR * self.planned_lead_hours if planned else 0 * HOUR)
        start = start.tz_localize("UTC") if start.tzinfo is None else start
        length = pd.Timedelta(hours=duration_hours) if duration_hours else (tpl.end_time - tpl.start_time)
        self._n += 1
        spec = replace(tpl, scenario_id=f"{tpl.scenario_id}_{self._n}", start_time=start, end_time=start + length)
        event_id = None
        if spec.event_type is not None:
            event_id = f"evt_{100 + self._n}"
            ev_start = spec.start_time - pd.Timedelta(minutes=spec.event_lead_minutes)
            ev_end = None if spec.event_type == "release" else (
                spec.end_time + HOUR * (1 + self.release_delay_hours) if key == "late_missing_data" else spec.end_time)
            row = {
                "event_id": event_id,
                "start_time": ev_start.strftime(TIMESTAMP_FORMAT),
                "end_time": ev_end.strftime(TIMESTAMP_FORMAT) if ev_end is not None else None,
                "event_type": spec.event_type, "planned": planned,
                "country": spec.affected_segment.get("country"), "platform": spec.affected_segment.get("platform"),
                "category": spec.affected_segment.get("category"),
                "description": spec.description, "expected_effect": spec.expected_effect,
            }
            self.events = pd.concat([self.events, pd.DataFrame([row])], ignore_index=True)
        (self._late if key == "late_missing_data" else self._active).append(spec)
        entry = {
            "scenario_id": spec.scenario_id, "preset": key, "kind": spec.kind, "root_cause": spec.root_cause,
            "description": spec.description,
            "start_time": spec.start_time.strftime(TIMESTAMP_FORMAT), "end_time": spec.end_time.strftime(TIMESTAMP_FORMAT),
            "affected_segment": dict(spec.affected_segment), "primary_metric": spec.primary_metric,
            "related_event_id": event_id, "injected_at": self.now.strftime(TIMESTAMP_FORMAT),
        }
        self.truth.append(entry)
        return entry
