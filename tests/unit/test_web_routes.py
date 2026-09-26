"""FastAPI ルーター結合テスト (TestClient).

Firestore は呼ばない範囲で疎通を確認する. Firestore アクセスを含むルート
(/proposals, /history, /profile) はここではスキップし integration/ 配下で扱う.
"""

from __future__ import annotations

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from recipe_system.main import create_app
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


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


def test_healthz_は_200_を返す(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_未認証トップは_login_にリダイレクトされる(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_ページがレンダリングされる(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200
    assert "ログイン" in r.text
    assert "sign-in-google" in r.text


def test_存在しないページは_404(client: TestClient) -> None:
    r = client.get("/does-not-exist")
    assert r.status_code == 404
    assert "見つかりません" in r.text


def test_logout_は_login_に_303(client: TestClient) -> None:
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_認証済みならトップが表示される(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
) -> None:
    from recipe_system.web.routes import dashboard

    def _fake_client() -> object:
        return object()

    def _empty_plans(*_args: object, **_kwargs: object) -> tuple:
        return ()

    def _no_shopping_list(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(dashboard, "get_firestore_client", _fake_client)
    monkeypatch.setattr(dashboard, "list_meal_plans_in_range", _empty_plans)
    monkeypatch.setattr(dashboard, "get_shopping_list", _no_shopping_list)

    r = authenticated_client.get("/")
    assert r.status_code == 200
    # 今週ストリップ・タブが表示されることを確認
    assert "今週" in r.text
    assert "献立を提案" in r.text


def test_週レビューは月曜のみ受け付ける(authenticated_client: TestClient) -> None:
    # 火曜日 (2026-05-26) を指定 → 400
    r = authenticated_client.get("/plans/week/2026-05-26/review")
    assert r.status_code == 400
    assert "月曜" in r.text or "月曜" in r.json().get("detail", "")


def test_週レビューが空状態でも200を返す(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
) -> None:
    from recipe_system.web.routes import plans

    def _fake_client() -> object:
        return object()

    def _empty_plans(*_args: object, **_kwargs: object) -> tuple:
        return ()

    monkeypatch.setattr(plans, "get_firestore_client", _fake_client)
    monkeypatch.setattr(plans, "list_meal_plans_in_range", _empty_plans)

    # 月曜日 (2026-05-25) を指定
    r = authenticated_client.get("/plans/week/2026-05-25/review")
    assert r.status_code == 200
    assert "Weekly Review" in r.text or "週レビュー" in r.text


def test_週レビューに概算食材費が表示される(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
) -> None:
    from datetime import date

    from recipe_system.domain import MealPlan, MealPlanDish
    from recipe_system.web.routes import plans

    plan = MealPlan(
        family_id="test-family",
        plan_date=date(2026, 5, 25),
        status="proposed",
        dishes=(
            MealPlanDish(
                name="肉じゃが",
                category="主菜",
                main_ingredient="牛肉",
                ingredients=(
                    {"name": "牛肉", "quantity": 300, "unit": "g"},
                    {"name": "醤油", "quantity": 3, "unit": "大さじ"},
                    {"name": "謎の食材", "quantity": 1, "unit": "個"},
                ),
            ),
        ),
    )

    monkeypatch.setattr(plans, "get_firestore_client", object)
    monkeypatch.setattr(plans, "list_meal_plans_in_range", lambda *_a, **_k: (plan,))

    r = authenticated_client.get("/plans/week/2026-05-25/review")
    assert r.status_code == 200
    assert "概算食材費" in r.text
    assert "価格不明 1 品目" in r.text
    assert "謎の食材" in r.text


def test_週一括確定は月曜以外を拒否する(authenticated_client: TestClient) -> None:
    # 水曜日 (2026-05-27) を指定 → 400
    r = authenticated_client.post(
        "/plans/week/2026-05-27/confirm-all",
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_週一括確定は過去週を拒否する(authenticated_client: TestClient) -> None:
    # 2000-01-03 (月) を指定 → 過去週で 400
    r = authenticated_client.post(
        "/plans/week/2000-01-03/confirm-all",
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_週一括確定はproposed_かつ_dishes有りの日のみ確定する(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
) -> None:
    """happy path: 状態別に確定対象が正しく選別され, リダイレクト先がレビュー画面."""
    from datetime import date, timedelta

    from recipe_system.domain.meal_plan import MealPlan, MealPlanDish
    from recipe_system.services.calendar_view import monday_of, today_in_jst
    from recipe_system.web.routes import plans

    week_start = monday_of(today_in_jst())

    sample_dish = MealPlanDish(
        name="鶏の照り焼き",
        category="主菜",
        main_ingredient="鶏もも肉",
        reason="家族の好物",
        ingredients=(),
    )

    # 月: proposed + dishes 有り (確定対象)
    # 火: proposed + dishes 無し (pending, 触らない)
    # 水: confirmed (触らない)
    # 木: cooked (触らない)
    # 金: skipped (触らない)
    # 土: proposed + dishes 有り (確定対象)
    # 日: 該当 plan なし (触らない)
    sample_plans = (
        MealPlan(
            family_id="test-family",
            plan_date=week_start,
            status="proposed",
            proposal_id="p1",
            dishes=(sample_dish,),
            source="weekly_batch",
        ),
        MealPlan(
            family_id="test-family",
            plan_date=week_start + timedelta(days=1),
            status="proposed",
            proposal_id="p2",
            dishes=(),
            source="weekly_batch",
        ),
        MealPlan(
            family_id="test-family",
            plan_date=week_start + timedelta(days=2),
            status="confirmed",
            proposal_id="p3",
            dishes=(sample_dish,),
            source="single",
        ),
        MealPlan(
            family_id="test-family",
            plan_date=week_start + timedelta(days=3),
            status="cooked",
            proposal_id="p4",
            dishes=(sample_dish,),
            source="single",
        ),
        MealPlan(
            family_id="test-family",
            plan_date=week_start + timedelta(days=4),
            status="skipped",
            proposal_id=None,
            dishes=(),
            source="manual",
        ),
        MealPlan(
            family_id="test-family",
            plan_date=week_start + timedelta(days=5),
            status="proposed",
            proposal_id="p6",
            dishes=(sample_dish,),
            source="weekly_batch",
        ),
    )

    called: list[date] = []

    def _fake_client() -> object:
        return object()

    def _fake_list(*_args: object, **_kwargs: object) -> tuple[MealPlan, ...]:
        return sample_plans

    def _fake_update(
        _client: object,
        _family_id: str,
        plan_date: date,
        **_kwargs: object,
    ) -> None:
        called.append(plan_date)

    monkeypatch.setattr(plans, "get_firestore_client", _fake_client)
    monkeypatch.setattr(plans, "list_meal_plans_in_range", _fake_list)
    monkeypatch.setattr(plans, "update_status", _fake_update)

    r = authenticated_client.post(
        f"/plans/week/{week_start.isoformat()}/confirm-all",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/plans/week/{week_start.isoformat()}/review"
    assert called == [week_start, week_start + timedelta(days=5)]


def test_session_POST_に不正な_id_token_で_401(client: TestClient) -> None:
    # Firebase Admin SDK 初期化を試みるがトークンは検証失敗する (ADC なし or invalid)
    r = client.post("/session", json={"id_token": "invalid"}, follow_redirects=False)
    # JSON クライアントなので JSON で 401 が返る (リダイレクトはしない)
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    body = r.json()
    assert "detail" in body
