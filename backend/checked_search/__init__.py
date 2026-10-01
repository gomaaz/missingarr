"""Checked search: missingarr fetches the search results itself, checks every
approved release with a pre-filter and grabs only the first clean one.

normalize, verdict, settings, radarr_rules, sonarr_rules and fingerprint are
pure: no network, no database. runner.py ties them to an agent.
"""
