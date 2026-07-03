"""Download STT models from hf-mirror.com."""
import os
from pathlib import Path

import httpx

MODEL_DIR = Path(__file__).parent / "models"
HF_MIRROR = "https://hf-mirror.com"

MODELS = {
    "csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09": [
        "model.int8.onnx",
        "tokens.txt",
    ],
    "istupakov/silero-vad-onnx": [
        "silero_vad.onnx",
    ],
}


def download(url: str, dest: Path):
    print(f"  downloading {url} ...")
    with httpx.Client(timeout=120.0, follow_redirects=True, verify=False) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
    print(f"  saved: {dest} ({dest.stat().st_size // 1024}KB)")


def main():
    MODEL_DIR.mkdir(exist_ok=True)

    for repo, files in MODELS.items():
        # Use owner_repo format to match stt.py expectations
        repo_name = repo.replace("/", "_")
        repo_dir = MODEL_DIR / repo_name
        repo_dir.mkdir(exist_ok=True)

        for fname in files:
            dest = repo_dir / fname
            if dest.exists():
                print(f"  [skip] {dest}")
                continue
            url = f"{HF_MIRROR}/{repo}/resolve/main/{fname}"
            download(url, dest)
        print(f"  done: {repo_name}")

    # Copy silero_vad.onnx to top level
    vad_src = MODEL_DIR / "istupakov-silero-vad-onnx" / "silero_vad.onnx"
    vad_dst = MODEL_DIR / "silero_vad.onnx"
    if vad_src.exists() and not vad_dst.exists():
        vad_dst.write_bytes(vad_src.read_bytes())

    print("\nAll models ready.")


if __name__ == "__main__":
    main()
