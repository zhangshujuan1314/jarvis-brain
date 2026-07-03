package com.jarvis.brain

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * Main activity — configuration UI + service control.
 *
 * Minimal UI for v1:
 *   - Server URI input
 *   - Token input
 *   - Start/Stop service button
 *   - Status display
 *   - Battery optimization exemption button
 */
class MainActivity : AppCompatActivity() {
    companion object {
        private const val REQUEST_PERMISSIONS = 1001
        private val REQUIRED_PERMISSIONS = arrayOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.POST_NOTIFICATIONS,
        )
    }

    private lateinit var editServerUri: EditText
    private lateinit var editToken: EditText
    private lateinit var btnToggle: Button
    private lateinit var btnBattery: Button
    private lateinit var textStatus: TextView
    private lateinit var textState: TextView

    private var isServiceRunning = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        editServerUri = findViewById(R.id.edit_server_uri)
        editToken = findViewById(R.id.edit_token)
        btnToggle = findViewById(R.id.btn_toggle)
        btnBattery = findViewById(R.id.btn_battery)
        textStatus = findViewById(R.id.text_status)
        textState = findViewById(R.id.text_state)

        // Load saved config
        val prefs = getSharedPreferences("jarvis", MODE_PRIVATE)
        editServerUri.setText(prefs.getString("server_uri", "wss://your-server/ws"))
        editToken.setText(prefs.getString("token", ""))

        btnToggle.setOnClickListener { toggleService() }
        btnBattery.setOnClickListener { requestBatteryExemption() }

        // Check permissions
        requestPermissions()

        updateUI()
    }

    override fun onResume() {
        super.onResume()
        updateUI()
    }

    private fun toggleService() {
        if (isServiceRunning) {
            JarvisService.stop(this)
            isServiceRunning = false
        } else {
            val uri = editServerUri.text.toString().trim()
            val token = editToken.text.toString().trim()

            if (uri.isEmpty() || token.isEmpty()) {
                Toast.makeText(this, "请填写服务器地址和 Token", Toast.LENGTH_SHORT).show()
                return
            }

            // Save config
            getSharedPreferences("jarvis", MODE_PRIVATE).edit()
                .putString("server_uri", uri)
                .putString("token", token)
                .apply()

            JarvisService.start(this, uri, token)
            isServiceRunning = true
        }
        updateUI()
    }

    private fun requestBatteryExemption() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(POWER_SERVICE) as PowerManager
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
                startActivity(intent)
            } else {
                Toast.makeText(this, "已忽略电池优化", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun requestPermissions() {
        val missing = REQUIRED_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQUEST_PERMISSIONS)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_PERMISSIONS) {
            val denied = permissions.zip(grantResults.toList())
                .filter { it.second != PackageManager.PERMISSION_GRANTED }
                .map { it.first }
            if (denied.isNotEmpty()) {
                Toast.makeText(this, "需要权限: ${denied.joinToString()}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun updateUI() {
        btnToggle.text = if (isServiceRunning) "停止服务" else "启动服务"
        textStatus.text = if (isServiceRunning) "运行中" else "已停止"
        textStatus.setTextColor(ContextCompat.getColor(this,
            if (isServiceRunning) android.R.color.holo_green_dark else android.R.color.holo_red_dark
        ))
        editServerUri.isEnabled = !isServiceRunning
        editToken.isEnabled = !isServiceRunning
    }
}
