"""Local check of a candidate fallback model on the 8 bundled samples. Nothing is changed or deployed.

Run from the demo-api folder, inside its venv:
    python tools\\a2_fallback_check.py --model <exact model id from Google AI Studio> [--provider gemini]
The key and settings come from the workspace .env (llm-client loads it); the key is never printed.
It prints, per sample: pages, needs_review (got / expected), attempts, latency and the issue codes. Calls: at most one or two per text sample.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from extractor.pipeline import process_pdf
from llm_client import LLM

SAMPLES = Path(__file__).resolve().parents[1] / "static" / "a2" / "samples"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="exact model id, copied from the provider's docs")
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between samples (free tiers rate-limit)")
    args = ap.parse_args()

    items = json.loads((SAMPLES / "samples.json").read_text(encoding="utf-8"))
    llm = LLM(args.provider, model=args.model, max_retries=1)
    print(f"provider={args.provider} model={args.model}\n")
    print(f"{'sample':<22}{'pages':>5} {'review':>7} {'expect':>7} {'ok':>3} {'tries':>5} {'ms':>6}  issues / error")
    matches = failures = 0
    for it in items:
        stem = Path(it["file"]).stem
        try:
            res = process_pdf((SAMPLES / it["file"]).read_bytes(), llm, max_pages=5, default_currency=os.getenv("DEFAULT_CURRENCY") or None)
        except Exception as exc:  # noqa: BLE001  report and carry on with the next sample
            failures += 1
            print(f"{stem:<22}{'-':>5} {'-':>7} {str(bool(it['expected_review'])):>7} {'ERR':>3} {'-':>5} {'-':>6}  EXCEPTION {type(exc).__name__}")
            continue
        what = ",".join(i.code for i in res.issues) or "-"
        if res.error:
            failures += 1
            mark = "ERR"
            what += f"  error: {res.error[:70]}"
        else:
            ok = res.needs_review == bool(it["expected_review"])
            matches += ok
            mark = "yes" if ok else "NO"
        print(f"{stem:<22}{res.pages:>5} {str(res.needs_review):>7} {str(bool(it['expected_review'])):>7} {mark:>3} {res.attempts:>5} {res.latency_ms:>6}  {what}")
        time.sleep(args.sleep)
    print(f"\nreview flag matches the expectation on {matches}/{len(items)} samples; {failures} sample(s) failed (provider errors)")
    print("A model is a safe fallback only if this is close to the main model AND no sample failed. Run it twice: answers vary between runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
