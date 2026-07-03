package com.jarvis.brain

import android.app.*
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*

/**
 * Foreground service that runs wake word detection + voice pipeline.
 *
 * Lifecycle:
 *   1. Start as foreground (notification required)
 *   2. Initialize Porcupine wake word detector
 *   3. On wake word → connect to brain → record → play TTS
 *   4. Keep running in background (battery optimization exempt)
 *
 * §4 state machine: IDLE → RECORDING → WAITING → PLAYING → IDLE
 */
class JarvisService : Service(), BrainClient.Listener, AudioManager.AudioListener {
    companion object {
        private const val TAG = "JarvisService"
        private const val NOTIFICATION_ID = 1
        private const val CHANNEL_ID = "jarvis_service"

        const val EXTRA_SERVER_URI = "server_uri"
        const val EXTRA_TOKEN = "token"

        @Volatile
        private var isRunning = false

        fun start(context: Context, serverUri: String, token: String) {
            if (isRunning) {
                Log.w(TAG, "service already running, ignoring start()")
                return
            }
            val intent = Intent(context, JarvisService::class.java).apply {
                putExtra(EXTRA_SERVER_URI, serverUri)
                putExtra(EXTRA_TOKEN, token)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            isRunning = false
            context.stopService(Intent(context, JarvisService::class.java))
        }
    }

    private lateinit var brainClient: BrainClient
    private lateinit var audioManager: AudioManager
    private lateinit var stateMachine: JarvisStateMachine
    private var wakeWordManager: WakeWordManager? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var serverUri = ""
    private var token = ""

    // ── Lifecycle ─────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        isRunning = true
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification("初始化中..."),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)

        stateMachine = JarvisStateMachine()
        audioManager = AudioManager()
        audioManager.listener = this

        stateMachine.listener = object : JarvisStateMachine.StateListener {
            override fun onStateChanged(old: JarvisStateMachine.State, new: JarvisStateMachine.State) {
                updateNotification(when (new) {
                    JarvisStateMachine.State.IDLE -> "等待唤醒"
                    JarvisStateMachine.State.RECORDING -> "🎤 录音中"
                    JarvisStateMachine.State.WAITING -> "🧠 思考中"
                    JarvisStateMachine.State.PLAYING -> "🔊 播放中"
                })
                // §4: Half-duplex — pause/resume wake word based on state
                if (new == JarvisStateMachine.State.IDLE) {
                    wakeWordManager?.start()
                } else {
                    wakeWordManager?.stop()
                }
            }

            override fun onTimeout(state: JarvisStateMachine.State) {
                Log.w(TAG, "timeout in state: $state")
                stateMachine.cancel()
                brainClient.sendCancel(stateMachine.currentTurnId)
            }
        }

        Log.i(TAG, "service created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        serverUri = intent?.getStringExtra(EXTRA_SERVER_URI) ?: ""
        token = intent?.getStringExtra(EXTRA_TOKEN) ?: ""

        if (serverUri.isEmpty() || token.isEmpty()) {
            Log.e(TAG, "missing server URI or token")
            stopSelf()
            return START_NOT_STICKY
        }

        // Initialize brain client
        brainClient = BrainClient(serverUri, token)
        brainClient.listener = this
        brainClient.connect()

        // Initialize wake word detector
        initWakeWord()

        return START_STICKY  // Restart if killed
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        Log.i(TAG, "service destroying")
        isRunning = false
        wakeWordManager?.release()
        brainClient.disconnect()
        audioManager.release()
        scope.cancel()
        super.onDestroy()
    }

    // ── Wake word ─────────────────────────────────────────────────

    private fun initWakeWord() {
        try {
            wakeWordManager = WakeWordManager(this) {
                // Wake word detected!
                if (stateMachine.shouldListenWakeWord()) {
                    val turnId = brainClient.nextTurnId()
                    if (stateMachine.startRecording(turnId)) {
                        brainClient.sendWakeEvent(turnId)
                        audioManager.startRecording()
                    }
                }
            }

            val started = wakeWordManager?.start() ?: false
            if (started) {
                Log.i(TAG, "wake word active — say '贾维斯' to activate")
                updateNotification("等待唤醒 (说「贾维斯」)")
            } else if (wakeWordManager?.isConfigured == true) {
                Log.w(TAG, "wake word configured but failed to start")
                updateNotification("唤醒词启动失败 — 使用按钮触发")
            } else {
                Log.i(TAG, "wake word not configured — degraded to button-only mode")
                updateNotification("就绪 (按钮触发模式)")
            }
        } catch (e: Exception) {
            Log.e(TAG, "wake word init failed: ${e.message}")
            updateNotification("就绪 (按钮触发模式)")
        }
    }

    // ── BrainClient.Listener ──────────────────────────────────────

    override fun onConnected() {
        Log.i(TAG, "brain connected")
    }

    override fun onDisconnected(reason: String) {
        Log.w(TAG, "brain disconnected: $reason")
        audioManager.stopRecording()
        audioManager.stopPlayback()
        stateMachine.cancel()
    }

    override fun onTurnAccepted(turnId: Int) {
        Log.d(TAG, "turn $turnId accepted")
    }

    override fun onTurnRejected(turnId: Int, reason: String) {
        Log.w(TAG, "turn $turnId rejected: $reason")
        audioManager.stopRecording()
        stateMachine.cancel()
    }

    override fun onUtteranceEnd(turnId: Int) {
        if (!stateMachine.isCurrentTurn(turnId)) return
        Log.d(TAG, "utterance end for turn $turnId")
        audioManager.stopRecording()
        stateMachine.stopRecording()
    }

    override fun onSttResult(turnId: Int, text: String) {
        Log.i(TAG, "STT: $text")
    }

    override fun onState(turnId: Int, value: String) {
        if (!stateMachine.isCurrentTurn(turnId)) return
        when (value) {
            "thinking" -> { /* already in WAITING state */ }
            "speaking" -> {
                stateMachine.startPlaying()
                audioManager.initPlayback()
            }
            "cancelled" -> {
                audioManager.stopPlayback()
                stateMachine.cancel()
            }
        }
    }

    override fun onTtsAudio(turnId: Int, pcm: ByteArray) {
        if (!stateMachine.isCurrentTurn(turnId)) return
        audioManager.playPcmChunk(pcm)
    }

    override fun onTtsDone(turnId: Int) {
        if (!stateMachine.isCurrentTurn(turnId)) return
        audioManager.stopPlayback()
        stateMachine.finishPlaying()
    }

    override fun onError(turnId: Int, stage: String, message: String) {
        Log.e(TAG, "error [$stage]: $message")
        audioManager.stopRecording()
        audioManager.stopPlayback()
        stateMachine.cancel()
    }

    override fun onSessionSync(turnId: Int, userText: String, assistantText: String) {
        Log.i(TAG, "session sync: user=$userText assistant=$assistantText")
    }

    // ── AudioManager.AudioListener ────────────────────────────────

    override fun onAudioChunk(pcm: ByteArray) {
        brainClient.sendAudio(stateMachine.currentTurnId, pcm)
    }

    override fun onRecordingStopped() {
        // Recording stopped (either by utterance end or manual stop)
    }

    // ── Notification ──────────────────────────────────────────────

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "Jarvis 语音服务",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Jarvis 语音助手后台服务"
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Jarvis")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIFICATION_ID, buildNotification(text))
    }
}
