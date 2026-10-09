<!-- ============================================================ -->
<!-- Logo & Title                                                 -->
<!-- ============================================================ -->
<div align="center">

<!-- ضع شعارك هنا (استبدل logo.png) -->
<img src="docs/assets/logo.png" alt="RecoverDataPhone Logo" width="160" height="160" />

# RecoverDataPhone

### استرجاع البيانات من الهواتف ذات الشاشات المكسورة

[![Build APK](https://github.com/YOUR_USERNAME/RecoverDataPhone/actions/workflows/build-apk.yml/badge.svg)](https://github.com/YOUR_USERNAME/RecoverDataPhone/actions/workflows/build-apk.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/YOUR_USERNAME/RecoverDataPhone/releases)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Android%2024%2B-green.svg)](https://developer.android.com)
[![Language](https://img.shields.io/badge/language-Java%20%7C%20Python-yellow.svg)](#-التقنيات-المستخدمة)

</div>

---

<!-- ============================================================ -->
<!-- Screenshots                                                  -->
<!-- ============================================================ -->
## 📸 لقطات الشاشة

<div align="center">

<!-- استبدل هذه الصور بصور تطبيقك الحقيقية -->
<table>
  <tr>
    <td align="center">
      <img src="docs/assets/screenshot-1.png" alt="الشاشة الرئيسية" width="220" />
      <br />
      <sub><b>الشاشة الرئيسية</b></sub>
    </td>
    <td align="center">
      <img src="docs/assets/screenshot-2.png" alt="الصلاحيات" width="220" />
      <br />
      <sub><b>إدارة الصلاحيات</b></sub>
    </td>
    <td align="center">
      <img src="docs/assets/screenshot-3.png" alt="الطرفية" width="220" />
      <br />
      <sub><b>واجهة الطرفية</b></sub>
    </td>
  </tr>
</table>

</div>

---

<!-- ============================================================ -->
<!-- Description                                                  -->
<!-- ============================================================ -->
## 📖 نظرة عامة

**RecoverDataPhone** هو نظام متكامل لاسترجاع البيانات من الهواتف ذات الشاشات المكسورة أو التالفة. يتكوّن من:

- 📱 **تطبيق Android** — يُثبَّت على الهاتف المُتضرر.
- 🖥️ **سيرفر Python** — يستقبل البيانات ويتحكم بالهاتف عن بُعد.
- 💻 **واجهة طرفية** — للتحكم الكامل من أي جهاز.

> **⚠️ ملاحظة قانونية**: هذا النظام مُخصَّص **فقط** لاسترجاع بيانات مالك الجهاز. يُمنع استخدامه لأي غرض غير قانوني.

---

<!-- ============================================================ -->
<!-- Features                                                     -->
<!-- ============================================================ -->
## ✨ الميزات

### 📦 استرجاع البيانات
- ✅ سحب الصور من الجهاز (بأحجام كبيرة)
- ✅ سحب الفيديوهات (حتى 2 GB لكل ملف)
- ✅ سحب ملفات محددة بمسارها
- ✅ عرض محتوى المجلدات
- ✅ نقل آمن كأجزاء (Chunks) مع Base64

### 🎮 التحكم عن بُعد
- ✅ تنفيذ اللمس (نقرة + ضغط طويل)
- ✅ السحب (Swipe) بين الشاشات
- ✅ كتابة نصوص عن بُعد
- ✅ التمرير التلقائي
- ✅ النقر على عناصر بنصها
- ✅ قراءة شجرة الواجهة (UI Tree)
- ✅ فتح التطبيقات والإعدادات
- ✅ الموافقة التلقائية على الحوارات

### 🔒 الأمان
- ✅ تشفير AES-256-GCM للرسائل
- ✅ تبادل مفاتيح RSA-2048-OAEP
- ✅ مصادقة بمفتاح ترخيص
- ✅ ربط الترخيص بالجهاز
- ✅ Rate Limiting لكل عميل
- ✅ سجل تدقيق (Audit Log) شامل

### 🌐 الاستمرارية
- ✅ Foreground Service دائم
- ✅ إعادة اتصال تلقائي (Exponential Backoff)
- ✅ إعادة التشغيل بعد إعادة تشغيل الهاتف
- ✅ WakeLock لمنع النوم العميق

---

<!-- ============================================================ -->
<!-- Architecture                                                 -->
<!-- ============================================================ -->
## 🏗️ المعمارية
