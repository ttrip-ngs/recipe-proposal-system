"""レシピ検証ロジックのテスト. アレルゲン検出は命に関わるため高カバレッジ必須."""

from __future__ import annotations

from datetime import UTC, datetime

from recipe_system.domain import FamilyMember, Ingredient, ItemPolicy, Recipe, Violation
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


def _normalized(name: str) -> Ingredient:
    canonical, tags = load_dictionary().normalize(name)
    return Ingredient(name=name, canonical=canonical, allergen_tags=tags)


def test_辞書に無い複合語の食材名にアレルゲンの語があればblockする() -> None:
    # 「溶き卵」は辞書に無く allergen_tags が空になるが, 名前に「卵」を含む
    family = [_member("妻", allergens={"卵"})]
    recipe = _recipe([_normalized("豚こま"), _normalized("溶き卵")])
    violations = validate_recipe(recipe, family, load_dictionary())
    assert has_blocking_violation(violations)
    assert [v.reason for v in violations] == ["食材名にアレルゲン: 卵 (溶き卵)"]


def test_辞書に無い食材名がアレルゲンと無関係ならblockしない() -> None:
    family = [_member("妻", allergens={"卵"})]
    recipe = _recipe([_normalized("豚こま"), _normalized("ごぼう")])
    assert validate_recipe(recipe, family, load_dictionary()) == ()


def test_辞書にある食材は食材名の部分一致で二重に報告しない() -> None:
    family = [_member("妻", allergens={"甲殻類"})]
    recipe = _recipe([_normalized("むきえび")])
    violations = validate_recipe(recipe, family, load_dictionary())
    assert len(violations) == 1
    assert violations[0].reason == "アレルゲン: 甲殻類"


def test_全角英字の手順もアレルゲンの語で検出する() -> None:
    family = [_member("妻", allergens={"卵"})]
    recipe = Recipe(
        name="テスト料理",
        category="主菜",
        main_ingredient="豚肉",
        ingredients=(_normalized("豚こま"),),
        steps=("\uff25\uff27\uff27を割り入れる",),
    )
    assert has_blocking_violation(validate_recipe(recipe, family, load_dictionary()))


def test_大豆アレルギーの家族に手順だけに現れた味噌もblockする() -> None:
    family = [_member("子", allergens={"大豆"})]
    recipe = _recipe_with_steps(["火を止めてみそを溶く"])
    assert has_blocking_violation(validate_recipe(recipe, family, load_dictionary()))


def test_括弧の外が辞書にある食材でも括弧内のアレルゲンを検出する() -> None:
    # 「牛乳(または豆乳)」は括弧を除いて 乳 に当たりタグが付くが, 括弧内の豆乳も検査する
    family = [_member("子", allergens={"大豆"})]
    recipe = _recipe([_normalized("牛乳(または豆乳)")])
    violations = validate_recipe(recipe, family, load_dictionary())
    assert has_blocking_violation(violations)
    assert [v.canonical for v in violations] == ["大豆"]


def test_同じ文に同じ食材の語が複数当たっても違反は1件にまとめる() -> None:
    # 「鶏もも肉」は 鶏 と 鶏もも の両方に部分一致する
    family = [_member("妻", allergens={"鶏肉"})]
    recipe = _recipe([_normalized("鶏もも肉")])
    violations = validate_recipe(recipe, family, load_dictionary())
    assert len(violations) == 1


def _member_with_policy(
    allergens: set[str], policies: dict[str, dict[str, ItemPolicy]]
) -> FamilyMember:
    return _member("子", allergens=allergens).model_copy(update={"item_policies": policies})


def _check(member: FamilyMember, names: list[str], steps: list[str] | None = None) -> list[str]:
    recipe = Recipe(
        name="テスト料理",
        category="主菜",
        main_ingredient="豚肉",
        ingredients=tuple(_normalized(n) for n in names),
        steps=tuple(steps or []),
    )
    return [v.reason for v in validate_recipe(recipe, [member], load_dictionary())]


def test_通常は除去不要な食品も未選択なら除去する() -> None:
    member = _member_with_policy({"大豆"}, {})
    assert _check(member, ["醤油"]) == ["アレルゲン: 大豆"]


def test_摂取可を選んだ食品は通し本体の食材は除去する() -> None:
    member = _member_with_policy({"大豆"}, {"大豆": {"醤油": "allow", "味噌": "block"}})
    assert _check(member, ["しょうゆ"]) == []
    assert _check(member, ["味噌"]) == ["アレルゲン: 大豆"]
    assert _check(member, ["豆腐"]) == ["アレルゲン: 大豆"]


def test_複数グループに属する食品は全てのアレルギー指定で可のときだけ通す() -> None:
    # 大豆では醤油を可にしたが, 小麦では未選択 -> 小麦として除去
    member = _member_with_policy({"大豆", "小麦"}, {"大豆": {"醤油": "allow"}})
    assert _check(member, ["醤油"]) == ["アレルゲン: 小麦"]
    both = _member_with_policy(
        {"大豆", "小麦"}, {"大豆": {"醤油": "allow"}, "小麦": {"醤油": "allow"}}
    )
    assert _check(both, ["醤油"]) == []


def test_除去不要候補でない食品は可を設定しても除去する() -> None:
    member = _member_with_policy({"大豆"}, {"大豆": {"豆腐": "allow"}})
    assert _check(member, ["豆腐"]) == ["アレルゲン: 大豆"]


def test_摂取可の食品名に含まれるアレルゲンの語では手順を誤検出しない() -> None:
    member = _member_with_policy({"ごま"}, {"ごま": {"ごま油": "allow"}})
    assert _check(member, ["豚こま"], ["ごま油で炒める"]) == []
    # 伏せた残りに ごま があれば除去する
    assert _check(member, ["豚こま"], ["ごま油で炒め、ごまをふる"]) == [
        "手順にアレルゲン: ごま (ごま油で炒め、ごまをふる)"
    ]


def test_摂取不可なら除去不要候補の表記も手順で検出する() -> None:
    member = _member_with_policy({"小麦"}, {"小麦": {"醤油": "block"}})
    assert _check(member, ["豚こま"], ["しょうゆを回しかける"]) == [
        "手順にアレルゲン: 醤油 (しょうゆを回しかける)"
    ]
