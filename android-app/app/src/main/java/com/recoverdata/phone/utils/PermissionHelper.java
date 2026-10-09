package com.recoverdata.phone.utils;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.util.ArrayList;
import java.util.List;

/**
 * PermissionHelper
 * ============================================================
 * إدارة كل صلاحيات التطبيق + التوجيه لشاشات الإعدادات الخاصة.
 *
 * الصلاحيات المُدارة:
 *   1. READ_MEDIA_IMAGES/VIDEO/AUDIO (Android 13+)
 *   2. READ_EXTERNAL_STORAGE (Android 12-)
 *   3. MANAGE_EXTERNAL_STORAGE (Android 11+ - خاصة)
 *   4. POST_NOTIFICATIONS (Android 13+)
 *   5. Accessibility Service (خاصة)
 *   6. Battery Optimization exemption (خاصة)
 *
 * الميزات:
 *   - كشف إصدار Android تلقائياً.
 *   - توجيه المستخدم للشاشة المناسبة.
 *   - التحقق من حالة كل صلاحية.
 * ============================================================
 */
public final class PermissionHelper {

    private static final String TAG = "PermissionHelper";

    // =============================================================
    // 1. أكواد الطلب (Request Codes)
    // =============================================================
    public static final int RC_MEDIA_PERMISSIONS = 1001;
    public static final int RC_NOTIFICATIONS = 1002;
    public static final int RC_ALL_PERMISSIONS = 1003;

    // =============================================================
    // 2. منع الإنشاء
    // =============================================================
    private PermissionHelper() {
        throw new UnsupportedOperationException("Utility class");
    }

    // =============================================================
    // 3. قائمة الصلاحيات المطلوبة
    // =============================================================

    /**
     * إرجاع قائمة الصلاحيات "العادية" المطلوبة حسب إصدار Android.
     * (لا تشمل MANAGE_EXTERNAL_STORAGE و Accessibility).
     */
    @NonNull
    public static String[] getRequiredRuntimePermissions() {
        List<String> permissions = new ArrayList<>();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // Android 13+ (API 33+)
            permissions.add(Manifest.permission.READ_MEDIA_IMAGES);
            permissions.add(Manifest.permission.READ_MEDIA_VIDEO);
            permissions.add(Manifest.permission.READ_MEDIA_AUDIO);
            permissions.add(Manifest.permission.POST_NOTIFICATIONS);

        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Android 11-12 (API 30-32)
            permissions.add(Manifest.permission.READ_EXTERNAL_STORAGE);
            // MANAGE_EXTERNAL_STORAGE تُطلب عبر Intent خاص

        } else {
            // Android 6-10 (API 23-29)
            permissions.add(Manifest.permission.READ_EXTERNAL_STORAGE);
        }

        return permissions.toArray(new String[0]);
    }

    // =============================================================
    // 4. التحقق من الصلاحيات
    // =============================================================

    /**
     * هل كل الصلاحيات "العادية" ممنوحة؟
     */
    public static boolean hasRuntimePermissions(@NonNull Context context) {
        String[] permissions = getRequiredRuntimePermissions();
        for (String p : permissions) {
            if (ContextCompat.checkSelfPermission(context, p)
                    != PackageManager.PERMISSION_GRANTED) {
                return false;
            }
        }
        return true;
    }

    /**
     * هل صلاحية MANAGE_EXTERNAL_STORAGE ممنوحة؟ (Android 11+)
     * على Android 10 وأقل، ترجع true دائماً (غير مطلوبة).
     */
    public static boolean hasManageExternalStorage(@NonNull Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            return android.os.Environment.isExternalStorageManager();
        }
        // على Android 10-، نعتمد على READ_EXTERNAL_STORAGE
        return hasRuntimePermissions(context);
    }

    /**
     * هل خدمة Accessibility مفعّلة؟
     */
    public static boolean isAccessibilityServiceEnabled(@NonNull Context context) {
        try {
            String expected = new ComponentName(
                    context,
                    com.recoverdata.phone.accessibility.AutoPilotService.class
            ).flattenToString();

            String enabled = Settings.Secure.getString(
                    context.getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            );

            if (enabled == null || enabled.isEmpty()) {
                return false;
            }

            String[] services = enabled.split(":");
            for (String service : services) {
                if (service.equalsIgnoreCase(expected)
                        || service.contains("AutoPilotService")) {
                    return true;
                }
            }

            return false;

        } catch (Exception e) {
            Logger.e(TAG, "isAccessibilityServiceEnabled error", e);
            return false;
        }
    }

    /**
     * هل التطبيق مُستثنى من تحسين البطارية؟
     */
    public static boolean isIgnoringBatteryOptimizations(@NonNull Context context) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PowerManager pm = (PowerManager)
                        context.getSystemService(Context.POWER_SERVICE);
                if (pm == null) return false;
                return pm.isIgnoringBatteryOptimizations(context.getPackageName());
            }
            return true;  // Android 5- لا يحتاج
        } catch (Exception e) {
            Logger.e(TAG, "isIgnoringBatteryOptimizations error", e);
            return false;
        }
    }

    /**
     * هل كل الصلاحيات المطلوبة (بما فيها الخاصة) ممنوحة؟
     */
    public static boolean hasAllRequiredPermissions(@NonNull Context context) {
        return hasRuntimePermissions(context)
                && hasManageExternalStorage(context)
                && isAccessibilityServiceEnabled(context)
                && isIgnoringBatteryOptimizations(context);
    }

    // =============================================================
    // 5. طلب الصلاحيات
    // =============================================================

    /**
     * طلب الصلاحيات "العادية" عبر النظام.
     */
    public static void requestRuntimePermissions(@NonNull Activity activity,
                                                 int requestCode) {
        String[] permissions = getRequiredRuntimePermissions();
        if (permissions.length == 0) return;

        Logger.i(TAG, "Requesting " + permissions.length + " permissions");

        ActivityCompat.requestPermissions(
                activity,
                permissions,
                requestCode
        );
    }

    // =============================================================
    // 6. التوجيه لشاشات الإعدادات الخاصة
    // =============================================================

    /**
     * فتح شاشة "الوصول لجميع الملفات" (Android 11+).
     */
    public static void openManageStorageSettings(@NonNull Activity activity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            Logger.w(TAG, "MANAGE_EXTERNAL_STORAGE requires API 30+");
            // على الأقدم: اطلب READ_EXTERNAL_STORAGE
            requestRuntimePermissions(activity, RC_MEDIA_PERMISSIONS);
            return;
        }

        try {
            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
            intent.setData(Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(intent);
            Logger.i(TAG, "Opened MANAGE_EXTERNAL_STORAGE settings");

        } catch (Exception e) {
            // بعض الأجهزة لا تدعم ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION
            try {
                Intent intent = new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION);
                activity.startActivity(intent);
            } catch (Exception e2) {
                Logger.e(TAG, "Failed to open storage settings", e2);
            }
        }
    }

    /**
     * فتح شاشة Accessibility لتفعيل الخدمة.
     */
    public static void openAccessibilitySettings(@NonNull Activity activity) {
        try {
            Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            activity.startActivity(intent);
            Logger.i(TAG, "Opened accessibility settings");
        } catch (Exception e) {
            Logger.e(TAG, "Failed to open accessibility settings", e);
        }
    }

    /**
     * طلب استثناء من تحسين البطارية.
     */
    public static void requestIgnoreBatteryOptimizations(@NonNull Activity activity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return;

        try {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
            intent.setData(Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(intent);
            Logger.i(TAG, "Requested battery optimization exemption");

        } catch (Exception e) {
            // بعض الأجهزة لا تدعمها مباشرة - افتح شاشة عامة
            try {
                Intent intent = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                activity.startActivity(intent);
            } catch (Exception e2) {
                Logger.e(TAG, "Failed to open battery settings", e2);
            }
        }
    }

    /**
     * فتح شاشة الإشعارات (Android 13+).
     */
    public static void openNotificationSettings(@NonNull Activity activity) {
        try {
            Intent intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS);
            intent.putExtra(Settings.EXTRA_APP_PACKAGE, activity.getPackageName());
            activity.startActivity(intent);
        } catch (Exception e) {
            Logger.e(TAG, "Failed to open notification settings", e);
        }
    }

    // =============================================================
    // 7. أدوات مساعدة
    // =============================================================

    /**
     * هل الصلاحية ممنوحة؟
     */
    public static boolean isGranted(@NonNull Context context, @NonNull String permission) {
        return ContextCompat.checkSelfPermission(context, permission)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * ملخص حالة الصلاحيات (للعرض).
     */
    @NonNull
    public static String getStatusSummary(@NonNull Context context) {
        StringBuilder sb = new StringBuilder();

        sb.append("📋 حالة الصلاحيات:\n");
        sb.append("─────────────────────────────\n");

        // Media
        sb.append(hasRuntimePermissions(context) ? "✅" : "❌")
                .append("  صلاحيات الوسائط\n");

        // MANAGE_EXTERNAL_STORAGE
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            sb.append(hasManageExternalStorage(context) ? "✅" : "❌")
                    .append("  الوصول لجميع الملفات\n");
        }

        // Accessibility
        sb.append(isAccessibilityServiceEnabled(context) ? "✅" : "❌")
                .append("  خدمة Accessibility\n");

        // Battery
        sb.append(isIgnoringBatteryOptimizations(context) ? "✅" : "❌")
                .append("  استثناء البطارية\n");

        return sb.toString();
    }

    /**
     * عدد الصلاحيات الناقصة (رقم سريع).
     */
    public static int countMissingPermissions(@NonNull Context context) {
        int missing = 0;
        if (!hasRuntimePermissions(context)) missing++;
        if (!hasManageExternalStorage(context)) missing++;
        if (!isAccessibilityServiceEnabled(context)) missing++;
        if (!isIgnoringBatteryOptimizations(context)) missing++;
        return missing;
    }

    /**
     * رسالة الخطوة التالية للعميل.
     */
    @NonNull
    public static String getNextStepMessage(@NonNull Context context) {
        if (!hasRuntimePermissions(context)) {
            return "الخطوة التالية: منح صلاحيات الوصول للوسائط";
        }
        if (!hasManageExternalStorage(context)) {
            return "الخطوة التالية: تفعيل \"الوصول لجميع الملفات\"";
        }
        if (!isAccessibilityServiceEnabled(context)) {
            return "الخطوة التالية: تفعيل خدمة Accessibility";
        }
        if (!isIgnoringBatteryOptimizations(context)) {
            return "الخطوة التالية: استثناء التطبيق من تحسين البطارية";
        }
        return "✅ كل الصلاحيات ممنوحة - جاهز للاتصال";
    }
}