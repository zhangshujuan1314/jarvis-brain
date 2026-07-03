package com.jarvis.brain

import android.util.Log

/**
 * §4 Client state machine.
 *
 * States:
 *   IDLE       — wake word detection active
 *   RECORDING  — mic streaming to server, wake word paused
 *   WAITING    — waiting for LLM response, wake word paused
 *   PLAYING    — TTS playback, wake word paused
 *
 * Rules:
 *   - Half-duplex: RECORDING/WAITING/PLAYING pause wake word detection
 *   - Timeouts: RECORDING 15s, WAITING 20s → back to IDLE
 *   - Every abnormal path → IDLE with user-visible feedback
 */
class JarvisStateMachine {
    enum class State {
        IDLE, RECORDING, WAITING, PLAYING
    }

    interface StateListener {
        fun onStateChanged(old: State, new: State)
        fun onTimeout(state: State)
    }

    var listener: StateListener? = null

    @Volatile var state: State = State.IDLE
        private set

    @Volatile var currentTurnId: Int = 0
        private set

    private val lock = Any()

    /** Transition to RECORDING state. */
    fun startRecording(turnId: Int): Boolean = synchronized(lock) {
        if (state != State.IDLE) {
            Log.w(TAG, "startRecording rejected: state=$state")
            return false
        }
        currentTurnId = turnId
        transition(State.RECORDING)
        return true
    }

    /** Transition from RECORDING to WAITING. */
    fun stopRecording(): Boolean = synchronized(lock) {
        if (state != State.RECORDING) return false
        transition(State.WAITING)
        return true
    }

    /** Transition from WAITING to PLAYING. */
    fun startPlaying(): Boolean = synchronized(lock) {
        if (state != State.WAITING) return false
        transition(State.PLAYING)
        return true
    }

    /** Transition from PLAYING to IDLE (playback complete). */
    fun finishPlaying(): Boolean = synchronized(lock) {
        if (state != State.PLAYING) return false
        transition(State.IDLE)
        return true
    }

    /** Cancel current turn, return to IDLE. */
    fun cancel(): State = synchronized(lock) {
        val old = state
        transition(State.IDLE)
        return old
    }

    /** Check if wake word detection should be active. */
    fun shouldListenWakeWord(): Boolean = state == State.IDLE

    /** Check if the given turnId matches the current turn. */
    fun isCurrentTurn(turnId: Int): Boolean = turnId == currentTurnId

    private fun transition(newState: State) {
        val old = state
        if (old == newState) return
        state = newState
        Log.i(TAG, "$old → $newState (turn=$currentTurnId)")
        listener?.onStateChanged(old, newState)
    }

    companion object {
        private const val TAG = "StateMachine"
    }
}
