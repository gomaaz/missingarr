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


def test_no_host_details_in_new_files():
    files = [ROOT / "README.md", *sorted((ROOT / "backend" / "checked_search").glob("*.py")),
             ROOT / "backend" / "db" / "checked_search_log.py", ROOT / "backend" / "skills" / "profiles.py",
             ROOT / "templates" / "checked_search.html", *sorted((ROOT / "tests").glob("test_g*_*.py")),
             *sorted((ROOT / "backend" / "imports").glob("*.py")), ROOT / "backend" / "api" / "imports.py",
             ROOT / "templates" / "imports.html", *sorted((ROOT / "tests").glob("test_h*_*.py")),
             ROOT / "tests" / "imports_fake_arr.py", ROOT / "CHANGELOG.md"]
    for path in files:
        text = path.read_text()
        for marker in ("/root/", "/home/", "/tmp/", "/mnt/", "/srv/"):
            assert marker not in text, f"{marker} in {path.name}"
