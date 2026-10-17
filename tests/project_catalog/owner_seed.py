from __future__ import annotations

import sqlite3
from pathlib import Path


def seed_project_owner(database: Path, project_id: str, requester: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO project_owners(project_id, requester) VALUES(?, ?)",
            (project_id, requester),
        )
