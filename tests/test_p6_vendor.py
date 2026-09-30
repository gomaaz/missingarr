import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

EXPECTED = {
    "alpinejs-3.14.1.min.js": (
        "alpinejs", "3.14.1",
        "sha512-ICar8UsnRZAYvv/fCNfNeKMXNoXGUfwHrjx7LqXd08zIP95G2d9bAOuaL97re+1mgt/HojqHsfdOLo/A5LuWgQ==",
    ),
    "htmx-2.0.4.min.js": (
        "htmx.org", "2.0.4",
        "sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==",
    ),
}


def test_vendor_files_match_their_recorded_checksums():
    checksums = json.loads((VENDOR / "checksums.json").read_text())
    assert set(checksums) == set(EXPECTED)
    for name, (package, version, integrity) in EXPECTED.items():
        entry = checksums[name]
        assert (entry["package"], entry["version"], entry["npm_integrity"]) == (package, version, integrity)
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == entry["sha256"]


def test_vendor_script_pins_the_same_integrity():
    script = (ROOT / "scripts" / "vendor_assets.py").read_text()
    for _, _, integrity in EXPECTED.values():
        assert integrity in script
