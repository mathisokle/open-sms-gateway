"""Admin panel: database backup download."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import admin_login, insert_message


def test_backup_downloads_valid_sqlite_snapshot(api: TestClient, tmp_path: Path) -> None:
    insert_message(api, body="backup-proof")
    admin_login(api)

    response = api.get("/admin/backup")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "gateway-backup-" in response.headers["content-disposition"]
    assert response.content.startswith(b"SQLite format 3\x00")

    snapshot = tmp_path / "restored.db"
    snapshot.write_bytes(response.content)
    with sqlite3.connect(snapshot) as connection:
        bodies = [row[0] for row in connection.execute("SELECT body FROM messages")]
    assert bodies == ["backup-proof"]


def test_settings_page_links_to_backup(api: TestClient) -> None:
    admin_login(api)

    response = api.get("/admin/settings")

    assert 'href="/admin/backup"' in response.text
