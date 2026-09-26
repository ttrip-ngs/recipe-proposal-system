"""プロンプトローダーの単体テスト."""

from __future__ import annotations

import pytest

from recipe_system.services.prompts import get_prompt


def test_suggest_dinner_system_がロードできる() -> None:
    p = get_prompt("suggest_dinner_system")
    assert p.version == "2"
    assert p.model == "claude-sonnet-4-6"
    assert "family_profile" in p.template or "{{ family_profile" in p.template


def test_suggest_dinner_user_がロードできる() -> None:
    p = get_prompt("suggest_dinner_user")
    assert p.version == "1"


def test_存在しないプロンプトはFileNotFoundError() -> None:
    with pytest.raises(FileNotFoundError):
        get_prompt("does_not_exist")


def test_レンダリングできる() -> None:
    p = get_prompt("suggest_dinner_system")
    rendered = p.render(
        family_profile={"name": "T家", "members": [{"name": "夫", "role": "adult"}]},
        allergen_summary=[],
        dislike_summary=[],
        recent_recipe_names=[],
        previous_violations=[],
    )
    assert "T家" in rendered
    assert "夫" in rendered
