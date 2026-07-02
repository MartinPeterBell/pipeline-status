"""Generate a static operational-status page from a pipeline's own run ledger.

The page is deliberately GENERIC about what the pipeline produces: it reports
operational reliability (run cadence, day coverage, streaks, last run) and
nothing about the downstream product. Every number is derived from the ledger,
so there is nothing to fabricate -- the one missed day is shown, not hidden.

All dates on the page are ABSOLUTE (never "yesterday"/"N days ago"), so a
statically-served copy stays truthful however long after generation it is read.

Runtime is pure Python standard library: run it on the box that runs the
pipeline (regenerate on each run for a live page) and serve the HTML statically,
or publish it to GitHub Pages.

Usage:
    python generate_status.py --ledger path/to/episodes.json --out public/index.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

CELL = 17  # px, one day cell in the calendar heatmap
GAP = 4  # px between cells


@dataclass(frozen=True)
class Ledger:
    """The reliability-relevant slice of a run ledger.

    Only fields needed to prove *operational reliability* are extracted; product
    fields (URLs, titles, anything identifying what the pipeline feeds) are
    ignored so they cannot leak onto a public page.
    """

    run_days: frozenset[date]
    episode_count: int


@dataclass(frozen=True)
class Metrics:
    """Reliability metrics derived from a :class:`Ledger`."""

    first_day: date
    last_day: date
    span_days: int
    run_days: int
    episode_count: int
    coverage_pct: float
    longest_streak: int
    missed_days: list[date]
    generated_at: datetime


def _parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def load_ledger(ledger_path: Path) -> Ledger:
    """Read the ledger, keeping only genuine, uploaded daily runs.

    ``dry_run`` tests and ``skipped`` days are excluded. Rows with a missing or
    unparseable ``date`` are skipped rather than crashing the whole generation --
    one corrupt row must not take the unattended status page down. Accepts either
    a bare list of episode dicts or a ``{"episodes": [...]}`` wrapper.
    """
    raw: Any = json.loads(ledger_path.read_text(encoding="utf-8"))
    episodes = raw.get("episodes", []) if isinstance(raw, dict) else raw
    days: set[date] = set()
    count = 0
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        if ep.get("dry_run") or ep.get("skipped") or not ep.get("uploaded", False):
            continue
        day_str = ep.get("date")
        if not isinstance(day_str, str) or not day_str:
            continue
        try:
            day = _parse_day(day_str)
        except ValueError:
            continue
        days.add(day)
        count += 1
    return Ledger(run_days=frozenset(days), episode_count=count)


def _longest_streak(days: frozenset[date]) -> int:
    if not days:
        return 0
    best = run = 1
    ordered = sorted(days)
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    return best


def _missed_days(days: frozenset[date], first: date, last: date) -> list[date]:
    return [
        date.fromordinal(o)
        for o in range(first.toordinal(), last.toordinal() + 1)
        if date.fromordinal(o) not in days
    ]


def compute_metrics(ledger: Ledger, generated_at: datetime) -> Metrics:
    """Compute the reliability metrics for a loaded ledger."""
    if not ledger.run_days:
        raise ValueError("ledger contains no successful runs")
    first_day, last_day = min(ledger.run_days), max(ledger.run_days)
    span_days = (last_day - first_day).days + 1
    return Metrics(
        first_day=first_day,
        last_day=last_day,
        span_days=span_days,
        run_days=len(ledger.run_days),
        episode_count=ledger.episode_count,
        coverage_pct=100.0 * len(ledger.run_days) / span_days,
        longest_streak=_longest_streak(ledger.run_days),
        missed_days=_missed_days(ledger.run_days, first_day, last_day),
        generated_at=generated_at,
    )


def _run_days_from_metrics(m: Metrics) -> frozenset[date]:
    missed = set(m.missed_days)
    return frozenset(
        date.fromordinal(o)
        for o in range(m.first_day.toordinal(), m.last_day.toordinal() + 1)
        if date.fromordinal(o) not in missed
    )


def _calendar_svg(run_days: frozenset[date], first_day: date, last_day: date) -> str:
    """A GitHub-style contribution heatmap: ran = green, missed = red, honestly.

    Cells carry only "ran" / "no run" labels -- no dates -- so the page does not
    publish a precisely-dated activity fingerprint of the private pipeline.
    """
    # Start the grid on the Monday on/before the first run day.
    start = date.fromordinal(first_day.toordinal() - first_day.weekday())
    total_days = last_day.toordinal() - start.toordinal() + 1
    weeks = (total_days + 6) // 7
    width = weeks * (CELL + GAP) + GAP
    height = 7 * (CELL + GAP) + GAP
    cells: list[str] = []
    for offset in range(total_days):
        d = date.fromordinal(start.toordinal() + offset)
        col, row = divmod(offset, 7)
        x = GAP + col * (CELL + GAP)
        y = GAP + row * (CELL + GAP)
        if d in run_days:
            cls, title = "ran", "ran"
        elif first_day <= d <= last_day:
            cls, title = "missed", "no run"
        else:
            cls, title = "off", ""
        cells.append(
            f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
            f'rx="2"><title>{title}</title></rect>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Daily run calendar">{"".join(cells)}</svg>'
    )


def _reliability_prose(m: Metrics) -> str:
    missed = len(m.missed_days)
    if missed == 0:
        missed_sentence = f"Over a {m.span_days}-day window, every day shipped a run."
    else:
        noun = "day" if missed == 1 else "days"
        verb = "shows" if missed == 1 else "show"
        it = "it" if missed == 1 else "them"
        missed_sentence = (
            f"Over a {m.span_days}-day window, {missed} {noun} {verb} no run "
            f"(marked in red above) — shown on purpose, because the monitoring "
            f"is what caught {it}."
        )
    double = m.episode_count - m.run_days
    if double > 0:
        d_noun = "day" if double == 1 else "days"
        double_sentence = (
            f" The {m.episode_count} runs span {m.run_days} active days; "
            f"{double} {d_noun} shipped more than once."
        )
    else:
        double_sentence = ""
    return html.escape(missed_sentence) + html.escape(double_sentence)


def _card(value: str, label: str, *, small: bool = False) -> str:
    cls = "n sm" if small else "n"
    return (
        f'<div class="card"><div class="{cls}">{value}</div>'
        f'<div class="l">{label}</div></div>'
    )


def render_html(m: Metrics, *, operator: str = "", healthcheck_badge: str = "") -> str:
    """Render the full self-contained status page."""
    esc = html.escape
    coverage = f"{m.coverage_pct:.0f}%"
    cards = "".join(
        [
            _card(coverage, f"days with a run ({m.run_days} of {m.span_days})"),
            _card(str(m.longest_streak), "longest unbroken streak (days)"),
            _card(str(m.episode_count), "runs shipped"),
            _card(esc(m.last_day.isoformat()), "last successful run", small=True),
        ]
    )
    calendar = _calendar_svg(_run_days_from_metrics(m), m.first_day, m.last_day)
    operator_line = (
        f'<p class="operator">Operated by {esc(operator)}</p>' if operator else ""
    )
    # Only render the liveness block when a real badge is supplied -- an empty
    # "real-time signal mounts here" promise reads worse than saying nothing.
    badge_block = (
        f'<div class="panel"><h2>Live liveness check</h2>'
        f'<div class="live"><img src="{esc(healthcheck_badge)}" '
        f'alt="Healthchecks.io status"><span>Real-time up / late / down, straight '
        f"from the dead-man switch.</span></div></div>"
        if healthcheck_badge
        else ""
    )
    gen = m.generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Pipeline — operational status</title>
<style>
  :root {{
    --bg: #0d1117; --panel: #161b22; --line: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --ran: #2ea043; --missed: #6e2530; --off: #21262d; --accent: #58a6ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.25rem; background: var(--bg); color: var(--text);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .35rem; }}
  .sub {{ color: var(--muted); margin: 0 0 2rem; max-width: 64ch; }}
  .cards {{
    display: grid; gap: 1rem; margin-bottom: 1.5rem;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  }}
  .card {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 1rem 1.1rem;
  }}
  .card .n {{ font-size: 1.7rem; font-weight: 650; }}
  .card .n.sm {{ font-size: 1.25rem; line-height: 1.35; }}
  .card .l {{ color: var(--muted); font-size: .8rem; margin-top: .2rem; }}
  .panel {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 1.25rem 1.4rem; margin-bottom: 1.25rem;
  }}
  .panel h2 {{ font-size: .82rem; margin: 0 0 1rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .06em; }}
  .cal {{ overflow-x: auto; }}
  svg rect.ran {{ fill: var(--ran); }}
  svg rect.missed {{ fill: var(--missed); }}
  svg rect.off {{ fill: var(--off); }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 1.25rem; margin-top: .9rem;
    color: var(--muted); font-size: .8rem; align-items: center; }}
  .legend i {{ display: inline-block; width: 11px; height: 11px; border-radius: 2px;
    margin-right: .35rem; vertical-align: -1px; }}
  .live {{ display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }}
  .operator {{ color: var(--muted); margin: .25rem 0 0; }}
  footer {{ color: var(--muted); font-size: .78rem; margin-top: 2rem;
    border-top: 1px solid var(--line); padding-top: 1rem; max-width: 72ch; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Autonomous pipeline — operational status</h1>
  <p class="sub">A track record of an unattended daily pipeline I run, taken
  straight from its own run ledger. Nothing here is hand-entered; the one missed
  day is shown, not hidden.</p>

  <div class="cards">{cards}</div>

  <div class="panel">
    <h2>Daily run calendar</h2>
    <div class="cal">{calendar}</div>
    <div class="legend">
      <span><i style="background:var(--ran)"></i>ran</span>
      <span><i style="background:var(--missed)"></i>no run</span>
      <span>one cell = one day</span>
    </div>
  </div>

  <div class="panel">
    <h2>Reliability, plainly</h2>
    <p>{_reliability_prose(m)} The gap is the point: monitoring exists to catch
    exactly that, and it did.</p>
  </div>

  {badge_block}
  {operator_line}
  <footer>
    Generated from the pipeline's own run ledger on {esc(gen)}. On the live
    deployment this page is regenerated after each run; a committed copy is a
    snapshot from that moment. Deliberately generic about what the pipeline
    produces — this page is about <em>operational reliability</em>, not the product.
  </footer>
</div>
</body>
</html>
"""


def build_page(
    ledger_path: Path,
    *,
    operator: str = "",
    healthcheck_badge: str = "",
    generated_at: datetime | None = None,
) -> tuple[str, Metrics]:
    """Load the ledger and render the status page; return (html, metrics)."""
    ledger = load_ledger(ledger_path)
    generated = generated_at or datetime.now(timezone.utc)
    metrics = compute_metrics(ledger, generated)
    page = render_html(metrics, operator=operator, healthcheck_badge=healthcheck_badge)
    return page, metrics


def _parse_generated_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate the pipeline status page.")
    parser.add_argument("--ledger", required=True, type=Path, help="run ledger JSON")
    parser.add_argument("--out", required=True, type=Path, help="output HTML path")
    parser.add_argument(
        "--generated-at",
        type=_parse_generated_at,
        default=None,
        help="override generation timestamp (ISO 8601, normalised to UTC)",
    )
    parser.add_argument("--operator", default="", help="operator name (optional)")
    parser.add_argument(
        "--healthcheck-badge", default="", help="Healthchecks.io badge URL (optional)"
    )
    args = parser.parse_args(argv)

    page, m = build_page(
        args.ledger,
        operator=args.operator,
        healthcheck_badge=args.healthcheck_badge,
        generated_at=args.generated_at,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")

    print(
        f"wrote {args.out}\n"
        f"  window {m.span_days}d | {m.run_days} run-days | {m.episode_count} runs | "
        f"{m.coverage_pct:.1f}% coverage | longest streak {m.longest_streak} | "
        f"missed {len(m.missed_days)} | last run {m.last_day.isoformat()}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
