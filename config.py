"""
Startup configuration validation.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("jarvis-config")

MODEL_DIR = Path(__file__).parent / "models"


def validate_all() -> bool:
    """Run all validation checks. Non-fatal."""
    checks = [
        _check_python_deps(),
        _check_api_keys(),
        _check_model_files(),
    ]
    ok = all(checks)
    if not ok:
        logger.warning("Some components unavailable — server will run in degraded mode")
    return ok


def _check_python_deps() -> bool:
    required = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "dotenv": "python-dotenv",
        "httpx": "httpx",
        "numpy": "numpy",
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
    ok = True

    token = os.environ.get("JARVIS_TOKEN", "")
    if not token or token == "dev-token-change-me":
        logger.warning("JARVIS_TOKEN not set or using default — change for production!")

    llm_key = os.environ.get("LLM_API_KEY", "")
    if not llm_key:
        logger.warning("LLM_API_KEY not set — LLM will not work")

    tts_key = os.environ.get("TTS_API_KEY", "") or llm_key
    if not tts_key:
        logger.warning("TTS_API_KEY not set — TTS will not work")

    return ok


def _check_model_files() -> bool:
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
    model = os.environ.get("LLM_MODEL", "mimo-v2.5")
    api_base = os.environ.get("LLM_API_BASE", "https://api.xiaomimimo.com/v1")
    tts_model = os.environ.get("TTS_MODEL", "mimo-v2.5-tts")
    token = os.environ.get("JARVIS_TOKEN", "")

    logger.info("─" * 40)
    logger.info("Jarvis Brain v0.4.0")
    logger.info("LLM:        %s (%s)", model, api_base)
    logger.info("TTS:        %s", tts_model)
    logger.info("Auth token: %s", "configured" if token and token != "dev-token-change-me" else "DEFAULT (change!)")
    logger.info("STT models: %s", "ready" if _check_model_files() else "MISSING")
    logger.info("─" * 40)
