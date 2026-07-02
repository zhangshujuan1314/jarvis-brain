"""Download STT models from hf-mirror.com (GitHub blocked, HF mirror accessible)."""
import os
import urllib.request
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models"
HF_MIRROR = "https://hf-mirror.com"

# (repo, files) — files are downloaded into MODEL_DIR / repo_name /
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
    urllib.request.urlretrieve(url, dest)


def main():
    MODEL_DIR.mkdir(exist_ok=True)

    for repo, files in MODELS.items():
        repo_name = repo.split("/")[-1]
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

    # Also keep silero_vad.onnx at top level for easy access
    vad_src = MODEL_DIR / "istupakov-silero-vad-onnx" / "silero_vad.onnx"
    vad_dst = MODEL_DIR / "silero_vad.onnx"
    if vad_src.exists() and not vad_dst.exists():
        vad_dst.write_bytes(vad_src.read_bytes())

    print("\nAll models ready.")


if __name__ == "__main__":
    main()
