"""
Startup configuration validation.
Checks dependencies, API keys, and model files before the server starts.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("jarvis-config")

MODEL_DIR = Path(__file__).parent / "models"


def validate_all() -> bool:
    """Run all validation checks. Returns True if all pass."""
    checks = [
        _check_python_deps(),
        _check_api_keys(),
        _check_model_files(),
    ]
    return all(checks)


def _check_python_deps() -> bool:
    """Verify required Python packages are importable."""
    required = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "dotenv": "python-dotenv",
        "websockets": "websockets",
        "anthropic": "anthropic",
        "httpx": "httpx",
        "numpy": "numpy",
        "sherpa_onnx": "sherpa-onnx",
    }
    ok = True
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            logger.error("Missing package: %s (pip install %s)", package, package)
            ok = False
    return ok


def _check_api_keys() -> bool:
    """Check API keys. Warn for optional, error for required."""
    ok = True

    token = os.environ.get("JARVIS_TOKEN", "")
    if not token or token == "dev-token-change-me":
        logger.warning("JARVIS_TOKEN not set or using default — change for production!")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        logger.warning("ANTHROPIC_API_KEY not set — LLM will not work")

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not elevenlabs_key:
        logger.warning("ELEVENLABS_API_KEY not set — TTS will not work")

    return ok


def _check_model_files() -> bool:
    """Verify STT model files exist."""
    asr_dir = MODEL_DIR / "csukuangfj_sherpa-onnx-paraformer-zh-small-2024-03-09"
    vad_path = MODEL_DIR / "istupakov_silero-vad-onnx" / "silero_vad.onnx"

    ok = True
    if not asr_dir.exists():
        logger.error("STT model not found: %s (run: python download_models.py)", asr_dir)
        ok = False
    if not vad_path.exists():
        logger.error("VAD model not found: %s (run: python download_models.py)", vad_path)
        ok = False
    return ok


def print_config_summary():
    """Print a summary of the current configuration."""
    model = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    token = os.environ.get("JARVIS_TOKEN", "")

    logger.info("─" * 40)
    logger.info("Jarvis Brain v0.4.0")
    logger.info("LLM model:  %s", model)
    logger.info("TTS voice:  %s", voice)
    logger.info("Auth token: %s", "configured" if token and token != "dev-token-change-me" else "DEFAULT (change!)")
    logger.info("STT models: %s", "ready" if _check_model_files() else "MISSING")
    logger.info("─" * 40)
