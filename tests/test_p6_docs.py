from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIABLES = ["SECRET_KEY", "COOKIE_SECURE", "HISTORY_RETENTION_DAYS", "PUID", "PGID", "AUTH_PASSWORD"]


def test_version_is_0_8_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.8.0"


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
