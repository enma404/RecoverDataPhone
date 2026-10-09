package com.recoverdata.phone.service;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import com.recoverdata.phone.utils.Logger;

/**
 * BootReceiver
 * ============================================================
 * يستقبل بث إعادة تشغيل الهاتف ويعيد تشغيل ConnectionService تلقائياً.
 *
 * الأحداث المدعومة:
 *   - android.intent.action.BOOT_COMPLETED          : إعادة تشغيل عادية
 *   - android.intent.action.QUICKBOOT_POWERON       : إعادة تشغيل سريعة (HTC)
 *   - com.htc.intent.action.QUICKBOOT_POWERON       : إعادة تشغيل سريعة (HTC)
 *   - android.intent.action.LOCKED_BOOT_COMPLETED   : إعادة تشغيل قبل فتح القفل
 *
 * ملاحظة مهمة:
 *   على Android 8+ لا يمكن بدء Foreground Service مباشرة من BroadcastReceiver
 *   في بعض الحالات. نستخدم startForegroundService() مع تأخير بسيط.
 * ============================================================
 */
public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "BootReceiver";

    // =============================================================
    // 1. الأحداث المدعومة
    // =============================================================
    private static final String ACTION_BOOT_COMPLETED       = Intent.ACTION_BOOT_COMPLETED;
    private static final String ACTION_QUICKBOOT_POWERON    = "android.intent.action.QUICKBOOT_POWERON";
    private static final String ACTION_HTC_QUICKBOOT        = "com.htc.intent.action.QUICKBOOT_POWERON";
    private static final String ACTION_LOCKED_BOOT_COMPLETED = Intent.ACTION_LOCKED_BOOT_COMPLETED;

    // =============================================================
    // 2. onReceive
    // =============================================================
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) {
            Logger.w(TAG, "Received null intent or action");
            return;
        }

        String action = intent.getAction();
        Logger.i(TAG, "Boot event received: " + action);

        // التحقق من أن الحدث من الأحداث المدعومة
        if (!isSupportedAction(action)) {
            Logger.d(TAG, "Unsupported action, ignoring: " + action);
            return;
        }

        // بدء الخدمة
        try {
            startConnectionService(context);
            Logger.i(TAG, "ConnectionService start requested after boot");
        } catch (Exception e) {
            Logger.e(TAG, "Failed to start ConnectionService after boot", e);
        }
    }

    // =============================================================
    // 3. التحقق من الأحداث
    // =============================================================
    private boolean isSupportedAction(String action) {
        return ACTION_BOOT_COMPLETED.equals(action)
                || ACTION_QUICKBOOT_POWERON.equals(action)
                || ACTION_HTC_QUICKBOOT.equals(action)
                || ACTION_LOCKED_BOOT_COMPLETED.equals(action);
    }

    // =============================================================
    // 4. بدء الخدمة
    // =============================================================
    private void startConnectionService(Context context) {
        Intent serviceIntent = new Intent(context, ConnectionService.class);
        serviceIntent.setAction(ConnectionService.ACTION_START);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // Android 8+ : يجب استخدام startForegroundService
            context.startForegroundService(serviceIntent);
        } else {
            // Android 7 وأقدم : startService عادي
            context.startService(serviceIntent);
        }
    }
}