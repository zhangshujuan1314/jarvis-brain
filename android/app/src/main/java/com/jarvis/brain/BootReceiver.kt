package com.jarvis.brain

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Auto-start Jarvis service on device boot.
 * Requires RECEIVE_BOOT_COMPLETED permission.
 */
class BootReceiver : BroadcastReceiver() {
    companion object {
        private const val TAG = "BootReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i(TAG, "boot completed, checking if service should start")
            val prefs = context.getSharedPreferences("jarvis", Context.MODE_PRIVATE)
            val autoStart = prefs.getBoolean("auto_start", false)
            if (autoStart) {
                val uri = prefs.getString("server_uri", "") ?: ""
                val token = prefs.getString("token", "") ?: ""
                if (uri.isNotEmpty() && token.isNotEmpty()) {
                    Log.i(TAG, "auto-starting Jarvis service")
                    JarvisService.start(context, uri, token)
                }
            }
        }
    }
}
