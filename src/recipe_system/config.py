"""環境変数・設定を Pydantic Settings で管理する."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from recipe_system.observability.logging import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    # 既定は .env。検証環境で実 LLM を使う際などは ENV_FILE=.env.staging で切り替える。
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_cloud_project: str = Field(..., alias="GOOGLE_CLOUD_PROJECT")
    vertex_ai_location: str = Field("asia-northeast1", alias="VERTEX_AI_LOCATION")
    anthropic_vertex_project_id: str = Field(..., alias="ANTHROPIC_VERTEX_PROJECT_ID")

    firestore_emulator_host: str | None = Field(None, alias="FIRESTORE_EMULATOR_HOST")
    firebase_auth_emulator_host: str | None = Field(None, alias="FIREBASE_AUTH_EMULATOR_HOST")
    firebase_auth_emulator_host_browser: str | None = Field(
        None, alias="FIREBASE_AUTH_EMULATOR_HOST_BROWSER"
    )

    firebase_web_config_json: str | None = Field(None, alias="FIREBASE_WEB_CONFIG_JSON")

    # 開発用ワンクリックログイン (エミュレータ時のみ有効) で使うメールアドレス.
    # data/seeds/family.example.yaml の allowed_emails に含まれている必要がある.
    dev_login_email: str = Field("parent@example.com", alias="DEV_LOGIN_EMAIL")
    dev_login_password: str = Field("password", alias="DEV_LOGIN_PASSWORD")

    use_prompt_cache: bool = Field(True, alias="USE_PROMPT_CACHE")
    # 後方互換のためのフラグ. 新規に切り替える場合は LLM_PROVIDER を使うこと.
    use_fake_llm: bool = Field(True, alias="USE_FAKE_LLM")
    # LLM プロバイダの明示選択. 未指定 (None) の場合は use_fake_llm から導出する
    # (effective_llm_provider 参照)。既存の .env / .env.staging はこのフィールドを
    # 持たないため、指定しなくても従来どおり動作する。
    llm_provider: Literal["fake", "claude", "gemini", "anthropic"] | None = Field(
        None, alias="LLM_PROVIDER"
    )
    # モデル名の上書き (例: gemini-2.5-pro). 未指定時は各クライアントの既定値を使う.
    llm_model: str | None = Field(None, alias="LLM_MODEL")
    # 週間提案の詳細フェーズ (食材リストと手順の生成) に使うモデルの上書き.
    # 未指定時は骨子と同じモデル (llm_model / 各クライアントの既定値) を使う.
    llm_detail_model: str | None = Field(None, alias="LLM_DETAIL_MODEL")
    # LLM_PROVIDER=anthropic のときのみ使う. Vertex 経由 (claude) では不要.
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    unknown_ingredient_policy: Literal["warn", "block"] = Field(
        "warn", alias="UNKNOWN_INGREDIENT_POLICY"
    )

    # LLM 利用コストの円換算レート (1 USD = ? JPY).
    # `BudgetGuardedClient` がトークン課金 (USD/1M) を JPY に換算する際に使う.
    # 為替変動・運用判断で変更したい値なので env から上書き可能にする.
    usd_jpy_rate: float = Field(155.0, alias="USD_JPY_RATE")

    log_level: str = Field("INFO", alias="LOG_LEVEL")
    session_secret: str = Field(..., alias="SESSION_SECRET")

    port: int = Field(8080, alias="PORT")

    @property
    def is_emulator(self) -> bool:
        return self.firestore_emulator_host is not None

    @property
    def effective_llm_provider(self) -> Literal["fake", "claude", "gemini", "anthropic"]:
        """実際に使う LLM プロバイダ. llm_provider 明示があればそれを優先する."""
        if self.llm_provider is not None:
            return self.llm_provider
        return "fake" if self.use_fake_llm else "claude"

    @property
    def firebase_web_config(self) -> dict[str, Any] | None:
        # 明示設定があればそれを優先 (本番).
        if self.firebase_web_config_json:
            try:
                parsed = json.loads(self.firebase_web_config_json)
            except json.JSONDecodeError as e:
                logger.warning("config.firebase_web_config_json_invalid", error=str(e))
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        # エミュレータ時は Firebase Web SDK 初期化用のダミー config を返す.
        # Auth Emulator 接続なのでキーは何でも通る. これで /login の「Google でサインイン」
        # ボタンから connectAuthEmulator + signInWithPopup の Fake-Google フローを使える.
        if self.is_emulator:
            return {
                "apiKey": "fake-api-key",
                "authDomain": f"{self.google_cloud_project}.firebaseapp.com",
                "projectId": self.google_cloud_project,
                "appId": "fake-app-id",
            }
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
