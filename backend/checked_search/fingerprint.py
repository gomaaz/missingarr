"""Fingerprint of a quality profile (spec addendum: notice profile changes).

Radarr and Sonarr score every release with the profile in force when they
search; missingarr does not need to know the rules. It only notices that a
profile changed: a title searched under the old profile may be searched
again, and a dry-run row checked under it no longer counts for the round.

One fingerprint per quality profile: SHA-256 over canonical JSON (sorted
keys) of the fields that decide a release's score, cut to 16 hex characters.
Not the whole API resources: they carry translated labels, help texts and
select options that change with the UI language or an *arr update.
  profile          the quality ranking (order kept: it is the priority),
                   cutoff, upgradeAllowed, minFormatScore, cutoffFormatScore,
                   minUpgradeFormatScore, the format scores, language (Radarr)
  custom formats   per specification: implementation, negate, required and
                   the field values
  release profiles enabled, indexerId, required, ignored, tags
  quality          per quality (by quality id): minSize and maxSize, null and
  definitions      0 alike (no limit) — AcceptableSizeSpecification
  indexer config   minimumAge, maximumSize, retention (Minimum Age,
                   MaximumSize, Retention specifications); Radarr also
                   allowHardcodedSubs and, while that is off, the
                   whitelistedHardcodedSubs terms (HardcodeSubsSpecification)
The quality definitions and the indexer config are global: they decide
`approved` in GET /release for every profile, so they go into every profile's
fingerprint like the custom formats. Left out because they only sort the
approved releases or are skipped in an interactive search: preferredSize,
weight (fixed by *arr), title, rssSyncInterval, preferIndexerFlags,
availabilityDelay (AvailabilitySpecification skips user-invoked searches).
Names and display fields do not count; unordered collections are sorted.
Deliberately coarse: a changed custom format, release profile, size limit or
indexer setting changes the fingerprint of every profile. A rule field a
future *arr update adds counts only once it is added here. Pure: no network,
no database.
"""

import hashlib
import json

LENGTH = 16
SHORT = 8


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sorted(values) -> list:
    return sorted(values, key=_canonical)


def _qualities(items) -> list:
    """The quality ranking with groups, in *arr's order."""
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        quality = item.get("quality")
        out.append({
            "id": item.get("id"),
            "quality": quality.get("id") if isinstance(quality, dict) else None,
            "allowed": item.get("allowed"),
            "items": _qualities(item.get("items")),
        })
    return out


def _profile(profile: dict) -> dict:
    language = profile.get("language")
    return {
        "cutoff": profile.get("cutoff"),
        "upgradeAllowed": profile.get("upgradeAllowed"),
        "minFormatScore": profile.get("minFormatScore"),
        "cutoffFormatScore": profile.get("cutoffFormatScore"),
        "minUpgradeFormatScore": profile.get("minUpgradeFormatScore"),
        "language": language.get("id") if isinstance(language, dict) else None,
        "items": _qualities(profile.get("items")),
        "formatItems": _sorted([item.get("format"), item.get("score")]
                               for item in profile.get("formatItems") or [] if isinstance(item, dict)),
    }


def _specification(spec: dict) -> dict:
    # Field names as text: a field without a name must not break the sorted
    # JSON (None next to str keys cannot be ordered).
    fields = {str(field.get("name")): field.get("value")
              for field in spec.get("fields") or [] if isinstance(field, dict)}
    return {"implementation": spec.get("implementation"), "negate": spec.get("negate"),
            "required": spec.get("required"), "fields": fields}


def _custom_format(custom_format: dict) -> dict:
    specs = [_specification(s) for s in custom_format.get("specifications") or [] if isinstance(s, dict)]
    return {"id": custom_format.get("id"), "specifications": _sorted(specs)}


def _terms(value) -> list:
    """required / ignored: a list, or (as *arr accepts it) one text split at commas."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return sorted({str(term).strip() for term in value if str(term).strip()})


def _release_profile(profile: dict) -> dict:
    """Without the id: *arr picks release profiles by tags, enabled and
    indexer, never by id, so a profile recreated with the same terms is no
    change."""
    tags = profile.get("tags")
    return {"enabled": profile.get("enabled"), "indexerId": profile.get("indexerId"),
            "required": _terms(profile.get("required")), "ignored": _terms(profile.get("ignored")),
            "tags": _sorted(tags) if isinstance(tags, list) else []}


def _size(value):
    """A size limit (MB per minute of runtime). *arr checks no limit for a
    missing maximum or a maximum of 0, and a minimum of 0 rejects nothing:
    both count as None. Numbers as float, so 5 and 5.0 are the same."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) or None


def _quality_definitions(definitions: list) -> list:
    """The size limits per quality, ordered by quality id (the list order
    and the definition row ids decide nothing)."""
    rows = []
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        quality = definition.get("quality")
        rows.append({"quality": quality.get("id") if isinstance(quality, dict) else None,
                     "minSize": _size(definition.get("minSize")), "maxSize": _size(definition.get("maxSize"))})
    return sorted(rows, key=lambda row: (type(row["quality"]) is not int,
                                         row["quality"] if type(row["quality"]) is int else 0, _canonical(row)))


def _hardcoded_subs(value) -> list:
    """Radarr splits the whitelist at commas and compares case-insensitively;
    blank terms match nothing. Terms are not trimmed: upstream does not."""
    if not isinstance(value, str):
        return []
    return sorted({term.lower() for term in value.split(",") if term.strip()})


def _indexer_config(config: dict) -> dict:
    allow = config.get("allowHardcodedSubs")
    return {
        "minimumAge": config.get("minimumAge"),
        "maximumSize": config.get("maximumSize"),
        "retention": config.get("retention"),
        "allowHardcodedSubs": allow,
        "whitelistedHardcodedSubs": [] if allow else _hardcoded_subs(config.get("whitelistedHardcodedSubs")),
    }


def _shared(custom_formats: list, release_profiles: list, quality_definitions: list, indexer_config: dict) -> dict:
    """The part of the payload every profile shares."""
    return {
        "customFormats": _sorted(_custom_format(c) for c in custom_formats if isinstance(c, dict)),
        "releaseProfiles": _sorted(_release_profile(r) for r in release_profiles if isinstance(r, dict)),
        "qualityDefinitions": _quality_definitions(quality_definitions),
        "indexerConfig": _indexer_config(indexer_config),
    }


def _digest(profile: dict, shared: dict) -> str:
    payload = {"profile": _profile(profile), **shared}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:LENGTH]


def fingerprint(profile: dict, custom_formats: list, release_profiles: list,
                quality_definitions: list, indexer_config: dict) -> str:
    return _digest(profile, _shared(custom_formats, release_profiles, quality_definitions, indexer_config))


def fingerprints(profiles, custom_formats, release_profiles, quality_definitions, indexer_config) -> dict[str, str]:
    """str(profile id) -> fingerprint for every profile with an integer id.
    Anything but four lists and one object is not an answer to rely on:
    ValueError."""
    for name, value in (("quality profiles", profiles), ("custom formats", custom_formats),
                        ("release profiles", release_profiles), ("quality definitions", quality_definitions)):
        if not isinstance(value, list):
            raise ValueError(f"unexpected answer for the {name}")
    if not isinstance(indexer_config, dict):
        raise ValueError("unexpected answer for the indexer settings")
    shared = _shared(custom_formats, release_profiles, quality_definitions, indexer_config)
    out: dict[str, str] = {}
    for profile in profiles:
        if isinstance(profile, dict) and type(profile.get("id")) is int:
            out[str(profile["id"])] = _digest(profile, shared)
    return out


def changes(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """(profile id, old, new) for every profile known before and now with a
    different fingerprint. New or removed profiles are no change of a
    profile a title was searched under."""
    both = sorted(set(old) & set(new), key=lambda key: (len(key), key))
    return [(key, old[key], new[key]) for key in both if old[key] != new[key]]


def short(value: str | None) -> str:
    """The first characters, enough to tell two fingerprints apart in a log."""
    return value[:SHORT] if value else "—"
