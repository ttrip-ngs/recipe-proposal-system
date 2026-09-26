"""統合テスト共通設定.

docker-compose で Firestore エミュレータが起動していることを前提とする.
起動していない場合は skip する.
"""

from __future__ import annotations

import os
import socket

import pytest

FIRESTORE_EMULATOR_DEFAULT = "127.0.0.1:8080"


def _is_emulator_up(host_port: str) -> bool:
    host, port = host_port.split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_emulator() -> None:
    host_port = os.environ.get("FIRESTORE_EMULATOR_HOST", FIRESTORE_EMULATOR_DEFAULT)
    if not _is_emulator_up(host_port):
        pytest.skip(
            f"Firestore エミュレータに接続できない ({host_port}). "
            "docker compose up -d で起動してから再実行してください.",
            allow_module_level=True,
        )
    os.environ["FIRESTORE_EMULATOR_HOST"] = host_port
    os.environ["GOOGLE_CLOUD_PROJECT"] = "recipe-system-dev"
    # 単体テストが先に get_settings() / get_firestore_client() を呼んで test-project で
    # キャッシュしている可能性があるためクリアする (lru_cache).
    from recipe_system.config import get_settings
    from recipe_system.repository.firestore_client import get_firestore_client

    get_settings.cache_clear()
    get_firestore_client.cache_clear()
