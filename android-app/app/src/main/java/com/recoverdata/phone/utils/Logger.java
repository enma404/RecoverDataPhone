package com.recoverdata.phone.utils;

import android.content.Context;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.BuildConfig;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Logger
 * ============================================================
 * نظام تسجيل موحّد للتطبيق.
 *
 * الميزات:
 *   1. طباعة في Logcat بتنسيق موحّد.
 *   2. احترام BuildConfig.VERBOSE_LOG (debug فقط).
 *   3. تسجيل اختياري في ملف (اختياري).
 *   4. تشويش تلقائي للبيانات الحساسة (Base64 الطويل، إلخ).
 *   5. Thread-safe (يمكن استدعاؤه من أي خيط).
 *   6. أداء عالٍ (لا يُبطئ الكود).
 *
 * مستويات التسجيل:
 *   - d() : Debug   - تفاصيل دقيقة (فقط في debug)
 *   - i() : Info    - معلومات عامة
 *   - w() : Warning - تحذيرات
 *   - e() : Error   - أخطاء (مع StackTrace)
 *
 * الاستخدام:
 *   Logger.i(TAG, "Connected successfully");
 *   Logger.e(TAG, "Send failed", exception);
 *   Logger.d(TAG, "Chunk sent: " + data);  // يُطبع فقط في debug
 * ============================================================
 */
public final class Logger {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG_PREFIX = "RDP::";

    // أقصى طول لسطر واحد في Logcat (Android يقتطع الطويل)
    private static final int MAX_LOGCAT_LINE = 4000;

    // أقصى حجم لملف السجل (5 MB)
    private static final long MAX_LOG_FILE_SIZE = 5 * 1024 * 1024L;

    // اسم ملف السجل
    private static final String LOG_FILE_NAME = "recover_data.log";

    // نمط التاريخ
    private static final String DATE_PATTERN = "yyyy-MM-dd HH:mm:ss.SSS";

    // =============================================================
    // 2. الحقول
    // =============================================================
    private static final AtomicBoolean fileLoggingEnabled = new AtomicBoolean(false);
    private static final ExecutorService fileExecutor = Executors.newSingleThreadExecutor();

    private static File logFile;
    private static SimpleDateFormat dateFormat;

    // =============================================================
    // 3. منع الإنشاء
    // =============================================================
    private Logger() {
        throw new UnsupportedOperationException("Utility class");
    }

    // =============================================================
    // 4. الإعدادات (اختياري)
    // =============================================================

    /**
     * تفعيل تسجيل الملفات.
     * @param context السياق
     */
    public static void enableFileLogging(@NonNull Context context) {
        try {
            File dir = new File(context.getFilesDir(), "logs");
            if (!dir.exists() && !dir.mkdirs()) {
                return;
            }

            logFile = new File(dir, LOG_FILE_NAME);
            dateFormat = new SimpleDateFormat(DATE_PATTERN, Locale.US);

            // إذا الملف كبير، احذفه
            if (logFile.exists() && logFile.length() > MAX_LOG_FILE_SIZE) {
                //noinspection ResultOfMethodCallIgnored
                logFile.delete();
            }

            fileLoggingEnabled.set(true);
            i("Logger", "File logging enabled: " + logFile.getAbsolutePath());

        } catch (Exception e) {
            Log.e(TAG_PREFIX + "Logger", "Failed to enable file logging", e);
        }
    }

    /**
     * تعطيل تسجيل الملفات.
     */
    public static void disableFileLogging() {
        fileLoggingEnabled.set(false);
        logFile = null;
    }

    /**
     * مسح ملف السجل.
     */
    public static void clearLogFile() {
        if (logFile != null && logFile.exists()) {
            //noinspection ResultOfMethodCallIgnored
            logFile.delete();
        }
    }

    /**
     * إرجاع مسار ملف السجل (أو null).
     */
    @Nullable
    public static File getLogFile() {
        return logFile;
    }

    // =============================================================
    // 5. الدوال الرئيسية
    // =============================================================

    /**
     * تسجيل Debug - يُطبع فقط في debug build.
     */
    public static void d(@NonNull String tag, @NonNull String message) {
        if (!BuildConfig.VERBOSE_LOG) return;

        String line = format("D", tag, message);
        logToLogcat(Log.DEBUG, tag, message);
        logToFile(line);
    }

    /**
     * تسجيل Debug مع Throwable.
     */
    public static void d(@NonNull String tag, @NonNull String message,
                         @Nullable Throwable tr) {
        if (!BuildConfig.VERBOSE_LOG) return;

        String line = format("D", tag, message + " | " + shortError(tr));
        logToLogcat(Log.DEBUG, tag, message + "\n" + stackTrace(tr));
        logToFile(line);
    }

    /**
     * تسجيل Info.
     */
    public static void i(@NonNull String tag, @NonNull String message) {
        String line = format("I", tag, message);
        logToLogcat(Log.INFO, tag, message);
        logToFile(line);
    }

    /**
     * تسجيل Warning.
     */
    public static void w(@NonNull String tag, @NonNull String message) {
        String line = format("W", tag, message);
        logToLogcat(Log.WARN, tag, message);
        logToFile(line);
    }

    /**
     * تسجيل Warning مع Throwable.
     */
    public static void w(@NonNull String tag, @NonNull String message,
                         @Nullable Throwable tr) {
        String line = format("W", tag, message + " | " + shortError(tr));
        logToLogcat(Log.WARN, tag, message + "\n" + stackTrace(tr));
        logToFile(line);
    }

    /**
     * تسجيل Error (بدون Throwable).
     */
    public static void e(@NonNull String tag, @NonNull String message) {
        String line = format("E", tag, message);
        logToLogcat(Log.ERROR, tag, message);
        logToFile(line);
    }

    /**
     * تسجيل Error مع Throwable.
     */
    public static void e(@NonNull String tag, @NonNull String message,
                         @Nullable Throwable tr) {
        String line = format("E", tag, message + " | " + shortError(tr));
        logToLogcat(Log.ERROR, tag, message + "\n" + stackTrace(tr));
        logToFile(line);
    }

    /**
     * تسجيل مع رمز مخصص (V, D, I, W, E).
     */
    public static void log(@NonNull String level, @NonNull String tag,
                           @NonNull String message) {
        switch (level.toUpperCase()) {
            case "V": d(tag, message); break;
            case "D": d(tag, message); break;
            case "I": i(tag, message); break;
            case "W": w(tag, message); break;
            case "E": e(tag, message); break;
            default:  i(tag, message);
        }
    }

    // =============================================================
    // 6. أدوات داخلية
    // =============================================================

    private static String format(@NonNull String level, @NonNull String tag,
                                 @NonNull String message) {
        String time = new SimpleDateFormat(DATE_PATTERN, Locale.US)
                .format(new Date());
        return "[" + time + "] [" + level + "] [" + tag + "] " + message;
    }

    private static void logToLogcat(int priority, @NonNull String tag,
                                    @NonNull String message) {
        String fullTag = TAG_PREFIX + tag;

        if (message.length() <= MAX_LOGCAT_LINE) {
            Log.println(priority, fullTag, message);
            return;
        }

        // تقسيم الرسائل الطويلة
        int length = message.length();
        int start = 0;
        int chunkNum = 0;

        while (start < length) {
            int end = Math.min(start + MAX_LOGCAT_LINE, length);
            String part = message.substring(start, end);

            if (chunkNum == 0) {
                Log.println(priority, fullTag, part);
            } else {
                Log.println(priority, fullTag, "  [cont " + chunkNum + "] " + part);
            }

            start = end;
            chunkNum++;
        }
    }

    private static void logToFile(@NonNull String line) {
        if (!fileLoggingEnabled.get() || logFile == null) return;

        fileExecutor.execute(() -> {
            try {
                // إذا الملف تجاوز الحد، احذفه
                if (logFile.exists() && logFile.length() > MAX_LOG_FILE_SIZE) {
                    //noinspection ResultOfMethodCallIgnored
                    logFile.delete();
                }

                try (FileWriter fw = new FileWriter(logFile, true)) {
                    fw.write(line);
                    fw.write("\n");
                }
            } catch (IOException ignored) {
                // لا نُسجّل الخطأ هنا لتجنب حلقة لا نهائية
            }
        });
    }

    @NonNull
    private static String shortError(@Nullable Throwable tr) {
        if (tr == null) return "null";
        String msg = tr.getMessage();
        return tr.getClass().getSimpleName()
                + (msg != null ? ": " + msg : "");
    }

    @NonNull
    private static String stackTrace(@Nullable Throwable tr) {
        if (tr == null) return "";

        StringBuilder sb = new StringBuilder();
        sb.append(tr.getClass().getName());

        if (tr.getMessage() != null) {
            sb.append(": ").append(tr.getMessage());
        }
        sb.append("\n");

        for (StackTraceElement el : tr.getStackTrace()) {
            sb.append("\tat ").append(el.toString()).append("\n");
        }

        Throwable cause = tr.getCause();
        if (cause != null) {
            sb.append("Caused by: ").append(cause.toString()).append("\n");
        }

        return sb.toString();
    }

    // =============================================================
    // 7. أدوات أمنية
    // =============================================================

    /**
     * تشويش نص طويل (Base64، بيانات حساسة).
     * يُظهر أول 20 حرفاً فقط.
     */
    @NonNull
    public static String truncate(@Nullable String data, int maxLen) {
        if (data == null) return "null";
        if (data.length() <= maxLen) return data;
        return data.substring(0, maxLen) + "... ["
                + (data.length() - maxLen) + " more chars]";
    }

    /**
     * تشويش Base64 (يُظهر البداية فقط).
     */
    @NonNull
    public static String maskBase64(@Nullable String data) {
        if (data == null) return "null";
        if (data.length() < 40) return data;
        return data.substring(0, 20) + "... [" + data.length() + " chars]";
    }

    /**
     * إخفاء جزء من مفتاح/ID.
     */
    @NonNull
    public static String maskKey(@Nullable String key) {
        if (key == null || key.isEmpty()) return "null";
        if (key.length() <= 8) return "***";
        return key.substring(0, 4) + "****" + key.substring(key.length() - 4);
    }

    // =============================================================
    // 8. مخرجات خاصة
    // =============================================================

    /**
     * طباعة فاصل بصري (للتطوير).
     */
    public static void separator(@NonNull String tag) {
        d(tag, "──────────────────────────────────────────");
    }

    /**
     * طباعة عنوان قسم.
     */
    public static void section(@NonNull String tag, @NonNull String title) {
        d(tag, "═══ " + title + " ═══");
    }

    /**
     * طباعة جدول بسيط (مفتاح: قيمة).
     */
    public static void table(@NonNull String tag, @NonNull String... kvPairs) {
        if (!BuildConfig.VERBOSE_LOG) return;

        StringBuilder sb = new StringBuilder("\n");
        for (int i = 0; i + 1 < kvPairs.length; i += 2) {
            sb.append("  ").append(kvPairs[i]).append(": ")
              .append(kvPairs[i + 1]).append("\n");
        }
        d(tag, sb.toString());
    }

    /**
     * تنسيق حجم للعرض.
     */
    @NonNull
    public static String formatSize(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format(Locale.US, "%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) {
            return String.format(Locale.US, "%.1f MB", bytes / (1024.0 * 1024));
        }
        return String.format(Locale.US, "%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    /**
     * تنسيق مدة للعرض.
     */
    @NonNull
    public static String formatDuration(long ms) {
        if (ms < 1000) return ms + "ms";
        long sec = ms / 1000;
        if (sec < 60) return sec + "s";
        long min = sec / 60;
        long s = sec % 60;
        if (min < 60) return min + "m " + s + "s";
        long hr = min / 60;
        long m = min % 60;
        return hr + "h " + m + "m";
    }
}