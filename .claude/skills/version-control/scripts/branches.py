#!/usr/bin/env python3
"""Print the repository's integration branch, production branch and current branch.

The integration branch is the first of dev, develop that exists on origin, otherwise
main. Asks origin with `git ls-remote`; falls back to local remote-tracking refs when
origin is unreachable. Run anywhere inside the repository.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

CANDIDATES = ("dev", "develop")
PRODUCTION = "main"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def remote_heads() -> tuple[set[str], str]:
    out = git("ls-remote", "--heads", "origin", *CANDIDATES, PRODUCTION)
    if out.returncode == 0:
        heads = {line.split("refs/heads/", 1)[1] for line in out.stdout.splitlines() if "refs/heads/" in line}
        return heads, "origin"
    out = git("for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin/")
    return set(out.stdout.split()), "local refs (origin unreachable)"


def main() -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    if git("rev-parse", "--git-dir").returncode != 0:
        print("Not inside a git repository.", file=sys.stderr)
        return 1

    heads, source = remote_heads()
    integration = next((b for b in CANDIDATES if b in heads), PRODUCTION)
    current = git("branch", "--show-current").stdout.strip() or "(detached HEAD)"
    protected = current in {integration, PRODUCTION}

    print(f"integration: {integration}")
    print(f"production:  {PRODUCTION}")
    print(f"current:     {current}{'  (protected: branch off before committing)' if protected else ''}")
    print(f"source:      {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
