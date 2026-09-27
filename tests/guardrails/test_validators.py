"""レシピ検証ロジックのテスト. アレルゲン検出は命に関わるため高カバレッジ必須."""

from __future__ import annotations

from datetime import UTC, datetime

from recipe_system.domain import FamilyMember, Ingredient, Recipe, Violation
from recipe_system.guardrails.dictionary_loader import load_dictionary
from recipe_system.guardrails.validators import has_blocking_violation, validate_recipe


def _member(
    name: str,
    *,
    allergens: set[str] | None = None,
    dislikes: set[str] | None = None,
) -> FamilyMember:
    return FamilyMember(
        member_id=f"m-{name}",
        name=name,
        allergens=frozenset(allergens or set()),
        dislikes=frozenset(dislikes or set()),
        reviewed_at=datetime.now(UTC),
    )


def _recipe(ingredients: list[Ingredient]) -> Recipe:
    return Recipe(
        name="テスト料理",
        category="主菜",
        main_ingredient=ingredients[0].canonical,
        ingredients=tuple(ingredients),
    )


def test_エビアレルギーの家族にエビ料理は絶対に通さない() -> None:
    family = [_member("妻", allergens={"甲殻類"})]
    recipe = _recipe(
        [Ingredient(name="むきえび", canonical="エビ", allergen_tags=frozenset({"甲殻類"}))]
    )
    violations = validate_recipe(recipe, family)
    blocks = [v for v in violations if v.severity == "block"]
    assert len(blocks) == 1
    assert blocks[0].member == "妻"
    assert has_blocking_violation(violations)


def test_アレルゲンが無関係ならblockは出ない() -> None:
    family = [_member("夫", allergens={"卵"})]
    recipe = _recipe([Ingredient(name="牛肉", canonical="牛肉", allergen_tags=frozenset({"肉類"}))])
    violations = validate_recipe(recipe, family)
    assert not has_blocking_violation(violations)


def test_嫌いな食材はwarnになる() -> None:
    family = [_member("夫", dislikes={"セロリ"})]
    recipe = _recipe([Ingredient(name="セロリ", canonical="セロリ", allergen_tags=frozenset())])
    violations = validate_recipe(recipe, family)
    warns = [v for v in violations if v.severity == "warn"]
    assert len(warns) == 1
    assert not has_blocking_violation(violations)


def test_複数家族全員のアレルゲンが同時に検証される() -> None:
    family = [
        _member("妻", allergens={"甲殻類"}),
        _member("子", allergens={"卵"}),
    ]
    recipe = _recipe(
        [
            Ingredient(name="むきえび", canonical="エビ", allergen_tags=frozenset({"甲殻類"})),
            Ingredient(name="卵", canonical="卵", allergen_tags=frozenset({"卵"})),
        ]
    )
    violations = validate_recipe(recipe, family)
    blocks = [v for v in violations if v.severity == "block"]
    assert len(blocks) == 2
    members = {b.member for b in blocks}
    assert members == {"妻", "子"}


def test_canonical_個別指定で該当食材だけがblockされる() -> None:
    # くるみだけアレルギーの場合, アーモンドなど同じナッツ群の他食材は通る.
    family = [_member("夫", allergens={"くるみ"})]
    walnut_recipe = _recipe(
        [
            Ingredient(
                name="くるみ",
                canonical="くるみ",
                allergen_tags=frozenset({"ナッツ", "くるみ"}),
            )
        ]
    )
    almond_recipe = _recipe(
        [
            Ingredient(
                name="アーモンド",
                canonical="アーモンド",
                allergen_tags=frozenset({"ナッツ", "アーモンド"}),
            )
        ]
    )
    assert has_blocking_violation(validate_recipe(walnut_recipe, family))
    assert not has_blocking_violation(validate_recipe(almond_recipe, family))


def test_グループ名指定なら同グループ全食材がblockされる() -> None:
    # ナッツグループ全体を指定した場合, ナッツ類すべてが block される.
    family = [_member("子", allergens={"ナッツ"})]
    for canonical in ("くるみ", "アーモンド", "ピスタチオ", "マカダミアナッツ"):
        recipe = _recipe(
            [
                Ingredient(
                    name=canonical,
                    canonical=canonical,
                    allergen_tags=frozenset({"ナッツ", canonical}),
                )
            ]
        )
        assert has_blocking_violation(validate_recipe(recipe, family)), (
            f"{canonical} がブロックされていない"
        )


def test_Violationはfrozenである() -> None:
    v = Violation(
        severity="block",
        member="妻",
        ingredient="むきえび",
        canonical="エビ",
        reason="アレルゲン: 甲殻類",
    )
    try:
        v.severity = "warn"  # type: ignore[misc]
    except (TypeError, AttributeError, ValueError):
        return
    raise AssertionError("Violation が frozen になっていない")


# ---- 手順 (自由文) のアレルゲン検査 ----


def _recipe_with_steps(steps: list[str]) -> Recipe:
    """食材リストはアレルゲンを含まず、手順にだけ語が現れるレシピ."""
    return Recipe(
        name="テスト料理",
        category="主菜",
        main_ingredient="豚肉",
        ingredients=(Ingredient(name="豚肉", canonical="豚肉", allergen_tags=frozenset({"肉類"})),),
        steps=tuple(steps),
    )


def test_手順にだけ現れた卵は食材リストに無くてもblockされる() -> None:
    family = [_member("長男", allergens={"卵"})]
    violations = validate_recipe(_recipe_with_steps(["最後に溶き卵を回し入れてとじる"]), family)
    blocks = [v for v in violations if v.severity == "block"]
    assert len(blocks) == 1
    assert blocks[0].member == "長男"
    assert blocks[0].canonical == "卵"


def test_手順のエイリアス表記もblockされる() -> None:
    family = [_member("妻", allergens={"甲殻類"})]
    violations = validate_recipe(_recipe_with_steps(["海老を加えて炒める"]), family)
    assert has_blocking_violation(violations)


def test_グループ指定なら手順の同グループ食材もblockされる() -> None:
    family = [_member("妻", allergens={"甲殻類"})]
    violations = validate_recipe(_recipe_with_steps(["ズワイガニの身をほぐしてのせる"]), family)
    assert has_blocking_violation(violations)


def test_手順のフライパンは小麦アレルギーでも誤検出しない() -> None:
    """「パン」は text_match_exclude のため部分一致に使わない."""
    family = [_member("夫", allergens={"小麦"})]
    violations = validate_recipe(_recipe_with_steps(["フライパンで両面を焼く"]), family)
    assert not has_blocking_violation(violations)


def test_手順のなめらかにはカニアレルギーでも誤検出しない() -> None:
    family = [_member("妻", allergens={"甲殻類"})]
    violations = validate_recipe(_recipe_with_steps(["なめらかになるまで混ぜる"]), family)
    assert not has_blocking_violation(violations)


def test_除外語でも同じ食材の別表記は手順で検出される() -> None:
    """「パン」を除外しても「パン粉」は検出する (除外は語単位で、食材単位ではない)."""
    family = [_member("夫", allergens={"小麦"})]
    violations = validate_recipe(_recipe_with_steps(["パン粉をまぶして揚げる"]), family)
    assert has_blocking_violation(violations)


def test_手順の語が家族のアレルゲンと無関係ならblockしない() -> None:
    """家族のアレルギーに関係する語だけを検査する (無関係な語で誤検出させない)."""
    family = [_member("夫", allergens=set()), _member("妻", allergens={"卵"})]
    violations = validate_recipe(
        _recipe_with_steps(["海老を加えて炒める", "パン粉をまぶす"]), family
    )
    assert not has_blocking_violation(violations)


def test_手順が無ければ手順検査は行わない() -> None:
    family = [_member("長男", allergens={"卵"})]
    recipe = _recipe([Ingredient(name="豚肉", canonical="豚肉", allergen_tags=frozenset())])
    assert validate_recipe(recipe, family) == ()


def test_大豆アレルギーの家族に味噌を使う料理は通さない() -> None:
    dictionary = load_dictionary()
    canonical, tags = dictionary.normalize("味噌")
    family = [_member("子", allergens={"大豆"})]
    recipe = _recipe([Ingredient(name="味噌", canonical=canonical, allergen_tags=tags)])
    assert has_blocking_violation(validate_recipe(recipe, family, dictionary))
