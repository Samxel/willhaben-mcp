"""Title matching: words, spec terms however they are spelled, and the
reserved/sold marker sellers put in the title."""

import re
import unicodedata
from typing import Optional


def fold(s: str) -> str:
    """Lowercase and strip accents, so "Grün" and "gruen" compare equal."""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def norm(s: str) -> str:
    """Normalise a label for lookup: lowercase, strip accents, drop all spaces."""
    return "".join(fold(s).split())


_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Ads fuse series and model number ("RTX4070"); split those so a keyword written
# with a space still matches, and the other way round.
_FUSED_RE = re.compile(r"^([a-z]{2,4})(\d{3,5})$")


def tokens(text: str) -> set:
    """Comparable word tokens of a title or keyword. Fused model codes are
    replaced by their parts on both sides, so "RTX4070" and "RTX 4070" match
    each other whichever one the keyword uses."""
    out = set()
    for token in _TOKEN_RE.findall(fold(text or "")):
        fused = _FUSED_RE.match(token)
        out.update(fused.groups() if fused else [token])
    return out


# A number and its unit: sellers write the same storage size as "128GB",
# "128 GB", "128gb" or "256Gb", so a term given in one of those has to match all.
_NUM_UNIT_RE = re.compile(r"^(\d+)\s*([a-z]+)$")


def term_forms(term: str) -> list[set]:
    """The token sets a filter term may appear as. Everything is one form,
    except a number with a unit, which is also accepted split in two -- that way
    a term matches whichever way the seller spelled it, without changing how the
    rest of a title is read."""
    folded = fold(term).strip()
    match = _NUM_UNIT_RE.match(folded)
    if not match:
        return [tokens(folded)]
    number, unit = match.groups()
    return [{f"{number}{unit}"}, {number, unit}]


def has_term(tokens: set, forms: list[set]) -> bool:
    """Whether a title (or ad text) carries a filter term. A word matches in
    full, or as the tail of a German compound ("kabel" catches "Ladekabel"), but
    never one that merely starts with it -- otherwise "pro" would throw away
    every "Prozessor" and "provisionsfrei" and be useless for separating an
    iPhone 13 from an iPhone 13 Pro."""
    return any(all(any(token == word or token.endswith(word) for token in tokens)
                   for word in form)
               for form in forms)


# willhaben has no reserved/sold flag in the API -- sellers write it into the
# title, in every bracket style there is: "(reserviert)", "*reserviert*",
# "RESERVIERT - ...". Only those set-apart forms count, so a title that merely
# talks about reserving ("Artikel werden nicht reserviert") is not a match.
def _marker_re(words: str) -> re.Pattern:
    return re.compile(
        # bracketed or leading, in any case ...
        rf"(?i:(?:^\s*|[(\[*/|-]\s*)(?:{words})\b|\b(?:{words})\s*[)\]*])"
        # ... or shouted anywhere in the title
        rf"|\b(?:{words.upper()})\b"
    )


_RESERVED_RE = _marker_re("reserviert|reserved|vergeben")
_SOLD_RE = _marker_re("verkauft|sold|abgeholt")


def ad_status(title: Optional[str], ad: Optional[dict] = None) -> tuple[str, bool]:
    """``(status, reserved)`` for an ad. willhaben only ever reports "active",
    so the real state comes from the marker sellers put in the title."""
    status = ((ad or {}).get("advertStatus") or {}).get("id") or "active"
    text = title or ""
    if status == "active" and _SOLD_RE.search(text):
        status = "sold"
    elif status == "active" and _RESERVED_RE.search(text):
        status = "reserved"
    return status, status == "reserved"
