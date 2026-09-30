import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def lock_entries():
    """{name: (version, [hashes])} from a pip-compile --generate-hashes file."""
    entries, current = {}, None
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", stripped)
        if match and not line.startswith(" "):
            current = match.group(1).lower().replace("_", "-")
            entries[current] = (match.group(2), [])
        elif stripped.startswith("--hash=sha256:") and current:
            entries[current][1].append(stripped.split(":", 1)[1].rstrip(" \\"))
    return entries


def test_passlib_is_gone_and_bcrypt_is_direct():
    requirements = (ROOT / "requirements.txt").read_text().lower()
    assert "passlib" not in requirements
    assert re.search(r"^bcrypt>=4\.1", requirements, re.M)
    assert re.search(r"^apscheduler>=3\.10\.4,<4", requirements, re.M)


def test_every_locked_package_is_pinned_with_hashes():
    entries = lock_entries()
    for name in ("fastapi", "uvicorn", "starlette", "bcrypt", "cryptography", "apscheduler", "requests"):
        assert name in entries, name
    assert "passlib" not in entries
    for name, (version, hashes) in entries.items():
        assert hashes, f"{name}=={version} has no hash"
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes), name
