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

from .messaging import TargetRef, SendResult
from .source_models import (
    CalendarCategoryOption,
    CalendarEntry,
    ChinaNewsDetail,
    ClassifiedEventCandidate,
    EventCandidate,
    ActivityDigestPolicy,
    MaintenancePolicy,
    MaintenanceObservation,
    ManualQueryPolicy,
    ReviewPage,
    SourceDocument,
    SourceHealth,
)

SCHEMA_VERSION = "22"
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


@dataclass(frozen=True)
class DeliveryClaim:
    attempt_id: int
    target: TargetRef
    message_text: str


@dataclass(frozen=True)
class DeliveryRecord:
    notification_id: str
    target_name: str
    stream: str
    status: str
    message_text: str
    last_error: str | None
    attempts: int


from .p3_storage import P3StoreMethods, migrate_p3


class StateStore(P3StoreMethods):
    """保存应用状态和稳定投递目标。"""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        now = utc_now_text()
        if self._database_path.exists() and self._database_path.stat().st_size:
            previous_version = self._read_schema_version()
            if previous_version is not None and int(previous_version) > int(SCHEMA_VERSION):
                raise ValueError("数据库来自较新版本，禁止自动降级")
            if previous_version != SCHEMA_VERSION:
                versioned_backup = self._database_path.with_name(
                    self._database_path.name + f".pre-v{int(previous_version) if previous_version else 0}-to-v{SCHEMA_VERSION}.bak"
                )
                if not versioned_backup.exists():
                    self.backup_to(versioned_backup)
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
            migrate_p3(connection, self._ensure_column, target_version=int(SCHEMA_VERSION))
            self._ensure_notification_status_schema(connection)
            self._ensure_column(connection, "notification_candidate", "retirement_code", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "maintenance_observation", "notification_kind", "TEXT NOT NULL DEFAULT ''")
            for column in ("archived", "activity_query_enabled", "maintenance_query_enabled"):
                self._ensure_column(connection, "target", column, "INTEGER NOT NULL DEFAULT 0")
            connection.execute("CREATE TABLE IF NOT EXISTS delivery_review ("
                               "review_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                               "notification_id TEXT NOT NULL REFERENCES notification_candidate(notification_id), "
                               "reviewed_at TEXT NOT NULL, decision TEXT NOT NULL)")
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
                (MIN_UPCOMING_WINDOW_MINUTES, MIN_UPCOMING_WINDOW_MINUTES if self._read_schema_version() not in {"16","17","18","19","20","21","22"} else 0),
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
                "FROM target WHERE archived = 0 ORDER BY provider, target_type, provider_target_id"
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
                "ON CONFLICT(target_id) DO UPDATE SET enabled = excluded.enabled, archived = 0, "
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
            if not enabled:
                self._supersede_pending(connection, target.target_id, reason="服务群已停用", reason_code="target_disabled")
        return target

    @staticmethod
    def _supersede_pending(connection, target_id, *, stream=None, reason, reason_code="policy_disabled"):
        connection.execute(
            "UPDATE notification_candidate SET status = CASE WHEN status = 'pending' THEN 'superseded' ELSE status END, "
            "retirement_code = ?, last_error = ?, updated_at = ? "
            "WHERE target_id = ? AND status IN ('pending', 'sending')" + (" AND stream = ?" if stream else ""),
            (reason_code, reason, utc_now_text(), target_id) + ((stream,) if stream else ()),
        )

    def delete_onebot_group(self, group_id: str) -> bool:
        target = TargetRef.for_onebot_group(group_id)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE target SET archived = 1, enabled = 0, activity_query_enabled = 0, "
                "maintenance_query_enabled = 0, updated_at = ? WHERE target_id = ? AND archived = 0",
                (utc_now_text(), target.target_id),
            )
            changed = cursor.rowcount == 1
            if changed:
                self._supersede_pending(connection, target.target_id, reason="服务群已移除", reason_code="target_archived")
                connection.execute("UPDATE news_scope SET active=0 WHERE target_id=?",(target.target_id,))
                connection.execute("UPDATE announcement_policy SET payload=json_set(payload, '$.enabled', json('false')) WHERE target_id=?",(target.target_id,))
                for table in ("activity_digest_policy", "maintenance_policy"):
                    connection.execute(f"UPDATE {table} SET enabled = 0 WHERE target_id = ?", (target.target_id,))
        return changed

    def get_manual_query_policy(self, target_id: str) -> ManualQueryPolicy:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT activity_query_enabled, maintenance_query_enabled FROM target "
                "WHERE target_id = ? AND archived = 0", (target_id,),
            ).fetchone()
        return ManualQueryPolicy(bool(row[0]), bool(row[1])) if row else ManualQueryPolicy()

    def set_manual_query_policy(self, target_id: str, policy: ManualQueryPolicy) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE target SET activity_query_enabled = ?, maintenance_query_enabled = ?, updated_at = ? "
                "WHERE target_id = ? AND archived = 0",
                (int(policy.activity_enabled), int(policy.maintenance_enabled), utc_now_text(), target_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("未知服务群")

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
                "SELECT enabled FROM target WHERE target_id = ? AND archived = 0", (target_id,)
            ).fetchone()
        return row is not None and row[0] == 1

    def get_activity_digest_policy(self, target_id: str) -> ActivityDigestPolicy:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT enabled, region, classification_codes_json, timezone, digest_hour, "
                "digest_minute, upcoming_window_minutes, ending_window_minutes, regions_json "
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
            regions=tuple(json.loads(row[8])) if row[8] else (str(row[1]),),
        )

    def set_activity_digest_policy(
        self, target_id: str, policy: ActivityDigestPolicy
    ) -> None:
        if any(r not in {"cn","global-na"} for r in policy.selected_regions):
            raise ValueError("不支持的区域")
        previous_policy = self.get_activity_digest_policy(target_id)
        now = utc_now_text()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM target WHERE target_id = ? AND archived = 0", (target_id,)
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

            connection.execute("UPDATE activity_digest_policy SET regions_json=? WHERE target_id=?",(json.dumps(policy.selected_regions),target_id))

            if not policy.enabled:
                self._supersede_pending(connection, target_id, stream="activity_digest", reason="消息策略已关闭")

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
                "notify_updates, watermark_at, regions_json FROM maintenance_policy WHERE target_id = ?",
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
            regions=tuple(json.loads(row[7])) if row[7] else (str(row[1]),),
        )

    def set_maintenance_policy(self, target_id: str, policy: MaintenancePolicy) -> None:
        if any(r not in {"cn","global-na"} for r in policy.selected_regions):
            raise ValueError("不支持的区域")
        previous_policy = self.get_maintenance_policy(target_id)
        now = utc_now_text()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM target WHERE target_id = ? AND archived = 0", (target_id,)
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

            connection.execute("UPDATE maintenance_policy SET regions_json=? WHERE target_id=?",(json.dumps(policy.selected_regions),target_id))
            for region in policy.selected_regions:
                if not previous_policy.enabled or region not in previous_policy.selected_regions:
                    connection.execute('INSERT INTO maintenance_scope VALUES(?,?,?) ON CONFLICT(target_id,region) DO UPDATE SET watermark_at=excluded.watermark_at',(target_id,region,now))

            if not policy.enabled:
                self._supersede_pending(connection, target_id, stream="maintenance", reason="消息策略已关闭")

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
        self, *, target_id: str, candidate: EventCandidate, watermark_at: str | None,
    ) -> MaintenanceObservation:
        """把待规划的 new/changed 意图持久化，避免观察后崩溃丢公告。"""
        document_key = candidate.document_id if candidate.source_id=="china_news_api" else candidate.source_id+":"+candidate.document_id
        now = utc_now_text()
        fresh = (watermark_at is not None and candidate.created_at is not None
                 and _parse_timestamp(candidate.created_at) >= _parse_timestamp(watermark_at))
        with self._connection() as connection:
            row = connection.execute(
                "SELECT candidate_id, start_at, end_at, notification_kind FROM maintenance_observation "
                "WHERE target_id = ? AND document_id = ?", (target_id, document_key),
            ).fetchone()
            version_id = candidate.candidate_id
            if row is None:
                transition = "new" if fresh else "baseline"
            elif (_parse_timestamp(str(row[1])) != _parse_timestamp(candidate.start_at)
                  or _parse_timestamp(str(row[2])) != _parse_timestamp(candidate.end_at)):
                transition = "changed" if fresh else "baseline"
            else:
                transition = str(row[3]) if fresh else "baseline"
                version_id = str(row[0])
            connection.execute(
                "INSERT INTO maintenance_observation(target_id, document_id, candidate_id, start_at, end_at, "
                "observed_at, notification_kind) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target_id, document_id) DO UPDATE SET candidate_id = excluded.candidate_id, "
                "start_at = excluded.start_at, end_at = excluded.end_at, observed_at = excluded.observed_at, "
                "notification_kind = excluded.notification_kind",
                (target_id, document_key, version_id, candidate.start_at, candidate.end_at, now, transition),
            )
        return MaintenanceObservation(transition or "known", version_id)

    def notification_exists(self, business_key: str) -> bool:
        with self._connection() as connection:
            return connection.execute("SELECT 1 FROM notification_candidate WHERE business_key = ?", (business_key,)).fetchone() is not None

    def maintenance_announcement_covers_reminder(self, business_key: str, *, start: datetime, lead_minutes: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status, notification_id FROM notification_candidate WHERE business_key = ?", (business_key,),
            ).fetchone()
            if row is None or row[0] in {"failed", "superseded"}:
                return False
            if row[0] in {"pending", "sending"}:
                return True
            attempts = connection.execute(
                "SELECT attempted_at FROM delivery_attempt WHERE notification_id = ? "
                "AND outcome IN ('sent', 'uncertain')", (row[1],),
            ).fetchall()
        return any(start - timedelta(minutes=lead_minutes) <= _parse_timestamp(item[0]) < start for item in attempts)

    def digest_window_started(self, target_id: str, window_key: str) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM notification_candidate WHERE target_id = ? AND stream = 'activity_digest' "
                "AND substr(window_key, 1, 10) = ? AND (status NOT IN ('pending', 'superseded') OR "
                "(status = 'superseded' AND retirement_code NOT IN ('pages_reduced','digest_replan'))) LIMIT 1",
                (target_id, window_key),
            ).fetchone() is not None

    def prune_digest_pages(self, target_id: str, window_key: str, page_count: int) -> None:
        keys = [f"activity_digest:{target_id}:{window_key}:{index}" for index in range(page_count)]
        with self._connection() as connection:
            connection.execute(
                "UPDATE notification_candidate SET status = 'superseded', retirement_code = 'pages_reduced', last_error = '摘要分页已减少', updated_at = ? "
                "WHERE target_id = ? AND stream = 'activity_digest' AND window_key = ? AND status = 'pending' "
                "AND business_key NOT IN (" + ",".join("?" for _ in keys) + ")",
                (utc_now_text(), target_id, window_key, *keys),
            )

    def create_notification_candidate(
        self,
        *,
        business_key: str,
        target_id: str,
        stream: str,
        window_key: str,
        message_text: str,
        expires_at: str | None = None,
        kind: str = "",
        region: str | None = None,
    ) -> str | None:
        if expires_at is not None:
            expires_at = _parse_timestamp(expires_at).isoformat()
        notification_id = str(uuid4())
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT notification_id, status, retirement_code FROM notification_candidate WHERE business_key = ?",
                (business_key,),
            ).fetchone()
            if existing is not None:
                if str(existing[1]) == "pending" or (existing[1] == "superseded" and existing[2] in {"pages_reduced","digest_replan"}):
                    connection.execute(
                        "UPDATE notification_candidate SET message_text = ?, window_key = ?, updated_at = ?, status = 'pending', retirement_code = '', "
                        "last_error = CASE WHEN status = 'superseded' THEN NULL ELSE last_error END, "
                        "expires_at = ?, kind = ?, region = ? WHERE notification_id = ?",
                        (message_text, window_key, now, expires_at, kind, region, str(existing[0])),
                    )
                return None
            cursor = connection.execute(
                "INSERT INTO notification_candidate("
                "notification_id, business_key, target_id, stream, window_key, message_text, status, "
                "created_at, updated_at, expires_at, kind, region"
                ") VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                (
                    notification_id,
                    business_key,
                    target_id,
                    stream,
                    window_key,
                    message_text,
                    now,
                    now,
                    expires_at,
                    kind,
                    region,
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
                "WHERE candidate.status = 'pending' AND target.enabled = 1 AND target.archived = 0 "
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

    def claim_notification(self, notification_id: str, *, now: datetime | None = None) -> DeliveryClaim | None:
        current = _parse_timestamp((now or datetime.now(timezone.utc)).isoformat())
        timestamp = current.isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT n.status, n.stream, n.expires_at, n.kind, n.region, n.message_text, "
                "t.target_id, t.provider, t.target_type, t.provider_target_id, t.display_name, t.enabled, t.archived "
                "FROM notification_candidate n JOIN target t ON t.target_id = n.target_id "
                "WHERE n.notification_id = ?", (notification_id,),
            ).fetchone()
            if row is None or row[0] != "pending":
                return None
            stream, deadline, kind, region = row[1:5]
            target_id = str(row[6])
            reason = None
            if not row[11] or row[12]:
                reason = "服务群已停用或移除"
            elif deadline and current >= _parse_timestamp(str(deadline)):
                reason = "消息业务窗口已结束"
            elif stream == "activity_digest":
                policy = connection.execute(
                    "SELECT enabled, regions_json FROM activity_digest_policy WHERE target_id = ?", (target_id,),
                ).fetchone()
                if not policy or not policy[0] or not self._regions_allowed(policy[1], region):
                    reason = "活动摘要策略已关闭或区域已改变"
                elif kind=="digest":
                    reason=self._digest_claim_reason(connection,notification_id,target_id,current)
            elif stream == "maintenance":
                policy = connection.execute(
                    "SELECT enabled, regions_json, notify_announcements, notify_updates, reminder_window_minutes "
                    "FROM maintenance_policy WHERE target_id = ?", (target_id,),
                ).fetchone()
                if not policy or not policy[0] or not self._regions_allowed(policy[1], region):
                    reason = "维护策略已关闭或区域已改变"
                elif ((kind == "announcement" and not policy[2]) or
                      (kind == "update" and not policy[3]) or
                      (kind == "reminder" and not policy[4])):
                    reason = "此类维护通知已关闭"
                elif kind == "reminder" and deadline:
                    if current < _parse_timestamp(str(deadline)) - timedelta(minutes=int(policy[4])):
                        return None
            elif stream == "announcement":
                reason = self._news_claim_reason(connection, notification_id, target_id,current)
            else:
                reason = "不支持的消息流"
            if reason:
                connection.execute(
                    "UPDATE notification_candidate SET status = 'superseded', last_error = ?, updated_at = ?, retirement_code=? "
                    "WHERE notification_id = ?", (reason, timestamp,"digest_replan" if reason=="活动核实或内容版本已改变，等待重新规划" else "", notification_id),
                )
                return None
            connection.execute(
                "UPDATE notification_candidate SET status = 'sending', updated_at = ? WHERE notification_id = ?",
                (timestamp, notification_id),
            )
            cursor = connection.execute(
                "INSERT INTO delivery_attempt(notification_id, attempted_at, success, retryable, outcome) "
                "VALUES (?, ?, 0, 0, 'in_flight')", (notification_id, timestamp),
            )
            return DeliveryClaim(
                int(cursor.lastrowid),
                TargetRef(target_id, str(row[7]), str(row[8]), str(row[9]), row[10]),
                str(row[5]),
            )

    def finish_delivery_attempt(self, attempt_id: int, result: SendResult) -> None:
        from dataclasses import replace
        from .diagnostics import safe_reason
        if result.error:result=replace(result,error=safe_reason(result.error))
        if result.success and (not result.provider_message_id or str(result.provider_message_id).strip() in {"", "0"}):
            result = SendResult(success=False, uncertain=True, error="发送未返回有效消息ID，结果未知")
        if result.uncertain:
            status, outcome = "uncertain", "uncertain"
        elif result.success:
            status, outcome = "sent", "sent"
        elif result.retryable:
            status, outcome = "pending", "not_sent"
        else:
            status, outcome = "failed", "failed"
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT a.notification_id, n.retirement_code FROM delivery_attempt a "
                "JOIN notification_candidate n ON n.notification_id = a.notification_id "
                "WHERE a.attempt_id = ? AND a.outcome = 'in_flight'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("投递尝试不存在或已经结束")
            self._finish_news(connection, str(row[0]), status)
            candidate_error = result.error
            if status == "pending" and row[1]:
                status = "superseded"
                candidate_error = "消息策略或服务群在发送期间已关闭，不再重试"
            connection.execute(
                "UPDATE delivery_attempt SET success = ?, retryable = ?, provider_message_id = ?, error = ?, "
                "outcome = ?, completed_at = ? WHERE attempt_id = ?",
                (int(outcome == "sent"), int(outcome == "not_sent"), result.provider_message_id,
                 result.error, outcome, now, attempt_id),
            )
            connection.execute(
                "UPDATE notification_candidate SET status = ?, last_error = ?, updated_at = ? "
                "WHERE notification_id = ? AND status = 'sending'",
                (status, candidate_error, now, row[0]),
            )

    def recover_interrupted_deliveries(self) -> int:
        """仅在独占运行时启动时调用；在途消息不重发、不冒充成功。"""
        now = utc_now_text()
        reason = "上次运行在发送确认前中断，结果未知；请核查"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE delivery_attempt SET outcome = 'uncertain', retryable = 0, completed_at = ?, error = ? "
                "WHERE outcome = 'in_flight'", (now, reason),
            )
            cursor = connection.execute(
                "UPDATE notification_candidate SET status = 'uncertain', updated_at = ?, last_error = ? "
                "WHERE status = 'sending'", (now, reason),
            )
            return cursor.rowcount

    def list_deliveries(self, *, limit: int = 100) -> tuple[DeliveryRecord, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("记录数量必须在 1 到 500 之间")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT n.notification_id, COALESCE(t.display_name,t.provider_target_id), n.stream, n.status, "
                "n.message_text, n.last_error, (SELECT COUNT(*) FROM delivery_attempt a "
                "WHERE a.notification_id = n.notification_id) FROM notification_candidate n "
                "JOIN target t ON t.target_id = n.target_id "
                "ORDER BY CASE WHEN n.status = 'uncertain' THEN 0 ELSE 1 END, n.created_at DESC, n.rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(DeliveryRecord(*row) for row in rows)

    def close_uncertain_delivery(self, notification_id: str) -> bool:
        """关闭未知结果；追加人工处理记录，保留原 attempt，不产生发送。"""
        now = utc_now_text()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE notification_candidate SET status = 'failed', updated_at = ?, "
                "last_error = '维护者已关闭未知结果，未确认发送成功' "
                "WHERE notification_id = ? AND status = 'uncertain'", (now, notification_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                "INSERT INTO delivery_review(notification_id, reviewed_at, decision) VALUES (?, ?, 'closed_without_resend')",
                (notification_id, now),
            )
            return True

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

    def record_source_failure(self, *, source_id: str, display_name: str, error: str, http_status: int | None = None) -> None:
        from .diagnostics import safe_reason
        error=safe_reason(error)
        now = utc_now_text()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO source_state("
                "source_id, display_name, last_attempt_at, consecutive_failures, last_error, document_count, last_http_status"
                ") VALUES (?, ?, ?, 1, ?, 0, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "display_name = excluded.display_name, last_attempt_at = excluded.last_attempt_at, "
                "consecutive_failures = source_state.consecutive_failures + 1, last_error = excluded.last_error, last_http_status = excluded.last_http_status",
                (source_id, display_name, now, error,http_status),
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
            "OR EXISTS (SELECT 1 FROM article_version av WHERE av.source_id=document.source_id AND av.document_id=document.document_id AND av.ready=0) OR detail.parse_status = 'degraded' OR document.title != (SELECT title FROM article_version v WHERE v.source_id=document.source_id AND v.document_id=document.document_id ORDER BY version_id DESC LIMIT 1)"
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
                "COALESCE((SELECT last_attempt_at FROM event_verification ev WHERE ev.source_id=document.source_id AND ev.document_id=document.document_id),detail.fetched_at,'') ASC, document.published_at DESC LIMIT ?",
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
        snapshot_source_id = f"{detail.source_id}/detail/{detail.document_id}"
        previous_candidates = bool([c for c in self.get_event_candidates(detail.source_id) if c.candidate.document_id==detail.document_id])
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
                    ("degraded" if previous_candidates else "empty") if not candidates else "parsed",
                    len(candidates),
                    parser_version,
                ),
            )
            # A zero-candidate parse is retained as an explicit result, while
            # existing candidates remain the last-known-good snapshot until a
            # later successful observation replaces them.
            if candidates:
                prior_created={(kind,_parse_timestamp(start).isoformat(),_parse_timestamp(end).isoformat()):created for kind,start,end,created in connection.execute('SELECT kind,start_at,end_at,created_at FROM event_candidate WHERE source_id=? AND document_id=?',(detail.source_id,detail.document_id))}
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
                        "classification_reason, parser_version, created_at,precision,time_assumption"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,?,?)",
                        (
                            candidate.source_id,
                            candidate.document_id,
                            candidate.candidate_id,
                            candidate.kind,
                            candidate.label,
                            candidate.evidence_text,
                            _parse_timestamp(candidate.start_at).isoformat(),
                            _parse_timestamp(candidate.end_at).isoformat(),
                            candidate.timezone,
                            candidate.confidence,
                            candidate.region,
                            classified.classification,
                            classified.classification_label,
                            classified.routing,
                            classified.routing_label,
                            classified.reason,
                            parser_version,
                            prior_created.get((candidate.kind,_parse_timestamp(candidate.start_at).isoformat(),_parse_timestamp(candidate.end_at).isoformat()),now),
                            candidate.precision,
                            candidate.time_assumption,
                        ),
                    )

            if candidates:
                connection.execute('INSERT INTO event_verification VALUES(?,?,?,?,?,NULL,NULL) ON CONFLICT(source_id,document_id) DO UPDATE SET valid_hash=excluded.valid_hash,last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,error_kind=NULL,error=NULL',(detail.source_id,detail.document_id,content_hash,now,now))
            elif previous_candidates:
                connection.execute("INSERT INTO event_verification(source_id,document_id,last_attempt_at,error_kind,error) VALUES(?,?,?,'parse_zero','新正文未能核实原有活动，保留最后有效时间') ON CONFLICT(source_id,document_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,error_kind=excluded.error_kind,error=excluded.error",(detail.source_id,detail.document_id,now))
            else:
                connection.execute('UPDATE event_verification SET error_kind=NULL,error=NULL WHERE source_id=? AND document_id=?',(detail.source_id,detail.document_id))
            connection.execute('UPDATE article_version SET ready=1 WHERE version_id=(SELECT MAX(version_id) FROM article_version WHERE source_id=? AND document_id=?)',(detail.source_id,detail.document_id))

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

        self.record_verification_attempt(source_id,document_id,error=error,error_kind='detail_failed')
        self.finish_article_observation(source_id,document_id)

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
                "candidate.classification_reason, candidate.precision, candidate.time_assumption "
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
                    precision=str(row[19]),time_assumption=str(row[20]),
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
            for source in ("china_news_api","lodestone_atom","lodestone_topics")
            for item in self.get_event_candidates(source)
            if item.candidate.kind == "maintenance" and item.candidate.region == region and item.candidate.precision=="minute"
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
        use_global_categories: bool = True,
        include_window_end: bool = False,
    ) -> tuple[CalendarEntry, ...]:
        """取得与给定半开时间区间相交，且分类策略已选入日历的候选。"""

        filters = [
            "COALESCE(policy.included, CASE WHEN candidate.routing = 'calendar_candidate' THEN 1 ELSE 0 END) = 1"
            if use_global_categories else "candidate.routing = 'calendar_candidate'"
        ]
        start_operator = "<=" if include_window_end else "<"
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
                "candidate.region, candidate.classification, candidate.source_id,candidate.precision "
                "FROM event_candidate AS candidate "
                "JOIN source_document AS document "
                "ON document.source_id = candidate.source_id AND document.document_id = candidate.document_id "
                "LEFT JOIN calendar_category_policy AS policy "
                "ON policy.classification_label = candidate.classification_label "
                f"WHERE julianday(candidate.start_at) {start_operator} julianday(?) "
                "AND julianday(candidate.end_at) >= julianday(?) "
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
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'notification_candidate'"
        ).fetchone()[0]
        if "'uncertain'" in sql:
            return
        # Executed before other migration writes. No executescript inside the
        # transaction: rebuilding the two FK-linked tables must be atomic.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("""CREATE TABLE notification_candidate_v16 (
                notification_id TEXT PRIMARY KEY, business_key TEXT NOT NULL UNIQUE,
                target_id TEXT NOT NULL REFERENCES target(target_id),
                stream TEXT NOT NULL, window_key TEXT NOT NULL, message_text TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','sending','uncertain','sent','failed','superseded')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT,
                expires_at TEXT, kind TEXT NOT NULL DEFAULT '', region TEXT
            )""")
            connection.execute("""INSERT INTO notification_candidate_v16
                (notification_id,business_key,target_id,stream,window_key,message_text,status,created_at,updated_at,last_error)
                SELECT notification_id,business_key,target_id,stream,window_key,message_text,
                    CASE
                        WHEN status = 'sent' AND last_error = '被新的维护时间版本替代' THEN 'superseded'
                        WHEN status = 'sent' AND last_error IS NOT NULL THEN 'failed'
                        WHEN status = 'pending' AND EXISTS (
                            SELECT 1 FROM delivery_attempt a WHERE a.notification_id = notification_candidate.notification_id
                        ) THEN 'uncertain'
                        WHEN status = 'pending' THEN 'superseded'
                        ELSE status
                    END,
                    created_at,updated_at,
                    CASE WHEN status = 'pending' THEN '旧版本待发送消息已暂停，需核查历史；不自动回放'
                         ELSE last_error END
                FROM notification_candidate""")
            connection.execute("""CREATE TABLE delivery_attempt_v16 (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id TEXT NOT NULL REFERENCES notification_candidate_v16(notification_id),
                attempted_at TEXT NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)),
                retryable INTEGER NOT NULL CHECK(retryable IN (0,1)),
                provider_message_id TEXT, error TEXT,
                outcome TEXT NOT NULL CHECK(outcome IN ('in_flight','sent','not_sent','failed','uncertain')),
                completed_at TEXT
            )""")
            connection.execute("""INSERT INTO delivery_attempt_v16
                SELECT attempt_id,notification_id,attempted_at,success,retryable,provider_message_id,error,
                    CASE WHEN success = 1 THEN 'sent' WHEN retryable = 1 THEN 'uncertain' ELSE 'failed' END,
                    attempted_at FROM delivery_attempt""")
            connection.execute("DROP TABLE delivery_attempt")
            connection.execute("DROP TABLE notification_candidate")
            connection.execute("ALTER TABLE notification_candidate_v16 RENAME TO notification_candidate")
            connection.execute("ALTER TABLE delivery_attempt_v16 RENAME TO delivery_attempt")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("投递表迁移后的外键校验失败")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
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
            connection.execute("PRAGMA synchronous = FULL")
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
        source_id=str(row[10]) if len(row)>10 else "china_news_api",
        precision=str(row[11]) if len(row)>11 else "unknown",
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
