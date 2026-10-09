#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
============================================================
نقطة دخول سيرفر RecoverDataPhone.

المسؤوليات:
  1. إعداد التسجيل (logger).
  2. إنشاء المجلدات المطلوبة.
  3. تشغيل خادم WebSocket.
  4. تشغيل واجهة الطرفية (CLI).
  5. ربط كل المكونات (SessionManager + Handlers).
  6. إيقاف نظيف عند SIGINT/SIGTERM.

الاستخدام:
  $ python main.py
  $ python main.py --host 0.0.0.0 --port 8765 --log-level DEBUG
  $ python main.py --no-cli           # بدون واجهة طرفية

المتطلبات:
  pip install -r requirements.txt
============================================================
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# ============================================================
# 1. إعداد المسارات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# 2. الاستيرادات الداخلية
# ============================================================
from utils.logger import setup_logger, get_logger
from core.session_manager import SessionManager
from core.websocket_server import WebSocketServer
from core.auth import get_auth_manager

from commands.photos import PhotosCommand
from commands.videos import VideosCommand
from commands.files import FilesCommand
from commands.accessibility import AccessibilityCommand

from cli.terminal import TerminalCLI


# ============================================================
# 3. الإعدادات الافتراضية
# ============================================================
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_LOG_LEVEL = "INFO"


# ============================================================
# 4. تحليل وسائط سطر الأوامر
# ============================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="RecoverDataPhone Server",
        description="WebSocket server for RecoverDataPhone Android app",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  python main.py                          # تشغيل عادي
  python main.py --port 9000              # منفذ مخصص
  python main.py --log-level DEBUG        # تفاصيل أكثر
  python main.py --no-cli                 # بدون طرفية (للاختبار)
        """,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to bind (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--log-level",
        default=DEFAULT_LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"Log level (default: {DEFAULT_LOG_LEVEL})",
    )
    parser.add_argument(
        "--no-cli",
        action="store_true",
        help="Disable interactive terminal CLI",
    )
    parser.add_argument(
        "--no-file-log",
        action="store_true",
        help="Disable logging to file",
    )
    return parser.parse_args()


# ============================================================
# 5. إعداد المجلدات
# ============================================================
def ensure_directories() -> None:
    """إنشاء المجلدات المطلوبة إن لم تكن موجودة."""
    dirs = [
        BASE_DIR / "storage",
        BASE_DIR / "storage" / "received",
        BASE_DIR / "storage" / "temp",
        BASE_DIR / "storage" / "keys",
        BASE_DIR / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 6. الدالة الرئيسية
# ============================================================
async def run_server(args: argparse.Namespace) -> None:
    logger = get_logger("main")

    # ========================================================
    # 6.1 إنشاء SessionManager
    # ========================================================
    session_manager = SessionManager()
    logger.info("SessionManager initialized")

    # ========================================================
    # 6.2 إنشاء المعالجات (Handlers)
    # ========================================================
    logger.info("Initializing command handlers...")

    photos_handler = PhotosCommand(session_manager)
    videos_handler = VideosCommand(session_manager)
    files_handler  = FilesCommand(session_manager)
    acc_handler    = AccessibilityCommand(session_manager)

    logger.info("Command handlers initialized")

    # ========================================================
    # 6.3 ربط المعالجات بالمدير (Dependency Injection)
    # ========================================================
    session_manager.register_handlers(
        photos=photos_handler,
        videos=videos_handler,
        files=files_handler,
        accessibility=acc_handler,
    )
    logger.info("Handlers registered with SessionManager")

    # ========================================================
    # 6.4 إعداد AuthManager
    # ========================================================
    try:
        auth = get_auth_manager()
        stats = auth.stats()
        logger.info(
            f"Auth loaded: {stats['total']} licenses "
            f"({stats['active']} active, {stats['expired']} expired)"
        )
    except Exception as e:
        logger.error(f"Failed to load auth: {e}")

    # ========================================================
    # 6.5 تشغيل Cleanup task
    # ========================================================
    await session_manager.start_cleanup()
    logger.info("Cleanup task started")

    # ========================================================
    # 6.6 إنشاء وتشغيل الخادم
    # ========================================================
    server = WebSocketServer(
        host=args.host,
        port=args.port,
        session_manager=session_manager,
    )

    try:
        await server.start()
        logger.info(f"WebSocket server started on ws://{args.host}:{args.port}")
    except OSError as e:
        logger.error(f"Failed to bind on {args.host}:{args.port}: {e}")
        logger.error("تأكد من أن المنفذ غير مستخدم، وأن لديك صلاحيات.")
        return
    except Exception as e:
        logger.exception(f"Server start error: {e}")
        return

    # ========================================================
    # 6.7 تشغيل الطرفية (اختياري)
    # ========================================================
    cli_task = None
    if not args.no_cli:
        try:
            cli = TerminalCLI(session_manager)
            cli_task = asyncio.create_task(cli.run())
            logger.info("Terminal CLI started")
        except Exception as e:
            logger.exception(f"Failed to start CLI: {e}")
            cli_task = None

    # ========================================================
    # 6.8 انتظار إشارة الإيقاف
    # ========================================================
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.warning(f"Received signal: {sig_name}")
        stop_event.set()

    loop = asyncio.get_running_loop()

    # SIGINT (Ctrl+C) و SIGTERM (kill)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
        except NotImplementedError:
            # Windows لا يدعم add_signal_handler بشكل كامل
            pass

    logger.info("Server is running. Press Ctrl+C to stop.")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")

        # ====================================================
        # 6.9 إيقاف نظيف
        # ====================================================

        # 1. إيقاف الطرفية
        if cli_task and not cli_task.done():
            logger.info("Stopping CLI...")
            cli_task.cancel()
            try:
                await cli_task
            except asyncio.CancelledError:
                pass

        # 2. إيقاف Cleanup task
        logger.info("Stopping cleanup task...")
        await session_manager.stop_cleanup()

        # 3. إغلاق كل الجلسات
        logger.info("Closing all sessions...")
        await session_manager.close_all()

        # 4. إيقاف الخادم
        logger.info("Stopping WebSocket server...")
        await server.stop()

        logger.info("Server stopped. Goodbye.")


# ============================================================
# 7. نقطة الدخول
# ============================================================
def main() -> int:
    args = parse_args()

    # ----- إعداد التسجيل أولاً -----
    setup_logger(
        level=args.log_level,
        log_to_file=not args.no_file_log,
    )

    logger = get_logger("main")

    # ----- إنشاء المجلدات -----
    try:
        ensure_directories()
    except Exception as e:
        print(f"[X] Failed to create directories: {e}", file=sys.stderr)
        return 1

    # ----- طباعة Banner -----
    logger.info("=" * 60)
    logger.info("RecoverDataPhone Server v1.0.0")
    logger.info("=" * 60)
    logger.info(f"Host: {args.host}")
    logger.info(f"Port: {args.port}")
    logger.info(f"Log level: {args.log_level}")
    logger.info(f"File logging: {'disabled' if args.no_file_log else 'enabled'}")
    logger.info(f"CLI: {'disabled' if args.no_cli else 'enabled'}")
    logger.info("=" * 60)

    # ----- تشغيل -----
    try:
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
        return 0
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1

    return 0


# ============================================================
# 8. تشغيل
# ============================================================
if __name__ == "__main__":
    sys.exit(main())