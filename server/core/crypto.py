#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto.py
============================================================
طبقة التشفير للقناة بين التطبيق والسيرفر.

المسؤوليات:
  1. تشفير/فك تشفير الرسائل باستخدام AES-256-GCM.
  2. توليد وتبادل مفاتيح الجلسة (Session Keys).
  3. تشفير المفاتيح باستخدام RSA-OAEP.
  4. توقيع الرسائل باستخدام HMAC (اختياري).

النموذج الأمني (Security Model):
  - كل جلسة لها مفتاح متماثل (Symmetric Key) فريد.
  - يُتبادل المفتاح عبر RSA (لا يُرسل كنص صريح).
  - كل رسالة تُشفَّر بـ AES-256-GCM (يوفر سرية + تحقق).
  - كل رسالة تحمل IV عشوائي (Nonce) فريد.

الاستخدام:
  # على السيرفر
  keypair = RSAKeyPair.generate()
  session_key = SessionKey.generate()
  encrypted = session_key.encrypt("secret message")
  decrypted = session_key.decrypt(encrypted)

  # تبادل المفتاح
  wrapped = keypair.wrap_key(session_key.raw_key)
  unwrapped = keypair.unwrap_key(wrapped)

  # من جانب التطبيق (Java)
  # يستخدم نفس الصيغة: Base64(IV || ciphertext || tag)

التوافق مع Java:
  - AES/GCM/NoPadding
  - RSA/ECB/OAEPWithSHA-256AndMGF1Padding
  - Base64 (RFC 4648)
============================================================
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.exceptions import InvalidTag

logger = logging.getLogger("crypto")


# ============================================================
# 1. الإعدادات
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
KEYS_DIR = BASE_DIR / "storage" / "keys"

# أحجام المفاتيح
AES_KEY_SIZE = 32       # 256 بت
AES_IV_SIZE = 12        # 96 بت (موصى به لـ GCM)
AES_TAG_SIZE = 16       # 128 بت
RSA_KEY_SIZE = 2048     # 2048 بت
RSA_PUBLIC_EXPONENT = 65537

# الملفات
RSA_PRIVATE_KEY_FILE = KEYS_DIR / "server_private.pem"
RSA_PUBLIC_KEY_FILE = KEYS_DIR / "server_public.pem"


# ============================================================
# 2. استثناءات
# ============================================================
class CryptoError(Exception):
    """خطأ عام في التشفير."""
    pass


class DecryptionError(CryptoError):
    """فشل فك التشفير (بيانات مشوّهة أو مفتاح خاطئ)."""
    pass


class KeyExchangeError(CryptoError):
    """فشل تبادل المفاتيح."""
    pass


# ============================================================
# 3. SessionKey - مفتاح جلسة متماثل
# ============================================================
class SessionKey:
    """
    مفتاح متماثل لجلسة واحدة.
    يُستخدم AES-256-GCM لتشفير الرسائل.
    """

    def __init__(self, raw_key: Optional[bytes] = None):
        """
        :param raw_key: مفتاح 32 بايت (إذا لم يُعطَ، يُولَّد عشوائياً)
        """
        if raw_key is None:
            self.raw_key = os.urandom(AES_KEY_SIZE)
        else:
            if len(raw_key) != AES_KEY_SIZE:
                raise ValueError(f"Key must be {AES_KEY_SIZE} bytes")
            self.raw_key = raw_key

        self._aesgcm = AESGCM(self.raw_key)
        self.created_at = time.time()

    # --------------------------------------------------------
    # 3.1 التشفير
    # --------------------------------------------------------
    def encrypt(self, plaintext: str, aad: Optional[bytes] = None) -> str:
        """
        تشفير نص واسترجاعه كـ Base64.

        :param plaintext: النص الأصلي
        :param aad: بيانات إضافية موثّقة (اختياري)
        :return: Base64(IV || ciphertext || tag)
        """
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be str")

        try:
            # IV عشوائي لكل رسالة
            iv = os.urandom(AES_IV_SIZE)

            # التشفير
            data = plaintext.encode("utf-8")
            ciphertext = self._aesgcm.encrypt(iv, data, aad)

            # الصيغة: IV (12) || ciphertext (variable)
            # ciphertext من AESGCM يتضمن tag في النهاية
            combined = iv + ciphertext

            return base64.b64encode(combined).decode("ascii")

        except Exception as e:
            logger.exception(f"Encryption failed: {e}")
            raise CryptoError(f"Encryption failed: {e}") from e

    def encrypt_bytes(self, plaintext: bytes, aad: Optional[bytes] = None) -> bytes:
        """
        تشفير بايتات وإرجاعها كبايتات (بدون Base64).
        مفيد للملفات.
        """
        try:
            iv = os.urandom(AES_IV_SIZE)
            ciphertext = self._aesgcm.encrypt(iv, plaintext, aad)
            return iv + ciphertext
        except Exception as e:
            logger.exception(f"Encryption (bytes) failed: {e}")
            raise CryptoError(f"Encryption failed: {e}") from e

    # --------------------------------------------------------
    # 3.2 فك التشفير
    # --------------------------------------------------------
    def decrypt(self, encrypted_b64: str, aad: Optional[bytes] = None) -> str:
        """
        فك تشفير نص من Base64.

        :param encrypted_b64: Base64(IV || ciphertext || tag)
        :param aad: نفس AAD المستخدم في التشفير
        :return: النص الأصلي
        """
        if not isinstance(encrypted_b64, str):
            raise TypeError("encrypted_b64 must be str")

        try:
            combined = base64.b64decode(encrypted_b64)
        except Exception as e:
            raise DecryptionError("Invalid Base64") from e

        if len(combined) < (AES_IV_SIZE + AES_TAG_SIZE):
            raise DecryptionError("Ciphertext too short")

        try:
            iv = combined[:AES_IV_SIZE]
            ciphertext = combined[AES_IV_SIZE:]

            data = self._aesgcm.decrypt(iv, ciphertext, aad)
            return data.decode("utf-8")

        except InvalidTag:
            raise DecryptionError("Invalid authentication tag (wrong key or tampered data)")
        except Exception as e:
            logger.exception(f"Decryption failed: {e}")
            raise DecryptionError(f"Decryption failed: {e}") from e

    def decrypt_bytes(self, encrypted: bytes, aad: Optional[bytes] = None) -> bytes:
        """فك تشفير بايتات."""
        if len(encrypted) < (AES_IV_SIZE + AES_TAG_SIZE):
            raise DecryptionError("Ciphertext too short")

        try:
            iv = encrypted[:AES_IV_SIZE]
            ciphertext = encrypted[AES_IV_SIZE:]
            return self._aesgcm.decrypt(iv, ciphertext, aad)
        except InvalidTag:
            raise DecryptionError("Invalid authentication tag")
        except Exception as e:
            raise DecryptionError(f"Decryption failed: {e}") from e

    # --------------------------------------------------------
    # 3.3 تشفير JSON
    # --------------------------------------------------------
    def encrypt_json(self, data: Dict[str, Any], aad: Optional[bytes] = None) -> str:
        """تشفير قاموس كـ JSON ثم Base64."""
        try:
            plaintext = json.dumps(data, ensure_ascii=False)
        except Exception as e:
            raise CryptoError(f"JSON serialization failed: {e}") from e
        return self.encrypt(plaintext, aad)

    def decrypt_json(self, encrypted_b64: str, aad: Optional[bytes] = None) -> Dict[str, Any]:
        """فك تشفير JSON."""
        plaintext = self.decrypt(encrypted_b64, aad)
        try:
            return json.loads(plaintext)
        except Exception as e:
            raise DecryptionError(f"JSON parse failed: {e}") from e

    # --------------------------------------------------------
    # 3.4 التصدير / الاستيراد
    # --------------------------------------------------------
    def export_b64(self) -> str:
        """تصدير المفتاح كـ Base64."""
        return base64.b64encode(self.raw_key).decode("ascii")

    @classmethod
    def import_b64(cls, key_b64: str) -> "SessionKey":
        """استيراد مفتاح من Base64."""
        try:
            raw = base64.b64decode(key_b64)
            return cls(raw)
        except Exception as e:
            raise CryptoError(f"Invalid key format: {e}") from e

    @classmethod
    def generate(cls) -> "SessionKey":
        """توليد مفتاح جديد."""
        return cls()


# ============================================================
# 4. RSAKeyPair - زوج مفاتيح RSA
# ============================================================
class RSAKeyPair:
    """
    زوج مفاتيح RSA لتبادل مفاتيح الجلسة.
    """

    def __init__(self, private_key: rsa.RSAPrivateKey):
        self.private_key = private_key
        self.public_key = private_key.public_key()

    # --------------------------------------------------------
    # 4.1 التوليد
    # --------------------------------------------------------
    @classmethod
    def generate(cls, key_size: int = RSA_KEY_SIZE) -> "RSAKeyPair":
        """توليد زوج مفاتيح جديد (قد يستغرق ثانية)."""
        logger.info(f"Generating RSA-{key_size} key pair...")
        private = rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT,
            key_size=key_size,
        )
        logger.info("RSA key pair generated")
        return cls(private)

    # --------------------------------------------------------
    # 4.2 تشفير/فك تشفير مفتاح الجلسة
    # --------------------------------------------------------
    def wrap_key(self, session_key_bytes: bytes) -> str:
        """
        تشفير مفتاح جلسة باستخدام المفتاح العام.
        (يُستخدم على جانب العميل أو السيرفر لإرسال المفتاح).

        :param session_key_bytes: المفتاح الخام (32 بايت عادةً)
        :return: Base64 للناتج
        """
        if not isinstance(session_key_bytes, bytes):
            raise TypeError("session_key_bytes must be bytes")

        try:
            encrypted = self.public_key.encrypt(
                session_key_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            return base64.b64encode(encrypted).decode("ascii")
        except Exception as e:
            logger.exception(f"Key wrap failed: {e}")
            raise KeyExchangeError(f"Key wrap failed: {e}") from e

    def unwrap_key(self, wrapped_b64: str) -> bytes:
        """
        فك تشفير مفتاح جلسة باستخدام المفتاح الخاص.

        :param wrapped_b64: Base64 للناتج
        :return: المفتاح الخام
        """
        if not isinstance(wrapped_b64, str):
            raise TypeError("wrapped_b64 must be str")

        try:
            wrapped = base64.b64decode(wrapped_b64)
        except Exception as e:
            raise KeyExchangeError("Invalid Base64") from e

        try:
            decrypted = self.private_key.decrypt(
                wrapped,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            return decrypted
        except Exception as e:
            logger.exception(f"Key unwrap failed: {e}")
            raise KeyExchangeError(f"Key unwrap failed: {e}") from e

    # --------------------------------------------------------
    # 4.3 التوقيع / التحقق
    # --------------------------------------------------------
    def sign(self, data: bytes) -> str:
        """
        توقيع بيانات باستخدام المفتاح الخاص.
        (نادراً ما نستخدم هذا، لكنه مفيد للتوثيق).
        """
        try:
            signature = self.private_key.sign(
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return base64.b64encode(signature).decode("ascii")
        except Exception as e:
            raise CryptoError(f"Sign failed: {e}") from e

    def verify(self, data: bytes, signature_b64: str) -> bool:
        """التحقق من توقيع."""
        try:
            signature = base64.b64decode(signature_b64)
            self.public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    # --------------------------------------------------------
    # 4.4 التصدير / الاستيراد
    # --------------------------------------------------------
    def export_private_pem(self, password: Optional[bytes] = None) -> bytes:
        """تصدير المفتاح الخاص كـ PEM."""
        enc = serialization.NoEncryption()
        if password:
            enc = serialization.BestAvailableEncryption(password)

        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=enc,
        )

    def export_public_pem(self) -> bytes:
        """تصدير المفتاح العام كـ PEM."""
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def export_public_b64(self) -> str:
        """تصدير المفتاح العام كـ Base64 (بدون PEM headers)."""
        der = self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode("ascii")

    @classmethod
    def load_private_pem(cls, pem_data: bytes, password: Optional[bytes] = None) -> "RSAKeyPair":
        """تحميل من PEM."""
        private = serialization.load_pem_private_key(pem_data, password=password)
        if not isinstance(private, rsa.RSAPrivateKey):
            raise CryptoError("Not an RSA private key")
        return cls(private)

    # --------------------------------------------------------
    # 4.5 التخزين على القرص
    # --------------------------------------------------------
    def save_to_disk(self, force: bool = False) -> None:
        """
        حفظ المفاتيح على القرص.
        لا يحفظ إذا كانت الملفات موجودة إلا إذا force=True.
        """
        KEYS_DIR.mkdir(parents=True, exist_ok=True)

        if RSA_PRIVATE_KEY_FILE.exists() and not force:
            logger.info("RSA keys already exist, skipping save")
            return

        # حفظ المفتاح الخاص
        RSA_PRIVATE_KEY_FILE.write_bytes(self.export_private_pem())
        RSA_PRIVATE_KEY_FILE.chmod(0o600)  # قراءة/كتابة للمالك فقط

        # حفظ المفتاح العام
        RSA_PUBLIC_KEY_FILE.write_bytes(self.export_public_pem())
        RSA_PUBLIC_KEY_FILE.chmod(0o644)

        logger.info(f"RSA keys saved to {KEYS_DIR}")

    @classmethod
    def load_or_create(cls, password: Optional[bytes] = None) -> "RSAKeyPair":
        """
        تحميل المفاتيح من القرص، أو إنشاء جديدة إذا لم تكن موجودة.
        """
        if RSA_PRIVATE_KEY_FILE.exists():
            try:
                logger.info("Loading RSA keys from disk")
                pem = RSA_PRIVATE_KEY_FILE.read_bytes()
                return cls.load_private_pem(pem, password=password)
            except Exception as e:
                logger.error(f"Failed to load RSA keys: {e}, regenerating...")

        # إنشاء جديدة
        keypair = cls.generate()
        keypair.save_to_disk(force=True)
        return keypair


# ============================================================
# 5. HMAC - للتحقق من سلامة الرسائل (اختياري)
# ============================================================
def compute_hmac(key: bytes, message: bytes) -> str:
    """
    حساب HMAC-SHA256.
    """
    mac = hmac.new(key, message, hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")


def verify_hmac(key: bytes, message: bytes, signature_b64: str) -> bool:
    """التحقق من HMAC بشكل آمن (Constant-Time)."""
    try:
        expected = hmac.new(key, message, hashlib.sha256).digest()
        provided = base64.b64decode(signature_b64)
        return hmac.compare_digest(expected, provided)
    except Exception:
        return False


# ============================================================
# 6. أدوات مساعدة
# ============================================================
def derive_key(password: str, salt: Optional[bytes] = None,
               length: int = 32) -> Tuple[bytes, bytes]:
    """
    اشتقاق مفتاح من كلمة سر باستخدام HKDF-SHA256.

    :param password: كلمة السر
    :param salt: الملح (يُولَّد إذا لم يُعطَ)
    :param length: طول المفتاح الناتج
    :return: (المفتاح، الملح)
    """
    if salt is None:
        salt = os.urandom(16)

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=b"RecoverDataPhone",
    )
    key = hkdf.derive(password.encode("utf-8"))
    return key, salt


def generate_nonce(size: int = 16) -> str:
    """توليد Nonce عشوائي (Base64)."""
    return base64.b64encode(os.urandom(size)).decode("ascii")


def sha256_hex(data: bytes) -> str:
    """حساب SHA-256 (Hex)."""
    return hashlib.sha256(data).hexdigest()


def safe_compare(a: str, b: str) -> bool:
    """مقارنة آمنة (Constant-Time) للنصوص."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ============================================================
# 7. Singleton للتشفير العام
# ============================================================
_default_keypair: Optional[RSAKeyPair] = None


def get_keypair() -> RSAKeyPair:
    """إرجاع زوج مفاتيح السيرفر (يُحمَّل مرة واحدة)."""
    global _default_keypair
    if _default_keypair is None:
        _default_keypair = RSAKeyPair.load_or_create()
    return _default_keypair