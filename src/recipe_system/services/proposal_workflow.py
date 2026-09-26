"""単日・週間の提案生成ワークフロー (LLM 呼出 -> Firestore 書き戻し).

``web/routes/plans.py`` の FastAPI ``BackgroundTasks`` から呼ばれるユースケース
中核ロジック. ルーターは HTTP リクエスト/レスポンスの処理に専念させ、
提案生成・失敗時のステータス巻き戻しといった業務ロジックはここに集約する.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from google.cloud import firestore

from recipe_system.domain import MealPlanDish
from recipe_system.llm.client import build_client, build_detail_client
from recipe_system.llm.errors import BudgetExceededError
from recipe_system.observability.logging import get_logger
from recipe_system.repository.family_repository import get_family
from recipe_system.repository.firestore_client import get_firestore_client
from recipe_system.repository.history_repository import recent_history
from recipe_system.repository.meal_plan_repository import get_meal_plan, update_status
from recipe_system.repository.proposal_repository import update_proposal_record
from recipe_system.repository.recipe_repository import list_recipes
from recipe_system.services.suggest_dinner import SuggestContext, suggest_dinner
from recipe_system.services.weekly_planner import WeeklySuggestContext, plan_week

logger = get_logger(__name__)


def _dishes_to_meal_plan_dishes(dishes: Any) -> tuple[MealPlanDish, ...]:
    """正規化済み Dish (NormalizedDish 相当) を MealPlanDish のタプルへ変換する."""
    return tuple(
        MealPlanDish(
            name=d.name,
            category=d.category,
            main_ingredient=d.main_ingredient,
            reason=d.reason,
            ingredients=tuple(
                {
                    "name": ing.name,
                    "canonical": ing.canonical,
                    "allergen_tags": sorted(ing.allergen_tags),
                    "quantity": ing.quantity,
                    "unit": ing.unit,
                }
                for ing in d.ingredients
            ),
            steps=tuple(d.steps),
        )
        for d in dishes
    )


async def run_single_suggestion(family_id: str, plan_date: date, proposal_id: str) -> None:
    """単日提案を生成し、proposals / meal_plans の両方を更新する.

    失敗時 (予算超過・その他例外) は meal_plans を status=empty に戻す
    (フェイルオープンしない).
    """
    client = get_firestore_client()
    try:
        family = get_family(client, family_id)
        all_recipes = list_recipes(client)
        history = recent_history(client, family_id, days=14)
        context = SuggestContext(
            family=family,
            all_recipes=all_recipes,
            recent_history=history,
            pantry=(),
            user_request=None,
        )
        llm = build_client(purpose="single_day", family_id=family_id)
        result = await suggest_dinner(context, llm=llm)
    except BudgetExceededError as e:
        logger.warning("proposal.budget_exceeded", proposal_id=proposal_id, detail=str(e))
        update_proposal_record(
            client,
            proposal_id,
            {
                "status": "budget_exceeded",
                "error_code": "budget_exceeded",
                "error_message": e.user_message,
                "succeeded": False,
            },
        )
        update_status(client, family_id, plan_date, status="empty", proposal_id=proposal_id)
        return
    except Exception as e:
        logger.exception("proposal.background_failed", proposal_id=proposal_id, error=str(e))
        update_proposal_record(
            client,
            proposal_id,
            {"status": "error", "error_message": str(e), "succeeded": False},
        )
        update_status(client, family_id, plan_date, status="empty", proposal_id=proposal_id)
        return

    update_proposal_record(client, proposal_id, result.to_firestore_record())

    if result.succeeded:
        update_status(
            client,
            family_id,
            plan_date,
            status="proposed",
            proposal_id=proposal_id,
            dishes=_dishes_to_meal_plan_dishes(result.dishes),
            source="single",
        )
    else:
        update_status(client, family_id, plan_date, status="empty", proposal_id=proposal_id)

    logger.info(
        "proposal.stored",
        proposal_id=proposal_id,
        plan_date=plan_date.isoformat(),
        succeeded=result.succeeded,
        retry_count=result.retry_count,
    )


async def run_weekly_suggestion(family_id: str, week_start: date, proposal_id: str) -> None:
    """週間提案を生成し、proposals / meal_plans (対象日のみ) を更新する.

    対象日は呼出元 (ルーター) が事前に status=proposed + dishes=() で予約済み.
    他の操作で既に書き換わっていた日は触らない.
    """
    client = get_firestore_client()
    target_dates = [week_start + timedelta(days=i) for i in range(7)]
    try:
        family = get_family(client, family_id)
        all_recipes = list_recipes(client)
        history = recent_history(client, family_id, days=14)
        context = WeeklySuggestContext(
            family=family,
            all_recipes=all_recipes,
            recent_history=history,
            week_start=week_start,
            pantry=(),
            user_request=None,
        )
        llm = build_client(purpose="weekly", family_id=family_id)
        detail_llm = build_detail_client(family_id=family_id)
        result = await plan_week(context, llm=llm, detail_llm=detail_llm)
    except BudgetExceededError as e:
        logger.warning("weekly.budget_exceeded", proposal_id=proposal_id, detail=str(e))
        update_proposal_record(
            client,
            proposal_id,
            {
                "status": "budget_exceeded",
                "error_code": "budget_exceeded",
                "error_message": e.user_message,
                "succeeded": False,
            },
        )
        _rollback_reserved_dates(client, family_id, target_dates, proposal_id)
        return
    except Exception as e:
        logger.exception("weekly.background_failed", proposal_id=proposal_id, error=str(e))
        update_proposal_record(
            client,
            proposal_id,
            {"status": "error", "error_message": str(e), "succeeded": False},
        )
        _rollback_reserved_dates(client, family_id, target_dates, proposal_id)
        return

    update_proposal_record(client, proposal_id, result.to_firestore_record())

    for day_result in result.days:
        existing = get_meal_plan(client, family_id, day_result.plan_date)
        if existing is None or existing.proposal_id != proposal_id:
            # 既に他の操作で書き換わっていれば触らない
            continue
        if day_result.succeeded:
            update_status(
                client,
                family_id,
                day_result.plan_date,
                status="proposed",
                proposal_id=proposal_id,
                dishes=_dishes_to_meal_plan_dishes(day_result.dishes),
                source="weekly_batch",
            )
        else:
            update_status(
                client, family_id, day_result.plan_date, status="empty", proposal_id=proposal_id
            )

    logger.info(
        "weekly.proposal.stored",
        proposal_id=proposal_id,
        week_start=week_start.isoformat(),
        succeeded_days=result.succeeded_days,
        retry_count=result.retry_count,
    )


def _rollback_reserved_dates(
    client: firestore.Client,
    family_id: str,
    target_dates: list[date],
    proposal_id: str,
) -> None:
    """予約済み (status=proposed, dishes=()) の対象日を empty に戻す.

    LLM 呼出自体が失敗したケース向け. 呼出中に他操作で書き換わった日は触らない.
    """
    for d in target_dates:
        existing = get_meal_plan(client, family_id, d)
        is_reserved = (
            existing is not None
            and existing.proposal_id == proposal_id
            and existing.status == "proposed"
        )
        if is_reserved:
            update_status(client, family_id, d, status="empty", proposal_id=proposal_id)
