#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auth.py
============================================================
نظام التحقق من الاشتراك الشهري.

المسؤوليات:
  1. التحقق من مفتاح الترخيص (License Key) للعملاء.
  2. إدارة مدة الاشتراك (يبدأ عند أول استخدام).
  3. ربط المفتاح بمعرف الجهاز (لمنع مشاركة المفاتيح).
  4. تخزين سجل الاشتراكات في ملف JSON.
  5. السماح بوضع "تجريبي" (Trial) لمدة محددة.

آلية العمل:
  - كل عميل يحمل مفتاح ترخيص (مثل "RDP-XXXX-XXXX-XXXX").
  - عند أول اتصال، يُربط المفتاح بمعرّف الجهاز.
  - عند كل اتصال لاحق، يُتحقق من:
      1. صحة المفتاح.
      2. أن الجهاز مطابق.
      3. أن الاشتراك لم ينته.

تخزين:
  storage/licenses.json
    {
      "RDP-XXXX-XXXX-XXXX": {
        "device_id": "abc123",
        "created_at": 1700000000,
        "expires_at": 1702592000,
        "duration_days": 30,
        "active": true,
        "last_seen": 1700000000
      },
      ...
    }
============================================================
"""

import json
import logging
import secrets
import string
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("auth")

# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
LICENSES_FILE = BASE_DIR / "storage" / "licenses.json"

# مدة الاشتراك الافتراضية (بالأيام)
DEFAULT_DURATION_DAYS = 30

# بادئة مفاتيح الترخيص
KEY_PREFIX = "RDP"

# طول كل مجموعة في المفتاح (مثل: XXXX)
KEY_GROUP_LENGTH = 4
KEY_GROUPS = 3  # عدد المجموعات

# فترة السماح (بالثواني) - إذا انتهى الاشتراك، نسمح بفترة إضافية بسيطة
GRACE_PERIOD_SECONDS = 24 * 3600  # يوم واحد


# ============================================================
# 2. نموذج الترخيص (Dataclass)
# ============================================================
@dataclass
class License:
    """يمثّل ترخيص عميل واحد."""
    key: str
    device_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    duration_days: int = DEFAULT_DURATION_DAYS
    active: bool = True
    last_seen: float = 0.0
    notes: str = ""

    # ---------- خصائص ----------
    @property
    def is_expired(self) -> bool:
        """هل انتهى الاشتراك؟"""
        if self.expires_at <= 0:
            return False  # لم يُنشَط بعد
        return time.time() > (self.expires_at + GRACE_PERIOD_SECONDS)

    @property
    def is_activated(self) -> bool:
        """هل رُبط بجهاز؟"""
        return bool(self.device_id)

    @property
    def days_remaining(self) -> int:
        """عدد الأيام المتبقية."""
        if self.expires_at <= 0:
            return self.duration_days
        remaining = self.expires_at - time.time()
        return max(0, int(remaining / 86400))

    @property
    def is_valid(self) -> bool:
        """هل الترخيص صالح للاستخدام؟"""
        return self.active and not self.is_expired


# ============================================================
# 3. نتيجة التحقق
# ============================================================
@dataclass
class AuthResult:
    """نتيجة عملية التحقق."""
    success: bool
    reason: str = ""
    license: Optional[License] = None

    @classmethod
    def ok(cls, lic: License, reason: str = "OK") -> "AuthResult":
        return cls(success=True, reason=reason, license=lic)

    @classmethod
    def fail(cls, reason: str) -> "AuthResult":
        return cls(success=False, reason=reason, license=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "reason": self.reason,
            "license": asdict(self.license) if self.license else None,
        }


# ============================================================
# 4. AuthManager
# ============================================================
class AuthManager:
    """
    مدير الترخيص والتحقق.
    """

    def __init__(self, licenses_file: Path = LICENSES_FILE):
        self.licenses_file = Path(licenses_file)
        self.licenses: Dict[str, License] = {}

        # تحميل الملفات
        self._ensure_storage()
        self._load()

    # ========================================================
    # 4.1 إدارة التخزين
    # ========================================================
    def _ensure_storage(self) -> None:
        """إنشاء مجلد storage إذا لم يكن موجوداً."""
        self.licenses_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.licenses_file.exists():
            self._save()

    def _load(self) -> None:
        """تحميل التراخيص من الملف."""
        try:
            with open(self.licenses_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, lic_data in data.items():
                self.licenses[key] = License(
                    key=key,
                    device_id=lic_data.get("device_id", ""),
                    created_at=lic_data.get("created_at", 0.0),
                    expires_at=lic_data.get("expires_at", 0.0),
                    duration_days=lic_data.get("duration_days", DEFAULT_DURATION_DAYS),
                    active=lic_data.get("active", True),
                    last_seen=lic_data.get("last_seen", 0.0),
                    notes=lic_data.get("notes", ""),
                )
            logger.info(f"Loaded {len(self.licenses)} license(s)")

        except FileNotFoundError:
            logger.info("No licenses file found, starting fresh")
            self.licenses = {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid licenses file: {e}")
            self.licenses = {}

    def _save(self) -> None:
        """حفظ التراخيص إلى الملف."""
        try:
            data = {key: asdict(lic) for key, lic in self.licenses.items()}
            # كتابة ذرّية (اكتب في ملف مؤقت ثم انقل)
            tmp = self.licenses_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self.licenses_file)
        except Exception as e:
            logger.exception(f"Failed to save licenses: {e}")

    # ========================================================
    # 4.2 توليد المفاتيح
    # ========================================================
    @staticmethod
    def generate_key() -> str:
        """
        توليد مفتاح ترخيص جديد.
        الصيغة: RDP-XXXX-XXXX-XXXX
        """
        alphabet = string.ascii_uppercase + string.digits
        groups = []
        for _ in range(KEY_GROUPS):
            group = "".join(secrets.choice(alphabet) for _ in range(KEY_GROUP_LENGTH))
            groups.append(group)
        return f"{KEY_PREFIX}-" + "-".join(groups)

    # ========================================================
    # 4.3 إنشاء ترخيص جديد
    # ========================================================
    def create_license(
        self,
        duration_days: int = DEFAULT_DURATION_DAYS,
        notes: str = "",
        custom_key: Optional[str] = None,
    ) -> License:
        """
        إنشاء ترخيص جديد (لم يُنشَط بعد).

        :param duration_days: مدة الاشتراك بالأيام
        :param notes: ملاحظات (اسم العميل، إلخ)
        :param custom_key: مفتاح مخصص (اختياري)
        :return: كائن الترخيص
        """
        # توليد مفتاح فريد
        if custom_key:
            key = custom_key.strip().upper()
            if key in self.licenses:
                raise ValueError(f"License already exists: {key}")
        else:
            key = self.generate_key()
            while key in self.licenses:
                key = self.generate_key()

        lic = License(
            key=key,
            device_id="",
            created_at=time.time(),
            expires_at=0.0,  # يُحسب عند أول تنشيط
            duration_days=duration_days,
            active=True,
            last_seen=0.0,
            notes=notes,
        )

        self.licenses[key] = lic
        self._save()

        logger.info(f"License created: {key} ({duration_days} days)")
        return lic

    # ========================================================
    # 4.4 التحقق من ترخيص
    # ========================================================
    def verify(self, key: str, device_id: str) -> AuthResult:
        """
        التحقق من ترخيص لمفتاح وجهاز معينين.

        الخطوات:
          1. المفتاح موجود؟
          2. الترخيص نشط؟
          3. الجهاز مطابق؟
          4. الاشتراك لم ينته؟
          5. إذا لم يكن مُنشَّطاً، فعّله الآن.

        :param key: مفتاح الترخيص
        :param device_id: معرّف الجهاز
        :return: AuthResult
        """
        key = (key or "").strip().upper()
        device_id = (device_id or "").strip()

        # 1. المفتاح موجود؟
        lic = self.licenses.get(key)
        if lic is None:
            logger.warning(f"License not found: {key}")
            return AuthResult.fail("License not found")

        # 2. نشط؟
        if not lic.active:
            logger.warning(f"License disabled: {key}")
            return AuthResult.fail("License disabled")

        # 3. الجهاز
        if not lic.is_activated:
            # ----- أول تنشيط -----
            if not device_id:
                return AuthResult.fail("Missing device_id for activation")

            lic.device_id = device_id
            lic.expires_at = time.time() + (lic.duration_days * 86400)
            lic.last_seen = time.time()
            self._save()

            logger.info(
                f"License activated: {key} → device {device_id} "
                f"({lic.duration_days} days)"
            )
            return AuthResult.ok(lic, "Activated")

        # ----- التحقق من مطابقة الجهاز -----
        if lic.device_id != device_id:
            logger.warning(
                f"Device mismatch for {key}: "
                f"expected {lic.device_id}, got {device_id}"
            )
            return AuthResult.fail("Device mismatch (license bound to another device)")

        # 4. انتهى؟
        if lic.is_expired:
            logger.warning(f"License expired: {key}")
            return AuthResult.fail("License expired")

        # ----- تحديث آخر نشاط -----
        lic.last_seen = time.time()
        self._save()

        logger.info(f"License verified: {key} (device {device_id})")
        return AuthResult.ok(lic)

    # ========================================================
    # 4.5 إدارة التراخيص
    # ========================================================
    def get_license(self, key: str) -> Optional[License]:
        """إرجاع ترخيص بالمعرف."""
        return self.licenses.get((key or "").strip().upper())

    def list_licenses(self) -> Dict[str, License]:
        """إرجاع جميع التراخيص."""
        return dict(self.licenses)

    def set_active(self, key: str, active: bool) -> bool:
        """تفعيل/تعطيل ترخيص."""
        lic = self.get_license(key)
        if lic is None:
            return False
        lic.active = active
        self._save()
        logger.info(f"License {key} active={active}")
        return True

    def extend_license(self, key: str, days: int) -> bool:
        """تمديد ترخيص بعدد أيام."""
        lic = self.get_license(key)
        if lic is None:
            return False

        if lic.expires_at <= 0:
            # لم يُنشَط بعد → زيادة المدة الأصلية
            lic.duration_days += days
        else:
            # نشط → أضف الأيام على تاريخ الانتهاء
            lic.expires_at += days * 86400

        self._save()
        logger.info(f"License {key} extended by {days} days")
        return True

    def delete_license(self, key: str) -> bool:
        """حذف ترخيص."""
        key = (key or "").strip().upper()
        if key not in self.licenses:
            return False
        del self.licenses[key]
        self._save()
        logger.info(f"License deleted: {key}")
        return True

    def unbind_device(self, key: str) -> bool:
        """فصل الترخيص عن الجهاز (للسماح بإعادة التنشيط)."""
        lic = self.get_license(key)
        if lic is None:
            return False
        lic.device_id = ""
        lic.expires_at = 0.0
        self._save()
        logger.info(f"License unbound: {key}")
        return True

    # ========================================================
    # 4.6 إحصائيات
    # ========================================================
    def stats(self) -> Dict[str, int]:
        """إحصائيات عامة."""
        total = len(self.licenses)
        active = sum(1 for lic in self.licenses.values() if lic.is_valid)
        expired = sum(1 for lic in self.licenses.values() if lic.is_expired)
        unactivated = sum(1 for lic in self.licenses.values() if not lic.is_activated)

        return {
            "total": total,
            "active": active,
            "expired": expired,
            "unactivated": unactivated,
        }

    def find_expired(self) -> list[License]:
        """إرجاع قائمة التراخيص المنتهية."""
        return [lic for lic in self.licenses.values() if lic.is_expired]

    def find_expiring_soon(self, days: int = 3) -> list[License]:
        """إرجاع التراخيص التي ستنتهي خلال عدد أيام."""
        threshold = time.time() + days * 86400
        return [
            lic for lic in self.licenses.values()
            if lic.is_activated
            and not lic.is_expired
            and 0 < lic.expires_at <= threshold
        ]

    # ========================================================
    # 4.7 دعم الوضع التجريبي
    # ========================================================
    def create_trial(self, device_id: str, days: int = 3, notes: str = "Trial") -> License:
        """
        إنشاء ترخيص تجريبي لجهاز محدد.
        """
        lic = self.create_license(duration_days=days, notes=notes)
        lic.device_id = device_id
        lic.expires_at = time.time() + days * 86400
        lic.last_seen = time.time()
        self._save()
        logger.info(f"Trial created for device {device_id}: {days} days")
        return lic

    # ========================================================
    # 4.8 أدوات مساعدة
    # ========================================================
    @staticmethod
    def is_valid_key_format(key: str) -> bool:
        """
        التحقق من صيغة المفتاح.
        الصيغة المتوقعة: RDP-XXXX-XXXX-XXXX
        """
        if not key:
            return False
        parts = key.strip().upper().split("-")
        if len(parts) != (1 + KEY_GROUPS):
            return False
        if parts[0] != KEY_PREFIX:
            return False
        for group in parts[1:]:
            if len(group) != KEY_GROUP_LENGTH:
                return False
            if not all(c in (string.ascii_uppercase + string.digits) for c in group):
                return False
        return True


# ============================================================
# 5. Singleton (للاستخدام العام)
# ============================================================
_default_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """إرجاع المدير الافتراضي (Singleton)."""
    global _default_manager
    if _default_manager is None:
        _default_manager = AuthManager()
    return _default_manager