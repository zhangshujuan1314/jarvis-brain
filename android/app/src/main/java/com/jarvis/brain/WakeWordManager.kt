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
 * Thread safety:
 *   - stop() sets isListening=false THEN calls porcupine.delete()
 *   - delete() interrupts the blocking process() call → PorcupineException
 *   - The thread catches the exception and exits cleanly
 *   - stop() joins the thread with timeout to ensure cleanup
 *
 * Degraded mode:
 *   - If Porcupine is not configured (no AccessKey or no .ppn),
 *     the service falls back to button-only mode (no wake word).
 *   - isActive returns false in degraded mode.
 */
class WakeWordManager(
    private val context: Context,
    private val onWakeWord: () -> Unit,
) {
    companion object {
        private const val TAG = "WakeWordManager"
        private const val SENSITIVITY = 0.7f  // 0.0–1.0, higher = more sensitive
        private const val KEYWORD_ASSET = "jarvis_zh_android.ppn"
        private const val THREAD_JOIN_TIMEOUT_MS = 3000L
    }

    private var porcupine: Porcupine? = null
    @Volatile private var isListening = false
    private var listenThread: Thread? = null
    private val lock = Any()

    /** Whether wake word detection is available and active. */
    val isActive: Boolean get() = isListening

    /** Whether Porcupine is configured (has AccessKey). */
    val isConfigured: Boolean get() {
        return BuildConfig.PORCUPINE_ACCESS_KEY.isNotEmpty() && hasKeywordAsset()
    }

    /**
     * Start listening for wake word.
     * Uses Porcupine's built-in mic access (no contention with recording pipeline).
     *
     * @return true if listening started, false if degraded to button-only mode
     */
    fun start(): Boolean = synchronized(lock) {
        if (isListening) return true

        val accessKey = BuildConfig.PORCUPINE_ACCESS_KEY
        if (accessKey.isEmpty()) {
            Log.w(TAG, "PORCUPINE_ACCESS_KEY not set — degraded to button-only mode")
            return false
        }

        if (!hasKeywordAsset()) {
            Log.w(TAG, "keyword asset '$KEYWORD_ASSET' not found — degraded to button-only mode")
            return false
        }

        try {
            porcupine = Porcupine.Builder()
                .setAccessKey(accessKey)
                .setKeywordPath(KEYWORD_ASSET)
                .setSensitivity(SENSITIVITY)
                .build(context)

            isListening = true
            listenThread = Thread({
                Log.i(TAG, "listening for wake word '贾维斯'...")
                try {
                    while (isListening) {
                        val keywordIndex = porcupine!!.process()
                        if (keywordIndex >= 0 && isListening) {
                            Log.i(TAG, "wake word detected! (index=$keywordIndex)")
                            isListening = false
                            onWakeWord()
                            return@Thread
                        }
                    }
                } catch (e: PorcupineException) {
                    // Expected when stop() calls delete() — not an error
                    if (isListening) {
                        Log.e(TAG, "porcupine process error: ${e.message}")
                    } else {
                        Log.d(TAG, "porcupine stopped cleanly")
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "unexpected error in wake word thread: ${e.message}")
                }
            }, "wake-word").also {
                it.isDaemon = true
                it.priority = Thread.MAX_PRIORITY
                it.start()
            }

            Log.i(TAG, "wake word listening started")
            return true

        } catch (e: PorcupineException) {
            Log.e(TAG, "porcupine init failed: ${e.message}")
            porcupine?.delete()
            porcupine = null
            return false
        }
    }

    /**
     * Stop listening. Thread-safe, idempotent.
     *
     * Sequence:
     *   1. Set isListening = false (prevents re-entry)
     *   2. Call porcupine.delete() (interrupts blocking process())
     *   3. Join thread (waits for clean exit)
     */
    fun stop() = synchronized(lock) {
        if (!isListening && porcupine == null) return

        isListening = false

        // Delete porcupine first — this interrupts the blocking process() call
        try {
            porcupine?.delete()
        } catch (e: PorcupineException) {
            Log.w(TAG, "porcupine delete: ${e.message}")
        }
        porcupine = null

        // Wait for thread to exit
        try {
            listenThread?.join(THREAD_JOIN_TIMEOUT_MS)
            if (listenThread?.isAlive == true) {
                Log.w(TAG, "wake word thread did not exit in time, interrupting")
                listenThread?.interrupt()
            }
        } catch (e: InterruptedException) {
            Log.w(TAG, "thread join interrupted")
        }
        listenThread = null

        Log.i(TAG, "wake word stopped")
    }

    /**
     * Release all resources. Call from service onDestroy.
     */
    fun release() {
        stop()
    }

    /**
     * Check if the keyword asset file exists.
     */
    private fun hasKeywordAsset(): Boolean {
        return try {
            context.assets.open(KEYWORD_ASSET).close()
            true
        } catch (e: Exception) {
            false
        }
    }
}
