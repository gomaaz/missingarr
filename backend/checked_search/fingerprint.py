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
Names and display fields do not count; unordered collections are sorted.
Deliberately coarse: a changed custom format or release profile changes the
fingerprint of every profile. A rule field a future *arr update adds counts
only once it is added here. Pure: no network, no database.
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


def fingerprint(profile: dict, custom_formats: list, release_profiles: list) -> str:
    payload = {
        "profile": _profile(profile),
        "customFormats": _sorted(_custom_format(c) for c in custom_formats if isinstance(c, dict)),
        "releaseProfiles": _sorted(_release_profile(r) for r in release_profiles if isinstance(r, dict)),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:LENGTH]


def fingerprints(profiles, custom_formats, release_profiles) -> dict[str, str]:
    """str(profile id) -> fingerprint for every profile with an integer id.
    Anything but three lists is not an answer to rely on: ValueError."""
    for name, value in (("quality profiles", profiles), ("custom formats", custom_formats),
                        ("release profiles", release_profiles)):
        if not isinstance(value, list):
            raise ValueError(f"unexpected answer for the {name}")
    out: dict[str, str] = {}
    for profile in profiles:
        if isinstance(profile, dict) and type(profile.get("id")) is int:
            out[str(profile["id"])] = fingerprint(profile, custom_formats, release_profiles)
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
