#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit.py
============================================================
نظام سجل التدقيق (Audit Log).

المسؤوليات:
  1. تسجيل كل عملية حساسة (auth, data access, accessibility).
  2. حفظ السجل في ملف JSONL (JSON Lines) للتحليل.
  3. توفير استعلامات للتقارير.
  4. تنظيف السجلات القديمة.
  5. تصدير السجل لمشروع التخرج / المراجعة القانونية.

لماذا JSONL؟
  - سطر واحد لكل حدث → إضافة سهلة بدون إعادة كتابة الملف.
  - آمن ضد التلف (سطر تالف لا يُفسد الباقي).
  - يمكن قراءته كـ stream للأحداث الضخمة.

أحداث التسجيل:
  - AUTH_SUCCESS / AUTH_FAILURE
  - SESSION_OPEN / SESSION_CLOSE
  - COMMAND_RECEIVED / COMMAND_REJECTED
  - DATA_ACCESS (photos/videos/files)
  - ACCESSIBILITY_ACTION
  - RATE_LIMIT_HIT
  - BAN / UNBAN

يعتمد على:
  - utils.logger

الاستخدام:
  audit = get_audit_logger()
  audit.log("AUTH_SUCCESS", client_id="a3f8", details={"key": "RDP-****"})
  entries = audit.query(client_id="a3f8", event_type="AUTH_SUCCESS")
============================================================
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger, mask_secret, truncate

logger = get_logger("audit")


# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
AUDIT_DIR = BASE_DIR / "storage" / "audit"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"

# الحد الأقصى لحجم ملف السجل (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# عدد الملفات الاحتياطية
BACKUP_COUNT = 10

# عمر السجل قبل التنظيف (30 يوم)
RECORD_TTL_SECONDS = 30 * 24 * 3600


# ============================================================
# 2. أنواع الأحداث (Event Types)
# ============================================================
class EventType:
    # ----- المصادقة -----
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILURE = "AUTH_FAILURE"
    AUTH_EXPIRED = "AUTH_EXPIRED"

    # ----- الجلسات -----
    SESSION_OPEN = "SESSION_OPEN"
    SESSION_CLOSE = "SESSION_CLOSE"
    SESSION_STALE = "SESSION_STALE"

    # ----- الأوامر -----
    COMMAND_RECEIVED = "COMMAND_RECEIVED"
    COMMAND_REJECTED = "COMMAND_REJECTED"
    COMMAND_FAILED = "COMMAND_FAILED"

    # ----- البيانات -----
    DATA_ACCESS_PHOTOS = "DATA_ACCESS_PHOTOS"
    DATA_ACCESS_VIDEOS = "DATA_ACCESS_VIDEOS"
    DATA_ACCESS_FILE = "DATA_ACCESS_FILE"
    DATA_ACCESS_LIST = "DATA_ACCESS_LIST"
    DATA_FILE_SAVED = "DATA_FILE_SAVED"

    # ----- Accessibility -----
    ACCESSIBILITY_ACTION = "ACCESSIBILITY_ACTION"
    ACCESSIBILITY_ENABLED = "ACCESSIBILITY_ENABLED"
    ACCESSIBILITY_DISABLED = "ACCESSIBILITY_DISABLED"

    # ----- الأمان -----
    RATE_LIMIT_HIT = "RATE_LIMIT_HIT"
    CLIENT_BANNED = "CLIENT_BANNED"
    CLIENT_UNBANNED = "CLIENT_UNBANNED"

    # ----- النظام -----
    SERVER_START = "SERVER_START"
    SERVER_STOP = "SERVER_STOP"


# ============================================================
# 3. سجل واحد (AuditEntry)
# ============================================================
@dataclass
class AuditEntry:
    """سجل واحد."""
    event_type: str
    timestamp: float = field(default_factory=time.time)
    client_id: Optional[str] = None
    operator: Optional[str] = None       # المستخدم المسؤول
    ip_address: Optional[str] = None
    command: Optional[str] = None
    success: bool = True
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    # ----- تحويلات -----
    def to_dict(self) -> Dict[str, Any]:
        """تحويل إلى قاموس (مع تشويش الحساس)."""
        d = asdict(self)

        # تنسيق الوقت
        d["timestamp_iso"] = datetime.fromtimestamp(
            self.timestamp
        ).isoformat()

        # تشويش القيم الحساسة في details
        if d.get("details"):
            d["details"] = self._sanitize_details(d["details"])

        return d

    def to_json(self) -> str:
        """تحويل إلى JSON (سطر واحد)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def _sanitize_details(details: Dict[str, Any]) -> Dict[str, Any]:
        """تشويش البيانات الحساسة."""
        sanitized = {}
        sensitive_keys = (
            "password", "token", "key", "secret",
            "license_key", "private_key", "chunk",
        )

        for k, v in details.items():
            if k.lower() in sensitive_keys:
                sanitized[k] = mask_secret(str(v))
            elif isinstance(v, str) and len(v) > 200:
                sanitized[k] = truncate(v, 100)
            elif isinstance(v, dict):
                sanitized[k] = AuditEntry._sanitize_details(v)
            else:
                sanitized[k] = v

        return sanitized


# ============================================================
# 4. AuditLogger
# ============================================================
class AuditLogger:
    """
    مدير سجل التدقيق.
    """

    def __init__(self, audit_file: Path = AUDIT_FILE):
        self.audit_file = Path(audit_file)
        self._lock = threading.Lock()
        self._ensure_storage()

    # ========================================================
    # 4.1 إدارة الملف
    # ========================================================
    def _ensure_storage(self) -> None:
        """إنشاء المجلد إذا لم يكن موجوداً."""
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.audit_file.exists():
            self.audit_file.touch()

    def _rotate_if_needed(self) -> None:
        """تدوير الملف إذا تجاوز الحد."""
        if not self.audit_file.exists():
            return

        if self.audit_file.stat().st_size < MAX_FILE_SIZE:
            return

        # إعادة تسمية الملف الحالي
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rotated = self.audit_file.with_name(
            f"audit_{timestamp}.jsonl"
        )

        try:
            self.audit_file.rename(rotated)
            self.audit_file.touch()
            logger.info(f"Rotated audit log → {rotated.name}")

            # احذف الملفات القديمة
            self._cleanup_old_files()

        except Exception as e:
            logger.exception(f"Rotation failed: {e}")

    def _cleanup_old_files(self) -> None:
        """حذف الملفات الاحتياطية القديمة."""
        try:
            files = sorted(
                self.audit_file.parent.glob("audit_*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            # احتفظ بـ BACKUP_COUNT الأحدث
            for old_file in files[BACKUP_COUNT:]:
                old_file.unlink()
                logger.debug(f"Deleted old audit file: {old_file.name}")

        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")

    # ========================================================
    # 4.2 التسجيل
    # ========================================================
    def log(
        self,
        event_type: str,
        client_id: Optional[str] = None,
        operator: Optional[str] = None,
        ip_address: Optional[str] = None,
        command: Optional[str] = None,
        success: bool = True,
        message: str = "",
        details: Optional[Dict[str, Any]] = None,
        **extra,
    ) -> None:
        """
        تسجيل حدث.

        :param event_type: نوع الحدث (EventType.*)
        :param client_id:  معرف العميل
        :param operator:   المستخدم المسؤول
        :param ip_address: عنوان IP
        :param command:    الأمر المرتبط
        :param success:    نجاح العملية
        :param message:    رسالة نصية
        :param details:    تفاصيل إضافية
        :param extra:      حقول إضافية تُدمج في details
        """
        entry = AuditEntry(
            event_type=event_type,
            client_id=client_id,
            operator=operator,
            ip_address=ip_address,
            command=command,
            success=success,
            message=message,
            details={**(details or {}), **extra},
        )

        # ----- كتابة -----
        try:
            with self._lock:
                self._rotate_if_needed()

                with open(self.audit_file, "a", encoding="utf-8") as f:
                    f.write(entry.to_json())
                    f.write("\n")

        except Exception as e:
            logger.exception(f"Failed to write audit entry: {e}")

        # ----- تسجيل أيضاً في logger -----
        self._log_to_console(entry)

    def _log_to_console(self, entry: AuditEntry) -> None:
        """تسجيل مختصر في logger."""
        prefix = f"[AUDIT] {entry.event_type}"
        if entry.client_id:
            prefix += f" client={entry.client_id}"
        if entry.command:
            prefix += f" cmd={entry.command}"

        msg = f"{prefix} | {entry.message}"

        if not entry.success:
            logger.warning(msg)
        else:
            logger.info(msg)

    # ========================================================
    # 4.3 دوال مختصرة للأحداث الشائعة
    # ========================================================
    def auth_success(self, client_id: str, key: str,
                     expires_at: float = 0) -> None:
        self.log(
            EventType.AUTH_SUCCESS,
            client_id=client_id,
            success=True,
            message="Authentication successful",
            details={
                "key": mask_secret(key),
                "expires_at": expires_at,
            },
        )

    def auth_failure(self, client_id: str, key: str, reason: str) -> None:
        self.log(
            EventType.AUTH_FAILURE,
            client_id=client_id,
            success=False,
            message=f"Authentication failed: {reason}",
            details={
                "key": mask_secret(key),
                "reason": reason,
            },
        )

    def session_open(self, client_id: str, ip_address: str,
                     device_info: Optional[Dict] = None) -> None:
        self.log(
            EventType.SESSION_OPEN,
            client_id=client_id,
            ip_address=ip_address,
            success=True,
            message="Session opened",
            details={"device": device_info or {}},
        )

    def session_close(self, client_id: str, reason: str = "Normal") -> None:
        self.log(
            EventType.SESSION_CLOSE,
            client_id=client_id,
            success=True,
            message=f"Session closed: {reason}",
        )

    def command_received(self, client_id: str, command: str) -> None:
        self.log(
            EventType.COMMAND_RECEIVED,
            client_id=client_id,
            command=command,
            success=True,
            message="Command received",
        )

    def command_rejected(self, client_id: str, command: str,
                         reason: str) -> None:
        self.log(
            EventType.COMMAND_REJECTED,
            client_id=client_id,
            command=command,
            success=False,
            message=f"Command rejected: {reason}",
        )

    def data_access(self, client_id: str, command: str,
                    file_count: int = 0, bytes_count: int = 0) -> None:
        event = {
            "GET_PHOTOS": EventType.DATA_ACCESS_PHOTOS,
            "GET_VIDEOS": EventType.DATA_ACCESS_VIDEOS,
            "GET_FILE":   EventType.DATA_ACCESS_FILE,
            "LIST_DIR":   EventType.DATA_ACCESS_LIST,
        }.get(command, EventType.DATA_ACCESS_FILE)

        self.log(
            event,
            client_id=client_id,
            command=command,
            success=True,
            message=f"Data access: {file_count} files, {bytes_count} bytes",
            details={
                "file_count": file_count,
                "bytes_count": bytes_count,
            },
        )

    def file_saved(self, client_id: str, path: str,
                   size: int, cmd: str) -> None:
        self.log(
            EventType.DATA_FILE_SAVED,
            client_id=client_id,
            command=cmd,
            success=True,
            message=f"File saved: {os.path.basename(path)}",
            details={
                "path": path,
                "size": size,
            },
        )

    def accessibility_action(self, client_id: str, action: str,
                             params: Optional[Dict] = None,
                             success: bool = True) -> None:
        self.log(
            EventType.ACCESSIBILITY_ACTION,
            client_id=client_id,
            command=action,
            success=success,
            message=f"Accessibility: {action}",
            details={"params": params or {}},
        )

    def rate_limit_hit(self, client_id: str, command: str,
                       reason: str) -> None:
        self.log(
            EventType.RATE_LIMIT_HIT,
            client_id=client_id,
            command=command,
            success=False,
            message=f"Rate limit hit: {reason}",
        )

    def client_banned(self, client_id: str, duration: int) -> None:
        self.log(
            EventType.CLIENT_BANNED,
            client_id=client_id,
            success=True,
            message=f"Client banned for {duration}s",
            details={"duration": duration},
        )

    def server_start(self, host: str, port: int) -> None:
        self.log(
            EventType.SERVER_START,
            success=True,
            message=f"Server started on {host}:{port}",
            details={"host": host, "port": port},
        )

    def server_stop(self) -> None:
        self.log(
            EventType.SERVER_STOP,
            success=True,
            message="Server stopped",
        )

    # ========================================================
    # 4.4 الاستعلام (Query)
    # ========================================================
    def query(
        self,
        event_type: Optional[str] = None,
        client_id: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        success: Optional[bool] = None,
        limit: int = 1000,
    ) -> List[AuditEntry]:
        """
        استعلام السجل حسب الشروط.

        :param event_type: فلترة حسب النوع
        :param client_id:  فلترة حسب العميل
        :param since:      من وقت (timestamp)
        :param until:      حتى وقت
        :param success:    نجاح/فشل
        :param limit:      الحد الأقصى للنتائج
        :return: قائمة AuditEntry
        """
        results: List[AuditEntry] = []

        if not self.audit_file.exists():
            return results

        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    if len(results) >= limit:
                        break

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # ----- فلاتر -----
                    if event_type and data.get("event_type") != event_type:
                        continue
                    if client_id and data.get("client_id") != client_id:
                        continue
                    if since and data.get("timestamp", 0) < since:
                        continue
                    if until and data.get("timestamp", 0) > until:
                        continue
                    if success is not None and data.get("success") != success:
                        continue

                    entry = AuditEntry(
                        event_type=data.get("event_type", ""),
                        timestamp=data.get("timestamp", 0),
                        client_id=data.get("client_id"),
                        operator=data.get("operator"),
                        ip_address=data.get("ip_address"),
                        command=data.get("command"),
                        success=data.get("success", True),
                        message=data.get("message", ""),
                        details=data.get("details", {}),
                    )
                    results.append(entry)

        except Exception as e:
            logger.exception(f"Query failed: {e}")

        return results

    # ========================================================
    # 4.5 الإحصائيات
    # ========================================================
    def stats(self) -> Dict[str, Any]:
        """إحصائيات عامة."""
        counts: Dict[str, int] = {}
        total = 0
        failed = 0

        if not self.audit_file.exists():
            return {"total": 0, "failed": 0, "by_type": {}}

        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = data.get("event_type", "UNKNOWN")
                        counts[event] = counts.get(event, 0) + 1
                        total += 1
                        if not data.get("success", True):
                            failed += 1
                    except json.JSONDecodeError:
                        continue

        except Exception as e:
            logger.exception(f"Stats failed: {e}")

        return {
            "total": total,
            "failed": failed,
            "by_type": counts,
            "file_size": (
                self.audit_file.stat().st_size
                if self.audit_file.exists() else 0
            ),
        }

    # ========================================================
    # 4.6 التنظيف
    # ========================================================
    def cleanup_old(self, ttl_seconds: int = RECORD_TTL_SECONDS) -> int:
        """
        حذف السجلات الأقدم من ttl_seconds.
        :return: عدد السجلات المحذوفة
        """
        if not self.audit_file.exists():
            return 0

        cutoff = time.time() - ttl_seconds
        kept_lines: List[str] = []
        removed = 0

        try:
            with open(self.audit_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("timestamp", 0) >= cutoff:
                            kept_lines.append(line)
                        else:
                            removed += 1
                    except json.JSONDecodeError:
                        kept_lines.append(line)

            # إعادة كتابة الملف
            if removed > 0:
                with open(self.audit_file, "w", encoding="utf-8") as f:
                    for line in kept_lines:
                        f.write(line + "\n")

                logger.info(f"Cleaned {removed} old audit entries")

        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")

        return removed

    def clear(self) -> None:
        """مسح السجل بالكامل (للتطوير)."""
        try:
            self.audit_file.write_text("")
            logger.warning("Audit log cleared")
        except Exception as e:
            logger.exception(f"Clear failed: {e}")


# ============================================================
# 5. Singleton
# ============================================================
_default_audit: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """إرجاع المدير (Singleton)."""
    global _default_audit
    if _default_audit is None:
        _default_audit = AuditLogger()
    return _default_audit