package com.jarvis.brain

import android.content.Context
import android.util.Log
import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineException

/**
 * Wake word detection using Porcupine (Picovoice).
 *
 * M2: Local, offline, low-power wake word detection.
 * Listens for "贾维斯" (Jarvis) keyword.
 *
 * Design:
 *   - Porcupine uses its own built-in mic access (simpler, no contention)
 *   - On wake word → callback fires → JarvisService stops Porcupine, starts recording
 *   - When back to IDLE → JarvisService calls start() again
 *
 * Porcupine requires AccessKey from Picovoice Console:
 *   https://console.picovoice.ai/
 *   Free tier: 3 wake words
 */
class WakeWordManager(
    private val context: Context,
    private val onWakeWord: () -> Unit,
) {
    companion object {
        private const val TAG = "WakeWordManager"
        private const val SENSITIVITY = 0.7f  // 0.0–1.0, higher = more sensitive
    }

    private var porcupine: Porcupine? = null
    @Volatile private var isListening = false
    private var listenThread: Thread? = null

    /**
     * Start listening for wake word.
     * Uses Porcupine's built-in mic access.
     */
    fun start() {
        if (isListening) return

        val accessKey = BuildConfig.PORCUPINE_ACCESS_KEY
        if (accessKey.isEmpty()) {
            Log.w(TAG, "PORCUPINE_ACCESS_KEY not set — wake word disabled")
            return
        }

        try {
            porcupine = Porcupine.Builder()
                .setAccessKey(accessKey)
                .setKeywordPath("jarvis_zh_android.ppn")  // Must be in assets/
                .setSensitivity(SENSITIVITY)
                .build(context)

            isListening = true
            listenThread = Thread({
                Log.i(TAG, "listening for wake word '贾维斯'...")
                try {
                    // Porcupine.process() handles mic internally
                    // Blocks until wake word detected or stop() called
                    while (isListening) {
                        val keywordIndex = porcupine!!.process()
                        if (keywordIndex >= 0 && isListening) {
                            Log.i(TAG, "wake word detected! (index=$keywordIndex)")
                            isListening = false  // Stop before callback
                            onWakeWord()
                            return@Thread
                        }
                    }
                } catch (e: PorcupineException) {
                    if (isListening) {
                        Log.e(TAG, "porcupine process error: ${e.message}")
                    }
                }
            }, "wake-word").also {
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }

        } catch (e: PorcupineException) {
            Log.e(TAG, "porcupine init failed: ${e.message}")
        }
    }

    /**
     * Stop listening. Safe to call when already stopped.
     */
    fun stop() {
        isListening = false
        listenThread?.join(2000)
        listenThread = null
        try {
            porcupine?.delete()
        } catch (e: PorcupineException) {
            Log.w(TAG, "porcupine delete error: ${e.message}")
        }
        porcupine = null
    }

    /**
     * Release all resources. Call from service onDestroy.
     */
    fun release() {
        stop()
    }

    val isActive: Boolean get() = isListening
}
