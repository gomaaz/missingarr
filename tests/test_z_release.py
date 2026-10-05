from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_checked_search():
    readme = (ROOT / "README.md").read_text()
    assert "### Checked search" in readme
    assert "### Profile changes" in readme
    assert "**Search again after profile changes**" in readme
    assert "Interactive Search" in readme   # GET /release asks other indexers than the command
    assert "**Search again if still missing after (days)**" in readme
    assert "Redownload Failed from Interactive Search" in readme
    assert "## Upgrading to 0.9.0" in readme
    assert "/api/checked-search.csv" in readme
    assert "downloadUrl" in readme   # the security note says it is never stored


def test_readme_and_changelog_document_imports():
    readme = (ROOT / "README.md").read_text()
    for text in ("## Imports", "## Upgrading to 0.10.0", "/api/imports/count", "/activity/queue", "Not an upgrade",
                 "180 seconds", "`gomaaz/missingarr:0.10.0`", "eventType=3", "Another action for this download is running",
                 "An import may still be running in the app", "The instance was changed — reload the page",
                 "the queue has not confirmed the import", "/api/v3/queue/details", "Queue too large to read completely"):
        assert text in readme, text
    assert readme.index("### Profile changes") < readme.index("## Imports") < readme.index("## Example: Typical Home Setup")
    assert readme.index("## Upgrading to 0.10.0") < readme.index("## Upgrading to 0.9.0")
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "## [0.10.0]" in changelog and "180 seconds" in changelog
    assert "Another action for this download is running" in changelog
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.10.0]") < changelog.index("## [0.9.0]")


def test_readme_changelog_help_and_tooltips_document_0_10_1():
    from backend.tooltips import TOOLTIPS
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.10.1]") < changelog.index("## [0.10.0]")
    section = changelog[changelog.index("## [0.10.1]"):changelog.index("## [0.10.0]")]
    assert section.index("### Fixed") < section.index("### Changed")
    for text in ("without any cinema, digital or physical release date", "25 March 2026",
                 "at the earliest 30 seconds after the start", "It now follows the clock",
                 "already checked in this dry-run round", "type *Anime*", "no approved release",
                 "includeSeries=true", "50 seconds instead of 60",
                 "With *Minimum Availability* Released, Radarr never reports",
                 "searched on its own, also with *Missing Mode*", "at most 50 seconds old",
                 "With a small backlog a run reads much less", "a run can read more than before"):
        assert text in section, text
    assert "fresh count" not in section
    readme = (ROOT / "README.md").read_text()
    assert readme.index("## Upgrading to 0.10.1") < readme.index("## Upgrading to 0.10.0")
    for text in ("at the earliest 30 seconds later", "The card shows QUIET while the window lasts",
                 "series of type *Anime* per run", '("no approved release")', "only *Reset dry run* starts a new round",
                 "at most once every 50 seconds", "converts them to local time, once",
                 "with *Minimum Availability* Released, Radarr would never report",
                 "searched on its own, also with *Missing Mode*", "gets a count at most 50 seconds old",
                 "delete the row `local_timestamps_since` from the table `app_settings`",
                 "a run that pauses there does not read them"):
        assert text in readme, text
    assert "fresh count" not in readme
    assert '("no results")' not in readme
    help_page = (ROOT / "templates" / "help.html").read_text()
    assert "The card shows QUIET while the quiet hours last" in help_page
    assert "the Reset on the Progressed page does not" in help_page
    assert "anime series per run" in TOOLTIPS["missing_per_run"]
    assert "no approved release" in TOOLTIPS["cs_search_again_after_days"]


def test_no_host_details_in_new_files():
    files = [ROOT / "README.md", *sorted((ROOT / "backend" / "checked_search").glob("*.py")),
             ROOT / "backend" / "db" / "checked_search_log.py", ROOT / "backend" / "skills" / "profiles.py",
             ROOT / "templates" / "checked_search.html", *sorted((ROOT / "tests").glob("test_g*_*.py")),
             *sorted((ROOT / "backend" / "imports").glob("*.py")), ROOT / "backend" / "api" / "imports.py",
             ROOT / "templates" / "imports.html", *sorted((ROOT / "tests").glob("test_h*_*.py")),
             ROOT / "tests" / "imports_fake_arr.py", ROOT / "CHANGELOG.md",
             *sorted((ROOT / "tests").glob("test_f*_*.py")),
             ROOT / "scripts" / "mobile_check.py", ROOT / "tests" / "test_m1_mobile.py"]
    for path in files:
        text = path.read_text()
        for marker in ("/root/", "/home/", "/tmp/", "/mnt/", "/srv/"):
            assert marker not in text, f"{marker} in {path.name}"


def test_readme_and_changelog_document_0_11_0():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.11.0]") < changelog.index("## [0.10.1]")
    section = changelog[changelog.index("## [0.11.0]"):changelog.index("## [0.10.1]")]
    assert section.index("### Added") < section.index("### Changed")
    for text in ("tab bar", "More", "768 px", "44 × 44", "scripts/mobile_check.py", "on tap",
                 "On a PC the layout does not change"):
        assert text in section, text
    readme = (ROOT / "README.md").read_text()
    assert readme.index("## Imports") < readme.index("## On the phone") < readme.index("## Example: Typical Home Setup")
    assert readme.index("## Upgrading to 0.11.0") < readme.index("## Upgrading to 0.10.1")
    for text in ("viewport-fit=cover", "scripts/mobile_check.py", "**Filter**", "Works on a phone"):
        assert text in readme, text
