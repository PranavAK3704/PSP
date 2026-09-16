"""Build the embedding index. RUN THIS ON THE GPU MACHINE, not on the laptop.

    pip install "sentence-transformers>=3.0" torch --index-url https://download.pytorch.org/whl/cu124
    python scripts/build_embedding_index.py --model BAAI/bge-m3
    python scripts/build_embedding_index.py --model BAAI/bge-m3 --probe   # + linear probe

── YOU ARE NOT TRAINING A MODEL ──────────────────────────────────────────────────────────────
This is the single most common misunderstanding, so it is worth being blunt: the embedding
model is DOWNLOADED, FROZEN, and never modified. Running your messages through it is INFERENCE
— the same operation as asking it a question — and it takes minutes, not hours, and needs no
labels to do.

What comes out is one list of ~1024 numbers per message (a "vector"). Messages that mean
similar things land near each other, even with no words in common — which is exactly what BM25
cannot do, and why hardstop_loss / shortage_loss / cod_shortfall currently score 20-31%.

Classifying is then: embed the new message, find the nearest saved vectors, take their label.
No training anywhere in that sentence.

The ONE optional thing that IS training is `--probe`: a logistic regression over the frozen
vectors. Seconds to fit, a few hundred KB of coefficients, and it usually beats
nearest-neighbour by a few points. It is optional because the index alone already works.

── WHY THIS IS STILL DETERMINISTIC ───────────────────────────────────────────────────────────
Pinned model weights + a pinned index + a fixed threshold is a pure function: the same message
produces the same vector, the same neighbours, and the same label, forever, on any machine.
The model version is recorded in the output so a future run cannot silently drift.

── WHAT TO COPY TO THE GPU MACHINE ───────────────────────────────────────────────────────────
`data/intake/corpus/dispositions.json` (~850 KB). It is gitignored because it carries partner
message text, so move it deliberately — not through a public channel.

Copy the OUTPUT (`embeddings.npz` + `embedding_index.json`) back. Those are vectors and labels,
not readable message text.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "intake" / "corpus" / "dispositions.json"
HOLDOUT = ROOT / "data" / "intake" / "corpus" / "dispositions_holdout.json"
OUT_VEC = ROOT / "data" / "intake" / "corpus" / "embeddings.npz"
OUT_META = ROOT / "data" / "intake" / "corpus" / "embedding_index.json"

#: Both are multilingual and handle romanised Hindi. bge-m3 is the stronger default; e5-large
#: is smaller and faster if VRAM is tight. Pin whichever you choose — the id is written to the
#: output and a mismatch at query time must be an error, not a silent re-embed.
DEFAULT_MODEL = "BAAI/bge-m3"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", type=int, default=64, help="lower this if you run out of VRAM")
    ap.add_argument("--probe", action="store_true", help="also fit a logistic-regression head")
    ap.add_argument("--device", default=None, help="cuda | cpu (auto-detected by default)")
    a = ap.parse_args()

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise SystemExit(
            f"missing dependency: {e}\n\n"
            '  pip install "sentence-transformers>=3.0" torch '
            "--index-url https://download.pytorch.org/whl/cu124\n"
            "  (and scikit-learn, if you want --probe)")

    if not CORPUS.exists():
        raise SystemExit(f"{CORPUS} not found — copy it from the laptop, or regenerate it "
                         f"there with scripts/build_corpus_from_audits.py")

    rows = json.loads(CORPUS.read_text(encoding="utf-8"))["messages"]
    held = set()
    if HOLDOUT.exists():
        held = {m["id"] for m in json.loads(HOLDOUT.read_text(encoding="utf-8"))["messages"]}

    # Held-out rows are embedded too (the scorer needs their vectors) but are marked, so the
    # index builder can exclude them from the searchable set. Embedding them is not leakage;
    # letting them be NEIGHBOURS would be.
    train = [r for r in rows if r["id"] not in held and r["disposition"] != "NOVEL"]
    test = [r for r in rows if r["id"] in held]
    print(f"  corpus {len(rows)}  train {len(train)}  holdout {len(test)}")

    print(f"  loading {a.model} … (first run downloads ~2GB)")
    model = SentenceTransformer(a.model, device=a.device)
    dim = model.get_sentence_embedding_dimension()
    print(f"  device={model.device}  dim={dim}")

    def embed(items, label):
        t = time.perf_counter()
        v = model.encode([r["text"] for r in items], batch_size=a.batch,
                         convert_to_numpy=True, normalize_embeddings=True,
                         show_progress_bar=True)
        print(f"  {label}: {len(items)} in {time.perf_counter() - t:.1f}s")
        return v

    train_v = embed(train, "train")
    test_v = embed(test, "holdout") if test else np.zeros((0, dim), dtype="float32")

    np.savez_compressed(OUT_VEC, train=train_v, test=test_v)
    OUT_META.write_text(json.dumps({
        "_what": "Frozen-encoder vectors for app/intake/classify.py's semantic tier.",
        "_not_trained": "The encoder is downloaded and frozen. This file is inference output, "
                        "not a trained model. Only --probe fits anything.",
        "model": a.model,
        "dim": dim,
        "normalized": True,
        "train": [{"id": r["id"], "disposition": r["disposition"]} for r in train],
        "holdout": [{"id": r["id"], "disposition": r["disposition"]} for r in test],
    }, indent=1) + "\n", encoding="utf-8")
    print(f"\n  wrote {OUT_VEC.name} and {OUT_META.name}")

    if a.probe:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import accuracy_score
        except ImportError:
            raise SystemExit("  --probe needs scikit-learn: pip install scikit-learn")
        y = [r["disposition"] for r in train]
        clf = LogisticRegression(max_iter=2000, class_weight="balanced")
        clf.fit(train_v, y)
        real = [(i, r) for i, r in enumerate(test) if r["disposition"] != "NOVEL"]
        if real:
            idx = [i for i, _ in real]
            acc = accuracy_score([r["disposition"] for _, r in real], clf.predict(test_v[idx]))
            print(f"  linear probe held-out accuracy: {acc:.1%}  "
                  f"(BM25 baseline on the same rows: 65.2%)")
        import pickle
        (ROOT / "data" / "intake" / "corpus" / "probe.pkl").write_bytes(pickle.dumps(clf))
        print("  wrote probe.pkl")

    print("\n  COPY BACK to the laptop: embeddings.npz, embedding_index.json"
          + (", probe.pkl" if a.probe else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
