package com.recoverdata.phone.network;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.utils.Logger;

import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/**
 * WebSocketManager
 * ============================================================
 * إدارة اتصال WebSocket بالسيرفر.
 *
 * المسؤوليات:
 *   1. إنشاء اتصال WebSocket (ws:// أو wss://).
 *   2. إعادة الاتصال التلقائي عند الانقطاع (Exponential Backoff).
 *   3. إرسال الرسائل بشكل آمن (Thread-safe).
 *   4. استقبال الرسائل وتمريرها للـ Callback.
 *   5. تتبع حالة الاتصال.
 *
 * الخصائص:
 *   - readTimeout = 0 (لا يُقطع الاتصال بسبب الخمول)
 *   - pingInterval = 30s (للحفاظ على الاتصال حياً)
 *   - إعادة اتصال تلقائي مع تأخير متزايد
 *   - يقرأ العنوان من ServerConfig (SharedPreferences + BuildConfig)
 *
 * يعتمد على:
 *   - ServerConfig (لقراءة العنوان)
 *   - Logger (للتسجيل)
 * ============================================================
 */
public class WebSocketManager {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "WebSocketManager";

    // إعادة الاتصال (Exponential Backoff)
    private static final long RECONNECT_DELAY_MIN_MS = 2_000;    // 2 ثواني
    private static final long RECONNECT_DELAY_MAX_MS = 60_000;   // 60 ثانية

    // Ping interval (للحفاظ على الاتصال حياً)
    private static final long PING_INTERVAL_SEC = 30;

    // Timeouts
    private static final long CONNECT_TIMEOUT_SEC = 15;
    private static final long WRITE_TIMEOUT_SEC   = 30;

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;
    private final Callback callback;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean isConnected = new AtomicBoolean(false);
    private final AtomicBoolean isManuallyClosed = new AtomicBoolean(false);

    private OkHttpClient client;
    private WebSocket webSocket;
    private String serverUrl;

    // عدد محاولات إعادة الاتصال الحالية (لـ backoff)
    private int reconnectAttempts = 0;
    private final Runnable reconnectRunnable = this::connectInternal;

    // =============================================================
    // 3. الواجهة (Callback)
    // =============================================================
    public interface Callback {
        /** يُستدعى عند نجاح الاتصال */
        void onConnected();

        /** يُستدعى عند استقبال رسالة نصية */
        void onMessage(@NonNull String message);

        /** يُستدعى عند انقطاع الاتصال */
        void onDisconnected(int code, @NonNull String reason);

        /** يُستدعى عند حدوث خطأ */
        void onError(@NonNull Throwable error);
    }

    // =============================================================
    // 4. البناء (Constructor)
    // =============================================================

    /**
     * Constructor أساسي - يقرأ العنوان من ServerConfig.
     */
    public WebSocketManager(@NonNull Context context, @NonNull Callback callback) {
        this.appContext = context.getApplicationContext();
        this.callback = callback;
        this.serverUrl = ServerConfig.getServerUrl(appContext);
        buildClient();
        Logger.i(TAG, "WebSocketManager initialized with URL: " + serverUrl);
    }

    /**
     * Constructor مع عنوان مخصص (يتجاوز ServerConfig).
     */
    public WebSocketManager(@NonNull Context context,
                            @NonNull Callback callback,
                            @NonNull String url) {
        this.appContext = context.getApplicationContext();
        this.callback = callback;
        this.serverUrl = url;
        buildClient();
        Logger.i(TAG, "WebSocketManager initialized with custom URL: " + serverUrl);
    }

    // =============================================================
    // 5. بناء OkHttpClient
    // =============================================================
    private void buildClient() {
        client = new OkHttpClient.Builder()
                // لا مهلة قراءة (الاتصال يبقى مفتوحاً)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                // مهلة الاتصال الأولى
                .connectTimeout(CONNECT_TIMEOUT_SEC, TimeUnit.SECONDS)
                // مهلة الكتابة
                .writeTimeout(WRITE_TIMEOUT_SEC, TimeUnit.SECONDS)
                // Ping تلقائي كل 30 ثانية لتفادي قطع الاتصال
                .pingInterval(PING_INTERVAL_SEC, TimeUnit.SECONDS)
                // إعادة المحاولة عند فشل الاتصال الأولي
                .retryOnConnectionFailure(true)
                .build();
    }

    // =============================================================
    // 6. الاتصال
    // =============================================================

    /**
     * بدء الاتصال بالسيرفر (آمن للاستدعاء أكثر من مرة).
     */
    public synchronized void connect() {
        if (isConnected.get()) {
            Logger.w(TAG, "Already connected, skipping");
            return;
        }
        if (webSocket != null) {
            Logger.w(TAG, "WebSocket already exists, skipping");
            return;
        }

        // تحديث العنوان من ServerConfig (قد يكون تغيّر)
        this.serverUrl = ServerConfig.getServerUrl(appContext);

        isManuallyClosed.set(false);
        reconnectAttempts = 0;
        connectInternal();
    }

    private void connectInternal() {
        if (isManuallyClosed.get()) {
            Logger.d(TAG, "Manual close in effect, skipping reconnect");
            return;
        }

        // تحديث العنوان قبل كل محاولة
        String currentUrl = ServerConfig.getServerUrl(appContext);
        if (!currentUrl.equals(this.serverUrl)) {
            Logger.i(TAG, "Server URL changed: " + this.serverUrl + " → " + currentUrl);
            this.serverUrl = currentUrl;
        }

        Logger.i(TAG, "Connecting to " + serverUrl + " (attempt " + (reconnectAttempts + 1) + ")");

        try {
            Request request = new Request.Builder()
                    .url(serverUrl)
                    .addHeader("User-Agent", "RecoverDataPhone/" + ServerConfig.getClientVersion())
                    .addHeader("X-Client-Name", ServerConfig.getClientName())
                    .build();

            webSocket = client.newWebSocket(request, new InternalListener());
        } catch (Exception e) {
            Logger.e(TAG, "connectInternal exception: " + e.getMessage(), e);
            callback.onError(e);
            ServerConfig.markFailure(appContext);
            scheduleReconnect();
        }
    }

    // =============================================================
    // 7. قطع الاتصال
    // =============================================================

    /**
     * قطع الاتصال بشكل نهائي (لن يُعاد الاتصال تلقائياً).
     */
    public synchronized void disconnect() {
        Logger.i(TAG, "Manual disconnect requested");
        isManuallyClosed.set(true);
        isConnected.set(false);

        mainHandler.removeCallbacks(reconnectRunnable);

        if (webSocket != null) {
            try {
                webSocket.close(1000, "Client disconnect");
            } catch (Exception e) {
                Logger.w(TAG, "close error: " + e.getMessage());
            }
            webSocket = null;
        }
    }

    // =============================================================
    // 8. إرسال رسالة
    // =============================================================

    /**
     * إرسال رسالة نصية إلى السيرفر.
     *
     * @param message الرسالة (عادةً JSON)
     * @return true إذا تم الإرسال، false إذا لم يكن هناك اتصال
     */
    public boolean send(@NonNull String message) {
        WebSocket ws = this.webSocket;
        if (ws == null || !isConnected.get()) {
            Logger.w(TAG, "Cannot send: not connected");
            return false;
        }
        try {
            boolean ok = ws.send(message);
            if (!ok) {
                Logger.w(TAG, "send() returned false (buffer full?)");
            }
            return ok;
        } catch (Exception e) {
            Logger.e(TAG, "send error: " + e.getMessage(), e);
            return false;
        }
    }

    // =============================================================
    // 9. حالة الاتصال
    // =============================================================
    public boolean isConnected() {
        return isConnected.get();
    }

    public String getServerUrl() {
        return serverUrl;
    }

    /**
     * تحديث العنوان (يحفظه في ServerConfig أيضاً).
     */
    public void setServerUrl(@NonNull String url) {
        this.serverUrl = url;
        ServerConfig.setServerUrl(appContext, url);
        Logger.i(TAG, "Server URL updated and saved: " + url);
    }

    /**
     * إعادة تشغيل الاتصال (disconnect ثم connect).
     */
    public synchronized void restart() {
        Logger.i(TAG, "Restarting connection...");
        disconnect();
        // إعادة تعيين العلامة اليدوية
        isManuallyClosed.set(false);
        connect();
    }

    // =============================================================
    // 10. المستمع الداخلي (InternalListener)
    // =============================================================
    private class InternalListener extends WebSocketListener {

        @Override
        public void onOpen(@NonNull WebSocket ws, @NonNull Response response) {
            Logger.i(TAG, "onOpen: HTTP " + response.code());
            isConnected.set(true);
            reconnectAttempts = 0;

            // تسجيل نجاح الاتصال في ServerConfig
            ServerConfig.markSuccess(appContext);

            mainHandler.post(callback::onConnected);
        }

        @Override
        public void onMessage(@NonNull WebSocket ws, @NonNull String text) {
            Logger.d(TAG, "onMessage: " + truncate(text, 200));
            mainHandler.post(() -> callback.onMessage(text));
        }

        @Override
        public void onClosing(@NonNull WebSocket ws, int code, @NonNull String reason) {
            Logger.w(TAG, "onClosing: " + code + " / " + reason);
            try {
                ws.close(1000, null);
            } catch (Exception ignored) {}
        }

        @Override
        public void onClosed(@NonNull WebSocket ws, int code, @NonNull String reason) {
            Logger.w(TAG, "onClosed: " + code + " / " + reason);
            handleDisconnect(code, reason);
        }

        @Override
        public void onFailure(@NonNull WebSocket ws, @NonNull Throwable t,
                              @Nullable Response response) {
            int code = (response != null) ? response.code() : -1;
            String reason = (t.getMessage() != null) ? t.getMessage() : "unknown";
            Logger.e(TAG, "onFailure: code=" + code + " reason=" + reason, t);

            mainHandler.post(() -> callback.onError(t));
            handleDisconnect(code, reason);
        }
    }

    // =============================================================
    // 11. إدارة الانقطاع وإعادة الاتصال
    // =============================================================
    private void handleDisconnect(int code, @NonNull String reason) {
        boolean wasConnected = isConnected.getAndSet(false);

        if (wasConnected) {
            mainHandler.post(() -> callback.onDisconnected(code, reason));
        }

        // تسجيل فشل الاتصال
        ServerConfig.markFailure(appContext);

        webSocket = null;

        if (isManuallyClosed.get()) {
            Logger.d(TAG, "Manual close, no reconnect");
            return;
        }

        scheduleReconnect();
    }

    private void scheduleReconnect() {
        if (isManuallyClosed.get()) return;

        reconnectAttempts++;

        // حساب التأخير بـ Exponential Backoff
        // 2s, 4s, 8s, 16s, 32s, 60s, 60s, ...
        long delay = Math.min(
                RECONNECT_DELAY_MIN_MS * (1L << Math.min(reconnectAttempts - 1, 5)),
                RECONNECT_DELAY_MAX_MS
        );

        Logger.i(TAG, "Reconnect in " + delay + " ms (attempt " + reconnectAttempts + ")");

        mainHandler.removeCallbacks(reconnectRunnable);
        mainHandler.postDelayed(reconnectRunnable, delay);
    }

    // =============================================================
    // 12. أدوات مساعدة
    // =============================================================

    /**
     * قطع نص طويل (للأمان في اللوج).
     */
    private static String truncate(String s, int max) {
        if (s == null) return "null";
        return s.length() <= max ? s : s.substring(0, max) + "...";
    }

    /**
     * تنظيف الموارد (يُستدعى من onDestroy للخدمة).
     */
    public void cleanup() {
        disconnect();
        mainHandler.removeCallbacksAndMessages(null);
        webSocket = null;
        client = null;
        Logger.d(TAG, "Cleanup done");
    }
}