#!/usr/bin/env python3
"""The one-off migration of this registry from ltx-lora-registry v2 to adapter-registry format 3
(AB-A-0193, 2026-10-19). Kept so every deliberate data change is reviewable next to its reason;
the mechanical part is tools/adapter_registry.py `from_ltx_v2`, which round-trips v2 exactly.

    python3 -I tools/migrations/2026-10-19-v2-to-v3.py <v2.json> <out.json>
    python3 -I tools/adapter_registry.py pin <out.json> --local-dir /Volumes/Satechi/Models/ltx-lora-cache --write
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import adapter_registry as ar  # noqa: E402

LTX2_LICENSE_URL = ("https://huggingface.co/Lightricks/LTX-2.3/blob/"
                    "3c6a4e66e5d0a684231950b9c74dd4ded7b6fadc/LICENSE")


def migrate(v2):
    doc = ar.from_ltx_v2(v2, namespace="ltx", title="LTX-2 adapters")
    doc["description"] = ("Curated adapters for the LTX-2 joint audio-video model family (MLXLTX2). "
                          "Source of truth; packages vendor a copy.")
    by_id = {a["id"]: a for a in doc["adapters"]}

    # Every clearance was measured on the distilled checkpoints the engine serves.
    for a in doc["adapters"]:
        for key, c in a["clearance"].items():
            c["variant"] = "distilled"
            if key == "ltx-2.5" and c["level"] == "mechanical":
                c["note"] = "--lora-gate25: targets resolve, output moves, detach exact; effect not judged on 2.5."

    # LipDub: upstream renamed the repo (…-LipDub → …-DubIt) and the file (lipdub-0.9 → dubit-0.9)
    # on 2026-08-02. The v2 resolve/main URL no longer serves weights to a fresh install.
    lipdub = by_id["lipdub"]
    lipdub["title"] = "LipDub (DubIt): audio-driven lip re-animation (Lightricks)"
    lipdub["source"].update(repo="Lightricks/LTX-2.3-22b-IC-LoRA-DubIt",
                            file="ltx-2.3-22b-ic-lora-dubit-0.9.safetensors")
    lipdub["source"]["note"] = (
        "Renamed upstream 2026-08-02 from Lightricks/LTX-2.3-22b-IC-LoRA-LipDub, file "
        "ltx-2.3-22b-ic-lora-lipdub-0.9.safetensors (the card still names the old file). sha256 is our "
        "verified lipdub-0.9 copy of 2026-07-02, byte-for-byte the same size as dubit-0.9 here; the hub "
        "masks the oid without a token, so equality is unconfirmed and a differing download fails the "
        "hash check. Confirm with `pin --token-file`.")

    # Transition: the card has declared apache-2.0 since its first upload (2026-03-20); v2
    # recorded 'unspecified-community'.
    by_id["transition"]["license"] = "Apache-2.0"
    by_id["transition"]["licenseTerms"] = {
        "commercialUse": "allowed", "gated": False,
        "note": "Card frontmatter declares apache-2.0 at the pinned revision and since the first upload; "
                "v2 recorded it as unspecified."}

    for aid, note in {
        "fantasy-anime": "The card states no licence.",
        "i2v-adapter": "The card states no licence of its own and refers to the LTX-Video licence for base-model terms.",
        "omnicine": "Card frontmatter declares apache-2.0.",
        "cameraman-v2": "Card: 'created as part of a personal project for research purposes only and is not "
                        "intended for commercial use.' No licence file.",
    }.items():
        by_id[aid]["licenseTerms"]["note"] = note

    # These two repos carry no usable licence file (Ingredients none, DubIt an empty LICENSE);
    # `pin` links the repo's own LICENSE for the rest, which for SOLRICKS is the author's grant.
    for aid in ("lipdub", "ingredients"):
        by_id[aid]["licenseTerms"]["url"] = LTX2_LICENSE_URL

    # Ingredients: the live IC fixture passed on 2.5 too (gate25.liveIC, 2026-09-02); the same
    # harness's no-adapter control arm gives the one measured IC activation multiplier we have.
    ing = by_id["ingredients"]
    ing["clearance"]["ltx-2.5"]["level"] = "perceptual"
    ing["clearance"]["ltx-2.5"].pop("note")
    ing["promptTemplate"] = "Reference sheet: {references}\n\nGenerated video: {prompt}"
    ing["footprint"] = {"residentBytes": 1308778338, "activationMultiplier": 1.64}
    ing["verification"]["footprint"] = (
        "AB-R-0591: --ic-spot25, LTX-2.5 bf16, 768x448x121, seed 42, 2026-09-02: activation 39.06 GB with "
        "the adapter and its looped-still reference vs 23.91 GB for the same harness's no-adapter control "
        "arm = 1.634, declared 1.64. One "
        "geometry; the growth is the reference tokens (referenceDownscale 1, one reference the size of the "
        "target). residentBytes = the bf16 file, which the DiT's low-rank add keeps as loaded.")

    by_id["union-control"]["clearance"]["ltx-2.3"]["note"] = (
        "Canny signal live-verified; pose and depth fixtures not yet run (AB-T-0007).")
    by_id["sci-fi-cinema"]["verification"]["strengthNote"] = "Card recommends 0.6 to 1.1."
    return doc


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        v2 = json.load(f)
    with open(sys.argv[2], "w") as f:
        f.write(json.dumps(migrate(v2), indent=2, ensure_ascii=False) + "\n")
