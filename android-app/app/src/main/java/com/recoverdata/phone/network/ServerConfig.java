package com.recoverdata.phone.network;

import android.content.Context;
import android.content.SharedPreferences;
import android.text.TextUtils;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.BuildConfig;
import com.recoverdata.phone.utils.Logger;

/**
 * ServerConfig
 * ============================================================
 * إدارة عنوان السيرفر بشكل دائم ومرن.
 *
 * الأولويات (من الأعلى للأدنى):
 *   1. SharedPreferences (المحفوظ محلياً) ← الأولوية القصوى
 *   2. BuildConfig.DEFAULT_SERVER_URL     ← الافتراضي المدمج في APK
 *
 * الميزات:
 *   - عنوان دائم يبقى بعد إعادة تشغيل الهاتف
 *   - يمكن تغييره من MainActivity بدون إعادة بناء APK
 *   - يدعم Remote Config من السيرفر لاحقاً
 *   - Thread-safe (كل العمليات على SharedPreferences)
 *   - يتكامل مع BuildConfig (debug / release)
 *
 * مثال الاستخدام:
 *   String url = ServerConfig.getServerUrl(context);
 *   ServerConfig.setServerUrl(context, "wss://new-server.com:8765");
 * ============================================================
 */
public final class ServerConfig {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "ServerConfig";

    // اسم ملف SharedPreferences
    private static final String PREFS_NAME = "server_config_prefs";

    // المفاتيح
    private static final String KEY_SERVER_URL    = "server_url";
    private static final String KEY_LAST_SUCCESS  = "last_success_url";
    private static final String KEY_LAST_UPDATE   = "last_update_ts";
    private static final String KEY_FAIL_COUNT    = "fail_count";

    // بادئات URL المدعومة
    private static final String PREFIX_WS  = "ws://";
    private static final String PREFIX_WSS = "wss://";

    // =============================================================
    // 2. منع الإنشاء (Utility Class)
    // =============================================================
    private ServerConfig() {
        throw new UnsupportedOperationException("Utility class - cannot be instantiated");
    }

    // =============================================================
    // 3. قراءة عنوان السيرفر الحالي
    // =============================================================

    /**
     * إرجاع عنوان السيرفر الحالي.
     *
     * الأولوية:
     *   1. SharedPreferences (محفوظ سابقاً)
     *   2. BuildConfig.DEFAULT_SERVER_URL (مدمج في APK)
     *
     * @param context السياق (يُستخدم applicationContext)
     * @return عنوان WebSocket صالح
     */
    @NonNull
    public static String getServerUrl(@NonNull Context context) {
        SharedPreferences prefs = getPrefs(context);
        String saved = prefs.getString(KEY_SERVER_URL, null);

        if (!TextUtils.isEmpty(saved) && isValidUrl(saved)) {
            Logger.d(TAG, "Using saved URL: " + saved);
            return saved.trim();
        }

        // احتياط: إذا كانت القيمة المحفوظة غير صالحة، استخدم الافتراضي
        String defaultUrl = BuildConfig.DEFAULT_SERVER_URL;
        Logger.d(TAG, "Using default URL from BuildConfig: " + defaultUrl);
        return defaultUrl;
    }

    /**
     * إرجاع منفذ السيرفر.
     */
    public static int getServerPort() {
        return BuildConfig.SERVER_PORT;
    }

    /**
     * إرجاع اسم العميل (يُرسل للسيرفر عند الاتصال).
     */
    @NonNull
    public static String getClientName() {
        return BuildConfig.CLIENT_NAME;
    }

    /**
     * إرجاع إصدار العميل (يُرسل للسيرفر عند الاتصال).
     */
    @NonNull
    public static String getClientVersion() {
        return BuildConfig.CLIENT_VERSION;
    }

    // =============================================================
    // 4. حفظ عنوان جديد
    // =============================================================

    /**
     * حفظ عنوان سيرفر جديد.
     * يُستخدم:
     *   - عند التغيير اليدوي من MainActivity
     *   - عند استقبال Remote Config من السيرفر
     *
     * @param context السياق
     * @param url     العنوان الجديد (يجب أن يبدأ بـ ws:// أو wss://)
     */
    public static void setServerUrl(@NonNull Context context, @NonNull String url) {
        String clean = url.trim();

        if (TextUtils.isEmpty(clean)) {
            Logger.w(TAG, "Attempted to save empty URL, ignoring");
            return;
        }

        if (!isValidUrl(clean)) {
            Logger.w(TAG, "Invalid URL format (must start with ws:// or wss://): " + clean);
            return;
        }

        SharedPreferences prefs = getPrefs(context);
        prefs.edit()
                .putString(KEY_SERVER_URL, clean)
                .putLong(KEY_LAST_UPDATE, System.currentTimeMillis())
                .apply();

        Logger.i(TAG, "Server URL saved: " + clean);
    }

    // =============================================================
    // 5. تسجيل نجاح / فشل الاتصال
    // =============================================================

    /**
     * يُستدعى عند نجاح الاتصال.
     * يحفظ العنوان الناجح ويصفّر عدّاد الفشل.
     */
    public static void markSuccess(@NonNull Context context) {
        String current = getServerUrl(context);
        SharedPreferences prefs = getPrefs(context);
        prefs.edit()
                .putString(KEY_LAST_SUCCESS, current)
                .putLong(KEY_LAST_UPDATE, System.currentTimeMillis())
                .putInt(KEY_FAIL_COUNT, 0)   // إعادة تصفير العدّاد
                .apply();

        Logger.d(TAG, "Marked success: " + current);
    }

    /**
     * يُستدعى عند فشل الاتصال.
     * يزيد عدّاد الفشل (قد يُستخدم لاحقاً لتبديل السيرفر).
     */
    public static void markFailure(@NonNull Context context) {
        SharedPreferences prefs = getPrefs(context);
        int current = prefs.getInt(KEY_FAIL_COUNT, 0);
        prefs.edit()
                .putInt(KEY_FAIL_COUNT, current + 1)
                .putLong(KEY_LAST_UPDATE, System.currentTimeMillis())
                .apply();

        Logger.d(TAG, "Marked failure, count=" + (current + 1));
    }

    /**
     * إرجاع عدد مرات الفشل المتتالية.
     */
    public static int getFailureCount(@NonNull Context context) {
        return getPrefs(context).getInt(KEY_FAIL_COUNT, 0);
    }

    // =============================================================
    // 6. استعلامات مساعدة
    // =============================================================

    /**
     * إرجاع آخر عنوان نجح الاتصال به (قد يكون null).
     */
    @Nullable
    public static String getLastSuccessUrl(@NonNull Context context) {
        return getPrefs(context).getString(KEY_LAST_SUCCESS, null);
    }

    /**
     * إرجاع وقت آخر تحديث (timestamp بالميلي ثانية).
     */
    public static long getLastUpdateTs(@NonNull Context context) {
        return getPrefs(context).getLong(KEY_LAST_UPDATE, 0L);
    }

    /**
     * هل العنوان الحالي هو الافتراضي (لم يُعدّل يدوياً)؟
     */
    public static boolean isUsingDefaultUrl(@NonNull Context context) {
        SharedPreferences prefs = getPrefs(context);
        String saved = prefs.getString(KEY_SERVER_URL, null);
        return TextUtils.isEmpty(saved);
    }

    // =============================================================
    // 7. إعادة التعيين (Reset)
    // =============================================================

    /**
     * مسح كل الإعدادات والعودة للـ BuildConfig الافتراضي.
     * مفيد عند التصحيح أو عند تغيير السيرفر.
     */
    public static void reset(@NonNull Context context) {
        SharedPreferences prefs = getPrefs(context);
        prefs.edit().clear().apply();
        Logger.i(TAG, "Server config reset to defaults");
    }

    /**
     * مسح العنوان المحفوظ فقط (يبقي عدّاد الفشل).
     */
    public static void resetUrl(@NonNull Context context) {
        SharedPreferences prefs = getPrefs(context);
        prefs.edit().remove(KEY_SERVER_URL).apply();
        Logger.i(TAG, "Server URL reset to default");
    }

    // =============================================================
    // 8. التحقق من صحة العنوان
    // =============================================================

    /**
     * التحقق من صحة صيغة عنوان WebSocket.
     *
     * @param url العنوان المراد فحصه
     * @return true إذا كان صالحاً (يبدأ بـ ws:// أو wss://)
     */
    public static boolean isValidUrl(@Nullable String url) {
        if (TextUtils.isEmpty(url)) return false;

        String u = url.trim().toLowerCase();
        if (!u.startsWith(PREFIX_WS) && !u.startsWith(PREFIX_WSS)) {
            return false;
        }

        // تحقق بسيط: يجب أن يحتوي على نقطتين (host:port)
        // مثال: ws://192.1**.0.0:8765
        String afterPrefix = u.startsWith(PREFIX_WSS)
                ? u.substring(PREFIX_WSS.length())
                : u.substring(PREFIX_WS.length());

        // يجب أن يكون هناك على الأقل host
        if (afterPrefix.isEmpty()) return false;

        // منع المسافات داخل العنوان
        if (afterPrefix.contains(" ")) return false;

        return true;
    }

    /**
     * التحقق من أن الاتصال مشفّر (wss://).
     */
    public static boolean isSecure(@Nullable String url) {
        if (TextUtils.isEmpty(url)) return false;
        return url.trim().toLowerCase().startsWith(PREFIX_WSS);
    }

    // =============================================================
    // 9. أدوات داخلية
    // =============================================================

    /**
     * إرجاع SharedPreferences (يستخدم applicationContext).
     */
    @NonNull
    private static SharedPreferences getPrefs(@NonNull Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }
}
