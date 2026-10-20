#!/usr/bin/env python3
"""Measured IC footprints for every LTX IC adapter (AB-T-0334, receipt AB-R-0597, 2026-10-19).
Edits registry.json in place; kept so the data change is reviewable next to its reason.

    python3 -I tools/migrations/2026-10-19-ic-footprints.py registry.json
    python3 -I tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json

One binary, one session (ltx-2-mlx-swift 8704ef3, release): `RunLTX2 --ic-spot25 768 448 121`,
LTX-2.5 bf16, seed 42, the harness's default prompt for every arm, against the same harness's
no-adapter control arm (LTX_IC_BASE=1), act 24.71 GB on both of its runs. activationMultiplier =
act_IC / 24.71, rounded up to two decimals; residentBytes = source.bytes.

Ingredients is re-declared too. AB-R-0591's x1.64 came from a 2026-09-02 build, and the same arm
now reads act 58.42 GB, not 39.06. The IC peak has moved into the reference VAE encode (phys-trace
in AB-R-0597); the encoder's conv path changed in ltx-2-mlx-swift 79b5814 (mlx-swift 0.32.3 +
ExactConv). x1.64 would under-charge the current package by ~19 GB at this geometry.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import adapter_registry as ar  # noqa: E402

ACT_BASE_GB = 24.71

SAME_SHAPE = ("Its reference has cameraman-v2's shape (768x448x121), whose phys-trace puts the peak in the "
              "reference VAE encode.")

# id -> (act GB with the adapter, the reference it ran with, where the peak is)
MEASURED = {
    "lipdub": (59.77, "a 768x448x121 talking-face clip (lipdub-ref-talking-photoreal) whose own 5.0 s audio "
                      "track is the dub, the longest audio reference the span admits", SAME_SHAPE),
    "ingredients": (58.42, "the Tactical looped-still sheet (the AB-R-0591 reference)",
                    SAME_SHAPE + " Supersedes AB-R-0591's x1.64 (act 39.06 GB on the 2026-09-02 build)."),
    "union-control": (36.50, "the canny control clip (union-canny/canny_control.mp4) ingested at 384x224 "
                             "(referenceDownscale 2)",
                      "Its reference is encoded at a quarter of the pixels; which phase sets its peak was not traced."),
    "cameraman-v2": (59.77, "a 768x448x121 tracking-shot clip (59.75 and 59.77 GB on two runs)",
                     "A phys-trace of the repeat puts the peak in the reference VAE encode, before denoise."),
}


def multiplier(act_gb):
    return math.ceil(round(act_gb / ACT_BASE_GB * 100, 6)) / 100


def migrate(doc):
    by_id = {a["id"]: a for a in doc["adapters"]}
    for aid, (act, reference, peak) in MEASURED.items():
        a = by_id[aid]
        m = multiplier(act)
        a["footprint"] = {"residentBytes": a["source"]["bytes"], "activationMultiplier": m}
        a.setdefault("verification", {})["footprint"] = (
            f"AB-R-0597: --ic-spot25, LTX-2.5 bf16, 768x448x121, seed 42, 2026-10-19, ltx-2-mlx-swift 8704ef3: "
            f"activation {act:.2f} GB with the adapter and {reference}, vs 24.71 GB for the same harness's "
            f"no-adapter control arm on the same binary = {act / ACT_BASE_GB:.3f}, declared {m:.2f}. {peak} "
            "One geometry; the package runs IC one stage at target resolution whatever runtime.stage2 says. "
            "residentBytes = the bf16 file, which the DiT's low-rank add keeps as loaded.")
    return ar.canonical(doc)


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        doc = json.load(f)
    with open(path, "w") as f:
        f.write(json.dumps(migrate(doc), indent=2, ensure_ascii=False) + "\n")
