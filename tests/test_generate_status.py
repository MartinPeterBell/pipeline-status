"""Tests for the pipeline status-page generator."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import generate_status as gs


def _write_ledger(tmp_path: Path, episodes: list[dict[str, object]]) -> Path:
    path = tmp_path / "episodes.json"
    path.write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    return path


def _ep(day: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "date": day,
        "dry_run": False,
        "skipped": False,
        "uploaded": True,
        "youtube_url": f"https://youtu.be/SECRET-{day}",
    }
    base.update(over)
    return base


def test_load_ledger_counts_only_real_uploads(tmp_path: Path) -> None:
    path = _write_ledger(
        tmp_path,
        [
            _ep("2026-05-12"),
            _ep("2026-05-13", dry_run=True),  # excluded
            _ep("2026-05-14", skipped=True),  # excluded
            _ep("2026-05-15", uploaded=False),  # excluded
            _ep("2026-05-16"),
        ],
    )
    ledger = gs.load_ledger(path)
    assert ledger.run_days == frozenset({date(2026, 5, 12), date(2026, 5, 16)})
    assert ledger.episode_count == 2


def test_load_ledger_counts_double_post_days(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, [_ep("2026-05-12"), _ep("2026-05-12")])
    ledger = gs.load_ledger(path)
    assert len(ledger.run_days) == 1  # one distinct day
    assert ledger.episode_count == 2  # two runs


def test_load_ledger_skips_malformed_date_without_crashing(tmp_path: Path) -> None:
    path = _write_ledger(
        tmp_path,
        [_ep("2026-05-12"), _ep("not-a-date"), _ep("2026-13-99"), _ep("2026-05-13")],
    )
    ledger = gs.load_ledger(path)
    assert ledger.run_days == frozenset({date(2026, 5, 12), date(2026, 5, 13)})
    assert ledger.episode_count == 2


def test_load_ledger_accepts_bare_list(tmp_path: Path) -> None:
    path = tmp_path / "bare.json"
    path.write_text(json.dumps([_ep("2026-05-12")]), encoding="utf-8")
    assert gs.load_ledger(path).episode_count == 1


def test_longest_streak() -> None:
    assert gs._longest_streak(frozenset()) == 0
    assert gs._longest_streak(frozenset({date(2026, 5, 12)})) == 1
    days = {date(2026, 5, 12), date(2026, 5, 13), date(2026, 5, 15)}
    assert gs._longest_streak(frozenset(days)) == 2


def test_compute_metrics_coverage_and_gap() -> None:
    # 12th, 13th, (14th missing), 15th -> span 4, 3 run-days, one gap.
    days = frozenset({date(2026, 5, 12), date(2026, 5, 13), date(2026, 5, 15)})
    m = gs.compute_metrics(
        gs.Ledger(run_days=days, episode_count=3),
        datetime(2026, 5, 16, tzinfo=timezone.utc),
    )
    assert m.span_days == 4
    assert m.run_days == 3
    assert m.coverage_pct == pytest.approx(75.0)
    assert m.missed_days == [date(2026, 5, 14)]
    assert m.longest_streak == 2


def test_compute_metrics_rejects_empty() -> None:
    with pytest.raises(ValueError, match="no successful runs"):
        gs.compute_metrics(
            gs.Ledger(run_days=frozenset(), episode_count=0),
            datetime(2026, 5, 16, tzinfo=timezone.utc),
        )


def _sample_metrics() -> gs.Metrics:
    days = frozenset({date(2026, 5, 12), date(2026, 5, 13), date(2026, 5, 15)})
    return gs.compute_metrics(
        gs.Ledger(run_days=days, episode_count=4),
        datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc),
    )


def test_calendar_svg_leaks_no_dates() -> None:
    m = _sample_metrics()
    svg = gs._calendar_svg(gs._run_days_from_metrics(m), m.first_day, m.last_day)
    assert "2026-05" not in svg  # per-cell dates must not appear
    assert "<title>ran</title>" in svg
    assert "<title>no run</title>" in svg


def test_render_html_has_no_product_identity_or_raw_url() -> None:
    m = _sample_metrics()
    page = gs.render_html(m, operator="Martin Bell")
    assert "youtu" not in page.lower()
    assert "SECRET" not in page
    # Absolute (non-decaying) last-run date, never a relative "yesterday".
    assert m.last_day.isoformat() in page
    assert "yesterday" not in page.lower()


def test_render_html_badge_block_only_when_url_given() -> None:
    m = _sample_metrics()
    assert "healthchecks.io status" not in gs.render_html(m).lower()
    with_badge = gs.render_html(m, healthcheck_badge="https://hc.example/b.svg")
    assert "https://hc.example/b.svg" in with_badge


def test_render_html_discloses_double_post_reconciliation() -> None:
    # episode_count 4 across 3 run-days -> one extra run must be disclosed.
    page = gs.render_html(_sample_metrics())
    assert "4 runs span 3 active days" in page


def test_parse_generated_at_normalises_to_utc() -> None:
    naive = gs._parse_generated_at("2026-06-30T10:00:00")
    assert naive.tzinfo == timezone.utc
    assert naive.hour == 10  # naive assumed UTC
    aware = gs._parse_generated_at("2026-06-30T10:00:00+02:00")
    assert aware.tzinfo == timezone.utc
    assert aware.hour == 8  # converted to UTC


def test_build_page_end_to_end(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, [_ep("2026-05-12"), _ep("2026-05-13")])
    page, m = gs.build_page(
        path, generated_at=datetime(2026, 5, 14, tzinfo=timezone.utc)
    )
    assert "<!DOCTYPE html>" in page
    assert m.run_days == 2
