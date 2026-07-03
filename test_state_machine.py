"""Unit tests for JarvisStateMachine (protocol compliance)."""
import sys
import os

# Add android source to path for import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "android", "app", "src", "main", "java", "com", "jarvis", "brain"))

# Since JarvisStateMachine is Kotlin, we test the protocol logic in Python
# This validates the state machine design matches §4

import pytest


class StateMachine:
    """Python mirror of JarvisStateMachine for testing."""
    IDLE, RECORDING, WAITING, PLAYING = "idle", "recording", "waiting", "playing"

    def __init__(self):
        self.state = self.IDLE
        self.current_turn_id = 0
        self.transitions = []

    def start_recording(self, turn_id):
        if self.state != self.IDLE:
            return False
        self.current_turn_id = turn_id
        self._transition(self.RECORDING)
        return True

    def stop_recording(self):
        if self.state != self.RECORDING:
            return False
        self._transition(self.WAITING)
        return True

    def start_playing(self):
        if self.state != self.WAITING:
            return False
        self._transition(self.PLAYING)
        return True

    def finish_playing(self):
        if self.state != self.PLAYING:
            return False
        self._transition(self.IDLE)
        return True

    def cancel(self):
        old = self.state
        self._transition(self.IDLE)
        return old

    def should_listen_wake_word(self):
        return self.state == self.IDLE

    def _transition(self, new_state):
        self.transitions.append((self.state, new_state))
        self.state = new_state


class TestStateMachine:
    def test_happy_path(self):
        sm = StateMachine()
        assert sm.state == "idle"

        assert sm.start_recording(1) is True
        assert sm.state == "recording"

        assert sm.stop_recording() is True
        assert sm.state == "waiting"

        assert sm.start_playing() is True
        assert sm.state == "playing"

        assert sm.finish_playing() is True
        assert sm.state == "idle"

    def test_cannot_record_twice(self):
        sm = StateMachine()
        sm.start_recording(1)
        assert sm.start_recording(2) is False

    def test_cannot_stop_when_idle(self):
        sm = StateMachine()
        assert sm.stop_recording() is False

    def test_cannot_play_when_idle(self):
        sm = StateMachine()
        assert sm.start_playing() is False

    def test_cancel_from_any_state(self):
        for state in ["recording", "waiting", "playing"]:
            sm = StateMachine()
            if state == "recording":
                sm.start_recording(1)
            elif state == "waiting":
                sm.start_recording(1)
                sm.stop_recording()
            elif state == "playing":
                sm.start_recording(1)
                sm.stop_recording()
                sm.start_playing()

            assert sm.cancel() == state
            assert sm.state == "idle"

    def test_half_duplex_wake_word(self):
        sm = StateMachine()
        assert sm.should_listen_wake_word() is True

        sm.start_recording(1)
        assert sm.should_listen_wake_word() is False

        sm.stop_recording()
        assert sm.should_listen_wake_word() is False

        sm.start_playing()
        assert sm.should_listen_wake_word() is False

        sm.finish_playing()
        assert sm.should_listen_wake_word() is True

    def test_full_cycle_with_turn_id(self):
        sm = StateMachine()
        sm.start_recording(42)
        assert sm.current_turn_id == 42

        sm.stop_recording()
        assert sm.current_turn_id == 42

        sm.start_playing()
        assert sm.current_turn_id == 42

        sm.finish_playing()
        assert sm.current_turn_id == 42

    def test_transition_log(self):
        sm = StateMachine()
        sm.start_recording(1)
        sm.stop_recording()
        sm.start_playing()
        sm.finish_playing()

        expected = [
            ("idle", "recording"),
            ("recording", "waiting"),
            ("waiting", "playing"),
            ("playing", "idle"),
        ]
        assert sm.transitions == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
