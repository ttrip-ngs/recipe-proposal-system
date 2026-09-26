"""Firestore クライアント初期化.

エミュレータ接続は FIRESTORE_EMULATOR_HOST 環境変数で Firestore SDK が自動検知する.
"""

from __future__ import annotations

from functools import lru_cache

from google.cloud import firestore

from recipe_system.config import get_settings


@lru_cache(maxsize=1)
def get_firestore_client() -> firestore.Client:
    settings = get_settings()
    return firestore.Client(project=settings.google_cloud_project)
