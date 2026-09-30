import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "docker-entrypoint.sh"

pytestmark = [
    pytest.mark.skipif(os.geteuid() != 0, reason="needs root to chown and drop privileges"),
    pytest.mark.skipif(shutil.which("setpriv") is None, reason="setpriv not installed"),
]


def run(data_dir, *command, **env):
    return subprocess.run(
        ["sh", str(SCRIPT), *command],
        env={**os.environ, "DATA_DIR": str(data_dir), **env},
        capture_output=True, text=True, cwd="/",
    )


def test_root_owned_data_is_handed_over_and_the_app_drops_root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "missingarr.db").write_text("x")
    result = run(data, "sh", "-c", "id -u; id -g", PUID="4321", PGID="4322")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["4321", "4322"]
    for path in (data, data / "missingarr.db"):
        assert (path.stat().st_uid, path.stat().st_gid) == (4321, 4322)


def test_files_that_already_fit_are_not_touched(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db_file = data / "missingarr.db"
    db_file.write_text("x")
    data.chmod(0o700)
    db_file.chmod(0o600)
    for path in (data, db_file):
        os.chown(path, 4321, 4322)
    before = db_file.stat().st_ctime_ns
    assert run(data, "true", PUID="4321", PGID="4322").returncode == 0
    assert db_file.stat().st_ctime_ns == before


def test_data_and_backups_become_private(tmp_path):
    # A copy such as missingarr.db.bak-… holds the same API keys (C5, C9).
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o755)
    backup = data / "missingarr.db.bak-20260817-000513"
    backup.write_text("x")
    backup.chmod(0o644)
    result = run(data, "sh", "-c", "umask", PUID="4321", PGID="4322")
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert result.stdout.strip() == "0077"


def test_symlinks_in_the_data_directory_are_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    outside.chmod(0o644)
    data = tmp_path / "data"
    data.mkdir()
    (data / "link").symlink_to(outside)
    assert run(data, "true", PUID="4321", PGID="4322").returncode == 0
    assert (outside.stat().st_uid, outside.stat().st_gid) == (0, 0)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_puid_zero_keeps_root(tmp_path):
    result = run(tmp_path, "id", "-u", PUID="0")
    assert result.stdout.strip() == "0"


def test_non_numeric_ids_are_rejected(tmp_path):
    assert run(tmp_path, "true", PUID="abc").returncode == 64


@pytest.mark.parametrize("puid, pgid", [("1:2", "1000"), ("1000", "2:3"), ("1000", "-1")])
def test_each_id_is_checked_on_its_own(tmp_path, puid, pgid):
    # "1:2" must not reach chown as "1:2:1000".
    data = tmp_path / "data"
    data.mkdir()
    result = run(data, "true", PUID=puid, PGID=pgid)
    assert result.returncode == 64
    assert (data.stat().st_uid, data.stat().st_gid) == (0, 0)
