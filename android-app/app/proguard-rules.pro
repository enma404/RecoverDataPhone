# =====================================================================
# ProGuard Rules for RecoverDataPhone
# الهدف: حماية الكود + الحفاظ على OkHttp و Gson + منع كسر Accessibility
# =====================================================================

# =====================================================================
# 1. معلومات عامة (Debugging)
# =====================================================================
# الاحتفاظ بأرقام الأسطر لتسهيل قراءة StackTrace من Crash Reports
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# الاحتفاظ بأسماء الحقول والأنواع (مهمة لـ Gson و Reflection)
-keepattributes Signature
-keepattributes *Annotation*
-keepattributes InnerClasses
-keepattributes EnclosingMethod

# =====================================================================
# 2. OkHttp & Okio (مكتبة WebSocket)
# =====================================================================
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn javax.annotation.**

# الحفاظ على كلاس WebSocketListener وأي subclass له
-keep class okhttp3.WebSocketListener { *; }
-keep class okhttp3.WebSocket { *; }
-keep class okhttp3.Request { *; }
-keep class okhttp3.Response { *; }
-keep class okhttp3.OkHttpClient { *; }

# الحفاظ على Okio (يعتمد على Reflection داخلياً)
-keep class okio.** { *; }
-keep interface okio.** { *; }

# =====================================================================
# 3. Gson (تحويل JSON)
# =====================================================================
-dontwarn com.google.gson.**

# الحفاظ على كلاسات Gson الأساسية
-keep class com.google.gson.** { *; }
-keep interface com.google.gson.** { *; }

# الحفاظ على جميع كلاسات الموديلات (MessageProtocol, Command, etc.)
# أي كلاس يُستخدم مع Gson.fromJson/ toJson يجب أن يبقى كما هو
-keep class com.recoverdata.phone.network.MessageProtocol { *; }
-keep class com.recoverdata.phone.network.MessageProtocol$* { *; }
-keep class com.recoverdata.phone.network.CommandHandler { *; }

# الحفاظ على أسماء الحقول (Fields) لأن Gson يعتمد على أسمائها
-keepclassmembers class com.recoverdata.phone.network.** {
    <fields>;
    <init>(...);
}

# =====================================================================
# 4. Android Components (Services, Activities, Receivers)
# =====================================================================
# هذه المكونات تُستدعى من النظام عبر Reflection، لذا يجب حفظها

# MainActivity
-keep class com.recoverdata.phone.MainActivity { *; }

# ConnectionService (Foreground Service)
-keep class com.recoverdata.phone.service.ConnectionService { *; }

# BootReceiver
-keep class com.recoverdata.phone.service.BootReceiver { *; }

# AutoPilotService (Accessibility Service) - حرج جداً!
# النظام يستدعيه عبر اسمه الكامل في AndroidManifest
-keep class com.recoverdata.phone.accessibility.AutoPilotService { *; }
-keep class * extends android.accessibilityservice.AccessibilityService { *; }

# =====================================================================
# 5. كلاسات المشروع الحساسة (Data Layer)
# =====================================================================
# هذه الكلاسات تقرأ ملفات وتتعامل مع بيانات، الحفاظ عليها يمنع مشاكل غريبة
-keep class com.recoverdata.phone.data.** { *; }

# =====================================================================
# 6. Utilities
# =====================================================================
-keep class com.recoverdata.phone.utils.Logger { *; }
-keep class com.recoverdata.phone.utils.PermissionHelper { *; }

# =====================================================================
# 7. Android Framework الأساسية
# =====================================================================
# الحفاظ على كلاسات Android التي نستخدمها
-keep class android.accessibilityservice.** { *; }
-keep class android.view.accessibility.** { *; }

# منع التحذيرات من مكتبات Android غير المستخدمة
-dontwarn android.support.**
-dontwarn androidx.**

# =====================================================================
# 8. Reflection & Serialization
# =====================================================================
# الحفاظ على أي كلاس يستخدم Reflection
-keepclassmembers class * {
    @androidx.annotation.Keep <methods>;
    @androidx.annotation.Keep <fields>;
    @androidx.annotation.Keep <init>(...);
}

# الحفاظ على الـ Enums (لأن Gson يتعامل معها)
-keepclassmembers enum * {
    public static **[] values();
    public static ** valueOf(java.lang.String);
}

# الحفاظ على Parcelable (إن استُخدم لاحقاً)
-keepclassmembers class * implements android.os.Parcelable {
    public static final android.os.Parcelable$Creator *;
}

# =====================================================================
# 9. Coroutines / Threads (احتياطي)
# =====================================================================
-dontwarn kotlin.**
-dontwarn kotlinx.**

# =====================================================================
# 10. منع تشويش أسماء الـ Native Methods (إن استخدمنا C++ لاحقاً)
# =====================================================================
-keepclasseswithmembernames class * {
    native <methods>;
}

# =====================================================================
# 11. منع التحذيرات العامة
# =====================================================================
-dontwarn javax.**
-dontwarn java.lang.invoke.**
-dontwarn sun.misc.**

# =====================================================================
# 12. الاحتفاظ بمعلومات التصحيح (مفيد لـ Crashlytics لاحقاً)
# =====================================================================
-keepattributes RuntimeVisibleAnnotations
-keepattributes RuntimeInvisibleAnnotations
-keepattributes RuntimeVisibleParameterAnnotations
-keepattributes RuntimeInvisibleParameterAnnotations