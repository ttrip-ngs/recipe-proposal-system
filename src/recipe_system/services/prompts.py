"""プロンプト YAML をロードし Jinja2 で展開する."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    model: str
    template: str

    def render(self, **variables: Any) -> str:
        tpl = Template(
            self.template, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True
        )
        return tpl.render(**variables)


def _load_prompt(path: Path) -> Prompt:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if "version" not in data or "template" not in data:
        raise ValueError(f"{path}: プロンプト YAML に version / template が必要")
    return Prompt(
        name=data.get("name", path.stem),
        version=str(data["version"]),
        model=data.get("model", "claude-sonnet-4-6"),
        template=data["template"],
    )


@cache
def get_prompt(name: str) -> Prompt:
    """名前を指定してプロンプトをロードする (結果はキャッシュされる)."""
    path = PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"プロンプトが見つからない: {path}")
    return _load_prompt(path)


def clear_cache() -> None:
    """プロンプト変更をテストで反映したい場合に使う."""
    get_prompt.cache_clear()
