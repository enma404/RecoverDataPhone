#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
============================================================
نقطة دخول سيرفر RecoverDataPhone.

المسؤوليات:
  1. تشغيل خادم WebSocket على 0.0.0.0:8765
  2. استقبال اتصالات تطبيقات الأندرويد
  3. تشغيل واجهة طرفية (CLI) للتحكم
  4. تمرير الأوامر بين الطرفية والعملاء

الاستخدام:
  $ python main.py
  أو
  $ python main.py --host 0.0.0.0 --port 8765

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
from core.websocket_server import WebSocketServer
from core.session_manager import SessionManager
from cli.terminal import TerminalCLI
from utils.logger import setup_logger

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
    return parser.parse_args()


# ============================================================
# 5. الدالة الرئيسية
# ============================================================
async def run_server(args: argparse.Namespace) -> None:
    logger = logging.getLogger("main")

    # -------- إنشاء مدير الجلسات --------
    session_manager = SessionManager()
    logger.info("SessionManager initialized")

    # -------- إنشاء خادم WebSocket --------
    server = WebSocketServer(
        host=args.host,
        port=args.port,
        session_manager=session_manager,
    )

    # -------- تشغيل الخادم --------
    await server.start()
    logger.info(f"WebSocket server started on ws://{args.host}:{args.port}")

    # -------- تشغيل الطرفية (اختياري) --------
    cli_task = None
    if not args.no_cli:
        cli = TerminalCLI(session_manager=session_manager)
        cli_task = asyncio.create_task(cli.run())
        logger.info("Terminal CLI started")

    # -------- انتظار إشارة الإيقاف --------
    stop_event = asyncio.Event()

    def _handle_signal(sig_name: str) -> None:
        logger.warning(f"Received signal: {sig_name}")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig.name)
        except NotImplementedError:
            # Windows لا يدعم add_signal_handler
            pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")

        # إيقاف الطرفية
        if cli_task and not cli_task.done():
            cli_task.cancel()
            try:
                await cli_task
            except asyncio.CancelledError:
                pass

        # إيقاف الخادم
        await server.stop()
        logger.info("Server stopped. Goodbye.")


# ============================================================
# 6. نقطة الدخول
# ============================================================
def main() -> int:
    args = parse_args()

    # إعداد التسجيل
    setup_logger(level=args.log_level)

    # إنشاء المجلدات المطلوبة
    (BASE_DIR / "storage" / "received").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "storage" / "temp").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)

    # تشغيل
    try:
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
        return 0
    except Exception as e:
        print(f"[X] Fatal error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())