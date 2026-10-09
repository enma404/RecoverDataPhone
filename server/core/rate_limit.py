#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rate_limit.py
============================================================
نظام تحديد معدل الطلبات (Rate Limiting).

المسؤوليات:
  1. منع إساءة الاستخدام (DoS، سحب بيانات ضخم).
  2. تحديد سقف لكل عميل حسب فئة الأمر.
  3. حظر مؤقت للعملاء الذين يتجاوزون الحدود.
  4. تنظيف تلقائي للسجلات القديمة.

المفهوم:
  - كل عميل له "دلو" (Bucket) لكل فئة أوامر.
  - كل طلب يستهلك من الدلو.
  - عندما ينفد الدلو، يُرفض الطلب حتى يتجدد.
  - آلية "Token Bucket": تُضاف رموز بمعدل ثابت.

الفئات:
  - AUTH   : 5 طلبات / دقيقة
  - DATA   : 10 طلبات / دقيقة (GET_PHOTOS, GET_VIDEOS...)
  - ACCESS : 60 طلب / دقيقة (CLICK, SWIPE - سريعة)
  - QUERY  : 30 طلب / دقيقة (LIST_DIR, PING)
  - ADMIN  : 5 طلبات / دقيقة (STOP)

يعتمد على:
  - utils.logger

الاستخدام:
  limiter = RateLimiter()
  allowed, reason = limiter.check(client_id, "GET_PHOTOS")
  if not allowed:
      print(f"مرفوض: {reason}")
============================================================
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from utils.logger import get_logger, mask_secret

logger = get_logger("rate_limit")


# ============================================================
# 1. الإعدادات
# ============================================================
# (الحد الأقصى للطلبات، نافذة التجديد بالثواني)
RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    "AUTH":   (5, 60),    # 5 طلبات / دقيقة
    "DATA":   (10, 60),   # 10 طلبات / دقيقة (photos, videos, files)
    "ACCESS": (60, 60),   # 60 طلب / دقيقة (accessibility actions)
    "QUERY":  (30, 60),   # 30 طلب / دقيقة (list, ping, info)
    "ADMIN":  (5, 60),    # 5 طلبات / دقيقة (stop, restart)
}

# فترة حظر مؤقت عند التجاوز المتكرر (ثواني)
BAN_DURATION = 300  # 5 دقائق

# عدد التجاوزات قبل الحظر
VIOLATIONS_BEFORE_BAN = 5

# فترة تنظيف السجلات القديمة (ثواني)
CLEANUP_INTERVAL = 600  # 10 دقائق

# عمر السجل قبل التنظيف (ثواني)
RECORD_TTL = 3600  # ساعة


# ============================================================
# 2. تصنيف الأوامر
# ============================================================
# كل أمر ينتمي إلى فئة
COMMAND_CATEGORIES: Dict[str, str] = {
    # AUTH
    "AUTH": "AUTH",

    # DATA (عمليات ثقيلة)
    "GET_PHOTOS": "DATA",
    "GET_VIDEOS": "DATA",
    "GET_FILE": "DATA",
    "GET_CONTACTS": "DATA",
    "GET_SMS": "DATA",
    "GET_CALL_LOGS": "DATA",

    # ACCESS (accessibility - سريعة لكن كثيرة)
    "ACCESSIBILITY": "ACCESS",

    # QUERY (خفيفة)
    "PING": "QUERY",
    "LIST_DIR": "QUERY",
    "GET_DEVICE_INFO": "QUERY",

    # ADMIN
    "STOP": "ADMIN",
    "RESTART": "ADMIN",
    "DISCONNECT": "ADMIN",
}


# ============================================================
# 3. دلو الرموز (Token Bucket)
# ============================================================
@dataclass
class TokenBucket:
    """
    دلو رموز لفئة واحدة.
    """
    capacity: int          # السعة القصوى
    refill_rate: float     # معدل التجديد (رموز / ثانية)
    tokens: float = 0.0    # الرموز الحالية
    last_refill: float = 0.0
    total_requests: int = 0
    total_rejected: int = 0

    def __post_init__(self):
        # ابدأ ممتلئاً
        if self.last_refill == 0.0:
            self.last_refill = time.time()
            self.tokens = float(self.capacity)

    def _refill(self) -> None:
        """إضافة رموز جديدة حسب الوقت المنقضي."""
        now = time.time()
        elapsed = now - self.last_refill

        if elapsed <= 0:
            return

        new_tokens = elapsed * self.refill_rate
        self.tokens = min(float(self.capacity), self.tokens + new_tokens)
        self.last_refill = now

    def consume(self, amount: int = 1) -> bool:
        """
        محاولة استهلاك رموز.
        :return: True إذا نجحت (توجد رموز كافية)
        """
        self._refill()

        if self.tokens >= amount:
            self.tokens -= amount
            self.total_requests += 1
            return True

        self.total_rejected += 1
        return False

    @property
    def available(self) -> int:
        """عدد الرموز المتاحة (بعد التجديد)."""
        self._refill()
        return int(self.tokens)


# ============================================================
# 4. سجل العميل
# ============================================================
@dataclass
class ClientRecord:
    """
    سجل عميل واحد: دلاء لكل فئة + حالة الحظر.
    """
    client_id: str
    buckets: Dict[str, TokenBucket] = field(default_factory=dict)
    violations: int = 0
    banned_until: float = 0.0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # --------------------------------------------------------
    def get_bucket(self, category: str) -> TokenBucket:
        """إرجاع أو إنشاء دلو لفئة."""
        if category not in self.buckets:
            limit, window = RATE_LIMITS.get(category, (30, 60))
            # refill_rate = السعة / النافذة
            refill_rate = limit / float(window) if window > 0 else 1.0
            self.buckets[category] = TokenBucket(
                capacity=limit,
                refill_rate=refill_rate,
            )
        return self.buckets[category]

    # --------------------------------------------------------
    @property
    def is_banned(self) -> bool:
        """هل العميل محظور؟"""
        if self.banned_until <= 0:
            return False
        if time.time() >= self.banned_until:
            # انتهى الحظر
            self.banned_until = 0.0
            self.violations = 0
            return False
        return True

    @property
    def ban_remaining(self) -> int:
        """الوقت المتبقي للحظر (ثواني)."""
        if not self.is_banned:
            return 0
        return max(0, int(self.banned_until - time.time()))

    # --------------------------------------------------------
    def register_violation(self) -> None:
        """تسجيل مخالفة. قد يؤدي إلى حظر."""
        self.violations += 1

        if self.violations >= VIOLATIONS_BEFORE_BAN:
            self.banned_until = time.time() + BAN_DURATION
            logger.warning(
                f"[{self.client_id}] BANNED for {BAN_DURATION}s "
                f"({self.violations} violations)"
            )

    # --------------------------------------------------------
    def reset_violations(self) -> None:
        """إعادة تعيين عدد المخالفات."""
        self.violations = 0


# ============================================================
# 5. RateLimiter
# ============================================================
class RateLimiter:
    """
    مدير تحديد المعدل.
    """

    def __init__(self):
        self._records: Dict[str, ClientRecord] = {}
        self._last_cleanup: float = time.time()

    # ========================================================
    # 5.1 الفحص الرئيسي
    # ========================================================
    def check(
        self,
        client_id: str,
        command: str,
        amount: int = 1,
    ) -> Tuple[bool, str]:
        """
        فحص إذا كان العميل مسموحاً له بتنفيذ الأمر.

        :param client_id: معرف العميل
        :param command: اسم الأمر
        :param amount: عدد الرموز المطلوبة (عادة 1)
        :return: (allowed, reason)
            allowed: True إذا مسموح
            reason: "OK" أو رسالة الرفض
        """
        # ----- 1. فحص دوري للتنظيف -----
        self._maybe_cleanup()

        # ----- 2. الحصول على سجل العميل -----
        record = self._get_or_create(client_id)
        record.last_seen = time.time()

        # ----- 3. هل محظور؟ -----
        if record.is_banned:
            remaining = record.ban_remaining
            reason = f"Client banned for {remaining}s"
            logger.warning(f"[{client_id}] Rejected ({reason})")
            return False, reason

        # ----- 4. تحديد الفئة -----
        category = COMMAND_CATEGORIES.get(command.upper(), "QUERY")

        # ----- 5. الحصول على الدلو -----
        bucket = record.get_bucket(category)

        # ----- 6. محاولة الاستهلاك -----
        if bucket.consume(amount):
            return True, "OK"

        # ----- 7. مرفوض -----
        record.register_violation()
        available = bucket.available
        limit, window = RATE_LIMITS.get(category, (30, 60))
        reason = (
            f"Rate limit exceeded ({category}): "
            f"{available}/{limit} tokens available"
        )
        logger.warning(
            f"[{client_id}] {command} rejected: {reason}"
        )
        return False, reason

    # ========================================================
    # 5.2 إدارة السجلات
    # ========================================================
    def _get_or_create(self, client_id: str) -> ClientRecord:
        """إرجاع أو إنشاء سجل عميل."""
        if client_id not in self._records:
            self._records[client_id] = ClientRecord(client_id=client_id)
            logger.debug(f"New rate limit record: {client_id}")
        return self._records[client_id]

    def get_record(self, client_id: str) -> Optional[ClientRecord]:
        """إرجاع سجل عميل (أو None)."""
        return self._records.get(client_id)

    def remove_client(self, client_id: str) -> bool:
        """إزالة سجل عميل (عند الانقطاع)."""
        if client_id in self._records:
            del self._records[client_id]
            return True
        return False

    def clear(self) -> None:
        """مسح كل السجلات."""
        self._records.clear()
        logger.info("All rate limit records cleared")

    # ========================================================
    # 5.3 الحظر اليدوي (للإدارة)
    # ========================================================
    def ban(self, client_id: str, duration: int = BAN_DURATION) -> None:
        """حظر عميل يدوياً."""
        record = self._get_or_create(client_id)
        record.banned_until = time.time() + duration
        logger.warning(f"[{client_id}] Manually banned for {duration}s")

    def unban(self, client_id: str) -> bool:
        """رفع الحظر عن عميل."""
        record = self.get_record(client_id)
        if record is None:
            return False
        record.banned_until = 0.0
        record.violations = 0
        logger.info(f"[{client_id}] Unbanned")
        return True

    def is_banned(self, client_id: str) -> bool:
        """هل العميل محظور؟"""
        record = self.get_record(client_id)
        return record.is_banned if record else False

    # ========================================================
    # 5.4 التنظيف الدوري
    # ========================================================
    def _maybe_cleanup(self) -> None:
        """تنظيف السجلات القديمة (كل فترة)."""
        now = time.time()
        if (now - self._last_cleanup) < CLEANUP_INTERVAL:
            return

        self._last_cleanup = now
        removed = 0

        for client_id in list(self._records.keys()):
            record = self._records[client_id]
            # إذا لم يُرَ منذ RECORD_TTL، احذفه
            if (now - record.last_seen) > RECORD_TTL:
                del self._records[client_id]
                removed += 1

        if removed:
            logger.info(f"Cleaned {removed} old rate limit records")

    # ========================================================
    # 5.5 الإحصائيات
    # ========================================================
    def get_stats(self) -> Dict[str, any]:
        """إحصائيات عامة."""
        total_requests = 0
        total_rejected = 0
        banned_count = 0

        for record in self._records.values():
            if record.is_banned:
                banned_count += 1
            for bucket in record.buckets.values():
                total_requests += bucket.total_requests
                total_rejected += bucket.total_rejected

        return {
            "tracked_clients": len(self._records),
            "banned_clients": banned_count,
            "total_requests": total_requests,
            "total_rejected": total_rejected,
            "rejection_rate": (
                round(total_rejected / total_requests * 100, 2)
                if total_requests > 0 else 0.0
            ),
        }

    def get_client_status(self, client_id: str) -> Optional[Dict]:
        """حالة عميل مفصل."""
        record = self.get_record(client_id)
        if record is None:
            return None

        buckets_info = {}
        for category, bucket in record.buckets.items():
            limit, window = RATE_LIMITS.get(category, (30, 60))
            buckets_info[category] = {
                "available": bucket.available,
                "capacity": limit,
                "window": window,
                "requests": bucket.total_requests,
                "rejected": bucket.total_rejected,
            }

        return {
            "client_id": record.client_id,
            "is_banned": record.is_banned,
            "ban_remaining": record.ban_remaining,
            "violations": record.violations,
            "first_seen": record.first_seen,
            "last_seen": record.last_seen,
            "buckets": buckets_info,
        }

    def list_banned(self) -> list:
        """قائمة العملاء المحظورين."""
        return [
            cid for cid, rec in self._records.items()
            if rec.is_banned
        ]

    # ========================================================
    # 5.6 أدوات مساعدة
    # ========================================================
    @staticmethod
    def get_category(command: str) -> str:
        """إرجاع فئة أمر."""
        return COMMAND_CATEGORIES.get(command.upper(), "QUERY")

    @staticmethod
    def get_limit_for(command: str) -> Tuple[int, int]:
        """إرجاع (limit, window) لأمر."""
        category = RateLimiter.get_category(command)
        return RATE_LIMITS.get(category, (30, 60))

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"<RateLimiter clients={len(self._records)}>"


# ============================================================
# 6. Singleton
# ============================================================
_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """إرجاع المدير (Singleton)."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter