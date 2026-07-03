package com.jarvis.brain

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Particle visualization view for Android.
 *
 * Displays voice-reactive particles that respond to audio levels.
 * - Idle: gentle orbit, blue
 * - Recording: explode with voice, green
 * - Thinking: fast orbit, yellow
 * - Playing: wave with audio, purple
 */
class ParticleView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private data class Particle(
        var baseX: Float, var baseY: Float,
        var x: Float, var y: Float,
        var vx: Float = 0f, var vy: Float = 0f,
        val size: Float = 2f + Random.nextFloat() * 3f,
        var alpha: Float = 0.3f + Random.nextFloat() * 0.5f,
        val phase: Float = Random.nextFloat() * Math.PI.toFloat() * 2f,
        val speed: Float = 0.005f + Random.nextFloat() * 0.01f,
        val orbitRadius: Float = 80f + Random.nextFloat() * 120f,
        var orbitAngle: Float = Random.nextFloat() * Math.PI.toFloat() * 2f,
    )

    companion object {
        private const val PARTICLE_COUNT = 200
        private val STATE_COLORS = mapOf(
            "idle" to Color.rgb(79, 140, 255),
            "recording" to Color.rgb(34, 197, 94),
            "waiting" to Color.rgb(250, 204, 21),
            "playing" to Color.rgb(167, 139, 250),
        )
    }

    private val particles = mutableListOf<Particle>()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var audioLevel = 0f
    private var targetLevel = 0f
    private var currentState = "idle"
    private var cx = 0f
    private var cy = 0f
    private var lastTime = System.nanoTime()

    init {
        setLayerType(LAYER_TYPE_HARDWARE, null)
    }

    fun setAudioLevel(level: Float) {
        targetLevel = level.coerceIn(0f, 100f)
    }

    fun setState(state: String) {
        currentState = state
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        cx = w / 2f
        cy = h / 2f - 100f  // Offset up for UI overlay

        particles.clear()
        repeat(PARTICLE_COUNT) {
            val angle = Random.nextFloat() * Math.PI.toFloat() * 2f
            val radius = 80f + Random.nextFloat() * 120f
            particles.add(Particle(
                baseX = cx + cos(angle) * radius,
                baseY = cy + sin(angle) * radius,
                x = cx + cos(angle) * radius,
                y = cy + sin(angle) * radius,
                orbitRadius = radius,
                orbitAngle = angle,
            ))
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = System.nanoTime()
        val dt = ((now - lastTime) / 1_000_000_000f).coerceAtMost(0.05f)
        lastTime = now
        val t = now / 1_000_000_000f

        // Smooth audio level
        audioLevel += (targetLevel - audioLevel) * 0.15f
        val intensity = audioLevel / 100f

        // Draw center glow
        val glowRadius = 150f + audioLevel * 2f
        val glowColor = when (currentState) {
            "recording" -> Color.argb(15, 34, 197, 94)
            "waiting" -> Color.argb(12, 250, 204, 21)
            "playing" -> Color.argb(15, 167, 139, 250)
            else -> Color.argb(10, 79, 140, 255)
        }
        paint.shader = RadialGradient(cx, cy, glowRadius, glowColor, Color.TRANSPARENT, Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, cy, glowRadius, paint)
        paint.shader = null

        // Update and draw particles
        val color = STATE_COLORS[currentState] ?: STATE_COLORS["idle"]!!

        for (p in particles) {
            // Update physics
            when (currentState) {
                "recording" -> {
                    val push = intensity * 6f
                    val angle = atan2(p.y - cy, p.x - cx)
                    p.vx += cos(angle) * push
                    p.vy += sin(angle) * push
                    p.vx += (Random.nextFloat() - 0.5f) * intensity * 3f
                    p.vy += (Random.nextFloat() - 0.5f) * intensity * 3f
                }
                "waiting" -> {
                    p.orbitAngle += 0.03f
                    val r = p.orbitRadius + sin(t * 3f + p.phase) * 20f
                    p.baseX = cx + cos(p.orbitAngle) * r
                    p.baseY = cy + sin(p.orbitAngle) * r
                }
                "playing" -> {
                    val wave = sin(t * 5f + p.orbitAngle * 3f) * intensity * 25f
                    p.baseX = cx + cos(p.orbitAngle) * (p.orbitRadius + wave)
                    p.baseY = cy + sin(p.orbitAngle) * (p.orbitRadius + wave)
                }
                else -> {
                    p.orbitAngle += p.speed
                    p.baseX = cx + cos(p.orbitAngle) * p.orbitRadius
                    p.baseY = cy + sin(p.orbitAngle) * p.orbitRadius
                }
            }

            // Spring + damping
            p.vx += (p.baseX - p.x) * 0.02f
            p.vy += (p.baseY - p.y) * 0.02f
            p.vx *= 0.92f
            p.vy *= 0.92f
            p.x += p.vx
            p.y += p.vy

            // Alpha
            p.alpha = when (currentState) {
                "recording" -> 0.4f + intensity * 0.6f
                "waiting" -> 0.5f + sin(t * 4f + p.phase) * 0.3f
                else -> 0.3f + sin(t + p.phase) * 0.15f
            }

            // Draw particle
            paint.color = Color.argb((p.alpha * 255).toInt(), Color.red(color), Color.green(color), Color.blue(color))
            canvas.drawCircle(p.x, p.y, p.size, paint)

            // Glow for larger particles
            if (p.size > 3f) {
                paint.color = Color.argb((p.alpha * 25).toInt(), Color.red(color), Color.green(color), Color.blue(color))
                canvas.drawCircle(p.x, p.y, p.size * 3f, paint)
            }
        }

        // Schedule next frame
        postInvalidateOnAnimation()
    }
}
