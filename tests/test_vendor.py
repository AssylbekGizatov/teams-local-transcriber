"""Offline reproduction of vendor preparation inside/outside a parent Git checkout."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.mark.parametrize("parent_checkout", [False, True])
def test_vendor_applies_patch_before_export_even_under_parent_git(tmp_path, parent_checkout):
    if not shutil.which("git") or not shutil.which("tar"):
        pytest.skip("Vendor preparation requires Git and tar")
    upstream = tmp_path / "fixture-upstream"
    upstream.mkdir()
    git(upstream, "init", "-q")
    (upstream / "policy.txt").write_text("capture_enabled = true\n")
    git(upstream, "add", "policy.txt")
    git(
        upstream,
        "-c",
        "user.name=Test fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Offline upstream fixture",
    )
    revision = git(upstream, "rev-parse", "HEAD")
    (upstream / "policy.txt").write_text("capture_enabled = false\n")
    patch = git(upstream, "diff", "--binary") + "\n"
    git(upstream, "checkout", "--", "policy.txt")

    project = tmp_path / "project checkout"
    (project / "scripts").mkdir(parents=True)
    deployment = project / "deploy/vexa"
    deployment.mkdir(parents=True)
    if parent_checkout:
        git(project, "init", "-q")
    shutil.copy2(ROOT / "scripts/vendor_vexa.sh", project / "scripts/vendor_vexa.sh")
    (deployment / "UPSTREAM_REVISION").write_text(revision + "\n")
    (deployment / "local-only.patch").write_text(patch)
    (deployment / "Dockerfile.bot").write_text("# inert test fixture\n")
    expected = hashlib.sha256(b"capture_enabled = false\n").hexdigest()
    (deployment / "verify_vendor.py").write_text(
        "import hashlib, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1])\n"
        f"assert hashlib.sha256((root / 'policy.txt').read_bytes()).hexdigest() == '{expected}'\n"
        "assert not (root / '.git').exists()\n"
    )

    # Replace only the fixture's official-origin registration. All Git operations
    # still execute real Git, and no network or alternative runtime source is used.
    bin_dir = tmp_path / "test-bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if len(args) >= 4 and args[-4:] == ['remote', 'add', 'origin', 'https://github.com/Vexa-ai/vexa.git']:\n"
        "    args[-1] = os.environ['FIXTURE_UPSTREAM']\n"
        "os.execv(os.environ['REAL_GIT'], [os.environ['REAL_GIT'], *args])\n"
    )
    shim.chmod(0o755)
    global_git_config = tmp_path / "windows-git-config"
    global_git_config.write_text("[core]\n\tautocrlf = true\n")
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "REAL_GIT": shutil.which("git"),
        "FIXTURE_UPSTREAM": upstream.as_uri(),
        "GIT_CONFIG_GLOBAL": str(global_git_config),
    }
    destination = project / "vendor/vexa"
    completed = subprocess.run(
        ["bash", str(project / "scripts/vendor_vexa.sh"), str(destination)],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (destination / "policy.txt").read_text() == "capture_enabled = false\n"
    assert (destination / ".local-build/UPSTREAM_REVISION").read_text().strip() == revision
    assert (destination / ".local-build/PATCH_SHA256").read_text().strip() == hashlib.sha256(
        patch.encode()
    ).hexdigest()
