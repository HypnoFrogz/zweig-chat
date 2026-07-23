package ru.nebenan.app

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.app.NotificationManager
import android.provider.Settings
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {

    private val DIAGNOSTICS_CHANNEL = "ru.nebenan/diagnostics"

    // ─── Lifecycle ───────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyLockScreenFlags()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
    }

    // ─── Flutter engine ───────────────────────────────────────────────────────

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        // Diagnostics channel
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, DIAGNOSTICS_CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "getDeviceInfo" -> result.success(mapOf(
                        "manufacturer" to Build.MANUFACTURER.lowercase(),
                        "model"        to Build.MODEL,
                        "sdkInt"       to Build.VERSION.SDK_INT
                    ))
                    "isBatteryOptimizationIgnored" -> {
                        val pm = getSystemService(POWER_SERVICE) as PowerManager
                        result.success(pm.isIgnoringBatteryOptimizations(packageName))
                    }
                    "requestIgnoreBatteryOptimization" -> {
                        try {
                            startActivity(Intent(
                                Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                Uri.parse("package:$packageName")
                            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                            result.success(true)
                        } catch (e: Exception) {
                            result.error("FAILED", e.message, null)
                        }
                    }
                    "openBatterySettings" -> {
                        try {
                            startActivity(Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                            result.success(true)
                        } catch (_: Exception) {
                            startActivity(Intent(Settings.ACTION_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                            result.success(true)
                        }
                    }
                    "canUseFullScreenIntent" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
                            result.success(nm.canUseFullScreenIntent())
                        } else {
                            result.success(true)
                        }
                    }
                    "openFullScreenIntentSettings" -> {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                            try {
                                startActivity(Intent(
                                    Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                                    Uri.parse("package:$packageName")
                                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                                result.success(true)
                            } catch (e: Exception) {
                                result.error("FAILED", e.message, null)
                            }
                        } else {
                            result.success(true)
                        }
                    }
                    "isGmsAvailable" -> {
                        val available = try {
                            packageManager.getPackageInfo("com.google.android.gms", 0)
                            true
                        } catch (_: PackageManager.NameNotFoundException) { false }
                        result.success(available)
                    }
                    "openAutostartSettings" -> {
                        val manufacturer = Build.MANUFACTURER.lowercase()
                        val intent = when {
                            manufacturer.contains("huawei") || manufacturer.contains("honor") ->
                                Intent().apply {
                                    setClassName("com.huawei.systemmanager",
                                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity")
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            manufacturer.contains("xiaomi") || manufacturer.contains("redmi") ->
                                Intent("miui.intent.action.OP_AUTO_START").apply {
                                    addCategory(Intent.CATEGORY_DEFAULT)
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            manufacturer.contains("oppo") ->
                                Intent().apply {
                                    setClassName("com.coloros.safecenter",
                                        "com.coloros.safecenter.permission.startup.StartupAppListActivity")
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            manufacturer.contains("vivo") ->
                                Intent().apply {
                                    setClassName("com.vivo.permissionmanager",
                                        "com.vivo.permissionmanager.activity.BgStartUpManagerActivity")
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            manufacturer.contains("samsung") ->
                                Intent().apply {
                                    component = android.content.ComponentName(
                                        "com.samsung.android.lool",
                                        "com.samsung.android.sm.battery.ui.BatteryActivity")
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                            else -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.parse("package:$packageName")).apply {
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            }
                        }
                        try {
                            startActivity(intent)
                            result.success(true)
                        } catch (_: Exception) {
                            try {
                                startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                    Uri.parse("package:$packageName")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                                result.success(true)
                            } catch (e2: Exception) {
                                result.error("FAILED", e2.message, null)
                            }
                        }
                    }
                    "openNotificationSettings" -> {
                        try {
                            startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                                putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            })
                            result.success(true)
                        } catch (e: Exception) {
                            result.error("FAILED", e.message, null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    // ─── Helpers ──────────────────────────────────────────────────────────────

    private fun applyLockScreenFlags() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            )
        }
    }
}
