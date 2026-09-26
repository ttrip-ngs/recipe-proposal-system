"""`/admin/usage` ルーターの基本テスト. Firestore 関数は monkeypatch で差し替える."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from recipe_system.main import create_app
from recipe_system.repository.llm_usage_repository import MonthlySummary
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def authenticated_client() -> TestClient:
    app = create_app()

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            uid="test-uid",
            email="test@example.com",
            email_verified=True,
            family_id="test-family",
        )

    app.dependency_overrides[current_user] = _fake_user
    return TestClient(app)


def _patch_admin(
    monkeypatch: pytest.MonkeyPatch,
    *,
    total_jpy: float,
    budget_jpy: float,
    warn_pct: int = 80,
) -> None:
    from recipe_system.web.routes import admin

    def _fs_client() -> object:
        return object()

    def _summary(
        _client: Any,
        *,
        family_id: str,  # noqa: ARG001
        year_month: str,  # noqa: ARG001
    ) -> MonthlySummary:
        return MonthlySummary(
            year_month="2026-05",
            total_jpy=total_jpy,
            total_calls=3,
            success_calls=3,
            by_purpose={"single_day": total_jpy},
            by_purpose_count={"single_day": 3},
            by_day=[],
            cache_hit_rate=0.5,
            recent_records=[],
        )

    monkeypatch.setattr(admin, "get_firestore_client", _fs_client)
    monkeypatch.setattr(admin, "monthly_summary", _summary)
    monkeypatch.setattr(admin, "get_budget_jpy", lambda _c: budget_jpy)
    monkeypatch.setattr(admin, "get_warn_threshold_pct", lambda _c: warn_pct)


def test_未認証は_login_にリダイレクト(client: TestClient) -> None:
    r = client.get("/admin/usage", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_認証済みなら_200_を返す(
    monkeypatch: pytest.MonkeyPatch, authenticated_client: TestClient
) -> None:
    _patch_admin(monkeypatch, total_jpy=10.5, budget_jpy=3000.0)
    r = authenticated_client.get("/admin/usage")
    assert r.status_code == 200
    assert "AI" in r.text
    assert "今月" in r.text


def test_予算超過時は_danger_メッセージが表示される(
    monkeypatch: pytest.MonkeyPatch, authenticated_client: TestClient
) -> None:
    _patch_admin(monkeypatch, total_jpy=5000.0, budget_jpy=3000.0)
    r = authenticated_client.get("/admin/usage")
    assert r.status_code == 200
    assert "予算超過" in r.text
    assert "monthly_jpy_limit" in r.text


def test_warn_閾値超過なら_warn_テキストが見える(
    monkeypatch: pytest.MonkeyPatch, authenticated_client: TestClient
) -> None:
    _patch_admin(monkeypatch, total_jpy=2500.0, budget_jpy=3000.0, warn_pct=80)
    r = authenticated_client.get("/admin/usage")
    assert r.status_code == 200
    # 83% は warn 閾値 (80%) 以上、ただし 100% 未満.
    assert "budget-widget--warn" in r.text
