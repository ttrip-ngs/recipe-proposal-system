"""ダッシュボード・カレンダー・日別詳細の E2E (Firestore エミュレータ経由)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _authenticated_client():
    from fastapi.testclient import TestClient

    from recipe_system.main import create_app
    from recipe_system.web.middleware.auth import AuthenticatedUser, current_user

    app = create_app()

    def _fake_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            uid="dashboard-e2e",
            email="e2e@example.com",
            email_verified=True,
            family_id="dashboard-e2e-family",
        )

    app.dependency_overrides[current_user] = _fake_user
    return TestClient(app)


def _cleanup(family_id: str) -> None:
    from recipe_system.repository.firestore_client import get_firestore_client
    from recipe_system.repository.meal_plan_repository import delete_meal_plan
    from recipe_system.services.calendar_view import monday_of, today_in_jst

    client = get_firestore_client()
    monday = monday_of(today_in_jst())
    for offset in range(-7, 14):
        from datetime import timedelta

        delete_meal_plan(client, family_id, monday + timedelta(days=offset))


def test_dashboard_と週ビューがレンダリングされる() -> None:
    family_id = "dashboard-e2e-family"
    _cleanup(family_id)

    from recipe_system.domain import MealPlan, MealPlanDish
    from recipe_system.repository.firestore_client import get_firestore_client
    from recipe_system.repository.meal_plan_repository import upsert_meal_plan
    from recipe_system.services.calendar_view import monday_of, today_in_jst

    fs = get_firestore_client()
    today = today_in_jst()
    monday = monday_of(today)

    upsert_meal_plan(
        fs,
        MealPlan(
            family_id=family_id,
            plan_date=monday,
            status="cooked",
            dishes=(MealPlanDish(name="肉じゃが", category="主菜", main_ingredient="牛肉"),),
            source="manual",
        ),
    )

    client = _authenticated_client()

    # ダッシュボード
    r = client.get("/")
    assert r.status_code == 200, r.text[:500]
    assert "肉じゃが" in r.text
    assert "今週" in r.text

    # 週ビュー
    r = client.get(f"/calendar/week?start={monday.isoformat()}")
    assert r.status_code == 200
    assert "肉じゃが" in r.text

    # 月ビュー
    r = client.get(f"/calendar/month?year={today.year}&month={today.month}")
    assert r.status_code == 200

    # 日別詳細 (今日)
    r = client.get(f"/plans/{today.isoformat()}")
    assert r.status_code == 200

    # 日別詳細 (空の未来日)
    future = today.replace(day=min(28, today.day))
    from datetime import timedelta

    future = today + timedelta(days=3)
    r = client.get(f"/plans/{future.isoformat()}")
    assert r.status_code == 200
    assert "未定" in r.text or "提案" in r.text

    _cleanup(family_id)


def test_skip_clear_の状態遷移() -> None:
    family_id = "dashboard-e2e-family"
    _cleanup(family_id)

    from datetime import timedelta

    from recipe_system.services.calendar_view import today_in_jst

    client = _authenticated_client()
    target = today_in_jst() + timedelta(days=2)

    # skip
    r = client.post(f"/plans/{target.isoformat()}/skip", follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/plans/{target.isoformat()}")
    assert r.status_code == 200
    assert "スキップ" in r.text

    # clear
    r = client.post(f"/plans/{target.isoformat()}/clear", follow_redirects=False)
    assert r.status_code == 303

    r = client.get(f"/plans/{target.isoformat()}")
    assert r.status_code == 200
    assert "未定" in r.text or "提案" in r.text

    _cleanup(family_id)


def test_過去日への提案は400() -> None:
    family_id = "dashboard-e2e-family"
    _cleanup(family_id)

    from datetime import timedelta

    from recipe_system.services.calendar_view import today_in_jst

    client = _authenticated_client()
    past = today_in_jst() - timedelta(days=1)
    r = client.post(f"/plans/{past.isoformat()}/propose", follow_redirects=False)
    assert r.status_code == 400


def test_存在しない_meal_plan_への_confirm_は400() -> None:
    family_id = "dashboard-e2e-family"
    _cleanup(family_id)

    from datetime import timedelta

    from recipe_system.services.calendar_view import today_in_jst

    client = _authenticated_client()
    target = today_in_jst() + timedelta(days=5)
    r = client.post(f"/plans/{target.isoformat()}/confirm", follow_redirects=False)
    assert r.status_code == 400


def test_不正な日付フォーマットは400() -> None:
    client = _authenticated_client()
    r = client.get("/plans/notadate", follow_redirects=False)
    assert r.status_code == 400


def test_旧proposals_POSTは新ルートにリダイレクト() -> None:
    family_id = "dashboard-e2e-family"
    _cleanup(family_id)

    from recipe_system.services.calendar_view import today_in_jst

    client = _authenticated_client()
    today = today_in_jst()
    r = client.post("/proposals", follow_redirects=False)
    # 307 = TEMPORARY_REDIRECT
    assert r.status_code == 307
    assert r.headers["location"] == f"/plans/{today.isoformat()}/propose"

    _cleanup(family_id)


def test_記録ページはログイン中はナビにログアウトを表示する() -> None:
    """回帰テスト: history.py が user をテンプレートに渡し忘れ、認証済みでも
    ナビが未ログイン表示 (「ログイン」リンク) になっていた不具合の再発防止."""
    client = _authenticated_client()
    r = client.get("/history")
    assert r.status_code == 200
    assert "ログアウト" in r.text
    assert '<a href="/login">ログイン</a>' not in r.text


def test_提案詳細ページはログイン中はナビにログアウトを表示する() -> None:
    """回帰テスト: proposals.py の各 TemplateResponse も同様に user 渡し忘れがあった."""
    family_id = "dashboard-e2e-family"
    from recipe_system.repository.firestore_client import get_firestore_client
    from recipe_system.repository.proposal_repository import (
        pending_placeholder_record,
        save_proposal_record,
        update_proposal_record,
    )

    fs = get_firestore_client()
    proposal_id = save_proposal_record(fs, family_id=family_id, record=pending_placeholder_record())

    client = _authenticated_client()
    try:
        r = client.get(f"/proposals/{proposal_id}")
        assert r.status_code == 200
        assert "ログアウト" in r.text
        assert '<a href="/login">ログイン</a>' not in r.text

        update_proposal_record(
            fs,
            proposal_id,
            {
                **pending_placeholder_record(),
                "status": "ready",
                "succeeded": True,
                "dishes": [],
            },
        )
        r = client.get(f"/proposals/{proposal_id}")
        assert r.status_code == 200
        assert "ログアウト" in r.text
        assert '<a href="/login">ログイン</a>' not in r.text

        r = client.post(
            f"/proposals/{proposal_id}/feedback",
            data={"rating": "good"},
        )
        assert r.status_code == 200
        assert "ログアウト" in r.text
        assert '<a href="/login">ログイン</a>' not in r.text
    finally:
        fs.collection("proposals").document(proposal_id).delete()
