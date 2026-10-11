"""Stdlib-only repository lint; offline and writes no bytecode.

Checks every git-tracked file:
  * ``*.py``   compiles (syntax) with the running interpreter, without writing ``__pycache__``;
  * ``*.json`` parses as JSON;
  * ``*.jsonl`` parses as one JSON value per non-empty line.

Prints ``path:line: message`` per problem. Exit 0 = clean, 1 = problems.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def lint(root: Path) -> tuple[list[str], int]:
    problems: list[str] = []
    checked = 0
    for rel in tracked(root):
        path = root / rel
        if path.suffix not in {".py", ".json", ".jsonl"} or path.is_symlink() or not path.is_file():
            continue
        checked += 1
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            try:
                compile(text, rel, "exec", dont_inherit=True)
            except SyntaxError as exc:
                problems.append(f"{rel}:{exc.lineno or 0}: syntax error: {exc.msg}")
        elif path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                problems.append(f"{rel}:{exc.lineno}: invalid JSON: {exc.msg}")
        else:
            for number, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        problems.append(f"{rel}:{number}: invalid JSONL record: {exc.msg}")
    return problems, checked


def main() -> int:
    problems, checked = lint(ROOT)
    for problem in problems:
        print(problem)
    print(f"lint: {'OK' if not problems else f'{len(problems)} problem(s)'}; {checked} tracked "
          ".py/.json/.jsonl files checked", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
