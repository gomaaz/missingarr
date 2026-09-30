"""Download the pinned front-end libraries into static/vendor and verify them.

Run from the repository root:  .venv/bin/python scripts/vendor_assets.py

The npm registry publishes a sha512 'integrity' for every package tarball.
Nothing is written unless the downloaded tarball matches the value pinned
here, so a changed CDN or registry cannot slip in unnoticed (C8). To update a
library, change version and integrity together (from
https://registry.npmjs.org/<package>/<version>, field dist.integrity) and run
the script again.
"""
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

ASSETS = [
    {
        "package": "alpinejs",
        "version": "3.14.1",
        "member": "package/dist/cdn.min.js",
        "target": "alpinejs-3.14.1.min.js",
        "integrity": "sha512-ICar8UsnRZAYvv/fCNfNeKMXNoXGUfwHrjx7LqXd08zIP95G2d9bAOuaL97re+1mgt/HojqHsfdOLo/A5LuWgQ==",
    },
    {
        "package": "htmx.org",
        "version": "2.0.4",
        "member": "package/dist/htmx.min.js",
        "target": "htmx-2.0.4.min.js",
        "integrity": "sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==",
    },
]


def tarball_url(package: str, version: str) -> str:
    return f"https://registry.npmjs.org/{package}/-/{package.split('/')[-1]}-{version}.tgz"


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    checksums = {}
    for asset in ASSETS:
        with urllib.request.urlopen(tarball_url(asset["package"], asset["version"]), timeout=60) as resp:
            data = resp.read()
        actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        if actual != asset["integrity"]:
            raise SystemExit(f"{asset['package']}@{asset['version']}: integrity mismatch, got {actual}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            content = tar.extractfile(asset["member"]).read()
        (VENDOR / asset["target"]).write_bytes(content)
        checksums[asset["target"]] = {
            "package": asset["package"],
            "version": asset["version"],
            "npm_integrity": asset["integrity"],
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        print(f"ok  {asset['target']}")
    (VENDOR / "checksums.json").write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
