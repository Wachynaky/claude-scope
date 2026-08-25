#!/usr/bin/env python3
"""Official Claude model pricing for the panel.

Anthropic publishes no pricing endpoint, so the rates come from the "Model
pricing" table of its public pricing page. Requesting the ``.md`` variant of
that page returns the same table as a small markdown document (~45 KB instead
of ~1.2 MB of HTML) whose columns map one-to-one onto the rates the panel
needs, which makes it far steadier to parse than the rendered page.

Rates are stored in ``app/pricing.json``, next to the panel itself: the file
that ships with the app is the same one a download updates, so there is a single
place holding the prices and nothing about them is written outside the app.

This module owns everything about that lookup (URL, parsing, storage) and knows
nothing about HTTP serving or panel configuration, so it can be run and tested
on its own:

    python3 app/pricing_source.py              # print the current table
    python3 app/pricing_source.py --force      # ignore the cache
    python3 app/pricing_source.py --json       # machine-readable

Fetching is the only thing in Claude Scope that reaches the network, and the
panel only calls it once the user has allowed it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

PRICING_DOC_URL = os.environ.get("CLAUDE_SCOPE_PRICING_URL") or (
    "https://platform.claude.com/docs/en/about-claude/pricing.md"
)
# Human-facing address of the same table, shown in the panel.
PRICING_PAGE_URL = "https://platform.claude.com/docs/en/about-claude/pricing#model-pricing"

# The published table changes a handful of times per year, so one check per day
# is plenty and keeps page reloads off the network.
PRICING_MAX_AGE = 24 * 3600
PRICING_TIMEOUT = 20

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PARENS_RE = re.compile(r"\([^)]*\)")
_MONEY_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")
# Column order of the "Model pricing" table, mapped onto the rate names the
# panel uses internally. All five are US dollars per million tokens.
_PRICING_COLUMNS = ("input", "cache_creation_5m", "cache_creation_1h", "cache_read", "output")


def pricing_file() -> Path:
    """The app's own pricing file: ``app/pricing.json``, beside this module."""
    override = os.environ.get("CLAUDE_SCOPE_PRICING_FILE")
    return Path(override) if override else (Path(__file__).resolve().parent / "pricing.json")


# Keys this module owns inside pricing.json. Everything else in the file (the
# note, the fallback tiers, server tools, service-tier multipliers) belongs to
# whoever edits it and is preserved untouched on every update.
_MODELS_KEY = "models"
_CHECKED_KEY = "models_checked_at"
_UPDATED_KEY = "models_updated"
_SOURCE_KEY = "models_source"


def pricing_key(display_name: str) -> str:
    """``"Claude Opus 4.8"`` -> ``"opus-4.8"``.

    Produces the same shape the front-end derives from a model id, so the two
    meet on a common key without either side keeping a list of model names.
    """
    name = _MD_LINK_RE.sub(r"\1", display_name)   # keep link text, drop the URL
    name = _PARENS_RE.sub("", name)               # drop "(retired, except on ...)"
    name = " ".join(name.split()).lower()
    if name.startswith("claude "):
        name = name[len("claude "):]
    return name.replace(" ", "-")


def parse_pricing_markdown(text: str) -> dict[str, dict[str, float]]:
    """Extract the "Model pricing" table into ``{key: {rate: usd_per_mtok}}``.

    Rows that do not carry all five ``$ / MTok`` columns are skipped, so a
    change in the page layout degrades into fewer models rather than into wrong
    numbers; an empty result raises instead of silently zeroing every price.
    """
    start = text.find("## Model pricing")
    if start == -1:
        raise ValueError("The published page has no 'Model pricing' section")
    end = text.find("\n## ", start + 1)
    section = text[start:end if end != -1 else len(text)]

    models: dict[str, dict[str, float]] = {}
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 1 + len(_PRICING_COLUMNS):
            continue
        if not cells[0].lower().startswith("claude "):
            continue          # header and separator rows
        rates: dict[str, float] = {}
        for column, cell in zip(_PRICING_COLUMNS, cells[1:]):
            found = _MONEY_RE.search(cell)
            if not found:
                break
            rates[column] = float(found.group(1))
        if len(rates) == len(_PRICING_COLUMNS):
            models[pricing_key(cells[0])] = rates

    if not models:
        raise ValueError("Pricing table found but no model row could be parsed")
    return models


def download_pricing_markdown(url: str = PRICING_DOC_URL, timeout: int = PRICING_TIMEOUT) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ClaudeScope (local dashboard)",
            "Accept": "text/markdown, text/plain, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def read_pricing_file(path: Path | None = None) -> dict:
    """Whole pricing.json as a dict, or empty if it cannot be read."""
    try:
        with (path or pricing_file()).open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load_stored(path: Path | None = None) -> dict:
    """The stored rates in the shape the panel consumes, or empty if none."""
    cfg = read_pricing_file(path)
    models = cfg.get(_MODELS_KEY)
    if not isinstance(models, dict) or not models:
        return {}
    return {
        "models": models,
        "fetched_at": int(cfg.get(_CHECKED_KEY) or 0),
        "source": cfg.get(_SOURCE_KEY) or PRICING_DOC_URL,
        "page": PRICING_PAGE_URL,
    }


def store_models(models: dict, fetched_at: int, path: Path | None = None) -> None:
    """Write the downloaded rates into pricing.json, keeping everything else.

    Rewrites the app's own file, so the rates that ship with the panel and the
    rates a download brings live in one place. Written atomically; the rest of
    the document (note, tiers, server tools, multipliers) is left as it was.
    """
    target = path or pricing_file()
    cfg = read_pricing_file(target)
    cfg[_MODELS_KEY] = models
    cfg[_CHECKED_KEY] = int(fetched_at)
    cfg[_UPDATED_KEY] = time.strftime("%Y-%m-%d", time.localtime(fetched_at))
    cfg[_SOURCE_KEY] = PRICING_DOC_URL
    # Keep the file readable: metadata first, then the rates, then whatever else
    # the file already had, in its original order.
    preferred = ["note", _UPDATED_KEY, _CHECKED_KEY, _SOURCE_KEY, _MODELS_KEY]
    cfg = {**{k: cfg[k] for k in preferred if k in cfg},
           **{k: v for k, v in cfg.items() if k not in preferred}}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(target)


def fetch_pricing(force: bool = False, path: Path | None = None) -> dict:
    """Return ``{models, fetched_at, source, page, cached, stale, changes}``.

    Rates read less than a day ago short-circuit the request. When the network
    fails, the stored ones are returned flagged as stale, so an offline machine
    keeps the last known rates instead of losing pricing altogether.
    """
    target = path or pricing_file()
    stored = load_stored(target)
    age = time.time() - float(stored.get("fetched_at") or 0)
    if stored and not force and 0 <= age < PRICING_MAX_AGE:
        return {**stored, "cached": True, "stale": False, "changes": []}

    try:
        models = parse_pricing_markdown(download_pricing_markdown())
    except Exception as exc:
        if stored:
            return {**stored, "cached": True, "stale": True, "error": str(exc), "changes": []}
        raise

    fetched_at = int(time.time())
    try:
        store_models(models, fetched_at, target)
    except OSError:
        pass          # a read-only install must not break the lookup itself
    # What this download changed with respect to the previous one. Reported only
    # here, never stored: it describes this check, not the state of the table,
    # so it is not something the panel should keep repeating.
    return {
        "models": models,
        "fetched_at": fetched_at,
        "source": PRICING_DOC_URL,
        "page": PRICING_PAGE_URL,
        "cached": False,
        "stale": False,
        "changes": diff_models(stored.get("models") or {}, models),
    }


def diff_models(previous: dict, current: dict) -> list[dict]:
    """Models whose rates moved between two versions of the table.

    An empty ``previous`` (first ever check) reports no changes: there is
    nothing the user could have been charged wrongly for yet.
    """
    if not previous:
        return []
    changed = []
    for key, rates in current.items():
        before = previous.get(key)
        if before is not None and before != rates:
            changed.append({"model": key, "from": before, "to": rates})
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="ignore the cache and download again")
    parser.add_argument("--json", action="store_true", help="print the raw payload")
    args = parser.parse_args()

    try:
        data = fetch_pricing(force=args.force)
    except Exception as exc:
        print(f"Could not read the pricing table: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    changed = data.get("changes") or []
    if changed:
        print(f"{len(changed)} model(s) changed price since the last check: "
              f"{', '.join(c['model'] for c in changed)}\n")
    origin = "app/pricing.json" if data.get("cached") else "download"
    if data.get("stale"):
        origin = "app/pricing.json (page unreachable)"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("fetched_at") or 0))
    print(f"{len(data['models'])} models, from {origin}, last checked {when}")
    print(f"{data.get('page', PRICING_PAGE_URL)}\n")
    width = max(len(k) for k in data["models"])
    header = f"{'model'.ljust(width)}  {'input':>8} {'5m write':>9} {'1h write':>9} {'cache rd':>9} {'output':>8}"
    print(header)
    print("-" * len(header))
    for key, rates in data["models"].items():
        print(f"{key.ljust(width)}  {rates['input']:>8} {rates['cache_creation_5m']:>9} "
              f"{rates['cache_creation_1h']:>9} {rates['cache_read']:>9} {rates['output']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
