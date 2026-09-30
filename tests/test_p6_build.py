import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_is_pinned_and_hash_checked():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert "--timeout-graceful-shutdown" in dockerfile
    assert "requirements.txt" not in dockerfile


def test_image_labels_name_the_pinned_base():
    # docker image inspect shows labels, not the FROM line; keep both in step.
    dockerfile = (ROOT / "Dockerfile").read_text()
    digest = re.search(r"^FROM python:3\.12-slim@(sha256:[0-9a-f]{64})$", dockerfile, re.M).group(1)
    assert 'org.opencontainers.image.base.name="docker.io/library/python:3.12-slim"' in dockerfile
    assert f'org.opencontainers.image.base.digest="{digest}"' in dockerfile


def test_every_action_is_pinned_to_a_commit():
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text()
    uses = re.findall(r"uses:\s*(\S+)", workflow)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.\-]+/[\w.\-]+@[0-9a-f]{40}", ref), ref
