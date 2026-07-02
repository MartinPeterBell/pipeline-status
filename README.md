# pipeline-status

[![CI](https://github.com/MartinPeterBell/pipeline-status/actions/workflows/ci.yml/badge.svg)](https://github.com/MartinPeterBell/pipeline-status/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Runtime deps: 0](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

A tiny, zero-dependency generator that turns an autonomous pipeline's **own run
ledger** into a static, self-contained operational-status page — the kind of
page you can point someone at to *show* reliability instead of claiming it.

It answers one question honestly: **did this thing actually run, day after day,
unattended?** Every number on the page is derived from the ledger, so there is
nothing to hand-enter and nothing to fabricate — the missed day is shown, not
hidden (a visible gap is more credible than a wall of green, and it's proof the
monitoring caught it).

## Design principles

- **Generic about the product.** The page reports operational reliability —
  cadence, day coverage, streaks, last run — and *nothing* about what the
  pipeline actually produces. The loader reads only `date` / `uploaded` /
  `dry_run` / `skipped`; product fields (URLs, titles) are never touched, so they
  can't leak onto a public page.
- **Absolute dates only.** The last-run value is a fixed date, never a relative
  "yesterday" — so a statically-served copy stays truthful however long after
  generation it's read.
- **No vanity, no sales.** No view/subscriber counts, no "hire me", no
  manufactured badges. Just the operational record.
- **Zero third-party dependencies.** Pure Python standard library, so it runs on
  the pipeline's own box with nothing to install or keep patched.

## Ledger format

Any JSON that is either a list of run records or `{"episodes": [...]}`, where a
record has at least:

```json
{ "date": "2026-05-12", "uploaded": true, "dry_run": false, "skipped": false }
```

A run counts toward the record only when `uploaded` is true and it is neither a
`dry_run` nor `skipped`. Rows with a missing or unparseable `date` are skipped,
not fatal — one corrupt row must never take the status page down.

## Usage

```bash
python generate_status.py \
  --ledger /path/to/episodes.json \
  --out public/index.html \
  --operator "Your Name" \
  --healthcheck-badge "https://healthchecks.io/b/2/<uuid>.svg"   # optional
```

`--healthcheck-badge` is optional: supply your [Healthchecks.io](https://healthchecks.io)
status badge URL and a live up/late/down block is added; omit it and the block
is left out entirely (an empty "real-time monitoring goes here" promise is worse
than none).

## Deploying it live

The page is only as current as the ledger it was generated from, so generate it
where the pipeline runs:

- **On the pipeline host (recommended).** Add a line to the wrapper that runs the
  pipeline so the page regenerates right after each run, then serve `public/`
  with any static server (nginx, Caddy, `python -m http.server`).
- **GitHub Pages.** Commit `public/index.html` and enable Pages; re-commit the
  regenerated file on a schedule. Keep the repo name neutral if you want to stay
  generic about the product.

> The `public/index.html` committed here is a **snapshot** generated from a
> development copy of the ledger — a representative sample, not a live feed.

## Development

Clean-code bar matches intent: `ruff`, `mypy --strict`, and `pytest` all pass.

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

## License

MIT — see [LICENSE](LICENSE).
