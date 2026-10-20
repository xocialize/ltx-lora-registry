#!/usr/bin/env python3
"""IC footprints re-measured after the reference-encode fix (AB-T-0338, receipt AB-R-0598, 2026-10-19).
Supersedes 2026-10-19-ic-footprints.py (AB-R-0597). Edits registry.json in place; kept so the data
change is reviewable next to its reason.

    python3 -I tools/migrations/2026-10-19-ic-remeasured-footprints.py registry.json
    python3 -I tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json

AB-R-0597's x2.42 / x2.37 measured the reference VAE encode, not the IC denoise: since ltx-2-mlx-swift
79b5814 the encode's ExactConv route made padded copies of every full-resolution activation and mlx's
uncapped pool kept them all. Fixed in ltx-2-mlx-swift 0067a19 (implicit-GEMM route, pool capped for
the call, values unchanged). Same method: `RunLTX2 --ic-spot25 768 448 121`, LTX-2.5 bf16, seed 42, the
harness's default prompt, the same references, two runs per adapter on the fixed library (0067a19, and
ff238ab whose only change is the harness's per-phase high-water). activationMultiplier = the larger
act of the two runs / the smallest of three no-adapter control readings (24.70, 24.57, 24.60), rounded
up to two decimals; residentBytes = source.bytes.

Every arm's whole-run peak is now the decode; the reference encode peaks below each arm's own denoise.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import adapter_registry as ar  # noqa: E402

ACT_BASE_GB = 24.57  # min of 24.70, 24.57 (0067a19) and 24.60 (ff238ab)

# id -> (act GB of each run, the reference it ran with, the phase high-waters of the ff238ab run)
MEASURED = {
    "lipdub": ((37.61, 37.69),
               "a 768x448x121 talking-face clip (lipdub-ref-talking-photoreal) whose own 5.0 s audio track is "
               "the dub",
               "reference encode 52.48 GB (audio reference 40.08), denoise 56.26, decode 76.36"),
    "ingredients": ((36.07, 36.13), "the Tactical looped-still sheet (the AB-R-0591 reference)",
                    "reference encode 51.27 GB, denoise 53.85, decode 75.23"),
    "union-control": ((36.49, 36.50),
                      "the canny control clip (union-canny/canny_control.mp4) ingested at 384x224 "
                      "(referenceDownscale 2)",
                      "reference encode 41.85 GB, denoise 48.79, decode 74.79. Its activation did not move "
                      "(36.50 GB in AB-R-0597); the declared value rises 1.48 -> 1.49 only through the smaller "
                      "control reading"),
    "cameraman-v2": ((36.21, 36.06), "a 768x448x121 tracking-shot clip",
                     "reference encode 52.48 GB, denoise 53.23, decode 74.73"),
}


def multiplier(act_gb):
    return math.ceil(round(act_gb / ACT_BASE_GB * 100, 6)) / 100


def migrate(doc):
    by_id = {a["id"]: a for a in doc["adapters"]}
    for aid, (runs, reference, phases) in MEASURED.items():
        a = by_id[aid]
        act = max(runs)
        m = multiplier(act)
        a["footprint"] = {"residentBytes": a["source"]["bytes"], "activationMultiplier": m}
        a.setdefault("verification", {})["footprint"] = (
            f"AB-R-0598: --ic-spot25, LTX-2.5 bf16, 768x448x121, seed 42, 2026-10-19, ltx-2-mlx-swift 0067a19 "
            f"(the reference-encode fix, AB-T-0338): activation {runs[0]:.2f} / {runs[1]:.2f} GB on two runs with "
            f"the adapter and {reference}, vs 24.57 GB, the smallest of three readings of the same harness's "
            f"no-adapter control arm = {act / ACT_BASE_GB:.3f}, declared {m:.2f}. Phase high-waters: {phases}; "
            "the whole-run peak is the decode. Supersedes AB-R-0597, whose figure was the reference VAE encode "
            "before the fix. One geometry; the package runs IC one stage at target resolution whatever "
            "runtime.stage2 says. residentBytes = the bf16 file, which the DiT's low-rank add keeps as loaded.")
    return ar.canonical(doc)


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        doc = json.load(f)
    with open(path, "w") as f:
        f.write(json.dumps(migrate(doc), indent=2, ensure_ascii=False) + "\n")
