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
    "search_upgrades_enabled": "Automatically searches for quality upgrades of existing titles (Radarr only).",
    "interval_minutes": "How often (in minutes) a search should be triggered. Recommended: 15–60 minutes.",
    "retry_hours": "After how many hours a searched item becomes eligible to be searched again. Set to 0 to never re-search (recommended — use the Searched cache reset to retry manually).",
    "rate_window_minutes": "Rolling time window (in minutes) for rate limiting. The rate cap applies within this window.",
    "rate_cap": "Maximum number of search actions allowed within the rate window. Prevents API overload.",
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
    "missing_per_run": "Maximum number of missing titles processed per search run.",
    "upgrades_per_run": "Maximum number of upgrade candidates processed per search run.",
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
}
