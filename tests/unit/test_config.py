"""Settings / firebase_web_config のテスト."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """各テスト前後で lru_cache を空にする."""
    from recipe_system import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_firebase_web_config_json_未設定なら_None() -> None:
    """非エミュレータかつ FIREBASE_WEB_CONFIG_JSON 未設定なら None."""
    from recipe_system.config import Settings

    s = Settings(  # type: ignore[call-arg]
        GOOGLE_CLOUD_PROJECT="test-project",
        ANTHROPIC_VERTEX_PROJECT_ID="test-project",
        SESSION_SECRET="test-secret",
        FIRESTORE_EMULATOR_HOST=None,
        FIREBASE_AUTH_EMULATOR_HOST=None,
        FIREBASE_WEB_CONFIG_JSON=None,
        _env_file=None,
    )
    assert s.firebase_web_config is None


def test_firebase_web_config_json_が有効なら_dict_に_parse_される(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FIREBASE_WEB_CONFIG_JSON",
        '{"apiKey":"key","authDomain":"example.firebaseapp.com","projectId":"p"}',
    )
    from recipe_system import config

    importlib.reload(config)
    s = config.get_settings()
    assert s.firebase_web_config is not None
    assert s.firebase_web_config["apiKey"] == "key"
    assert s.firebase_web_config["projectId"] == "p"


def test_firebase_web_config_json_が壊れた_JSON_なら_None() -> None:
    """非エミュレータかつ JSON 不正なら None."""
    from recipe_system.config import Settings

    s = Settings(  # type: ignore[call-arg]
        GOOGLE_CLOUD_PROJECT="test-project",
        ANTHROPIC_VERTEX_PROJECT_ID="test-project",
        SESSION_SECRET="test-secret",
        FIRESTORE_EMULATOR_HOST=None,
        FIREBASE_AUTH_EMULATOR_HOST=None,
        FIREBASE_WEB_CONFIG_JSON="not-json",
        _env_file=None,
    )
    assert s.firebase_web_config is None


def test_is_emulator_は_FIRESTORE_EMULATOR_HOST_が_ある時_True(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    from recipe_system import config

    importlib.reload(config)
    s = config.get_settings()
    assert s.is_emulator is True


def _make_settings(**overrides: object) -> object:
    from recipe_system.config import Settings

    base: dict[str, object] = {
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "ANTHROPIC_VERTEX_PROJECT_ID": "test-project",
        "SESSION_SECRET": "test-secret",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg, arg-type]


def test_effective_llm_provider_は_LLM_PROVIDER_未指定かつ_USE_FAKE_LLM_既定なら_fake() -> None:
    s = _make_settings()
    assert s.effective_llm_provider == "fake"


def test_effective_llm_provider_は_LLM_PROVIDER_未指定かつ_USE_FAKE_LLM_false_なら_claude() -> None:
    s = _make_settings(USE_FAKE_LLM=False)
    assert s.effective_llm_provider == "claude"


def test_effective_llm_provider_は_LLM_PROVIDER_明示指定を最優先する() -> None:
    s = _make_settings(USE_FAKE_LLM=False, LLM_PROVIDER="gemini")
    assert s.effective_llm_provider == "gemini"


def test_effective_llm_provider_は_LLM_PROVIDER_fake_指定を優先する() -> None:
    """USE_FAKE_LLM=False でも LLM_PROVIDER=fake の明示指定が勝つ."""
    s = _make_settings(USE_FAKE_LLM=False, LLM_PROVIDER="fake")
    assert s.effective_llm_provider == "fake"


def test_llm_provider_に不正な値を渡すと_ValidationError() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_settings(LLM_PROVIDER="openai")


def test_llm_model_は_未指定なら_None() -> None:
    s = _make_settings()
    assert s.llm_model is None


def test_llm_model_は_指定値をそのまま保持する() -> None:
    s = _make_settings(LLM_MODEL="gemini-2.5-pro")
    assert s.llm_model == "gemini-2.5-pro"
