package com.jarvis.brain

import android.util.Log
import kotlinx.coroutines.*
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * WebSocket client for Jarvis Brain server.
 *
 * Protocol (§5, §6):
 *   Control frames: JSON text frames
 *   Audio frames: binary [0x01][turn_id:4 LE][PCM] (upstream)
 *                 binary [0x02][turn_id:4 LE][PCM] (downstream TTS)
 */
class BrainClient(
    private val serverUri: String,
    private val token: String,
    private val deviceId: String = "android-${android.os.Build.MODEL}",
) {
    companion object {
        private const val TAG = "BrainClient"
        private const val CHANNEL_MIC: Byte = 0x01
        private const val CHANNEL_TTS: Byte = 0x02
        private const val RECONNECT_INITIAL_MS = 1000L
        private const val RECONNECT_MAX_MS = 30_000L
    }

    interface Listener {
        fun onConnected()
        fun onDisconnected(reason: String)
        fun onTurnAccepted(turnId: Int)
        fun onTurnRejected(turnId: Int, reason: String)
        fun onUtteranceEnd(turnId: Int)
        fun onSttResult(turnId: Int, text: String)
        fun onState(turnId: Int, value: String)
        fun onTtsAudio(turnId: Int, pcm: ByteArray)
        fun onTtsDone(turnId: Int)
        fun onError(turnId: Int, stage: String, message: String)
        fun onSessionSync(turnId: Int, userText: String, assistantText: String)
    }

    var listener: Listener? = null

    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)  // No read timeout for WS
        .pingInterval(30, TimeUnit.SECONDS)      // Keepalive
        .build()

    private var ws: WebSocket? = null
    private val turnIdCounter = AtomicInteger(0)
    private var reconnectDelay = RECONNECT_INITIAL_MS
    private var shouldReconnect = true
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    val currentTurnId: Int get() = turnIdCounter.get()

    fun connect() {
        shouldReconnect = true
        _connect()
    }

    fun disconnect() {
        shouldReconnect = false
        scope.coroutineContext.cancelChildren()
        ws?.close(1000, "client_disconnect")
        ws = null
    }

    fun nextTurnId(): Int = turnIdCounter.incrementAndGet()

    fun sendWakeEvent(turnId: Int) {
        sendJson(mapOf("type" to "wake_event", "turn_id" to turnId))
    }

    fun sendAudioDone(turnId: Int) {
        sendJson(mapOf("type" to "audio_done", "turn_id" to turnId))
    }

    fun sendCancel(turnId: Int) {
        sendJson(mapOf("type" to "cancel", "turn_id" to turnId))
    }

    /**
     * Send PCM audio chunk as binary frame (§5).
     * @param turnId Current turn ID
     * @param pcm PCM16 audio data (16kHz, 16-bit, mono)
     */
    fun sendAudio(turnId: Int, pcm: ByteArray) {
        val frame = ByteArray(5 + pcm.size)
        frame[0] = CHANNEL_MIC
        frame[1] = (turnId and 0xFF).toByte()
        frame[2] = ((turnId shr 8) and 0xFF).toByte()
        frame[3] = ((turnId shr 16) and 0xFF).toByte()
        frame[4] = ((turnId shr 24) and 0xFF).toByte()
        System.arraycopy(pcm, 0, frame, 5, pcm.size)
        ws?.send(frame.toByteString())
    }

    // ── Internal ──────────────────────────────────────────────────

    private fun _connect() {
        val request = Request.Builder()
            .url(serverUri)
            .build()

        ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "connected to $serverUri")
                // Send auth frame (§6.1)
                webSocket.send(JSONObject().apply {
                    put("type", "auth")
                    put("token", token)
                    put("device_id", deviceId)
                    put("platform", "android")
                }.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val msg = JSONObject(text)
                    handleMessage(msg)
                } catch (e: Exception) {
                    Log.e(TAG, "message parse error: $e")
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                handleBinary(bytes.toByteArray())
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "closed: $code $reason")
                listener?.onDisconnected(reason)
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "connection failure: ${t.message}")
                listener?.onDisconnected(t.message ?: "unknown")
                scheduleReconnect()
            }
        })
    }

    private fun handleMessage(msg: JSONObject) {
        val type = msg.optString("type")
        val turnId = msg.optInt("turn_id", 0)

        when (type) {
            "auth_ok" -> {
                Log.i(TAG, "auth ok")
                reconnectDelay = RECONNECT_INITIAL_MS
                listener?.onConnected()
            }
            "auth_fail" -> {
                Log.e(TAG, "auth fail: ${msg.optString("reason")}")
                shouldReconnect = false
                listener?.onDisconnected("auth_fail: ${msg.optString("reason")}")
            }
            "turn_accepted" -> listener?.onTurnAccepted(turnId)
            "turn_rejected" -> listener?.onTurnRejected(turnId, msg.optString("reason"))
            "utterance_end" -> listener?.onUtteranceEnd(turnId)
            "stt_result" -> listener?.onSttResult(turnId, msg.optString("text"))
            "state" -> listener?.onState(turnId, msg.optString("value"))
            "tts_done" -> listener?.onTtsDone(turnId)
            "error" -> listener?.onError(turnId, msg.optString("stage"), msg.optString("message"))
            "session_sync" -> listener?.onSessionSync(
                turnId, msg.optString("user_text"), msg.optString("assistant_text")
            )
            "ping" -> sendJson(mapOf("type" to "pong"))
            "pong" -> { /* ignore */ }
        }
    }

    private fun handleBinary(data: ByteArray) {
        if (data.size < 6) return
        val channel = data[0]
        if (channel != CHANNEL_TTS) return

        val turnId = data[1].toInt() or
                (data[2].toInt() and 0xFF shl 8) or
                (data[3].toInt() and 0xFF shl 16) or
                (data[4].toInt() and 0xFF shl 24)

        val pcm = data.copyOfRange(5, data.size)
        listener?.onTtsAudio(turnId, pcm)
    }

    private fun sendJson(fields: Map<String, Any>) {
        val json = JSONObject()
        for ((k, v) in fields) json.put(k, v)
        ws?.send(json.toString())
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        scope.launch {
            Log.i(TAG, "reconnecting in ${reconnectDelay}ms...")
            delay(reconnectDelay)
            reconnectDelay = (reconnectDelay * 2).coerceAtMost(RECONNECT_MAX_MS)
            _connect()
        }
    }
}
