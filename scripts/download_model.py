"""Download the pinned local model file into models/ and check its sha256. The weights are not in git.

Model: Qwen/Qwen2.5-1.5B-Instruct-GGUF, file qwen2.5-1.5b-instruct-q4_k_m.gguf (about 1.1 GB), Apache-2.0,
https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF. The dense retriever (BAAI/bge-small-en-v1.5, MIT) is
downloaded automatically by sentence-transformers at the pinned revision on first use.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from opsdesk.model_proposer import MODEL_FILE, MODEL_REPO, MODEL_REVISION, MODEL_SHA256  # noqa: E402

URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_FILE}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    dest = Path(__file__).resolve().parents[1] / "models" / MODEL_FILE
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        print(f"downloading {URL}")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(URL, tmp)
        tmp.rename(dest)
    digest = sha256(dest)
    if digest != MODEL_SHA256:
        raise SystemExit(f"sha256 mismatch for {dest}: {digest} != {MODEL_SHA256}")
    print(f"ok: {dest} sha256 {digest}")


if __name__ == "__main__":
    main()
