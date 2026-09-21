"""Small, local translation helper used by backend-owned messages and templates.

Italian is the source language. Browser requests can explicitly ask for another
language (``X-UI-Language``); background jobs use ``DEFAULT_UI_LANGUAGE``.
User-provided values are never machine translated.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

SUPPORTED = ("it", "en", "fr", "de")


def normalize_language(value: str | None, fallback: str = "en") -> str:
    lang = str(value or "").lower().split("-")[0].split("_")[0]
    return lang if lang in SUPPORTED else fallback


DEFAULT_LANGUAGE = normalize_language(os.getenv("DEFAULT_UI_LANGUAGE"), "en")
_BASE = Path(__file__).resolve().parent.parent / "static" / "i18n"


@lru_cache(maxsize=None)
def _catalog(language: str) -> dict[str, str]:
    language = normalize_language(language, DEFAULT_LANGUAGE)
    if language == "it":
        # The Italian dictionary is useful for key membership and keeps all
        # languages structurally aligned, even though the source text is Italian.
        return json.loads((_BASE / "it.json").read_text("utf-8"))
    return json.loads((_BASE / f"{language}.json").read_text("utf-8"))


@lru_cache(maxsize=None)
def _matcher(language: str) -> re.Pattern[str]:
    keys = sorted((k for k in _catalog(language) if not re.search(r"[{}<>]", k)), key=len, reverse=True)
    if not keys:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")


def t(text, language: str | None = None):
    """Translate app-owned text while preserving surrounding whitespace.

    ``text`` may contain more than one catalogued fragment; this is useful for
    compact subjects such as ``"2 aggiornati, 1 fallito"``. Unknown text is
    returned unchanged so user values and technical strings are safe.
    """
    if not isinstance(text, str):
        return text
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    if lang == "it":
        return text
    catalog = _catalog(lang)
    key = re.sub(r"\s+", " ", text).strip()
    if key in catalog:
        return text[: len(text) - len(text.lstrip())] + catalog[key] + text[len(text.rstrip()) :]
    matcher = _matcher(lang)
    return matcher.sub(lambda m: catalog.get(m[0], m[0]), text)


def template(text: str, language: str | None = None) -> str:
    """Translate visible text in an HTML/Jinja template without touching code.

    Markup, CSS, Jinja expressions and interpolated values are kept byte-for-byte;
    only application-owned human text between them is localized.
    """
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    if not isinstance(text, str) or lang == "it":
        return text
    parts = re.split(r"(<style\b.*?</style>|{{.*?}}|{%.*?%}|<[^>]*>)", text, flags=re.S | re.I)
    for i, part in enumerate(parts):
        if part.startswith(("{{", "{%")):
            continue
        if part.startswith("<"):
            if part.lower().startswith("<html"):
                parts[i] = re.sub(r'lang="[^"]*"', f'lang="{lang}"', part, count=1)
            elif part.lower().startswith("<style"):
                page = {"it": "Pagina ", "en": "Page ", "fr": "Page ", "de": "Seite "}[lang]
                of = {"it": " di ", "en": " of ", "fr": " sur ", "de": " von "}[lang]
                parts[i] = part.replace('"Pagina "', '"' + page + '"').replace('" di "', '"' + of + '"')
            continue
        parts[i] = t(part, lang)
    return "".join(parts)
