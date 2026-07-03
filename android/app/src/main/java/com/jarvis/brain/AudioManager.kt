package com.jarvis.brain

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.util.Log
import kotlin.math.sqrt

/**
 * Audio recording (mic → PCM16) and playback (PCM16 → speaker).
 *
 * §5 spec:
 *   - 16kHz, 16-bit, mono
 *   - 100ms chunks (1600 samples = 3200 bytes)
 *   - M4.2: energy-based silence filtering
 */
class AudioManager {
    companion object {
        private const val TAG = "AudioManager"
        const val SAMPLE_RATE = 16000
        const val CHUNK_MS = 100
        const val CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS / 1000  // 1600
        const val CHUNK_BYTES = CHUNK_SAMPLES * 2  // 16-bit = 2 bytes/sample

        // M4.2: Energy threshold for silence detection
        // Speech RMS: 0.01–0.1; silence: <0.005
        private const val ENERGY_THRESHOLD = 300.0  // PCM16 RMS threshold
    }

    interface AudioListener {
        fun onAudioChunk(pcm: ByteArray)
        fun onRecordingStopped()
    }

    var listener: AudioListener? = null

    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null
    @Volatile private var isRecording = false
    @Volatile private var silenceCount = 0

    /**
     * Start recording from microphone.
     * Chunks delivered via listener.onAudioChunk() at 100ms intervals.
     */
    @SuppressLint("MissingPermission")
    fun startRecording() {
        if (isRecording) return

        val bufferSize = maxOf(
            AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            ),
            CHUNK_BYTES * 4
        )

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize
        ).also { record ->
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord failed to initialize")
                record.release()
                return
            }
        }

        isRecording = true
        silenceCount = 0
        audioRecord?.startRecording()

        // Read chunks on background thread
        Thread({
            val buffer = ByteArray(CHUNK_BYTES)
            while (isRecording) {
                val read = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (read == CHUNK_BYTES) {
                    // M4.2: Energy-based silence filtering
                    val rms = calculateRms(buffer)
                    if (rms < ENERGY_THRESHOLD) {
                        silenceCount++
                        continue  // Skip silence
                    }
                    silenceCount = 0
                    listener?.onAudioChunk(buffer.copyOf())
                }
            }
            listener?.onRecordingStopped()
        }, "audio-record").start()

        Log.i(TAG, "recording started")
    }

    fun stopRecording() {
        if (!isRecording) return
        isRecording = false
        try {
            audioRecord?.stop()
        } catch (e: IllegalStateException) {
            Log.w(TAG, "AudioRecord stop error: ${e.message}")
        }
        audioRecord?.release()
        audioRecord = null
        Log.i(TAG, "recording stopped (silence_chunks=$silenceCount)")
    }

    /**
     * Initialize audio track for playback.
     * Call before playPcmChunk().
     */
    fun initPlayback() {
        if (audioTrack != null) return

        val bufferSize = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )

        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(bufferSize, CHUNK_BYTES * 8))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        audioTrack?.play()
        Log.i(TAG, "playback initialized")
    }

    /**
     * Play a PCM chunk. Call initPlayback() first.
     */
    fun playPcmChunk(pcm: ByteArray) {
        audioTrack?.write(pcm, 0, pcm.size)
    }

    /**
     * Stop playback and release resources.
     */
    fun stopPlayback() {
        audioTrack?.stop()
        audioTrack?.release()
        audioTrack = null
        Log.i(TAG, "playback stopped")
    }

    fun release() {
        stopRecording()
        stopPlayback()
    }

    private fun calculateRms(pcm: ByteArray): Double {
        var sum = 0.0
        var i = 0
        while (i < pcm.size - 1) {
            val sample = (pcm[i].toInt() and 0xFF) or (pcm[i + 1].toInt() shl 8)
            sum += sample.toDouble() * sample.toDouble()
            i += 2
        }
        return sqrt(sum / (pcm.size / 2))
    }
}
