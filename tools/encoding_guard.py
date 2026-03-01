#!/usr/bin/env python3
"""Fail fast if source files are not UTF-8 (without BOM)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".css",
    ".html",
    ".json",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
}

SPECIAL_TEXT_FILES = {
    ".editorconfig",
    ".gitattributes",
    "AGENTS.md",
    "README.md",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
}

BOMS = [
    ("UTF-8", b"\xEF\xBB\xBF"),
    ("UTF-16 LE", b"\xFF\xFE"),
    ("UTF-16 BE", b"\xFE\xFF"),
    ("UTF-32 LE", b"\xFF\xFE\x00\x00"),
    ("UTF-32 BE", b"\x00\x00\xFE\xFF"),
]


def _run_git(args: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        return []
    raw = proc.stdout.decode("utf-8", errors="replace")
    return [p for p in raw.split("\x00") if p]


def _iter_files_from_git(staged: bool) -> Iterable[Path]:
    if staged:
        rel_paths = _run_git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"])
    else:
        rel_paths = _run_git(["ls-files", "-z"])

    for rel in rel_paths:
        path = (REPO_ROOT / rel).resolve()
        if path.is_file():
            yield path


def _is_candidate(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name in SPECIAL_TEXT_FILES:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def _check_file(path: Path) -> list[str]:
    issues: list[str] = []
    data = path.read_bytes()

    for bom_name, bom in BOMS:
        if data.startswith(bom):
            issues.append(f"{path.relative_to(REPO_ROOT)}: starts with {bom_name} BOM")
            break

    if b"\x00" in data:
        issues.append(f"{path.relative_to(REPO_ROOT)}: contains NUL bytes (likely wrong encoding)")

    decoded = ""
    try:
        decoded = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        issues.append(f"{path.relative_to(REPO_ROOT)}: not valid UTF-8 ({exc})")
        return issues

    if "\uFFFD" in decoded:
        bad_lines = []
        for idx, line in enumerate(decoded.splitlines(), start=1):
            if "\uFFFD" in line:
                bad_lines.append(str(idx))
            if len(bad_lines) >= 5:
                break
        suffix = f" (lines: {', '.join(bad_lines)})" if bad_lines else ""
        issues.append(
            f"{path.relative_to(REPO_ROOT)}: contains replacement character U+FFFD{suffix}"
        )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate repository text files are UTF-8 without BOM.")
    parser.add_argument("--staged", action="store_true", help="Check staged files only.")
    args = parser.parse_args()

    files = [p for p in _iter_files_from_git(staged=args.staged) if _is_candidate(p)]
    issues: list[str] = []
    for file_path in files:
        issues.extend(_check_file(file_path))

    if issues:
        print("Encoding guard failed:")
        for issue in issues:
            print(f"  - {issue}")
        print("Fix files to UTF-8 (no BOM) and retry.")
        return 1

    checked_scope = "staged files" if args.staged else "tracked files"
    print(f"Encoding guard passed ({len(files)} {checked_scope}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
