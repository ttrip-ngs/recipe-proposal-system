"""プロンプト YAML の version フィールドが存在することを確認する pre-commit フック.

使い方 (pre-commit から自動起動):
    python scripts/check_prompt_version.py <file1> <file2> ...
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def check(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return f"YAML パースエラー: {e}"
    if not isinstance(data, dict):
        return "YAML のトップレベルがマップでない"
    version = data.get("version")
    if version is None:
        return "version フィールドがありません"
    if not isinstance(version, str) or not version.strip():
        return "version は空でない文字列である必要があります"
    return None


def main(argv: list[str]) -> int:
    errors: list[str] = []
    for p in argv:
        path = Path(p)
        err = check(path)
        if err:
            errors.append(f"{path}: {err}")
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
