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


def test_no_host_details_in_new_files():
    files = [ROOT / "README.md", *sorted((ROOT / "backend" / "checked_search").glob("*.py")),
             ROOT / "backend" / "db" / "checked_search_log.py", ROOT / "backend" / "skills" / "profiles.py",
             ROOT / "templates" / "checked_search.html", *sorted((ROOT / "tests").glob("test_g*_*.py"))]
    for path in files:
        text = path.read_text()
        for marker in ("/root/", "/home/", "/tmp/", "/mnt/", "/srv/"):
            assert marker not in text, f"{marker} in {path.name}"
