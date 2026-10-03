from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIABLES = ["SECRET_KEY", "COOKIE_SECURE", "HISTORY_RETENTION_DAYS", "PUID", "PGID", "AUTH_PASSWORD"]


def test_version_is_0_10_1():
    assert (ROOT / "VERSION").read_text().strip() == "0.10.1"


def test_every_new_variable_is_documented():
    for name in ("README.md", ".env.example", "docker-compose.yml"):
        text = (ROOT / name).read_text()
        for variable in VARIABLES:
            assert variable in text, f"{variable} missing in {name}"


def test_readme_explains_the_bcrypt_hash_and_the_upgrade():
    readme = (ROOT / "README.md").read_text()
    assert "bcrypt" in readme
    assert "Upgrading to 0.8.0" in readme
    assert "passlib" not in readme


def test_docs_explain_radarr_availability():
    from backend.tooltips import TOOLTIPS
    tooltip = TOOLTIPS["hours_after_release"]
    assert "Search Missing in Radarr" in tooltip
    assert "Minimum Availability" in tooltip
    assert "not from the moment Radarr" in tooltip
    readme = (ROOT / "README.md").read_text()
    assert ("Search Missing in Radarr: a movie with a known cinema, digital or physical release date is only "
            "searched once Radarr reports it available") in readme
    assert "A movie without any of these dates counts as available" in readme
    assert "a movie without any of these dates counts as available" in tooltip
    assert "not from the moment Radarr reports it available" in readme
    assert "In Search Missing, Force Runs skip this wait" in readme
    changelog = (ROOT / "CHANGELOG.md").read_text()
    section = changelog[changelog.index("## [0.10.0]"):changelog.index("## [0.9.0]")]
    assert "not yet available in Radarr" in section
    assert "a separate condition, still counted from the release date" in section
    assert section.index("### Added") < section.index("### Changed") < section.index("### Security")
