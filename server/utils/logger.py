#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py
============================================================
نظام تسجيل موحّد لسيرفر RecoverDataPhone.

المسؤوليات:
  1. إعداد logging بشكل موحّد لكل الوحدات.
  2. طباعة في الطرفية بألوان (إن كانت تدعم).
  3. تسجيل في ملف (اختياري).
  4. تدوير الملفات تلقائياً (Rotation) عند التجاوز.
  5. تشويش البيانات الحساسة.

المستويات:
  DEBUG   - تفاصيل دقيقة (للتطوير)
  INFO    - أحداث عامة (افتراضي)
  WARNING - تحذيرات
  ERROR   - أخطاء

الاستخدام:
  from utils.logger import setup_logger
  setup_logger(level="INFO")

  # في أي ملف آخر:
  import logging
  logger = logging.getLogger("my_module")
  logger.info("Hello")
============================================================
"""

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Optional


# ============================================================
# 1. الثوابت
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "server.log"

# الحد الأقصى لحجم الملف قبل التدوير (10 MB)
MAX_LOG_SIZE = 10 * 1024 * 1024

# عدد الملفات الاحتياطية
BACKUP_COUNT = 5

# اسم مسجل السجل الرئيسي
ROOT_LOGGER_NAME = "rdp"

# ============================================================
# 2. أكواد الألوان (ANSI)
# ============================================================
class Colors:
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


# ============================================================
# 3. مُنسّق الطرفية (ColoredFormatter)
# ============================================================
class ColoredFormatter(logging.Formatter):
    """
    مُنسّق يضيف ألوان حسب المستوى.
    """

    LEVEL_COLORS = {
        logging.DEBUG:    Colors.CYAN,
        logging.INFO:     Colors.GREEN,
        logging.WARNING:  Colors.YELLOW,
        logging.ERROR:    Colors.RED,
        logging.CRITICAL: Colors.RED + Colors.BOLD,
    }

    LEVEL_SHORT = {
        logging.DEBUG:    "DBG",
        logging.INFO:     "INF",
        logging.WARNING:  "WRN",
        logging.ERROR:    "ERR",
        logging.CRITICAL: "CRT",
    }

    def __init__(self, use_color: bool = True, use_short: bool = False):
        super().__init__()
        self.use_color = use_color and USE_COLOR
        self.use_short = use_short

    def format(self, record: logging.LogRecord) -> str:
        # ----- الوقت -----
        timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))

        # ----- المستوى -----
        if self.use_short:
            level = self.LEVEL_SHORT.get(record.levelno, "???")
        else:
            level = record.levelname

        # ----- الاسم -----
        name = record.name
        if name.startswith(ROOT_LOGGER_NAME + "."):
            name = name[len(ROOT_LOGGER_NAME) + 1:]

        # ----- الرسالة -----
        message = record.getMessage()

        # إضافة الاستثناء إن وُجد
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)

        # ----- التنسيق النهائي -----
        line = f"{timestamp} [{level:5}] {name}: {message}"

        # ----- الألوان -----
        if self.use_color:
            color = self.LEVEL_COLORS.get(record.levelno, "")
            reset = Colors.RESET
            line = f"{Colors.DIM}{timestamp}{reset} " \
                   f"[{color}{level:5}{reset}] " \
                   f"{Colors.BOLD}{name}{reset}: {message}"

        return line


# ============================================================
# 4. مُنسّق الملف (FileFormatter)
# ============================================================
class FileFormatter(logging.Formatter):
    """
    مُنسّق للملف (بدون ألوان، مع تاريخ كامل).
    """

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


# ============================================================
# 5. فلتر لتشويش البيانات الحساسة
# ============================================================
class SensitiveFilter(logging.Filter):
    """
    يمنع تسجيل البيانات الحساسة كاملة.
    """

    SENSITIVE_KEYS = [
        "password", "passwd", "pwd",
        "token", "secret", "api_key",
        "private_key", "license_key",
    ]

    MAX_VALUE_LENGTH = 200

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()

            # تشويش القيم الطويلة جداً (Base64, إلخ)
            if len(msg) > self.MAX_VALUE_LENGTH:
                # ابحث عن أنماط طويلة واقتطعها
                # (بسيط: اقتطع الرسالة كاملة إذا تجاوزت الحد)
                record.msg = msg[:self.MAX_VALUE_LENGTH] + \
                             f"... [+{len(msg) - self.MAX_VALUE_LENGTH} chars]"
                record.args = None

            # تشويش المفاتيح الحساسة
            for key in self.SENSITIVE_KEYS:
                if key in msg.lower():
                    # استبدل القيمة بعد = أو :
                    import re
                    pattern = rf'({key}["\']?\s*[:=]\s*)["\']?([^\s"\',}}]+)'
                    msg = re.sub(
                        pattern,
                        lambda m: m.group(1) + "***REDACTED***",
                        msg,
                        flags=re.IGNORECASE
                    )
                    record.msg = msg
                    record.args = None

        except Exception:
            pass  # لا نُفشل التسجيل بسبب الفلتر

        return True


# ============================================================
# 6. الدالة الرئيسية (setup_logger)
# ============================================================
_initialized = False


def setup_logger(
    level: str = "INFO",
    log_to_file: bool = True,
    use_colors: bool = True,
    use_short_level: bool = False,
) -> None:
    """
    إعداد نظام التسجيل.
    آمن للاستدعاء أكثر من مرة (idempotent).

    :param level: مستوى التسجيل (DEBUG/INFO/WARNING/ERROR)
    :param log_to_file: هل نسجّل في ملف؟
    :param use_colors: هل نستخدم ألوان في الطرفية؟
    :param use_short_level: هل نستخدم مستويات مختصرة (INF, WRN)؟
    """
    global _initialized

    # ----- المسجل الرئيسي -----
    root = logging.getLogger(ROOT_LOGGER_NAME)

    # إذا كان مُعدّاً مسبقاً، حدّث المستوى فقط
    if _initialized:
        try:
            root.setLevel(getattr(logging, level.upper(), logging.INFO))
        except Exception:
            pass
        return

    # ----- تعيين المستوى -----
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric_level)

    # ----- منع التكرار -----
    root.handlers.clear()
    root.propagate = False

    # ----- فلتر الحساسية -----
    sensitive_filter = SensitiveFilter()

    # ----- 1. Handler الطرفية (Console) -----
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(ColoredFormatter(
        use_color=use_colors,
        use_short=use_short_level,
    ))
    console.addFilter(sensitive_filter)
    root.addHandler(console)

    # ----- 2. Handler الملف (Rotating) -----
    if log_to_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                LOG_FILE,
                maxBytes=MAX_LOG_SIZE,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(FileFormatter())
            file_handler.addFilter(sensitive_filter)
            root.addHandler(file_handler)

        except Exception as e:
            # لا نُفشل التشغيل إذا تعذّر تسجيل الملف
            print(f"[logger] Warning: file logging disabled: {e}",
                  file=sys.stderr)

    # ----- 3. إخماد مكتبات الطرف الثالث -----
    for lib in ("websockets", "asyncio", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    _initialized = True


# ============================================================
# 7. دوال مساعدة
# ============================================================
def get_logger(name: str) -> logging.Logger:
    """
    إرجاع مسجل بادئة موحّدة.

    الاستخدام:
        logger = get_logger("my_module")
        logger.info("Hello")
    """
    if name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def set_level(level: str) -> None:
    """تغيير المستوى في الوقت الحقيقي."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger(ROOT_LOGGER_NAME).setLevel(numeric)


def get_log_file_path() -> Optional[Path]:
    """إرجاع مسار ملف السجل (أو None)."""
    if LOG_FILE.exists():
        return LOG_FILE
    return None


# ============================================================
# 8. أدوات مساعدة للتنسيق
# ============================================================
def format_size(bytes_size: int) -> str:
    """تنسيق الحجم."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    if bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    if bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """تنسيق المدة."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def mask_secret(value: Optional[str], visible: int = 4) -> str:
    """تشويش سر (مفتاح، token)."""
    if not value:
        return "None"
    s = str(value)
    if len(s) <= visible * 2:
        return "***"
    return s[:visible] + "***" + s[-visible:]


def truncate(text: Optional[str], max_len: int = 100) -> str:
    """اقتطاع نص طويل."""
    if text is None:
        return "None"
    s = str(text)
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... [+{len(s) - max_len} chars]"


# ============================================================
# 9. اختبار سريع
# ============================================================
if __name__ == "__main__":
    setup_logger(level="DEBUG", use_short_level=False)

    logger = get_logger("test")

    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")

    logger.info(f"Size: {format_size(2450000)}")
    logger.info(f"Duration: {format_duration(38420)}")
    logger.info(f"Secret: {mask_secret('RDP-A1B2-C3D4-E5F6')}")
    logger.info(f"Long: {truncate('A' * 500, 100)}")

    # اختبار الاستثناء
    try:
        raise ValueError("test error")
    except Exception as e:
        logger.exception("Caught exception")

    # اختبار الفلتر
    logger.info("Auth with password=secret123 and token=xyz")