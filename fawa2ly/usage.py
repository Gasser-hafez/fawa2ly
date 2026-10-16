"""
How many tokens the app has used (2026-09-11, maintainer's request).

Counted where every model request passes, the provider, by wrapping it: the answers to a call,
the early (speculative) requests, More and Retry, the speed test and the role analysis all go
through a provider, so none of them can be left out by a code path that forgot to report.

    %APPDATA%/Fawa2ly/usage.json
    {"days": {"2026-09-11": {"gemini/gemini-3.5-flash-lite":
                              {"requests": 12, "input_tokens": 9000, "output_tokens": 800, "unreported": 0}}}}

Some providers do not report usage for a streamed reply; those requests are counted under
"unreported" rather than as zero tokens, so a summary never claims a number it does not have.
Recording is best effort: a usage file that cannot be written must never break a call.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import AsyncIterator, Optional

from . import settings as user_settings
from .llm.base import SuggestionEvent, SuggestionProvider, SuggestionRequest

FIELDS = ("requests", "input_tokens", "output_tokens", "unreported")


def usage_path() -> str:
    return os.path.join(user_settings.app_dir(), "usage.json")


def _load() -> dict:
    try:
        with open(usage_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"days": {}}
    return data if isinstance(data, dict) and isinstance(data.get("days"), dict) else {"days": {}}


def _save(data: dict) -> None:
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    tmp = usage_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, usage_path())


def record(provider: str, model: str, usage: Optional[dict], day: Optional[datetime.date] = None) -> None:
    """Count one finished request."""
    try:
        data = _load()
        key = f"{provider}/{model}" if model else provider
        rows = data["days"].setdefault((day or datetime.date.today()).isoformat(), {})
        row = rows.setdefault(key, dict.fromkeys(FIELDS, 0))
        row["requests"] = row.get("requests", 0) + 1
        tokens_in = (usage or {}).get("input_tokens")
        tokens_out = (usage or {}).get("output_tokens")
        if tokens_in is None and tokens_out is None:
            row["unreported"] = row.get("unreported", 0) + 1
        else:
            row["input_tokens"] = row.get("input_tokens", 0) + int(tokens_in or 0)
            row["output_tokens"] = row.get("output_tokens", 0) + int(tokens_out or 0)
        _save(data)
    except (OSError, ValueError, TypeError):
        pass


def summary(today: Optional[datetime.date] = None) -> dict:
    """Totals for today, this month and all time, and all-time totals per provider/model."""
    today = today or datetime.date.today()
    days = _load()["days"]
    periods = {"today": lambda d: d == today.isoformat(),
               "month": lambda d: d.startswith(today.strftime("%Y-%m")),
               "all": lambda d: True}
    out = {name: dict.fromkeys(FIELDS, 0) for name in periods}
    models: dict[str, dict] = {}
    for day, rows in days.items():
        for key, row in rows.items():
            for name, match in periods.items():
                if match(day):
                    for f in FIELDS:
                        out[name][f] += int(row.get(f, 0) or 0)
            total = models.setdefault(key, dict.fromkeys(FIELDS, 0))
            for f in FIELDS:
                total[f] += int(row.get(f, 0) or 0)
    out["models"] = dict(sorted(models.items(), key=lambda kv: -(kv[1]["input_tokens"] + kv[1]["output_tokens"])))
    out["since"] = min(days) if days else None
    return out


def reset() -> None:
    try:
        os.remove(usage_path())
    except OSError:
        pass


class MeteredProvider(SuggestionProvider):
    """A provider whose every finished request is counted. Everything else is the wrapped one's."""

    def __init__(self, inner: SuggestionProvider):
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @model.setter
    def model(self, value: str) -> None:
        self._inner.model = value

    @property
    def inner(self) -> SuggestionProvider:
        return self._inner

    async def suggest(self, req: SuggestionRequest) -> AsyncIterator[SuggestionEvent]:
        usage, model, finished = None, self._inner.model, False
        try:
            async for ev in self._inner.suggest(req):
                if ev.model:
                    model = ev.model
                if ev.kind in ("done", "error"):
                    usage, finished = ev.usage, True
                yield ev
        finally:
            if finished:
                record(self._inner.name, model, usage)

    async def close(self) -> None:
        await self._inner.close()

    def __getattr__(self, attr):
        return getattr(self._inner, attr)
