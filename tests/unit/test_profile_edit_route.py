"""家族プロファイル編集ルートの「通常は除去不要な食品」の可/不可 (ADR 0007) のテスト.

Firestore はモックする. 選択必須の検証はサーバ側が正本なので, フォームを直接送って確かめる.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from recipe_system.domain import FamilyMember, FamilyProfile
from recipe_system.main import create_app
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user
from recipe_system.web.routes import profile, profile_edit


def _family(member: FamilyMember) -> FamilyProfile:
    now = datetime.now(UTC)
    return FamilyProfile(
        family_id="test-family", name="テスト家", members=(member,), created_at=now, updated_at=now
    )


def _member(allergens: set[str], policies: dict[str, Any] | None = None) -> FamilyMember:
    return FamilyMember(
        member_id="m-1",
        name="子",
        allergens=frozenset(allergens),
        item_policies=policies or {},
        reviewed_at=datetime.now(UTC),
    )


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(profile_edit, "get_firestore_client", object)
    monkeypatch.setattr(
        profile_edit, "upsert_member", lambda _client, _family_id, **kw: calls.append(kw)
    )
    return calls


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        uid="u", email="a@example.com", email_verified=True, family_id="test-family"
    )
    return TestClient(app)


def test_除去不要候補の可不可が未選択なら保存しない(
    client: TestClient, saved: list[dict[str, Any]]
) -> None:
    r = client.post(
        "/profile/members/m-1",
        data={"name": "子", "allergens": ["大豆"], "policy__大豆__醤油": "allow"},
        follow_redirects=False,
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert "大豆の味噌" in r.text
    assert "大豆の大豆油" in r.text
    assert saved == []


def test_全て選べば可不可を保存する(client: TestClient, saved: list[dict[str, Any]]) -> None:
    r = client.post(
        "/profile/members",
        data={
            "name": "子",
            "allergens": ["大豆", "エビ"],
            "policy__大豆__醤油": "allow",
            "policy__大豆__味噌": "block",
            "policy__大豆__大豆油": "allow",
            # 選んでいないアレルゲンの値は捨てる
            "policy__小麦__醤油": "allow",
        },
        follow_redirects=False,
    )
    assert r.status_code == status.HTTP_303_SEE_OTHER
    assert saved[0]["item_policies"] == {
        "大豆": {"醤油": "allow", "味噌": "block", "大豆油": "allow"}
    }


def test_不正な値は未選択として扱う(client: TestClient, saved: list[dict[str, Any]]) -> None:
    r = client.post(
        "/profile/members",
        data={"name": "子", "allergens": ["ごま"], "policy__ごま__ごま油": "maybe"},
        follow_redirects=False,
    )
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert saved == []


def test_編集画面は除去不要候補をアレルゲンの選択肢に出さず可不可として出す(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_edit, "get_firestore_client", object)
    monkeypatch.setattr(
        profile_edit,
        "get_family",
        lambda _c, _f: _family(_member({"大豆"}, {"大豆": {"醤油": "allow"}})),
    )
    html = client.get("/profile/edit").text
    assert 'name="allergens" value="味噌"' not in html
    assert 'name="allergens" value="醤油"' not in html
    assert 'name="policy__大豆__醤油" value="allow"\n                checked' in html
    assert "可/不可 未設定 2 件" in html  # 味噌・大豆油


def test_閲覧画面に未設定の警告と摂取可の食品を出す(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile, "get_firestore_client", object)
    monkeypatch.setattr(
        profile,
        "get_family",
        lambda _c, _f: _family(_member({"大豆"}, {"大豆": {"醤油": "allow", "味噌": "block"}})),
    )
    html = client.get("/profile").text
    assert "食べてよい: 醤油" in html
    assert "大豆の大豆油" in html


def test_既存メンバーの更新でも可不可を保存する(
    client: TestClient, saved: list[dict[str, Any]]
) -> None:
    r = client.post(
        "/profile/members/m-1",
        data={"name": "子", "allergens": ["ごま"], "policy__ごま__ごま油": "block"},
        follow_redirects=False,
    )
    assert r.status_code == status.HTTP_303_SEE_OTHER
    assert saved[0]["member_id"] == "m-1"
    assert saved[0]["item_policies"] == {"ごま": {"ごま油": "block"}}


@pytest.mark.parametrize("bad", [{"大豆": {"醤油": "ALLOW"}}, {"大豆": "allow"}])
def test_可不可の不正値はメンバーの読み込みで拒否する(bad: dict[str, Any]) -> None:
    # Firestore の直接編集などで不正値が入っても allow 扱いにせず, 読み込みを失敗させる
    with pytest.raises(ValidationError):
        _member({"大豆"}, bad)
