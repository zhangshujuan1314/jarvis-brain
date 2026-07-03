package com.jarvis.brain

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/**
 * Main activity — particle visualization + voice control.
 *
 * Full-screen particle background that reacts to voice.
 * Minimal overlay: status text + record button + settings.
 */
class MainActivity : AppCompatActivity() {
    companion object {
        private const val REQUEST_PERMISSIONS = 1001
        private val REQUIRED_PERMISSIONS = arrayOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.POST_NOTIFICATIONS,
        )
    }

    private lateinit var particleView: ParticleView
    private lateinit var textStatus: TextView
    private lateinit var btnRecord: Button
    private lateinit var btnCancel: Button
    private lateinit var btnSettings: Button
    private lateinit var configPanel: LinearLayout
    private lateinit var editServerUri: EditText
    private lateinit var editToken: EditText
    private lateinit var btnConnect: Button

    private var isServiceRunning = false
    private var serviceBinder: JarvisService.LocalBinder? = null

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            serviceBinder = binder as? JarvisService.LocalBinder
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            serviceBinder = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        particleView = findViewById(R.id.particle_view)
        textStatus = findViewById(R.id.text_status)
        btnRecord = findViewById(R.id.btn_record)
        btnCancel = findViewById(R.id.btn_cancel)
        btnSettings = findViewById(R.id.btn_settings)
        configPanel = findViewById(R.id.config_panel)
        editServerUri = findViewById(R.id.edit_server_uri)
        editToken = findViewById(R.id.edit_token)
        btnConnect = findViewById(R.id.btn_connect)

        // Load saved config
        val prefs = getSharedPreferences("jarvis", MODE_PRIVATE)
        editServerUri.setText(prefs.getString("server_uri", "wss://your-server/ws"))
        editToken.setText(prefs.getString("token", ""))

        btnRecord.setOnClickListener { toggleRecording() }
        btnCancel.setOnClickListener { cancelTurn() }
        btnSettings.setOnClickListener { configPanel.visibility = if (configPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE }
        btnConnect.setOnClickListener { toggleService() }

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
            particleView.setState("idle")
        } else {
            val uri = editServerUri.text.toString().trim()
            val token = editToken.text.toString().trim()
            if (uri.isEmpty() || token.isEmpty()) {
                Toast.makeText(this, "请填写服务器地址和 Token", Toast.LENGTH_SHORT).show()
                return
            }
            getSharedPreferences("jarvis", MODE_PRIVATE).edit()
                .putString("server_uri", uri)
                .putString("token", token)
                .apply()
            JarvisService.start(this, uri, token)
            isServiceRunning = true
            configPanel.visibility = View.GONE
        }
        updateUI()
    }

    private fun toggleRecording() {
        // Toggle via service broadcast or direct action
        if (!isServiceRunning) {
            Toast.makeText(this, "请先启动服务", Toast.LENGTH_SHORT).show()
            return
        }
        // The service handles recording via wake word or button
        // For button mode, send a broadcast
        val intent = Intent("com.jarvis.brain.TOGGLE_RECORDING")
        sendBroadcast(intent)
    }

    private fun cancelTurn() {
        val intent = Intent("com.jarvis.brain.CANCEL")
        sendBroadcast(intent)
        particleView.setState("idle")
        particleView.setAudioLevel(0f)
    }

    /** Called by service to update particle state. */
    fun updateParticleState(state: String) {
        particleView.setState(state)
    }

    /** Called by service to update audio level. */
    fun updateAudioLevel(level: Float) {
        particleView.setAudioLevel(level)
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
        if (isServiceRunning) {
            textStatus.text = "JARVIS"
            textStatus.setTextColor(ContextCompat.getColor(this, android.R.color.holo_green_dark))
            btnRecord.isEnabled = true
        } else {
            textStatus.text = "JARVIS"
            textStatus.setTextColor(ContextCompat.getColor(this, android.R.color.darker_gray))
            btnRecord.isEnabled = false
        }
    }
}
