"""Helpers that keep generated tests aligned with the operator's request."""

from __future__ import annotations

import re
from typing import Any, Iterable

MODULE_ALIASES: dict[str, list[str]] = {
    "signup": ["signup", "sign up", "sign-up", "register", "registration", "create account"],
    "login": ["login", "log in", "sign in", "signin", "authenticate", "authentication"],
    "logout": ["logout", "log out", "sign out", "signout"],
    "contacts": ["contact", "contacts", "address book"],
    "dashboard": ["dashboard", "home page", "overview"],
    "profile": ["profile", "account settings", "my account"],
    "search": ["search", "find", "filter"],
    "forms": ["form", "forms"],
}

SMOKE_MARKERS = ("smoke:", "page title present", "sanity:")
NEGATIVE_MARKERS = (
    "empty value",
    "required field",
    "invalid",
    "missing",
    "blank",
    "wrong credential",
    "error case",
    "negative",
)


def requested_types(objective: str | None, explicit: list[str] | None = None) -> list[str]:
    """Return the test-case types the operator asked for."""
    if explicit:
        cleaned = [t for t in explicit if t in {"positive", "negative", "exploratory"}]
        if cleaned:
            return cleaned
    obj = (objective or "").lower()
    if "positive only" in obj or "positive/happy-path" in obj or "do not test error cases" in obj:
        return ["positive"]
    if "negative only" in obj or "focus exclusively on error" in obj:
        return ["negative"]
    if "both positive and negative" in obj or "generate and execute both" in obj:
        return ["positive", "negative"]
    return []


def focus_terms(objective: str | None, explicit: list[str] | None = None) -> list[str]:
    """Feature keywords the run must stay inside."""
    terms: list[str] = []
    for item in explicit or []:
        if item:
            terms.append(str(item).lower().strip())
    obj = objective or ""
    match = re.search(r"Test ONLY the (.+?) feature", obj, re.IGNORECASE)
    if match:
        raw = match.group(1).lower()
        terms.extend(t.strip() for t in re.split(r"[\s,&/]+", raw) if t.strip() and t.strip() != "the")
    return list(dict.fromkeys(terms))


def expand_terms(terms: Iterable[str]) -> list[str]:
    """Expand module names to aliases so 'signup' also matches 'sign up'."""
    expanded: list[str] = []
    for term in terms:
        t = (term or "").lower().strip()
        if not t:
            continue
        expanded.append(t)
        if t in MODULE_ALIASES:
            expanded.extend(MODULE_ALIASES[t])
        for aliases in MODULE_ALIASES.values():
            if t in aliases:
                expanded.extend(aliases)
                expanded.append(next(k for k, v in MODULE_ALIASES.items() if v is aliases))
    # Keep longer phrases first so "sign up" is tried before leftover fragments
    return list(dict.fromkeys(a for a in expanded if a))


def _haystack(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def page_matches_focus(
    *,
    url: str = "",
    title: str = "",
    headings: list[str] | None = None,
    extra: str = "",
    focus: list[str],
) -> bool:
    """True when the observed page is the requested feature (or no focus set)."""
    terms = expand_terms(focus)
    if not terms:
        return True
    text = _haystack(url, title, extra, *(headings or []))
    return any(term in text for term in terms)


def is_smoke_title(title: str, description: str = "") -> bool:
    text = _haystack(title, description)
    return any(marker in text for marker in SMOKE_MARKERS)


def is_negative_title(title: str, description: str = "", category: str = "", test_case_type: str = "") -> bool:
    if (test_case_type or "").lower() == "negative" or (category or "").lower() == "negative":
        return True
    text = _haystack(title, description, category)
    return any(marker in text for marker in NEGATIVE_MARKERS)


def case_matches_request(
    *,
    title: str,
    description: str = "",
    category: str = "",
    test_case_type: str = "",
    objective: str | None = None,
    focus_modules: list[str] | None = None,
    test_case_types: list[str] | None = None,
) -> bool:
    """Keep a generated/saved case only if it matches the operator request."""
    focus = focus_terms(objective, focus_modules)
    types = requested_types(objective, test_case_types)
    text = _haystack(title, description, category)

    if focus:
        if is_smoke_title(title, description):
            return False
        aliases = expand_terms(focus)
        if not any(term in text for term in aliases):
            return False

    if types == ["positive"]:
        if is_smoke_title(title, description) or is_negative_title(title, description, category, test_case_type):
            return False
        if test_case_type and test_case_type != "positive":
            return False
    elif types == ["negative"]:
        return is_negative_title(title, description, category, test_case_type)

    return True
