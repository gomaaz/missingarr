from dataclasses import dataclass

# Reasons a release is rejected. Short English labels: they appear in the
# pre-filter log, on the page and in the CSV.
REASON_YEAR = "year"
REASON_OTHER_MOVIE = "other movie"
REASON_TITLE = "title"
REASON_TITLE_WITHOUT_YEAR = "title without year"
REASON_EXISTING_FILE = "existing file"
REASON_OTHER_SERIES = "other series"
REASON_YEAR_SUFFIX = "year suffix"
REASON_COUNTRY_SUFFIX = "country suffix"
REASON_TOO_EARLY = "published too early"
# Sonarr S5-S7 (0.12.0)
REASON_NAMESAKE = "other series by name"
REASON_OTHER_EPISODE = "other episode by title"
REASON_NUMBERING = "episode numbering in doubt"
NOTE_NO_EPISODE_FITS = "episode title fits no episode"
REASON_PARSE_ERROR = "parse error"
# Not rules of the pre-filter but of the checked search itself (runner.py):
# season packs are a non-goal, and a release GET /release did not map to this
# very title (or that came without quality and languages) is never grabbed —
# the grab names its target from that mapping.
REASON_SEASON_PACK = "season pack"
REASON_TARGET = "not mapped to this title"
# Sonarr: a release mapped to more than one episode. 0.9.0 grabs single
# episodes only; rules and cache would cover the searched episode alone.
REASON_MULTI_EPISODE = "multi-episode release"


@dataclass(frozen=True)
class Verdict:
    """Every rule that rejects the release (reasons) and every remark that
    does not (notes). A release passes when no rule rejects it."""

    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reasons
