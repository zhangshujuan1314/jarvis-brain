"""
Particle visualization window for PC client.

Opens a standalone window with voice-reactive particles.
No browser needed — runs alongside the PC client.

Usage:
  from particle_window import ParticleWindow
  pw = ParticleWindow()
  pw.start()  # Runs in background thread
  pw.set_level(50)  # Update audio level
  pw.set_state("recording")  # Change visual state
"""
from __future__ import annotations

import math
import random
import threading
import time
from typing import Optional

try:
    import tkinter as tk
    HAS_TK = True
except ImportError:
    HAS_TK = False


class Particle:
    def __init__(self, cx: float, cy: float):
        angle = random.random() * math.pi * 2
        radius = 80 + random.random() * 120
        self.base_x = cx + math.cos(angle) * radius
        self.base_y = cy + math.sin(angle) * radius
        self.x = self.base_x
        self.y = self.base_y
        self.vx = 0.0
        self.vy = 0.0
        self.size = 2 + random.random() * 3
        self.alpha = 0.3 + random.random() * 0.5
        self.phase = random.random() * math.pi * 2
        self.speed = 0.005 + random.random() * 0.01
        self.orbit_radius = radius
        self.orbit_angle = angle
        self.cx = cx
        self.cy = cy

    def update(self, audio_level: float, state: str, t: float):
        intensity = audio_level / 100.0

        if state == "recording":
            push = intensity * 6
            angle = math.atan2(self.y - self.cy, self.x - self.cx)
            self.vx += math.cos(angle) * push
            self.vy += math.sin(angle) * push
            self.vx += (random.random() - 0.5) * intensity * 3
            self.vy += (random.random() - 0.5) * intensity * 3
        elif state == "thinking":
            self.orbit_angle += 0.03
            r = self.orbit_radius + math.sin(t * 3 + self.phase) * 20
            self.base_x = self.cx + math.cos(self.orbit_angle) * r
            self.base_y = self.cy + math.sin(self.orbit_angle) * r
        elif state == "playing":
            wave = math.sin(t * 5 + self.orbit_angle * 3) * intensity * 25
            self.base_x = self.cx + math.cos(self.orbit_angle) * (self.orbit_radius + wave)
            self.base_y = self.cy + math.sin(self.orbit_angle) * (self.orbit_radius + wave)
        else:
            self.orbit_angle += self.speed
            self.base_x = self.cx + math.cos(self.orbit_angle) * self.orbit_radius
            self.base_y = self.cy + math.sin(self.orbit_angle) * self.orbit_radius

        dx = self.base_x - self.x
        dy = self.base_y - self.y
        self.vx += dx * 0.02
        self.vy += dy * 0.02
        self.vx *= 0.92
        self.vy *= 0.92
        self.x += self.vx
        self.y += self.vy

        if state == "recording":
            self.alpha = 0.4 + intensity * 0.6
        elif state == "thinking":
            self.alpha = 0.5 + math.sin(t * 4 + self.phase) * 0.3
        else:
            self.alpha = 0.3 + math.sin(t + self.phase) * 0.15


class ParticleWindow:
    """Standalone particle visualization window."""

    def __init__(self, width: int = 600, height: int = 600):
        self.width = width
        self.height = height
        self.audio_level = 0.0
        self.target_level = 0.0
        self.state = "idle"
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._particles: list[Particle] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the particle window in a background thread."""
        if not HAS_TK:
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Stop the particle window."""
        self._running = False
        if self._root:
            try:
                self._root.quit()
            except Exception:
                pass

    def set_level(self, level: float):
        """Update audio level (0-100)."""
        self.target_level = max(0, min(100, level))

    def set_state(self, state: str):
        """Update visual state (idle/recording/waiting/playing)."""
        self.state = state

    def _run(self):
        """Main window loop (runs in thread)."""
        self._root = tk.Tk()
        self._root.title("Jarvis")
        self._root.configure(bg="black")
        self._root.geometry(f"{self.width}x{self.height}")
        self._root.resizable(True, True)

        self._canvas = tk.Canvas(
            self._root, width=self.width, height=self.height,
            bg="black", highlightthickness=0
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        cx = self.width / 2
        cy = self.height / 2
        self._particles = [Particle(cx, cy) for _ in range(200)]

        self._root.bind("<Configure>", self._on_resize)
        self._animate()
        self._root.mainloop()

    def _on_resize(self, event):
        self.width = event.width
        self.height = event.height
        cx = self.width / 2
        cy = self.height / 2
        for p in self._particles:
            p.cx = cx
            p.cy = cy

    def _animate(self):
        if not self._running:
            return

        t = time.time()
        self.audio_level += (self.target_level - self.audio_level) * 0.15

        cx = self.width / 2
        cy = self.height / 2

        self._canvas.delete("all")

        # Center glow (oval)
        glow_r = 100 + self.audio_level * 1.5
        colors = {
            "idle": "#1a3a6e",
            "recording": "#0a3a1a",
            "waiting": "#3a3a0a",
            "playing": "#2a1a4a",
        }
        self._canvas.create_oval(
            cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
            fill=colors.get(self.state, "#1a3a6e"), outline=""
        )

        # Update and draw particles
        state_colors = {
            "idle": "#4f8cff",
            "recording": "#22c55e",
            "waiting": "#facc15",
            "playing": "#a78bfa",
        }
        color = state_colors.get(self.state, "#4f8cff")

        for p in self._particles:
            p.update(self.audio_level, self.state, t)
            r = p.size
            alpha_hex = format(int(p.alpha * 255), '02x')
            fill = color + alpha_hex
            self._canvas.create_oval(
                p.x - r, p.y - r, p.x + r, p.y + r,
                fill=fill, outline=""
            )

        self._root.after(16, self._animate)  # ~60fps
