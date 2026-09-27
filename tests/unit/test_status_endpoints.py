"""生成完了ポーリング用ステータスエンドポイントのテスト.

待機 UI は meta refresh による全画面リロードをやめ、これらの軽量 JSON を
3 秒間隔でポーリングして完了時に 1 回だけリロードする方式に変えた
(`web/static/js/app.js` の setupPolling)。Firestore 関数は monkeypatch で
差し替える (`test_admin_route.py` と同じ流儀)。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from recipe_system.domain.meal_plan import MealPlan, MealPlanDish, MealPlanStatus
from recipe_system.main import create_app
from recipe_system.web.middleware.auth import AuthenticatedUser, current_user

FAMILY_ID = "test-family"
MONDAY = "2026-08-03"


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
            family_id=FAMILY_ID,
        )

    app.dependency_overrides[current_user] = _fake_user
    return TestClient(app)


def _fs_client() -> object:
    return object()


def _meal_plan(
    *,
    status: MealPlanStatus,
    proposal_id: str | None = None,
    dishes: tuple[MealPlanDish, ...] = (),
) -> MealPlan:
    return MealPlan(
        family_id=FAMILY_ID,
        plan_date=date(2026, 8, 3),
        status=status,
        proposal_id=proposal_id,
        dishes=dishes,
    )


# --- /proposals/{id}/status -------------------------------------------------


def _patch_proposal(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any] | None) -> None:
    from recipe_system.web.routes import proposals

    monkeypatch.setattr(proposals, "get_firestore_client", _fs_client)
    monkeypatch.setattr(proposals, "get_proposal", lambda _client, _pid: data)


def test_提案が_pending_なら_done_false(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_proposal(monkeypatch, {"family_id": FAMILY_ID, "status": "pending"})
    r = authenticated_client.get("/proposals/p1/status")
    assert r.status_code == 200
    assert r.json() == {"done": False}


def test_提案が_ready_なら_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_proposal(monkeypatch, {"family_id": FAMILY_ID, "status": "ready"})
    r = authenticated_client.get("/proposals/p1/status")
    assert r.json() == {"done": True}


def test_提案が_error_でも_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成が失敗して終わった場合もポーリングは止めてリロードさせる."""
    _patch_proposal(monkeypatch, {"family_id": FAMILY_ID, "status": "error"})
    assert authenticated_client.get("/proposals/p1/status").json() == {"done": True}


def test_他家族の提案ステータスは_404(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_proposal(monkeypatch, {"family_id": "other-family", "status": "pending"})
    assert authenticated_client.get("/proposals/p1/status").status_code == 404


def test_存在しない提案のステータスは_404(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_proposal(monkeypatch, None)
    assert authenticated_client.get("/proposals/p1/status").status_code == 404


# --- /plans/{date}/status ---------------------------------------------------


def _patch_day(
    monkeypatch: pytest.MonkeyPatch,
    *,
    meal_plan: MealPlan | None,
    proposal: dict[str, Any] | None,
) -> None:
    from recipe_system.web.routes import plans

    monkeypatch.setattr(plans, "get_firestore_client", _fs_client)
    monkeypatch.setattr(plans, "get_meal_plan", lambda _c, _f, _d: meal_plan)
    monkeypatch.setattr(plans, "get_proposal", lambda _c, _p: proposal)


def test_日別_提案が_pending_なら_done_false(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_day(
        monkeypatch,
        meal_plan=_meal_plan(status="proposed", proposal_id="p1"),
        proposal={"family_id": FAMILY_ID, "status": "pending"},
    )
    assert authenticated_client.get(f"/plans/{MONDAY}/status").json() == {"done": False}


def test_日別_提案が完了していれば_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_day(
        monkeypatch,
        meal_plan=_meal_plan(status="proposed", proposal_id="p1"),
        proposal={"family_id": FAMILY_ID, "status": "ready"},
    )
    assert authenticated_client.get(f"/plans/{MONDAY}/status").json() == {"done": True}


def test_日別_meal_plan_が無ければ_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_day(monkeypatch, meal_plan=None, proposal=None)
    assert authenticated_client.get(f"/plans/{MONDAY}/status").json() == {"done": True}


# --- /plans/week/{week_start}/status ----------------------------------------


def _patch_week(monkeypatch: pytest.MonkeyPatch, plans_list: list[MealPlan]) -> None:
    from recipe_system.web.routes import plans

    monkeypatch.setattr(plans, "get_firestore_client", _fs_client)
    monkeypatch.setattr(
        plans,
        "list_meal_plans_in_range",
        lambda _c, _f, *, start_date, end_date: plans_list,  # noqa: ARG005
    )


def test_週間_生成中の日が残っていれば_done_false(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status=proposed かつ dishes 空 = 生成予約済みでまだ書き戻されていない日."""
    _patch_week(monkeypatch, [_meal_plan(status="proposed", proposal_id="w1")])
    assert authenticated_client.get(f"/plans/week/{MONDAY}/status").json() == {"done": False}


def test_週間_全日に献立が入っていれば_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dish = MealPlanDish(name="肉じゃが", category="主菜", main_ingredient="牛肉")
    _patch_week(monkeypatch, [_meal_plan(status="proposed", proposal_id="w1", dishes=(dish,))])
    assert authenticated_client.get(f"/plans/week/{MONDAY}/status").json() == {"done": True}


def test_週間_予定が無ければ_done_true(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_week(monkeypatch, [])
    assert authenticated_client.get(f"/plans/week/{MONDAY}/status").json() == {"done": True}


# --- 未認証 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/proposals/p1/status",
        f"/plans/{MONDAY}/status",
        f"/plans/week/{MONDAY}/status",
    ],
)
def test_未認証のステータス取得は_login_にリダイレクトされる(client: TestClient, path: str) -> None:
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_静的ファイルの_URL_にキャッシュバスティングが付く(client: TestClient) -> None:
    """CSS/JS の更新がブラウザキャッシュで無視される事象への対策の回帰テスト."""
    import re

    body = client.get("/login").text
    assert re.search(r'href="/static/css/app\.css\?v=\d+"', body)
    assert re.search(r'src="/static/js/app\.js\?v=\d+"', body)


def test_アレルゲン辞書が不正なら起動時に失敗する(monkeypatch: pytest.MonkeyPatch) -> None:
    from recipe_system import main

    def broken() -> None:
        raise ValueError("aliases.yaml: 重複登録")

    monkeypatch.setattr(main, "default_dictionary", broken)
    with pytest.raises(ValueError, match="重複登録"), TestClient(create_app()):
        pass
