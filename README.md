
---

<!-- ============================================================ -->
<!-- Tech Stack                                                   -->
<!-- ============================================================ -->
## 🛠️ التقنيات المستخدمة

### 📱 جانب الأندرويد

| التقنية | الإصدار | الاستخدام |
|---------|---------|-----------|
| Java | 17 | لغة البرمجة |
| Android SDK | 34 | Target SDK |
| Min SDK | 24 | Android 7.0+ |
| OkHttp | 4.12.0 | WebSocket |
| Gson | 2.10.1 | JSON |
| Material Components | 1.11.0 | الواجهات |
| Accessibility API | — | التحكم عن بُعد |

### 🖥️ جانب السيرفر

| التقنية | الإصدار | الاستخدام |
|---------|---------|-----------|
| Python | 3.9+ | لغة البرمجة |
| websockets | 12.0 | خادم WebSocket |
| cryptography | 42.0.2 | التشفير |
| asyncio | — | عدم التزامن |

---

<!-- ============================================================ -->
<!-- Installation                                                 -->
<!-- ============================================================ -->
## 🚀 التثبيت والتشغيل

### 📋 المتطلبات

- **للعميل**: هاتف Android 7.0+ مع اتصال بشبكة WiFi
- **للسيرفر**: Python 3.9+ + اتصال بنفس الشبكة
- **اختياري**: Termux للتحكم من الهاتف

### 1️⃣ تثبيت السيرفر

```bash
# استنساخ المستودع
git clone https://github.com/YOUR_USERNAME/RecoverDataPhone.git
cd RecoverDataPhone/server

# إنشاء بيئة افتراضية
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# أو: venv\Scripts\activate  # Windows

# تثبيت المتطلبات
pip install -r requirements.txt

# تشغيل السيرفر
python main.py
