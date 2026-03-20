"""
Central tooltip texts for all form fields.
Passed to Jinja2 templates via context.
"""

TOOLTIPS = {
    "name": "Frei wählbarer Name für diese Instanz, z.B. 'Radarr 4K' oder 'Sonarr Main'.",
    "type": "Typ der Instanz: Sonarr für Serien, Radarr für Filme.",
    "url": "Vollständige URL deiner Instanz inkl. Port, z.B. http://192.168.1.10:8989. Kein abschließender Slash.",
    "api_key": "API-Schlüssel deiner *arr-Instanz. Zu finden unter: Einstellungen → Allgemein → API-Schlüssel.",
    "enabled": "Aktiviert oder deaktiviert diese Instanz. Deaktivierte Instanzen werden nicht automatisch gesucht.",
    "search_missing_enabled": "Sucht automatisch nach fehlenden Episoden (Sonarr) oder Filmen (Radarr).",
    "search_upgrades_enabled": "Sucht automatisch nach Qualitäts-Upgrades für bereits vorhandene Titel (nur Radarr).",
    "interval_minutes": "Wie oft (in Minuten) soll eine Suche gestartet werden? Empfehlung: 15–60 Minuten.",
    "retry_hours": "Nach wie vielen Stunden soll ein fehlgeschlagener Suchvorgang erneut versucht werden?",
    "rate_window_minutes": "Zeitfenster (in Minuten) für das Rate-Limiting. Innerhalb dieses Fensters wird die Rate Cap eingehalten.",
    "rate_cap": "Maximale Anzahl an Such-Aktionen innerhalb des Rate-Windows. Verhindert API-Überlastung.",
    "search_order": (
        "Reihenfolge, in der fehlende Titel gesucht werden:\n"
        "• Random: Zufällig – gleichmäßige Verteilung\n"
        "• Smart: 50% neueste, 30% zufällig, 20% älteste Einträge\n"
        "• Newest First: Neueste Titel zuerst\n"
        "• Oldest First: Älteste Titel zuerst"
    ),
    "missing_mode": (
        "Bestimmt wie fehlende Episoden gesucht werden (nur Sonarr):\n"
        "• Episode: Einzelne Episoden werden gesucht\n"
        "• Season Packs: Ganze Staffeln werden als Paket gesucht\n"
        "• Show Batch: Komplette Serien werden gesucht\n"
        "• Smart: Automatische Wahl – Season Pack wenn ≥50% einer Staffel fehlen, sonst Episode"
    ),
    "missing_per_run": "Maximale Anzahl fehlender Titel, die pro Suchlauf verarbeitet werden.",
    "upgrades_per_run": "Maximale Anzahl von Upgrade-Kandidaten, die pro Suchlauf verarbeitet werden.",
    "seconds_between_actions": "Wartezeit in Sekunden zwischen einzelnen API-Aufrufen. Verhindert Überlastung.",
    "hours_after_release": "Warte X Stunden nach dem Erscheinungsdatum, bevor ein Titel gesucht wird. 0 = sofort suchen.",
    "upgrade_source": (
        "Quelle für Upgrade-Kandidaten (nur Radarr):\n"
        "• Wanted List Only: Nutzt Radarrs eigene Upgrade-Liste (cutoff unmet)\n"
        "• Monitored Items Only: Alle überwachten Filme mit vorhandener Datei\n"
        "• Both: Kombiniert beide Quellen"
    ),
    "quiet_start": "Beginn der Ruhezeit (HH:MM). In diesem Zeitraum werden keine automatischen Suchen gestartet.",
    "quiet_end": "Ende der Ruhezeit (HH:MM). Force-Runs aus dem Dashboard ignorieren die Ruhezeit.",
}
