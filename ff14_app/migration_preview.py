"""在独立副本上预演迁移，不启动网关、轮询或投递。"""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from ff14_core.storage import StateStore


def preview_migration(source: Path, destination: Path) -> dict[str, str | int | None]:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    # Exclusive creation also prevents overwriting the source or a previous preview.
    with destination.open("xb"):
        pass
    destination.chmod(0o600)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copy:
            original.backup(copy)
    store = StateStore(destination)
    previous = store.get_meta("schema_version")
    store.initialize()
    store.validate_integrity()
    with closing(sqlite3.connect(destination)) as connection:
        counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM notification_candidate GROUP BY status"
        ))
    return {"previous_schema": previous, "preview_schema": store.get_meta("schema_version"), **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True,
                        help="必须是尚不存在的文件；父目录必须已存在")
    args = parser.parse_args()
    for key, value in preview_migration(args.source, args.destination).items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
