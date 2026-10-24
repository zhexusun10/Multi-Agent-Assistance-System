"""Python port of the TS place-name rules (src/adapters/locations.ts, src/domain/geo-intake.ts).

``normalize_place`` must give the same output as TS ``normalizePlace`` for every input; the
shared fixture tests/fixtures/place-normalization.json is run by a TS test and a Python test.
JavaScript regex semantics are reproduced explicitly:

- ``\\s`` is the ECMAScript whitespace set (it includes U+FEFF and excludes U+001C-U+001F and
  U+0085, unlike Python's Unicode ``\\s``), so it is spelled out as ``JS_SPACE``;
- ``\\b`` is an ASCII word boundary in a JS regex without the ``u`` flag (``re.ASCII``);
- ``$`` without the ``m`` flag matches only at the very end (``\\Z``; Python's ``$`` also
  matches before a final newline);
- the ``i`` flag without ``u`` folds ASCII letters only (``re.ASCII | re.IGNORECASE``).
"""

import re
import unicodedata

JS_SPACE = "\t\n\x0b\x0c\r \xa0  -     　﻿"
_FLAGS = re.ASCII | re.IGNORECASE
_LEADING_THE = re.compile(rf"^(?:the)[{JS_SPACE}]+", re.ASCII)
_TRAILING_MODE = re.compile(rf"(?:mrt|lrt)(?:[{JS_SPACE}]*station)?\Z", _FLAGS)
_MODE_WORDS = re.compile(r"\b(?:mrt|lrt|station)\b|地铁站|地铁|捷运站|站", _FLAGS)
_SEPARATORS = re.compile(rf"[{JS_SPACE}_\-'’().]+", re.ASCII)


def normalize_place(name):
    """TS normalizePlace: NFKC, lower case, strip a leading 'the ', strip a trailing
    'mrt|lrt [station]', remove the words mrt/lrt/station and 地铁站/地铁/捷运站/站, then remove
    whitespace and _-'’(). characters."""
    value = unicodedata.normalize("NFKC", name).lower()
    value = _LEADING_THE.sub("", value, count=1)
    value = _TRAILING_MODE.sub("", value, count=1)
    value = _MODE_WORDS.sub("", value)
    return _SEPARATORS.sub("", value)


# TS rules, ported verbatim (no NFKC: the TS tests run on the raw mention):
# - src/domain/geo-intake.ts isExplicitStation: a mention containing MRT/LRT/地铁站/地铁 is an
#   explicit station request (otherwise URA areas/regions are matched first);
# - src/adapters/locations.ts requestedMode: LRT first (lrt|轻轨), then MRT (mrt|地铁|捷运);
# - src/domain/geo-intake.ts isGenericMrt: a generic MRT mention ('MRT', '地铁站', 'nearest MRT
#   station', ...) is the MRTStation category, not a named place.
# The shared fixture tests/fixtures/place-mention-rules.json is run by a TS test and a Python
# test so the two definitions cannot drift.
_EXPLICIT_STATION = re.compile(r"MRT|LRT|地铁站|地铁", _FLAGS)
_LRT = re.compile(r"lrt|轻轨", _FLAGS)
_MRT = re.compile(r"mrt|地铁|捷运", _FLAGS)
_GENERIC_MRT = re.compile(
    rf"^(?:(?:最近的?|任意的?|任一|一个|一座|附近的)|(?:the|an?|any|nearest)[{JS_SPACE}]+)?"
    rf"(?:MRT(?:[{JS_SPACE}]*(?:station|站))?|地铁站?|捷运站)\Z", _FLAGS)

# TS LocationService.resolve: fixed university names applied (on the upper-cased mention)
# before the OSM place lookup.
OSM_NAME_MAP = {
    "NUS": "National University of Singapore",
    "NTU": "Nanyang Technological University",
    "NUS国立大学": "National University of Singapore",
    "NUS新加坡国立大学": "National University of Singapore",
    "新国立大学": "National University of Singapore",
    "新加坡国立大学": "National University of Singapore",
}


def explicit_station(name):
    """TS isExplicitStation (geo-intake)."""
    return bool(_EXPLICIT_STATION.search(name))


def requested_mode(name):
    """'LRT' | 'MRT' | None from the mention text (TS requestedMode)."""
    if _LRT.search(name):
        return "LRT"
    if _MRT.search(name):
        return "MRT"
    return None


def is_generic_mrt(name):
    """TS isGenericMrt: the regex is tested on ``name.trim()`` (ECMAScript whitespace)."""
    return bool(_GENERIC_MRT.match(name.strip(JS_SPACE)))


def osm_name(name):
    """TS: ({...})[name.toUpperCase()] || name."""
    return OSM_NAME_MAP.get(name.upper(), name)
