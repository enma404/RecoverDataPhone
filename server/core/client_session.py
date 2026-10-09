#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_session.py
============================================================
تمثيل جلسة عميل واحد (هاتف أندرويد متصل).

المسؤوليات:
  1. تخزين معلومات العميل (client_id, device, address).
  2. إرسال الأوامر إلى العميل (send_command).
  3. استقبال الرسائل من العميل وإشعار CLI (on_message).
  4. تتبع حالة الاتصال (متصل/منقطع).
  5. تسجيل آخر نشاط (last_activity).

يعتمد على:
  - websockets (WebSocketServerProtocol)
  - asyncio (Queue للتواصل مع CLI)
  - utils.logger

الاستخدام:
  session = ClientSession(websocket, client_addr)
  await session.send_command({"cmd": "GET_PHOTOS"})
  # عند وصول رسالة:
  await session.on_message({"type": "HELLO", ...})
============================================================
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("client_session")


class ClientSession:
    """
    يمثّل جلسة عميل واحد (هاتف متصل).
    """

    # ============================================================
    # 1. البناء
    # ============================================================
    def __init__(
        self,
        websocket: WebSocketServerProtocol,
        client_addr: str,
        client_id: Optional[str] = None,
    ):
        """
        :param websocket: اتصال WebSocket
        :param client_addr: عنوان العميل (IP:Port)
        :param client_id: معرف اختياري (يُولَّد تلقائياً إذا لم يُعطَ)
        """
        # -------- معرف فريد --------
        self.client_id: str = client_id or self._generate_id()
        self.client_addr: str = client_addr

        # -------- الاتصال --------
        self._ws: WebSocketServerProtocol = websocket

        # -------- حالة --------
        self.connected: bool = True
        self.connected_at: float = time.time()
        self.last_activity: float = time.time()

        # -------- معلومات الجهاز (تُملأ عند استقبال HELLO) --------
        self.device_info: Dict[str, Any] = {
            "manufacturer": "unknown",
            "model": "unknown",
            "android": "unknown",
            "sdk": 0,
            "autopilot": False,
        }

        # -------- طابور الرسائل الواردة (يقرأه CLI) --------
        # CLI يستمع لهذا الطابور ليعرض رسائل العميل
        self.incoming_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        # -------- إحصائيات --------
        self.messages_received: int = 0
        self.messages_sent: int = 0
        self.bytes_received: int = 0
        self.bytes_sent: int = 0

        logger.debug(f"ClientSession created: {self.client_id} @ {client_addr}")

    # ============================================================
    # 2. الخصائص (Properties)
    # ============================================================
    @property
    def is_alive(self) -> bool:
        """هل الجلسة لا تزال متصلة؟"""
        return self.connected and not self._ws.closed

    @property
    def uptime(self) -> float:
        """مدة الاتصال بالثواني."""
        return time.time() - self.connected_at

    @property
    def idle_time(self) -> float:
        """مدة الخمول بالثواني."""
        return time.time() - self.last_activity

    # ============================================================
    # 3. إرسال الأوامر
    # ============================================================
    async def send_command(self, command: Dict[str, Any]) -> bool:
        """
        إرسال أمر إلى العميل.

        :param command: قاموس الأمر (سيُحوَّل إلى JSON)
        :return: True إذا تم الإرسال بنجاح
        """
        if not self.is_alive:
            logger.warning(f"Cannot send to {self.client_id}: not alive")
            return False

        try:
            # إضافة معرف فريد إن لم يكن موجوداً
            if "id" not in command:
                command["id"] = self._generate_request_id()

            # إضافة timestamp إن لم يكن موجوداً
            if "timestamp" not in command:
                command["timestamp"] = int(time.time() * 1000)

            # تحويل إلى JSON
            payload = json.dumps(command, ensure_ascii=False)

            # إرسال
            await self._ws.send(payload)

            # إحصائيات
            self.messages_sent += 1
            self.bytes_sent += len(payload)

            logger.debug(
                f"[{self.client_id}] → {command.get('cmd', command.get('type', '?'))} "
                f"({len(payload)} bytes)"
            )
            return True

        except ConnectionClosed:
            logger.warning(f"Connection closed while sending to {self.client_id}")
            self.connected = False
            return False

        except Exception as e:
            logger.exception(f"Send error to {self.client_id}: {e}")
            return False

    async def send_raw(self, raw_message: str) -> bool:
        """
        إرسال نص خام (للاستخدام المتقدم).
        """
        if not self.is_alive:
            return False
        try:
            await self._ws.send(raw_message)
            self.messages_sent += 1
            self.bytes_sent += len(raw_message)
            return True
        except Exception as e:
            logger.exception(f"send_raw error: {e}")
            return False

    # ============================================================
    # 4. استقبال الرسائل
    # ============================================================
    async def on_message(self, message: Dict[str, Any]) -> None:
        """
        يُستدعى عند وصول رسالة من العميل.
        - يحدّث معلومات الجهاز إن كانت HELLO.
        - يضع الرسالة في الطابور ليقرأها CLI.
        """
        # -------- إحصائيات --------
        self.messages_received += 1
        self.touch()

        # -------- معالجة HELLO --------
        msg_type = message.get("type", "")
        if msg_type == "HELLO":
            self._handle_hello(message)

        # -------- إضافة الرسالة للطابور --------
        try:
            self.incoming_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning(
                f"[{self.client_id}] incoming_queue full, dropping message"
            )

    def _handle_hello(self, message: Dict[str, Any]) -> None:
        """
        معالجة رسالة HELLO (معلومات الجهاز).
        """
        device = message.get("device")
        if isinstance(device, dict):
            self.device_info.update(device)

        autopilot = message.get("autopilot")
        if isinstance(autopilot, bool):
            self.device_info["autopilot"] = autopilot

        logger.info(
            f"[{self.client_id}] HELLO: "
            f"{self.device_info.get('manufacturer')} "
            f"{self.device_info.get('model')} "
            f"(Android {self.device_info.get('android')}, "
            f"Autopilot={self.device_info.get('autopilot')})"
        )

    # ============================================================
    # 5. قراءة الرسائل (لـ CLI)
    # ============================================================
    async def get_next_message(self, timeout: Optional[float] = None) -> Optional[Dict]:
        """
        قراءة الرسالة التالية من الطابور.
        يُستخدم من CLI لعرض الرسائل.

        :param timeout: مهلة الانتظار (None = انتظر للأبد)
        :return: الرسالة أو None عند المهلة
        """
        try:
            if timeout is None:
                return await self.incoming_queue.get()
            return await asyncio.wait_for(
                self.incoming_queue.get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    def has_pending_messages(self) -> bool:
        """هل هناك رسائل في الطابور؟"""
        return not self.incoming_queue.empty()

    # ============================================================
    # 6. الإغلاق
    # ============================================================
    async def close(self, code: int = 1000, reason: str = "Server closing") -> None:
        """
        إغلاق الجلسة.
        """
        if not self.connected:
            return

        logger.info(f"Closing session: {self.client_id} (code={code})")
        self.connected = False

        try:
            await self._ws.close(code, reason)
        except Exception as e:
            logger.debug(f"Close error for {self.client_id}: {e}")

        # إشارة نهاية للـ CLI
        try:
            self.incoming_queue.put_nowait({
                "type": "__CLOSED__",
                "reason": reason,
            })
        except asyncio.QueueFull:
            pass

    def mark_disconnected(self) -> None:
        """تعليم الجلسة كمنقطعة (بدون إرسال close)."""
        self.connected = False

    # ============================================================
    # 7. تحديث النشاط
    # ============================================================
    def touch(self) -> None:
        """تحديث وقت آخر نشاط."""
        self.last_activity = time.time()

    # ============================================================
    # 8. أدوات مساعدة
    # ============================================================
    def _generate_id(self) -> str:
        """توليد معرف فريد للعميل."""
        return uuid.uuid4().hex[:8]

    def _generate_request_id(self) -> str:
        """توليد معرف طلب فريد."""
        return f"req-{uuid.uuid4().hex[:8]}"

    def summary(self) -> str:
        """
        ملخص الجلسة (للعرض في CLI).
        """
        status = "🟢 Online" if self.is_alive else "🔴 Offline"
        device = (
            f"{self.device_info.get('manufacturer', '?')} "
            f"{self.device_info.get('model', '?')}"
        )
        android = self.device_info.get("android", "?")
        autopilot = "✓" if self.device_info.get("autopilot") else "✗"

        return (
            f"{self.client_id} | {status} | "
            f"{device} | Android {android} | "
            f"AutoPilot: {autopilot} | "
            f"↑{self.messages_sent} ↓{self.messages_received}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """تحويل الجلسة إلى قاموس (للـ JSON)."""
        return {
            "client_id": self.client_id,
            "client_addr": self.client_addr,
            "connected": self.connected,
            "connected_at": self.connected_at,
            "last_activity": self.last_activity,
            "uptime": self.uptime,
            "device_info": self.device_info,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
        }

    # ============================================================
    # 9. تمثيل نصي
    # ============================================================
    def __repr__(self) -> str:
        return f"<ClientSession {self.client_id} @ {self.client_addr}>"

    def __str__(self) -> str:
        return self.summary()