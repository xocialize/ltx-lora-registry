#!/usr/bin/env python3
"""IC footprints re-measured after the decode fix (AB-T-0339, receipt AB-R-0600, 2026-10-19).
Supersedes 2026-10-19-ic-remeasured-footprints.py (AB-R-0598). Edits registry.json in place; kept so the
data change is reviewable next to its reason.

    python3 -I tools/migrations/2026-10-19-ic-decode-fix-footprints.py registry.json
    python3 -I tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json

AB-R-0598's peaks were the DECODE: `VideoVAEDecoder.decode` returned its last stage unevaluated, so it ran
after decodePixels lifted the pool cap, and the audio decode never ran capped. ltx-2-mlx-swift 22f8553
fixes both (values unchanged; IC decode 74.7-76.4 -> 50.0-52.7 GB, base 63.4 -> 49.3-50.0). Same method:
`RunLTX2 --ic-spot25 768 448 121`, LTX-2.5 bf16, seed 42, the same references, two runs per adapter and
three of the no-adapter control, on 22f8553.

RULE, AMENDED. After the fix every arm's whole-run peak, the control's included, is the PROMPT ENCODE
(~63 GB). That peak is an ARTIFACT OF THE CLI HARNESS, not of IC: `--ic-spot25` runs with `profile: nil`,
so `evictDiTBeforeDecode` stays off and the ~38.7 GB DiT is resident while the bf16 Gemma-4 encoder
(~24 GB) loads and runs. It is the same phase with the same prompt in every arm. The engine never
co-locates the two: every tier profile evicts the DiT before the encode. The AB-R-0598 rule applied to
whole-run activation therefore reads x0.99-x1.03 (an adapter cheaper than no adapter). So activation
here EXCLUDES the prompt-encoder phases (its load and its layers): the max of every other phase's
high-water minus the run's low-water. On AB-R-0598's own logs that reproduces its multipliers exactly
(every arm was decode-bound, above the prompt encode). activationMultiplier = the larger reading of the
two runs / the smallest of three control readings (10.65, 11.20, 11.15 GB), rounded up to two decimals;
residentBytes = source.bytes.

ORDER OF PUBLICATION. Apply and push this only once ltx-2-mlx-swift 22f8553 + 80615bb are on origin.
On the build before them (ff238ab), IC activation is decode-bound (cameraman-v2 36.21 vs a 24.57 GB
control = x1.48), so these lower multipliers would under-charge anyone building the port from GitHub.

LipDub RISES (1.54 -> 1.66): its denoise over the appended reference tokens did not shrink, the control's
decode did. The tier activation hints these multiply are pre-fix and over-state; they are due a
re-measure (AB-R-0600), and a multiplier derived after the fix is the one that pairs with those.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import adapter_registry as ar  # noqa: E402

ACT_BASE_GB = 10.65  # min of 10.65, 11.20, 11.15 (activation excluding the prompt-encoder phases)

# id -> (activation of each run excluding the prompt encoder, the reference, the binding phase)
MEASURED = {
    "lipdub": ((17.59, 17.57),
               "a 768x448x121 talking-face clip (lipdub-ref-talking-photoreal) whose own 5.0 s audio track is "
               "the dub",
               "denoise 56.25 GB (reference encode 51.2-52.5, audio reference 40.1; decode 52.3-52.5)"),
    "ingredients": ((14.79, 14.52), "the Tactical looped-still sheet (the AB-R-0591 reference)",
                    "denoise 53.87 GB (reference encode 50.4-51.7; decode 50.0-51.3)"),
    "union-control": ((12.38, 14.40),
                      "the canny control clip (union-canny/canny_control.mp4) ingested at 384x224 "
                      "(referenceDownscale 2)",
                      "decode 50.67 / 52.69 GB (denoise 48.8; reference encode 41.8)"),
    "cameraman-v2": ((14.41, 14.53), "a 768x448x121 tracking-shot clip",
                     "denoise 53.20 GB (reference encode 51.2-51.9; decode 50.7)"),
}


def multiplier(act_gb):
    return math.ceil(round(act_gb / ACT_BASE_GB * 100, 6)) / 100


def migrate(doc):
    by_id = {a["id"]: a for a in doc["adapters"]}
    for aid, (runs, reference, binding) in MEASURED.items():
        a = by_id[aid]
        act = max(runs)
        m = multiplier(act)
        a["footprint"] = {"residentBytes": a["source"]["bytes"], "activationMultiplier": m}
        a.setdefault("verification", {})["footprint"] = (
            f"AB-R-0600: --ic-spot25, LTX-2.5 bf16, 768x448x121, seed 42, 2026-10-19, ltx-2-mlx-swift 22f8553 "
            f"(the decode fix, AB-T-0339): activation excluding the prompt-encoder phases {runs[0]:.2f} / "
            f"{runs[1]:.2f} GB on two runs with the adapter and {reference}, vs 10.65 GB, the smallest of three "
            f"readings of the same harness's no-adapter control arm = {act / ACT_BASE_GB:.3f}, declared {m:.2f}. "
            f"Binding phase: {binding}. The prompt encode (DiT resident + bf16 Gemma-4, ~63 GB in every arm) is "
            "excluded: identical across arms and never DiT-co-resident in the engine. Supersedes AB-R-0598, whose "
            "figure was the decode before the fix. One geometry; the package runs IC one stage at target "
            "resolution whatever runtime.stage2 says. residentBytes = the bf16 file, which the DiT's low-rank "
            "add keeps as loaded.")
    return ar.canonical(doc)


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        doc = json.load(f)
    with open(path, "w") as f:
        f.write(json.dumps(migrate(doc), indent=2, ensure_ascii=False) + "\n")
