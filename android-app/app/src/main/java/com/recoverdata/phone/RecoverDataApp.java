package com.recoverdata.phone;

import android.app.Application;
import android.content.Context;
import android.os.Build;
import android.os.Process;
import android.os.StrictMode;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.network.ServerConfig;
import com.recoverdata.phone.utils.Logger;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.Thread.UncaughtExceptionHandler;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * RecoverDataApp
 * ============================================================
 * Application class — نقطة الدخول الحقيقية للتطبيق.
 *
 * المسؤوليات:
 *   1. تهيئة Logger (file logging في debug).
 *   2. تثبيت Crash Handler مخصص.
 *   3. تسجيل معلومات الجهاز عند البدء.
 *   4. تفعيل StrictMode في debug (لكشف مشاكل الخيوط).
 *   5. تهيئة الإعدادات العامة.
 *
 * ملاحظة:
 *   - يُستدعى مرة واحدة فقط عند أول تشغيل للعملية.
 *   - لا يُستدعى عند إعادة تشغيل Service فقط.
 *   - أي crash هنا يُسقط التطبيق بأكمله.
 * ============================================================
 */
public class RecoverDataApp extends Application {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "RecoverDataApp";

    // مرجع ثابت للسياق (Application context)
    private static RecoverDataApp instance;

    // =============================================================
    // 2. دورة الحياة
    // =============================================================
    @Override
    public void onCreate() {
        super.onCreate();

        instance = this;

        // ----- 1. تفعيل StrictMode في debug -----
        if (BuildConfig.DEBUG) {
            enableStrictMode();
        }

        // ----- 2. تهيئة Logger -----
        initLogger();

        // ----- 3. تثبيت Crash Handler -----
        installCrashHandler();

        // ----- 4. تسجيل معلومات البدء -----
        logStartupInfo();
    }

    @Override
    public void onTerminate() {
        // ملاحظة: لا يُستدعى على الأجهزة الحقيقية، فقط على المحاكي
        Logger.i(TAG, "Application terminating");
        super.onTerminate();
    }

    // =============================================================
    // 3. الوصول العام
    // =============================================================

    /**
     * إرجاع instance للتطبيق.
     */
    @NonNull
    public static RecoverDataApp getInstance() {
        return instance;
    }

    /**
     * إرجاع Application context (آمن ضد التسريب).
     */
    @NonNull
    public static Context getAppContext() {
        return instance.getApplicationContext();
    }

    // =============================================================
    // 4. التهيئة
    // =============================================================

    /**
     * تهيئة Logger.
     * في debug: نفعّل ملف السجل.
     * في release: Logcat فقط.
     */
    private void initLogger() {
        try {
            if (BuildConfig.VERBOSE_LOG) {
                Logger.enableFileLogging(this);
            }
        } catch (Exception e) {
            // لا نُسقط التطبيق إذا فشل التسجيل
            android.util.Log.e("RDP::RecoverDataApp",
                    "Failed to init Logger", e);
        }
    }

    /**
     * تثبيت معالج الأخطاء غير الملتقطة.
     */
    private void installCrashHandler() {
        final UncaughtExceptionHandler defaultHandler =
                Thread.getDefaultUncaughtExceptionHandler();

        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            try {
                handleCrash(thread, throwable);
            } catch (Exception e) {
                // تجاهل: لا نريد crash أثناء معالجة crash
            } finally {
                // استدعاء المعالج الافتراضي (يُسقط التطبيق)
                if (defaultHandler != null) {
                    defaultHandler.uncaughtException(thread, throwable);
                } else {
                    Process.killProcess(Process.myPid());
                    System.exit(1);
                }
            }
        });
    }

    /**
     * معالجة crash: تسجيل تفاصيل كاملة.
     */
    private void handleCrash(@NonNull Thread thread, @NonNull Throwable throwable) {
        try {
            // ----- 1. بناء التقرير -----
            StringWriter sw = new StringWriter();
            PrintWriter pw = new PrintWriter(sw);
            throwable.printStackTrace(pw);
            String stackTrace = sw.toString();

            // ----- 2. تسجيل في Logcat -----
            Logger.e(TAG, "═══════════ CRASH ═══════════");
            Logger.e(TAG, "Thread: " + thread.getName());
            Logger.e(TAG, "Device: " + getDeviceInfo());
            Logger.e(TAG, "Android: " + Build.VERSION.RELEASE
                    + " (SDK " + Build.VERSION.SDK_INT + ")");
            Logger.e(TAG, "Exception: " + throwable.getClass().getName());
            Logger.e(TAG, "Message: " + throwable.getMessage());
            Logger.e(TAG, stackTrace);
            Logger.e(TAG, "═════════════════════════════");

        } catch (Exception e) {
            android.util.Log.e("RDP::RecoverDataApp",
                    "Failed to handle crash", e);
        }
    }

    // =============================================================
    // 5. StrictMode (debug فقط)
    // =============================================================

    /**
     * تفعيل StrictMode لكشف:
     *   - عمليات I/O على الخيط الرئيسي.
     *   - تسريب الموارد (Cursor, InputStream).
     *   - استدعاءات شبكة على الخيط الرئيسي.
     */
    private void enableStrictMode() {
        try {
            StrictMode.ThreadPolicy threadPolicy = new StrictMode.ThreadPolicy.Builder()
                    .detectDiskReads()
                    .detectDiskWrites()
                    .detectNetwork()
                    .penaltyLog()
                    .build();

            StrictMode.VmPolicy vmPolicy = new StrictMode.VmPolicy.Builder()
                    .detectLeakedSqlLiteObjects()
                    .detectLeakedClosableObjects()
                    .penaltyLog()
                    .build();

            StrictMode.setThreadPolicy(threadPolicy);
            StrictMode.setVmPolicy(vmPolicy);

            // ملاحظة: لا نستخدم penaltyDeath() لتجنب الإسقاط

        } catch (Exception e) {
            android.util.Log.w("RDP::RecoverDataApp",
                    "Failed to enable StrictMode", e);
        }
    }

    // =============================================================
    // 6. معلومات البدء
    // =============================================================

    /**
     * تسجيل معلومات التطبيق والجهاز عند البدء.
     */
    private void logStartupInfo() {
        try {
            Logger.section(TAG, "Application Started");
            Logger.table(TAG,
                    "Client", ServerConfig.getClientName(),
                    "Version", ServerConfig.getClientVersion(),
                    "Build Type", BuildConfig.DEBUG ? "DEBUG" : "RELEASE",
                    "Device", getDeviceInfo(),
                    "Android", Build.VERSION.RELEASE + " (SDK " + Build.VERSION.SDK_INT + ")",
                    "Server URL", ServerConfig.getServerUrl(this),
                    "Using Default URL", String.valueOf(
                            ServerConfig.isUsingDefaultUrl(this))
            );
        } catch (Exception e) {
            android.util.Log.e("RDP::RecoverDataApp",
                    "Failed to log startup info", e);
        }
    }

    /**
     * إرجاع وصف الجهاز.
     */
    @NonNull
    private String getDeviceInfo() {
        try {
            return Build.MANUFACTURER + " " + Build.MODEL;
        } catch (Exception e) {
            return "unknown";
        }
    }

    // =============================================================
    // 7. أدوات مساعدة
    // =============================================================

    /**
     * هل التطبيق يعمل في debug؟
     */
    public static boolean isDebug() {
        return BuildConfig.DEBUG;
    }

    /**
     * إرجاع وقت البدء بالمللي ثانية.
     */
    public long getStartupTime() {
        return System.currentTimeMillis();
    }

    /**
     * إرجاع نص بكود الخطأ (للعرض).
     */
    @NonNull
    public static String getBuildInfo() {
        return String.format(Locale.US,
                "%s v%s (%s)",
                BuildConfig.CLIENT_NAME,
                BuildConfig.CLIENT_VERSION,
                BuildConfig.DEBUG ? "debug" : "release"
        );
    }
}