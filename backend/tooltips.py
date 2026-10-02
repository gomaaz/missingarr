"""
Central tooltip texts for all form fields.
Passed to Jinja2 templates via context.
"""

TOOLTIPS = {
    "name": "A custom name for this instance, e.g. 'Radarr 4K' or 'Sonarr Main'.",
    "type": "Instance type: Sonarr for TV shows, Radarr for movies.",
    "url": "Full URL of your instance including port, e.g. http://192.168.1.10:8989. No trailing slash.",
    "api_key": "API key of your *arr instance. Found under: Settings → General → API Key.",
    "enabled": "Enables or disables this instance. Disabled instances will not be searched automatically.",
    "search_missing_enabled": "Automatically searches for missing episodes (Sonarr) or movies (Radarr).",
    "search_upgrades_enabled": "Automatically searches for better releases of titles that already have a file (Sonarr: cutoff-unmet list, Radarr: see Upgrade Source).",
    "interval_minutes": "How often (in minutes) a search runs. Between 1 and 10080 (one week). Recommended: 15–60 minutes. Upgrade searches run every 4× this interval.",
    "retry_hours": "How long a searched title stays in the Searched cache. 0 = never search it again automatically (recommended — use the cache reset to retry). A season or series search only blocks episodes that were already out (air date plus hours after release) when it ran.",
    "rate_window_minutes": "Rolling time window (in minutes) for rate limiting. The rate cap applies within this window.",
    "rate_cap": "Maximum number of search actions within the rate window (1 to 1,000,000,000). Prevents API overload.",
    "search_order": (
        "Order in which missing titles are searched:\n"
        "• Random: Shuffled — even distribution across your library\n"
        "• Smart: 50% newest, 30% random, 20% oldest entries\n"
        "• Newest First: Most recently added or released titles first\n"
        "• Oldest First: Titles waiting longest are searched first"
    ),
    "missing_mode": (
        "Determines how missing episodes are searched (Sonarr only):\n"
        "• Episode: Search for individual missing episodes\n"
        "• Season Packs: Search for entire seasons as a pack\n"
        "• Show Batch: Search for the entire series at once\n"
        "• Smart: Auto-selects Season Pack if ≥50% of a season is missing, otherwise Episode"
    ),
    "missing_per_run": "Maximum number of missing titles searched per run. Must be at least 1 while missing search is enabled.",
    "upgrades_per_run": "Maximum number of upgrade candidates searched per run. Must be at least 1 while upgrade search is enabled.",
    "seconds_between_actions": "Delay in seconds between individual API calls. Prevents overloading the instance.",
    "hours_after_release": "Wait X hours after the release date before searching for a title. Set to 0 to search immediately.",
    "upgrade_source": (
        "Source for upgrade candidates (Radarr only):\n"
        "• Wanted List Only: Uses Radarr's built-in upgrade list (cutoff unmet)\n"
        "• Monitored Items Only: All monitored movies that already have a file\n"
        "• Both: Combines both sources"
    ),
    "quiet_start": "Start of quiet hours (HH:MM). No automatic searches will run during this period.",
    "quiet_end": "End of quiet hours (HH:MM). Force runs from the dashboard always bypass quiet hours.",
    # Checked search (0.9.0)
    "checked_search": (
        "Instead of telling *arr to search and grab, missingarr fetches the search results itself, "
        "checks every approved release with the pre-filter below and grabs only the first clean one.\n"
        "• Off: searches as before (search command, *arr grabs its first approved release)\n"
        "• Dry run: searches and checks, but grabs nothing and remembers nothing. Every title is checked "
        "once per round (a Force Run too) and again after its quality profile or the rules below changed; "
        "see the Pre-filter page, reset the round there.\n"
        "• Active: grabs the first release that passes every rule, for exactly this title. No clean release: "
        "the title counts as searched, Retry decides when it is searched again.\n"
        "A run pauses (searches nothing) while an indexer has Automatic Search and Interactive Search set "
        "differently in *arr.\n"
        "Sonarr: single episodes only — the pack modes are locked while this is on."
    ),
    "cs_release_timeout_seconds": "How long to wait for *arr's release search (GET /release runs the indexer search before it answers). 10 to 600 seconds. A timeout counts as a failed search; the title is tried again next run.",
    "cs_time_budget_minutes": "No new title is started once a run has been going this long (1 to 1440 minutes). The rest waits for the next run.",
    "cs_dry_run_max_releases": "Dry run only: check at most this many approved releases per title (1 to 1000). The rest are listed as unchecked.",
    "cs_search_again_after_days": "Active only: a title the checked search grabbed that is still in the Wanted list (missing, or cutoff unmet) after this many days is searched again, checked, whatever Retry says. A title whose search returned no approved release at all (\"no results\" — an indexer failure may have hidden it) is searched again after this many days as well, also with Retry 0; a shorter Retry still frees it earlier. Titles whose results the filter rejected (\"no clean hit\") stay with Retry. 1 to 365 days. It takes the place of *arr's \"Redownload Failed from Interactive Search\" — switch that off in Radarr and Sonarr: a grab through the API counts as interactive.",
    "cs_year_tolerance": "Radarr rule a: the year in the release name may be this many years away from a year of the movie (year, secondary year and — if switched on — the years of its release dates). 0 to 10.",
    "cs_count_release_dates": "Radarr rule a: also accept the years of the cinema, digital and physical release as the movie's years.",
    "cs_veto_other_movie": "Radarr rule b: reject a release with a year that Radarr's /parse assigns to another movie of your library.",
    "cs_prefix_match": "Radarr rule c: a release title also fits when it starts with a name of the movie, or the other way round (both at least the minimum length).",
    "cs_prefix_min_length": "Radarr rule c: minimum length (letters and digits) of both titles for the prefix match. 1 to 50.",
    "cs_word_match": "Radarr rule c: a release title also fits when it contains every word of a name of the movie that has enough core words.",
    "cs_word_min_core_words": "Radarr rule c: a name counts for the word match only with at least this many core words (words other than the, a, an, der, die, das, and, und, of, le, la, les, el, il). 1 to 10.",
    "cs_no_year_needs_exact": "Radarr rule d: a release without a year must match a name of the movie exactly, or /parse must assign it to this movie.",
    "cs_skip_existing_file": "Never grab the release the existing file came from again (same scene name or file name). Protects upgrades from grabbing the same release in a loop.",
    "cs_veto_other_series": "Sonarr rule S1: reject a release that Sonarr's /parse assigns to another series of your library.",
    "cs_check_suffix": "Sonarr rule S2: a year or country code at the end of the parsed series title must fit the series — the year within the tolerance of the series year, the country code also at the end of the series title or an alternate title.",
    "cs_country_codes": "Sonarr rule S2: country codes that count as a suffix, separated by commas (two or three letters).",
    "cs_suffix_year_tolerance": "Sonarr rule S2: how many years a year suffix may be away from the series year. 0 to 10.",
    "cs_reject_days_before_air": "Sonarr rule S3: reject a release published more than this many days before the episode aired (a sign of a different numbering). 0 to 36500.",
    "cs_note_days_before_air": "Sonarr rule S3: releases published at least this many days (but not more than the reject limit) before the episode aired are only noted in the log. 0 to 36500.",
    "search_again_after_profile_change": (
        "A searched title stays in the Searched cache (see Retry). With this on, it may be searched again as soon "
        "as its quality profile changes in *arr — scores, qualities, custom formats or release profiles. "
        "Every run compares a fingerprint of the profiles; a changed custom format, release profile, quality "
        "size limit (Settings → Quality) or indexer setting (minimum age, maximum size, retention) counts as "
        "a change of every profile. Titles searched before 0.9.0 are released only by the next change. "
        "Off: cached titles stay blocked whatever the profile. A dry run always checks a title again after a "
        "profile change."
    ),
}
