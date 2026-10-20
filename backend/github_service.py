"""Clone data using Git; do not run repository programs, hooks, or tests."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse

from patch_validator import assert_safe_tree


def clone_repository(repo_url):
    parsed = urlparse(repo_url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or parsed.query or parsed.fragment):
        raise ValueError("Use a standard HTTPS github.com repository URL")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2:
        raise ValueError("Use a GitHub repository URL")
    owner, name = parts
    name = name.removesuffix(".git")
    if any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", part)
           or part in (".", "..") for part in (owner, name)):
        raise ValueError("Invalid repository owner or name")

    temp_dir = tempfile.TemporaryDirectory(prefix="codepilot-clone-")
    root = Path(temp_dir.name)
    repo_path = root / "repository"
    # An empty template and isolated config prevent inherited hooks, filters,
    # URL rewrites, credential helpers, and automatic submodule recursion.
    template = root / "empty-template"
    template.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    options = ["git", "-c", "core.hooksPath=" + str(template),
               "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "submodule.recurse=false", "-c", "core.symlinks=true"]

    def run(*args):
        subprocess.run(options + list(args), cwd=root, env=env,
                       check=True, capture_output=True, timeout=120)

    try:
        run("clone", "--no-checkout", "--depth=1", "--single-branch",
            "--no-recurse-submodules", "--template=" + str(template),
            "--", f"https://github.com/{owner}/{name}.git", str(repo_path))
        size = sum(f.stat().st_size for f in (repo_path / ".git").rglob("*") if f.is_file())
        if size > 20 * 1024 * 1024:
            raise ValueError("Repository metadata too large")
        # Reject Git symlink entries before materializing any worktree files.
        entries = subprocess.run(
            options + ["-C", str(repo_path), "ls-tree", "-r", "-z", "HEAD"],
            cwd=root, env=env, check=True, capture_output=True, timeout=30,
        ).stdout.split(b"\0")
        if any(entry.startswith(b"120000 ") for entry in entries):
            raise ValueError("Repositories containing symlinks are not supported")
        run("-C", str(repo_path), "checkout", "--force", "HEAD", "--", ".")
        assert_safe_tree(repo_path)
        return temp_dir, repo_path
    except Exception:
        temp_dir.cleanup()
        raise
