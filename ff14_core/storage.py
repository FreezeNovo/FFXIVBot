"""最小 SQLite 状态仓储，不依赖 Bot 框架。"""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .messaging import TargetRef
from .source_models import (
    CalendarCategoryOption,
    CalendarEntry,
    ChinaNewsDetail,
    ClassifiedEventCandidate,
    EventCandidate,
    ActivityDigestPolicy,
    MaintenancePolicy,
    ReviewPage,
    SourceDocument,
    SourceHealth,
)

SCHEMA_VERSION = "15"
MIN_UPCOMING_WINDOW_MINUTES = 7 * 24 * 60


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("存储时间必须包含时区")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ManagedTarget:
    """由 Bot 所有者在本机管理的消息目标。"""

    target: TargetRef
    enabled: bool


class StateStore:
    """保存应用状态和稳定投递目标。"""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        now = utc_now_text()
        if self._database_path.exists() and self._database_path.stat().st_size:
            previous_version = self._read_schema_version()
            if previous_version != SCHEMA_VERSION:
                backup_path = self._database_path.with_name(
                    self._database_path.name + ".pre-migration.bak"
                )
                self.backup_to(backup_path)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS target (
                    target_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    provider_target_id TEXT NOT NULL,
                    display_name TEXT,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, target_type, provider_target_id)
                );

                CREATE TABLE IF NOT EXISTS source_state (
                    source_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    last_http_status INTEGER,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    document_count INTEGER NOT NULL DEFAULT 0,
                    last_document_at TEXT,
                    etag TEXT,
                    last_modified TEXT
                );

                CREATE TABLE IF NOT EXISTS schema_migration (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_snapshot (
                    source_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (source_id, content_hash)
                );

                CREATE TABLE IF NOT EXISTS source_document (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    updated_at TEXT,
                    category TEXT,
                    summary TEXT,
                    image_url TEXT,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id)
                );

                CREATE TABLE IF NOT EXISTS source_document_detail (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id)
                );

                CREATE TABLE IF NOT EXISTS source_document_detail_version (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id, content_hash)
                );

                CREATE TABLE IF NOT EXISTS event_candidate (
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    evidence_text TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'cn',
                    classification TEXT NOT NULL DEFAULT 'unclassified',
                    classification_label TEXT NOT NULL DEFAULT '未分类',
                    routing TEXT NOT NULL DEFAULT 'review',
                    routing_label TEXT NOT NULL DEFAULT '待审核',
                    classification_reason TEXT NOT NULL DEFAULT '',
                    parser_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, document_id, candidate_id)
                );

                CREATE TABLE IF NOT EXISTS calendar_category_policy (
                    classification_label TEXT PRIMARY KEY,
                    included INTEGER NOT NULL CHECK (included IN (0, 1)),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activity_digest_policy (
                    target_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    region TEXT NOT NULL,
                    classification_codes_json TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    digest_hour INTEGER NOT NULL CHECK (digest_hour BETWEEN 0 AND 23),
                    digest_minute INTEGER NOT NULL CHECK (digest_minute BETWEEN 0 AND 59),
                    upcoming_window_minutes INTEGER NOT NULL CHECK (upcoming_window_minutes >= 0),
                    ending_window_minutes INTEGER NOT NULL CHECK (ending_window_minutes >= 0),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS maintenance_policy (
                    target_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    region TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    notify_announcements INTEGER NOT NULL DEFAULT 1 CHECK (notify_announcements IN (0, 1)),
                    reminder_window_minutes INTEGER NOT NULL CHECK (reminder_window_minutes >= 0),
                    notify_updates INTEGER NOT NULL CHECK (notify_updates IN (0, 1)),
                    watermark_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS maintenance_observation (
                    target_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, document_id),
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS maintenance_reminder_suppression (
                    target_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (target_id, document_id, candidate_id),
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS notification_candidate (
                    notification_id TEXT PRIMARY KEY,
                    business_key TEXT NOT NULL UNIQUE,
                    target_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'superseded')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS delivery_attempt (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                    provider_message_id TEXT,
                    error TEXT,
                    FOREIGN KEY (notification_id) REFERENCES notification_candidate(notification_id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_notification_status_schema(connection)
            self._ensure_column(
                connection,
                "event_candidate",
                "region",
                "TEXT NOT NULL DEFAULT 'cn'",
            )
            self._ensure_column(
                connection,
                "event_candidate",
                "classification",
                "TEXT NOT NULL DEFAULT 'unclassified'",
            )
            self._ensure_column(
                connection,
                "event_candidate",
                "classification_label",
                "TEXT NOT NULL DEFAULT '未分类'",
            )
            self._ensure_column(
                connection,
                "source_document_detail",
                "parse_status",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._ensure_column(
                connection,
                "source_document_detail",
                "parser_error",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "source_document_detail",
                "candidate_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "source_document_detail",
                "parser_version",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "maintenance_policy",
                "notify_announcements",
                "INTEGER NOT NULL DEFAULT 1",
            )
            # The old UI offered hourly upcoming windows. Existing non-zero
            # values are moved to the new minimum selectable range; zero still
            # means that the upcoming section is disabled.
            connection.execute(
                "UPDATE activity_digest_policy SET upcoming_window_minutes = ? "
                "WHERE upcoming_window_minutes > 0 AND upcoming_window_minutes < ?",
                (MIN_UPCOMING_WINDOW_MINUTES, MIN_UPCOMING_WINDOW_MINUTES),
            )
            self._ensure_column(
                connection,
                "source_state",
                "last_document_at",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "source_state",
                "etag",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "source_state",
                "last_modified",
                "TEXT",
            )
            self._ensure_column(
                connection,
                "event_candidate",
                "routing",
                "TEXT NOT NULL DEFAULT 'review'",
            )
            self._ensure_column(
                connection,
                "event_candidate",
                "routing_label",
                "TEXT NOT NULL DEFAULT '待审核'",
            )
            self._ensure_column(
                connection,
                "event_candidate",
                "classification_reason",
                "TEXT NOT NULL DEFAULT ''",
            )
            connection.execute(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("schema_version", SCHEMA_VERSION),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migration(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_meta(key, value) VALUES (?, ?)",
                ("first_started_at", now),
            )
            connection.execute(
                "INSERT INTO app_meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("last_started_at", now),
            )

    def _read_schema_version(self) -> str | None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._database_path)
            row = connection.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
        finally:
            if connection is not None:
                connection.close()
        return None if row is None else str(row[0])

    def backup_to(self, destination: Path) -> Path:
        """使用 SQLite backup API 创建一致性备份，并校验备份完整性。"""

        destination = destination.resolve()
        source = self._database_path.resolve()
        if destination == source:
            raise ValueError("备份目标不能与数据库相同")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        source_connection = sqlite3.connect(source)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            destination_connection.commit()
        finally:
            destination_connection.close()
            source_connection.close()
        StateStore(destination).validate_integrity()
        return destination

    def validate_integrity(self) -> None:
        """验证 SQLite 完整性和外键，不修改业务状态。"""

        connection = sqlite3.connect(self._database_path)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise sqlite3.DatabaseError(f"SQLite integrity_check failed: {result}")
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise sqlite3.DatabaseError(
                    f"SQLite foreign_key_check failed: {foreign_key_errors[:3]}"
                )
        finally:
            connection.close()
    def list_targets(self) -> tuple[ManagedTarget, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT target_id, provider, target_type, provider_target_id, display_name, enabled "
                "FROM target ORDER BY provider, target_type, provider_target_id"
            ).fetchall()
        return tuple(
            ManagedTarget(
                target=TargetRef(
                    target_id=str(row[0]),
                    provider=str(row[1]),
                    target_type=str(row[2]),
                    provider_target_id=str(row[3]),
                    display_name=str(row[4]) if row[4] is not None else None,
                ),
                enabled=bool(row[5]),
            )
            for row in rows
        )

    def save_onebot_group(self, group_id: str, *, enabled: bool) -> TargetRef:
        target = TargetRef.for_onebot_group(group_id)
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO target(target_id, provider, target_type, provider_target_id, "
                "display_name, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET enabled = excluded.enabled, "
                "updated_at = excluded.updated_at",
                (
                    target.target_id,
                    target.provider,
                    target.target_type,
                    target.provider_target_id,
                    target.display_name,
                    int(enabled),
                    now,
                    now,
                ),
            )
        return target

    def delete_onebot_group(self, group_id: str) -> bool:
        target = TargetRef.for_onebot_group(group_id)
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM target WHERE target_id = ?", (target.target_id,)
            )
        return cursor.rowcount == 1

    def get_owner_user_id(self) -> str | None:
        return self.get_meta("owner_user_id")

    def set_owner_user_id(self, owner_user_id: str | None) -> None:
        normalized = owner_user_id.strip() if owner_user_id is not None else ""
        with self._connection() as connection:
            if normalized:
                connection.execute(
                    "INSERT INTO app_meta(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("owner_user_id", normalized),
                )
            else:
                connection.execute("DELETE FROM app_meta WHERE key = ?", ("owner_user_id",))

    def is_target_enabled(self, target_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled FROM target WHERE target_id = ?", (target_id,)
            ).fetchone()
        return row is not None and row[0] == 1

    def get_activity_digest_policy(self, target_id: str) -> ActivityDigestPolicy:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled, region, classification_codes_json, timezone, digest_hour, "
                "digest_minute, upcoming_window_minutes, ending_window_minutes "
                "FROM activity_digest_policy WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        if row is None:
            return ActivityDigestPolicy()
        codes = json.loads(str(row[2]))
        return ActivityDigestPolicy(
            enabled=bool(row[0]),
            region=str(row[1]),
            classification_codes=tuple(str(code) for code in codes),
            timezone=str(row[3]),
            digest_hour=int(row[4]),
            digest_minute=int(row[5]),
            upcoming_window_minutes=int(row[6]),
            ending_window_minutes=int(row[7]),
        )

    def set_activity_digest_policy(
        self, target_id: str, policy: ActivityDigestPolicy
    ) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM target WHERE target_id = ?", (target_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("未知投递目标")
            connection.execute(
                "INSERT INTO activity_digest_policy("
                "target_id, enabled, region, classification_codes_json, timezone, digest_hour, "
                "digest_minute, upcoming_window_minutes, ending_window_minutes, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET enabled = excluded.enabled, "
                "region = excluded.region, classification_codes_json = excluded.classification_codes_json, "
                "timezone = excluded.timezone, digest_hour = excluded.digest_hour, "
                "digest_minute = excluded.digest_minute, "
                "upcoming_window_minutes = excluded.upcoming_window_minutes, "
                "ending_window_minutes = excluded.ending_window_minutes, updated_at = excluded.updated_at",
                (
                    target_id,
                    int(policy.enabled),
                    policy.region,
                    json.dumps(policy.classification_codes, ensure_ascii=True),
                    policy.timezone,
                    policy.digest_hour,
                    policy.digest_minute,
                    policy.upcoming_window_minutes,
                    policy.ending_window_minutes,
                    now,
                ),
            )

    def list_enabled_activity_digest_targets(
        self,
    ) -> tuple[tuple[ManagedTarget, ActivityDigestPolicy], ...]:
        managed_targets = self.list_targets()
        return tuple(
            (managed_target, policy)
            for managed_target in managed_targets
            if managed_target.enabled
            if (policy := self.get_activity_digest_policy(managed_target.target.target_id)).enabled
        )

    def get_maintenance_policy(self, target_id: str) -> MaintenancePolicy:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled, region, timezone, notify_announcements, reminder_window_minutes, "
                "notify_updates, watermark_at FROM maintenance_policy WHERE target_id = ?",
                (target_id,),
            ).fetchone()
        if row is None:
            return MaintenancePolicy()
        return MaintenancePolicy(
            enabled=bool(row[0]),
            region=str(row[1]),
            timezone=str(row[2]),
            notify_announcements=bool(row[3]),
            reminder_window_minutes=int(row[4]),
            notify_updates=bool(row[5]),
            watermark_at=str(row[6]) if row[6] is not None else None,
        )

    def set_maintenance_policy(self, target_id: str, policy: MaintenancePolicy) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM target WHERE target_id = ?", (target_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("未知投递目标")
            existing = connection.execute(
                "SELECT enabled, watermark_at FROM maintenance_policy WHERE target_id = ?",
                (target_id,),
            ).fetchone()
            if policy.enabled and (existing is None or not bool(existing[0])):
                # Enabling establishes a new subscription waterline and does
                # not replay maintenance notices discovered while disabled.
                watermark_at = now
            elif existing is not None and existing[1] is not None:
                watermark_at = str(existing[1])
            else:
                watermark_at = policy.watermark_at
            connection.execute(
                "INSERT INTO maintenance_policy("
                "target_id, enabled, region, timezone, notify_announcements, reminder_window_minutes, "
                "notify_updates, watermark_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET enabled = excluded.enabled, "
                "region = excluded.region, timezone = excluded.timezone, "
                "notify_announcements = excluded.notify_announcements, "
                "reminder_window_minutes = excluded.reminder_window_minutes, "
                "notify_updates = excluded.notify_updates, "
                "watermark_at = excluded.watermark_at, updated_at = excluded.updated_at",
                (
                    target_id,
                    int(policy.enabled),
                    policy.region,
                    policy.timezone,
                    int(policy.notify_announcements),
                    policy.reminder_window_minutes,
                    int(policy.notify_updates),
                    watermark_at,
                    now,
                ),
            )

    def list_enabled_maintenance_targets(
        self,
    ) -> tuple[tuple[ManagedTarget, MaintenancePolicy], ...]:
        managed_targets = self.list_targets()
        return tuple(
            (managed_target, policy)
            for managed_target in managed_targets
            if managed_target.enabled
            if (policy := self.get_maintenance_policy(managed_target.target.target_id)).enabled
        )

    def observe_maintenance_candidate(
        self,
        *,
        target_id: str,
        candidate: EventCandidate,
        watermark_at: str | None,
    ) -> str:
        """持久记录每群已观察的维护版本，返回 new/changed/known/baseline。"""

        now = utc_now_text()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT candidate_id, start_at, end_at FROM maintenance_observation "
                "WHERE target_id = ? AND document_id = ?",
                (target_id, candidate.document_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO maintenance_observation("
                    "target_id, document_id, candidate_id, start_at, end_at, observed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        target_id,
                        candidate.document_id,
                        candidate.candidate_id,
                        candidate.start_at,
                        candidate.end_at,
                        now,
                    ),
                )
                if watermark_at is not None and candidate.created_at is not None:
                    if _parse_timestamp(candidate.created_at) >= _parse_timestamp(watermark_at):
                        return "new"
                return "baseline"
            changed = str(row[1]) != candidate.start_at or str(row[2]) != candidate.end_at
            if changed or str(row[0]) != candidate.candidate_id:
                connection.execute(
                    "UPDATE maintenance_observation SET candidate_id = ?, start_at = ?, "
                    "end_at = ?, observed_at = ? WHERE target_id = ? AND document_id = ?",
                    (
                        candidate.candidate_id,
                        candidate.start_at,
                        candidate.end_at,
                        now,
                        target_id,
                        candidate.document_id,
                    ),
                )
            return "changed" if changed else "known"

    def suppress_maintenance_reminder(
        self,
        *,
        target_id: str,
        document_id: str,
        candidate_id: str,
        reason: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO maintenance_reminder_suppression("
                "target_id, document_id, candidate_id, reason, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (target_id, document_id, candidate_id, reason, utc_now_text()),
            )

    def is_maintenance_reminder_suppressed(
        self, *, target_id: str, document_id: str, candidate_id: str
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM maintenance_reminder_suppression "
                "WHERE target_id = ? AND document_id = ? AND candidate_id = ?",
                (target_id, document_id, candidate_id),
            ).fetchone()
        return row is not None

    def create_notification_candidate(
        self,
        *,
        business_key: str,
        target_id: str,
        stream: str,
        window_key: str,
        message_text: str,
    ) -> str | None:
        notification_id = str(uuid4())
        now = utc_now_text()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT notification_id, status FROM notification_candidate WHERE business_key = ?",
                (business_key,),
            ).fetchone()
            if existing is not None:
                if str(existing[1]) == "pending":
                    connection.execute(
                        "UPDATE notification_candidate SET message_text = ?, updated_at = ?, "
                        "last_error = NULL WHERE notification_id = ?",
                        (message_text, now, str(existing[0])),
                    )
                return None
            cursor = connection.execute(
                "INSERT INTO notification_candidate("
                "notification_id, business_key, target_id, stream, window_key, message_text, status, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    notification_id,
                    business_key,
                    target_id,
                    stream,
                    window_key,
                    message_text,
                    now,
                    now,
                ),
            )
        return notification_id if cursor.rowcount == 1 else None

    def list_pending_notifications(
        self, limit: int = 100, *, stream: str | None = None
    ) -> tuple[tuple[str, TargetRef, str], ...]:
        stream_filter = ""
        parameters: list[object] = []
        if stream is not None:
            stream_filter = " AND candidate.stream = ?"
            parameters.append(stream)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT candidate.notification_id, target.target_id, target.provider, target.target_type, "
                "target.provider_target_id, target.display_name, candidate.message_text "
                "FROM notification_candidate AS candidate "
                "JOIN target ON target.target_id = candidate.target_id "
                "WHERE candidate.status = 'pending' AND target.enabled = 1 "
                + stream_filter
                + " ORDER BY candidate.created_at ASC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(
            (
                str(row[0]),
                TargetRef(
                    target_id=str(row[1]),
                    provider=str(row[2]),
                    target_type=str(row[3]),
                    provider_target_id=str(row[4]),
                    display_name=str(row[5]) if row[5] is not None else None,
                ),
                str(row[6]),
            )
            for row in rows
        )

    def supersede_pending_notifications(
        self,
        *,
        target_id: str,
        stream: str,
        event_prefix: str,
        current_version_prefix: str,
    ) -> int:
        """保留当前维护版本的待投递通知，废弃同一事件的旧版本。"""

        now = utc_now_text()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT notification_id, business_key FROM notification_candidate "
                "WHERE target_id = ? AND stream = ? AND status = 'pending'",
                (target_id, stream),
            ).fetchall()
            stale_ids = [
                str(row[0])
                for row in rows
                if str(row[1]).startswith(event_prefix)
                and not str(row[1]).startswith(current_version_prefix)
            ]
            for notification_id in stale_ids:
                connection.execute(
                    "UPDATE notification_candidate SET status = 'superseded', updated_at = ?, "
                    "last_error = ? WHERE notification_id = ?",
                    (now, "被新的维护时间版本替代", notification_id),
                )
        return len(stale_ids)

    def record_delivery_attempt(
        self,
        *,
        notification_id: str,
        success: bool,
        retryable: bool,
        provider_message_id: str | None,
        error: str | None,
    ) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO delivery_attempt(notification_id, attempted_at, success, retryable, "
                "provider_message_id, error) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    notification_id,
                    now,
                    int(success),
                    int(retryable),
                    provider_message_id,
                    error,
                ),
            )
            if success:
                connection.execute(
                    "UPDATE notification_candidate SET status = 'sent', updated_at = ?, last_error = NULL "
                    "WHERE notification_id = ?",
                    (now, notification_id),
                )
            elif not retryable:
                connection.execute(
                    "UPDATE notification_candidate SET status = 'failed', updated_at = ?, last_error = ? "
                    "WHERE notification_id = ?",
                    (now, error, notification_id),
                )

    def get_meta(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_meta WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row[0])

    def record_source_success(
        self,
        *,
        source_id: str,
        display_name: str,
        http_status: int,
        raw_payload: bytes,
        documents: tuple[SourceDocument, ...],
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        now = utc_now_text()
        content_hash = sha256(raw_payload).hexdigest()
        last_document_at = _latest_document_timestamp(documents)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO source_snapshot"
                "(source_id, content_hash, fetched_at, http_status, payload) VALUES (?, ?, ?, ?, ?)",
                (source_id, content_hash, now, http_status, raw_payload),
            )
            for document in documents:
                connection.execute(
                    "INSERT INTO source_document("
                    "source_id, document_id, title, url, published_at, updated_at, category, summary, image_url, last_seen_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id, document_id) DO UPDATE SET "
                    "title = excluded.title, url = excluded.url, published_at = excluded.published_at, "
                    "updated_at = excluded.updated_at, category = excluded.category, summary = excluded.summary, "
                    "image_url = excluded.image_url, last_seen_at = excluded.last_seen_at",
                    (
                        document.source_id,
                        document.document_id,
                        document.title,
                        document.url,
                        document.published_at,
                        document.updated_at,
                        document.category,
                        document.summary,
                        document.image_url,
                        now,
                    ),
                )
            connection.execute(
                "INSERT INTO source_state("
                "source_id, display_name, last_attempt_at, last_success_at, last_http_status, "
                "consecutive_failures, last_error, document_count, last_document_at, etag, last_modified"
                ") VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "display_name = excluded.display_name, last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, last_http_status = excluded.last_http_status, "
                "consecutive_failures = 0, last_error = NULL, document_count = excluded.document_count, "
                "last_document_at = excluded.last_document_at, etag = excluded.etag, "
                "last_modified = excluded.last_modified",
                (
                    source_id,
                    display_name,
                    now,
                    now,
                    http_status,
                    len(documents),
                    last_document_at,
                    etag,
                    last_modified,
                ),
            )

    def record_source_not_modified(
        self,
        *,
        source_id: str,
        display_name: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """记录条件请求的 304；不改变来源文档、快照或文档数量。"""

        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO source_state("
                "source_id, display_name, last_attempt_at, last_success_at, last_http_status, "
                "consecutive_failures, last_error, document_count, etag, last_modified"
                ") VALUES (?, ?, ?, ?, 304, 0, NULL, 0, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "display_name = excluded.display_name, last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, last_http_status = 304, "
                "consecutive_failures = 0, last_error = NULL, "
                "etag = COALESCE(excluded.etag, source_state.etag), "
                "last_modified = COALESCE(excluded.last_modified, source_state.last_modified)",
                (source_id, display_name, now, now, etag, last_modified),
            )

    def get_source_validators(self, source_id: str) -> tuple[str | None, str | None]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT etag, last_modified FROM source_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return (None, None)
        return (
            str(row[0]) if row[0] is not None else None,
            str(row[1]) if row[1] is not None else None,
        )

    def record_source_failure(self, *, source_id: str, display_name: str, error: str) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO source_state("
                "source_id, display_name, last_attempt_at, consecutive_failures, last_error, document_count"
                ") VALUES (?, ?, ?, 1, ?, 0) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "display_name = excluded.display_name, last_attempt_at = excluded.last_attempt_at, "
                "consecutive_failures = source_state.consecutive_failures + 1, last_error = excluded.last_error",
                (source_id, display_name, now, error),
            )

    def get_source_health(self) -> tuple[SourceHealth, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT source_id, display_name, last_attempt_at, last_success_at, last_http_status, "
                "consecutive_failures, last_error, document_count, last_document_at, etag, last_modified "
                "FROM source_state ORDER BY source_id"
            ).fetchall()
        return tuple(
            SourceHealth(
                source_id=str(row[0]),
                display_name=str(row[1]),
                last_attempt_at=str(row[2]) if row[2] is not None else None,
                last_success_at=str(row[3]) if row[3] is not None else None,
                last_http_status=int(row[4]) if row[4] is not None else None,
                consecutive_failures=int(row[5]),
                last_error=str(row[6]) if row[6] is not None else None,
                document_count=int(row[7]),
                last_document_at=str(row[8]) if row[8] is not None else None,
                etag=str(row[9]) if row[9] is not None else None,
                last_modified=str(row[10]) if row[10] is not None else None,
            )
            for row in rows
        )

    def get_source_documents(self, source_id: str, limit: int) -> tuple[SourceDocument, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT source_id, document_id, title, url, published_at, updated_at, category, summary, image_url "
                "FROM source_document WHERE source_id = ? "
                "ORDER BY published_at DESC LIMIT ?",
                (source_id, limit),
            ).fetchall()
        return tuple(
            SourceDocument(
                source_id=str(row[0]),
                document_id=str(row[1]),
                title=str(row[2]),
                url=str(row[3]),
                published_at=str(row[4]) if row[4] is not None else None,
                updated_at=str(row[5]) if row[5] is not None else None,
                category=str(row[6]) if row[6] is not None else None,
                summary=str(row[7]) if row[7] is not None else None,
                image_url=str(row[8]) if row[8] is not None else None,
            )
            for row in rows
        )

    def get_documents_needing_detail_review(
        self,
        source_id: str,
        *,
        limit: int = 100,
        stale_after_hours: int = 24,
        parser_version: str | None = None,
    ) -> tuple[SourceDocument, ...]:
        if limit < 1 or stale_after_hours < 1:
            raise ValueError("limit and stale_after_hours must be positive")
        stale_before = (
            datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)
        ).isoformat(timespec="seconds")
        review_condition = (
            "detail.document_id IS NULL OR detail.fetched_at < ? "
            "OR detail.parse_status = 'degraded'"
        )
        parameters: list[object] = [source_id, stale_before]
        if parser_version is not None:
            review_condition += " OR COALESCE(detail.parser_version, '') != ?"
            parameters.append(parser_version)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT document.source_id, document.document_id, document.title, document.url, "
                "document.published_at, document.updated_at, document.category, document.summary, "
                "document.image_url FROM source_document AS document "
                "LEFT JOIN source_document_detail AS detail "
                "ON detail.source_id = document.source_id AND detail.document_id = document.document_id "
                "WHERE document.source_id = ? AND (" + review_condition + ") "
                "ORDER BY CASE WHEN detail.document_id IS NULL THEN 0 ELSE 1 END, "
                "document.published_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return tuple(
            SourceDocument(
                source_id=str(row[0]),
                document_id=str(row[1]),
                title=str(row[2]),
                url=str(row[3]),
                published_at=str(row[4]) if row[4] is not None else None,
                updated_at=str(row[5]) if row[5] is not None else None,
                category=str(row[6]) if row[6] is not None else None,
                summary=str(row[7]) if row[7] is not None else None,
                image_url=str(row[8]) if row[8] is not None else None,
            )
            for row in rows
        )

    def record_china_detail_success(
        self,
        detail: ChinaNewsDetail,
        candidates: tuple[ClassifiedEventCandidate, ...],
        parser_version: str,
    ) -> None:
        now = utc_now_text()
        content_hash = sha256(detail.content_html.encode("utf-8")).hexdigest()
        snapshot_source_id = f"china_news_detail/{detail.document_id}"
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO source_snapshot"
                "(source_id, content_hash, fetched_at, http_status, payload) VALUES (?, ?, ?, ?, ?)",
                (snapshot_source_id, content_hash, now, 200, detail.content_html.encode("utf-8")),
            )
            connection.execute(
                "INSERT OR IGNORE INTO source_document_detail_version("
                "source_id, document_id, content_hash, fetched_at, content_html, content_text"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    detail.source_id,
                    detail.document_id,
                    content_hash,
                    now,
                    detail.content_html,
                    detail.content_text,
                ),
            )
            connection.execute(
                "INSERT INTO source_document_detail("
                "source_id, document_id, content_hash, fetched_at, content_html, content_text, "
                "parse_status, parser_error, candidate_count, parser_version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?) "
                "ON CONFLICT(source_id, document_id) DO UPDATE SET "
                "content_hash = excluded.content_hash, fetched_at = excluded.fetched_at, "
                "content_html = excluded.content_html, content_text = excluded.content_text, "
                "parse_status = excluded.parse_status, parser_error = NULL, "
                "candidate_count = excluded.candidate_count, parser_version = excluded.parser_version",
                (
                    detail.source_id,
                    detail.document_id,
                    content_hash,
                    now,
                    detail.content_html,
                    detail.content_text,
                    "empty" if not candidates else "parsed",
                    len(candidates),
                    parser_version,
                ),
            )
            # A zero-candidate parse is retained as an explicit result, while
            # existing candidates remain the last-known-good snapshot until a
            # later successful observation replaces them.
            if candidates:
                connection.execute(
                    "DELETE FROM event_candidate WHERE source_id = ? AND document_id = ?",
                    (detail.source_id, detail.document_id),
                )
                for classified in candidates:
                    candidate = classified.candidate
                    connection.execute(
                        "INSERT INTO event_candidate("
                        "source_id, document_id, candidate_id, kind, label, evidence_text, start_at, end_at, "
                        "timezone, confidence, region, classification, classification_label, routing, routing_label, "
                        "classification_reason, parser_version, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            candidate.source_id,
                            candidate.document_id,
                            candidate.candidate_id,
                            candidate.kind,
                            candidate.label,
                            candidate.evidence_text,
                            candidate.start_at,
                            candidate.end_at,
                            candidate.timezone,
                            candidate.confidence,
                            candidate.region,
                            classified.classification,
                            classified.classification_label,
                            classified.routing,
                            classified.routing_label,
                            classified.reason,
                            parser_version,
                            now,
                        ),
                    )

    def get_detail_content_hash(self, source_id: str, document_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT content_hash FROM source_document_detail WHERE source_id = ? AND document_id = ?",
                (source_id, document_id),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def get_detail_parser_version(self, source_id: str, document_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT parser_version FROM source_document_detail "
                "WHERE source_id = ? AND document_id = ?",
                (source_id, document_id),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def record_detail_parse_failure(
        self, *, source_id: str, document_id: str, error: str
    ) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO source_document_detail("
                "source_id, document_id, content_hash, fetched_at, content_html, content_text, "
                "parse_status, parser_error, candidate_count"
                ") VALUES (?, ?, '', ?, '', '', 'degraded', ?, 0) "
                "ON CONFLICT(source_id, document_id) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, parse_status = 'degraded', "
                "parser_error = excluded.parser_error",
                (source_id, document_id, now, error),
            )

    def record_source_probe_success(
        self,
        *,
        source_id: str,
        display_name: str,
        document_count: int,
    ) -> None:
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO source_state("
                "source_id, display_name, last_attempt_at, last_success_at, last_http_status, "
                "consecutive_failures, last_error, document_count"
                ") VALUES (?, ?, ?, ?, 200, 0, NULL, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "display_name = excluded.display_name, last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, last_http_status = 200, "
                "consecutive_failures = 0, last_error = NULL, document_count = excluded.document_count",
                (source_id, display_name, now, now, document_count),
            )

    def get_event_candidates(self, source_id: str) -> tuple[ClassifiedEventCandidate, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT candidate.source_id, candidate.document_id, document.title, document.url, candidate.candidate_id, "
                "candidate.kind, candidate.label, candidate.evidence_text, candidate.start_at, candidate.end_at, "
                "candidate.timezone, candidate.confidence, candidate.region, candidate.created_at, "
                "candidate.classification, "
                "candidate.classification_label, candidate.routing, candidate.routing_label, "
                "candidate.classification_reason "
                "FROM event_candidate AS candidate "
                "JOIN source_document AS document "
                "ON document.source_id = candidate.source_id AND document.document_id = candidate.document_id "
                "WHERE candidate.source_id = ? ORDER BY candidate.start_at ASC",
                (source_id,),
            ).fetchall()
        return tuple(
            ClassifiedEventCandidate(
                candidate=EventCandidate(
                    source_id=str(row[0]),
                    document_id=str(row[1]),
                    title=str(row[2]),
                    url=str(row[3]),
                    candidate_id=str(row[4]),
                    kind=str(row[5]),
                    label=str(row[6]),
                    evidence_text=str(row[7]),
                    start_at=str(row[8]),
                    end_at=str(row[9]),
                    timezone=str(row[10]),
                    confidence=str(row[11]),
                    region=str(row[12]),
                    created_at=str(row[13]) if row[13] is not None else None,
                ),
                classification=str(row[14]),
                classification_label=str(row[15]),
                routing=str(row[16]),
                routing_label=str(row[17]),
                reason=str(row[18]),
            )
            for row in rows
        )

    def get_maintenance_candidates(
        self, *, region: str = "cn"
    ) -> tuple[ClassifiedEventCandidate, ...]:
        return tuple(
            item
            for item in self.get_event_candidates("china_news_api")
            if item.candidate.kind == "maintenance" and item.candidate.region == region
        )

    def get_calendar_category_options(self) -> tuple[CalendarCategoryOption, ...]:
        """返回所有当前分类的有效日历策略，未设置时使用既有候选路由作为默认值。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT candidate.classification_label, COUNT(*), "
                "COALESCE(policy.included, MAX(CASE WHEN candidate.routing = 'calendar_candidate' THEN 1 ELSE 0 END)) "
                "FROM event_candidate AS candidate "
                "LEFT JOIN calendar_category_policy AS policy "
                "ON policy.classification_label = candidate.classification_label "
                "GROUP BY candidate.classification_label, policy.included "
                "ORDER BY candidate.classification_label"
            ).fetchall()
        return tuple(
            CalendarCategoryOption(
                classification_label=str(row[0]),
                candidate_count=int(row[1]),
                included=bool(row[2]),
            )
            for row in rows
        )

    def set_calendar_category_included(self, classification_label: str, included: bool) -> None:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM event_candidate WHERE classification_label = ? LIMIT 1",
                (classification_label,),
            ).fetchone()
            if exists is None:
                raise ValueError("未知活动分类")
            connection.execute(
                "INSERT INTO calendar_category_policy(classification_label, included, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(classification_label) DO UPDATE SET "
                "included = excluded.included, updated_at = excluded.updated_at",
                (classification_label, int(included), utc_now_text()),
            )

    def get_calendar_entries(
        self,
        start_at: str,
        end_at: str,
        *,
        region: str | None = None,
        classification_codes: tuple[str, ...] = (),
    ) -> tuple[CalendarEntry, ...]:
        """取得与给定半开时间区间相交，且分类策略已选入日历的候选。"""

        filters: list[str] = []
        parameters: list[object] = [end_at, start_at]
        if region is not None:
            filters.append("candidate.region = ?")
            parameters.append(region)
        if classification_codes:
            filters.append(
                "candidate.classification IN ("
                + ", ".join("?" for _ in classification_codes)
                + ")"
            )
            parameters.extend(classification_codes)
        filter_sql = " AND " + " AND ".join(filters) if filters else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT candidate.document_id, document.title, document.url, candidate.classification_label, "
                "candidate.routing_label, candidate.start_at, candidate.end_at, candidate.evidence_text, "
                "candidate.region, candidate.classification "
                "FROM event_candidate AS candidate "
                "JOIN source_document AS document "
                "ON document.source_id = candidate.source_id AND document.document_id = candidate.document_id "
                "LEFT JOIN calendar_category_policy AS policy "
                "ON policy.classification_label = candidate.classification_label "
                "WHERE julianday(candidate.start_at) < julianday(?) "
                "AND julianday(candidate.end_at) >= julianday(?) "
                "AND COALESCE(policy.included, CASE WHEN candidate.routing = 'calendar_candidate' THEN 1 ELSE 0 END) = 1 "
                + filter_sql
                + " ORDER BY candidate.start_at ASC, candidate.document_id ASC",
                parameters,
            ).fetchall()
        return tuple(_calendar_entry_from_row(row) for row in rows)

    def get_review_page(self, page: int, page_size: int) -> ReviewPage:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        offset = (page - 1) * page_size
        with self._connection() as connection:
            total_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM event_candidate WHERE routing = 'review'"
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT candidate.document_id, document.title, document.url, candidate.classification_label, "
                "candidate.routing_label, candidate.start_at, candidate.end_at, candidate.evidence_text "
                "FROM event_candidate AS candidate "
                "JOIN source_document AS document "
                "ON document.source_id = candidate.source_id AND document.document_id = candidate.document_id "
                "WHERE candidate.routing = 'review' "
                "ORDER BY candidate.start_at ASC, candidate.document_id ASC LIMIT ? OFFSET ?",
                (page_size, offset),
            ).fetchall()
        return ReviewPage(
            entries=tuple(_calendar_entry_from_row(row) for row in rows),
            page=page,
            page_size=page_size,
            total_count=total_count,
        )

    @staticmethod
    def _ensure_notification_status_schema(connection: sqlite3.Connection) -> None:
        """Rebuild the notification tables when upgrading the status enum.

        SQLite cannot alter a CHECK constraint in place. The old schema used
        ``sent`` for both successful and terminally failed attempts; preserve
        those rows while giving failures and superseded candidates explicit
        terminal states.
        """

        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notification_candidate'"
        ).fetchone()
        table_sql = str(row[0]) if row is not None and row[0] is not None else ""
        attempt_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'delivery_attempt'"
        ).fetchone()
        attempt_sql = str(attempt_row[0]) if attempt_row is not None and attempt_row[0] is not None else ""
        if (
            "'failed'" in table_sql
            and "'superseded'" in table_sql
            and "ON DELETE CASCADE" in attempt_sql.upper()
        ):
            return

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute(
                """
                CREATE TABLE notification_candidate_v14 (
                    notification_id TEXT PRIMARY KEY,
                    business_key TEXT NOT NULL UNIQUE,
                    target_id TEXT NOT NULL,
                    stream TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'superseded')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (target_id) REFERENCES target(target_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO notification_candidate_v14(
                    notification_id, business_key, target_id, stream, window_key,
                    message_text, status, created_at, updated_at, last_error
                )
                SELECT notification_id, business_key, target_id, stream, window_key,
                       message_text,
                       CASE
                         WHEN status = 'sent' AND last_error IS NULL THEN 'sent'
                         WHEN status = 'sent' AND last_error = '被新的维护时间版本替代' THEN 'superseded'
                         WHEN status = 'sent' THEN 'failed'
                         ELSE status
                       END,
                       created_at, updated_at, last_error
                FROM notification_candidate
                """
            )
            connection.execute(
                """
                CREATE TABLE delivery_attempt_v14 (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                    provider_message_id TEXT,
                    error TEXT,
                    FOREIGN KEY (notification_id) REFERENCES notification_candidate_v14(notification_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO delivery_attempt_v14(
                    attempt_id, notification_id, attempted_at, success, retryable,
                    provider_message_id, error
                )
                SELECT attempt_id, notification_id, attempted_at, success, retryable,
                       provider_message_id, error
                FROM delivery_attempt
                """
            )
            connection.execute("DROP TABLE delivery_attempt")
            connection.execute("DROP TABLE notification_candidate")
            connection.execute(
                "ALTER TABLE notification_candidate_v14 RENAME TO notification_candidate"
            )
            connection.execute("ALTER TABLE delivery_attempt_v14 RENAME TO delivery_attempt")
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        existing_columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})")
        }
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL keeps the WebUI, source poller and dispatcher from serializing all reads.
        # SQLite falls back to its normal journal for in-memory databases.
        if self._database_path != Path(":memory:"):
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _calendar_entry_from_row(row: tuple[object, ...]) -> CalendarEntry:
    return CalendarEntry(
        document_id=str(row[0]),
        title=str(row[1]),
        url=str(row[2]),
        classification_label=str(row[3]),
        routing_label=str(row[4]),
        start_at=str(row[5]),
        end_at=str(row[6]),
        evidence_text=str(row[7]),
        region=str(row[8]) if len(row) > 8 else "cn",
        classification=str(row[9]) if len(row) > 9 else "unclassified",
    )


def _latest_document_timestamp(documents: tuple[SourceDocument, ...]) -> str | None:
    values = [
        value
        for document in documents
        for value in (document.updated_at, document.published_at)
        if value
    ]
    if not values:
        return None
    # Official sources currently use ISO or YYYY/MM/DD text. Keep the original
    # value for display while using parsed ordering whenever possible.
    def sort_key(value: str) -> tuple[int, object]:
        normalized = value.replace("/", "-")
        try:
            return (1, datetime.fromisoformat(normalized))
        except ValueError:
            return (0, value)

    return max(values, key=sort_key)
