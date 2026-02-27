# Encoding Policy

This repository uses UTF-8 (without BOM) for source and config text files.

## Why

- Prevent mojibake (garbled text).
- Prevent hidden UTF-16/CP949 file writes on Windows.
- Keep diffs and tooling stable.

## Enforcement

- `.editorconfig` sets UTF-8 and LF defaults.
- `.gitattributes` enforces UTF-8/LF normalization.
- `tools/encoding_guard.py` fails on:
  - UTF BOM markers
  - non-UTF-8 bytes
  - NUL bytes in text files
- `.githooks/pre-commit` runs the guard for staged files.

## One-time setup (per clone)

```bash
git config core.hooksPath .githooks
```

## Manual check

```bash
python tools/encoding_guard.py
```
