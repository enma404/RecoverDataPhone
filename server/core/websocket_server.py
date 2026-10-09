#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
websocket_server.py
============================================================
خادم WebSocket لاستقبال اتصالات تطبيقات الأندرويد.

المسؤوليات:
  1. تشغيل خادم WebSocket على host:port.
  2. قبول الاتصالات الجديدة وإنشاء جلسة لكل عميل.
  3. استقبال الرسائل وتمريرها إلى SessionManager.
  4. إرسال الأوامر إلى العملاء.
  5. إغلاق الاتصالات بشكل نظيف.

يعتمد على:
  - websockets (مكتبة Python)
  - SessionManager (إدارة الجلسات)
  - utils.logger (التسجيل)

الاستخدام:
  server = WebSocketServer("0.0.0.0", 8765, session_manager)
  await server.start()
  ...
  await server.stop()
============================================================
"""

import asyncio
import json
import logging
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed

from core.session_manager import SessionManager
from core.client_session import ClientSession

logger = logging.getLogger("websocket_server")


class WebSocketServer:
    """
    خادم WebSocket لإدارة اتصالات تطبيقات الأندرويد.
    """

    # ============================================================
    # 1. البناء
    # ============================================================
    def __init__(
        self,
        host: str,
        port: int,
        session_manager: SessionManager,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        max_message_size: int = 10 * 1024 * 1024,  # 10 MB
    ):
        """
        :param host: العنوان (0.0.0.0 للاستماع على كل الواجهات)
        :param port: المنفذ
        :param session_manager: مدير الجلسات
        :param ping_interval: فاصل Ping (ثواني)
        :param ping_timeout: مهلة Ping (ثواني)
        :param max_message_size: أقصى حجم للرسالة الواحدة
        """
        self.host = host
        self.port = port
        self.session_manager = session_manager
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.max_message_size = max_message_size

        self._server: Optional[websockets.WebSocketServer] = None
        self._running = False

    # ============================================================
    # 2. التشغيل والإيقاف
    # ============================================================
    async def start(self) -> None:
        """بدء الخادم."""
        if self._running:
            logger.warning("Server already running")
            return

        logger.info(f"Starting WebSocket server on {self.host}:{self.port}")

        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            max_size=self.max_message_size,
            # عدم قطع الاتصال عند عدم وجود رسائل
            close_timeout=10,
        )

        self._running = True
        logger.info(f"Server is listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        """إيقاف الخادم بشكل نظيف."""
        if not self._running:
            return

        logger.info("Stopping WebSocket server...")
        self._running = False

        # إغلاق كل الجلسات النشطة
        await self.session_manager.close_all()

        # إغلاق الخادم
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("WebSocket server stopped")

    # ============================================================
    # 3. معالج الاتصال (Handler)
    # ============================================================
    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """
        يُستدعى تلقائياً عند اتصال عميل جديد.
        يبقى يعمل حتى إغلاق الاتصال.
        """
        client_addr = self._get_client_address(websocket)
        logger.info(f"New connection from {client_addr}")

        session: Optional[ClientSession] = None

        try:
            # -------- 1. إنشاء جلسة جديدة --------
            session = await self.session_manager.create_session(websocket, client_addr)
            logger.info(f"Session created: {session.client_id}")

            # -------- 2. حلقة استقبال الرسائل --------
            async for raw_message in websocket:
                await self._on_message(session, raw_message)

        except ConnectionClosed as e:
            logger.info(
                f"Connection closed: {client_addr} "
                f"(code={e.code}, reason={e.reason})"
            )

        except Exception as e:
            logger.exception(f"Unexpected error in client handler: {e}")

        finally:
            # -------- 3. تنظيف الجلسة --------
            if session is not None:
                await self.session_manager.remove_session(session.client_id)
                logger.info(f"Session removed: {session.client_id}")
            else:
                logger.warning(f"Connection ended without session: {client_addr}")

    # ============================================================
    # 4. معالج الرسالة الواردة
    # ============================================================
    async def _on_message(self, session: ClientSession, raw_message: str) -> None:
        """
        يُستدعى عند وصول رسالة من العميل.
        """
        # تجاهل الرسائل الفارغة
        if not raw_message or not raw_message.strip():
            logger.debug("Empty message received, ignoring")
            return

        # محاولة تحليل JSON
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON from {session.client_id}: {e}")
            return

        # تحديث آخر نشاط للجلسة
        session.touch()

        # تسجيل مختصر
        msg_type = msg.get("type", "?")
        msg_cmd = msg.get("cmd", "")
        logger.debug(
            f"[{session.client_id}] ← type={msg_type} cmd={msg_cmd}"
        )

        # تمرير الرسالة إلى الجلسة (التي بدورها تمررها لـ CLI)
        await session.on_message(msg)

    # ============================================================
    # 5. إرسال أمر إلى عميل محدد
    # ============================================================
    async def send_to_client(
        self,
        client_id: str,
        command: dict,
    ) -> bool:
        """
        إرسال أمر إلى عميل محدد عبر SessionManager.

        :param client_id: معرف العميل
        :param command: الأمر كقاموس Python (سيُحوّل إلى JSON)
        :return: True إذا تم الإرسال بنجاح
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            logger.warning(f"Client not found: {client_id}")
            return False

        return await session.send_command(command)

    # ============================================================
    # 6. بث أمر لجميع العملاء
    # ============================================================
    async def broadcast(
        self,
        command: dict,
        exclude: Optional[list[str]] = None,
    ) -> int:
        """
        بث أمر لجميع العملاء النشطين.

        :param command: الأمر
        :param exclude: قائمة client_id للاستثناء
        :return: عدد العملاء الذين تم الإرسال لهم
        """
        exclude = exclude or []
        count = 0

        for session in self.session_manager.get_all_sessions():
            if session.client_id in exclude:
                continue
            if await session.send_command(command):
                count += 1

        logger.info(f"Broadcast sent to {count} client(s)")
        return count

    # ============================================================
    # 7. حالة الخادم
    # ============================================================
    def is_running(self) -> bool:
        """هل الخادم يعمل؟"""
        return self._running

    def get_client_count(self) -> int:
        """عدد العملاء المتصلين حالياً."""
        return self.session_manager.count()

    def get_host_port(self) -> str:
        """عنوان الخادم."""
        return f"{self.host}:{self.port}"

    # ============================================================
    # 8. أدوات مساعدة
    # ============================================================
    @staticmethod
    def _get_client_address(websocket: WebSocketServerProtocol) -> str:
        """
        استخراج عنوان العميل (IP:Port).
        آمن ضد القيم الفارغة.
        """
        try:
            if websocket.remote_address:
                host, port = websocket.remote_address[0], websocket.remote_address[1]
                return f"{host}:{port}"
        except Exception:
            pass
        return "unknown"

    # ============================================================
    # 9. Context Manager (اختياري)
    # ============================================================
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()