"""Resolve Zotero keys (from Ethereal Style's reading progress) to titles.

Uses only Zotero's local API on 127.0.0.1:23119 (Zotero 7: Settings → Advanced →
"Allow other applications on this computer to communicate with Zotero").
Nothing leaves the machine. Results are cached in ~/.cache/dreamon/zotero.json.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:23119/api/users/0/items/"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dreamon" / "zotero.json"


def _get(key: str) -> dict | None:
    try:
        with urllib.request.urlopen(API + key, timeout=3) as r:
            return json.load(r).get("data")
    except Exception:
        return None


def _authors(creators: list[dict]) -> str:
    names = [c.get("lastName") or c.get("name") or "" for c in creators
             if c.get("creatorType") in (None, "author", "editor")]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} et al."


def _year(date_str: str) -> str:
    for token in str(date_str or "").replace("/", "-").split("-"):
        token = token.strip()
        if len(token) == 4 and token.isdigit():
            return token
    return ""


class Resolver:
    def __init__(self) -> None:
        try:
            self.cache: dict[str, dict] = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            self.cache = {}

    def save(self) -> None:
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(self.cache), encoding="utf-8")
        except OSError:
            pass

    def cached(self, key: str) -> dict | None:
        return self.cache.get(key)

    def resolve(self, key: str) -> dict | None:
        """{'title', 'authors', 'year', 'item'} for an attachment or item key, or None."""
        if key in self.cache:
            return self.cache[key]
        data = _get(key)
        if not data:
            return None
        item_key = key
        if data.get("itemType") == "attachment" and data.get("parentItem"):
            parent = _get(data["parentItem"])
            if parent:
                item_key = data["parentItem"]
                data = parent
        info = {
            "title": data.get("title") or "Untitled",
            "authors": _authors(data.get("creators") or []),
            "year": _year(data.get("date", "")),
            "item": item_key,
        }
        self.cache[key] = info
        return info
