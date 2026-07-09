# ltx-lora-registry

Source of truth for **LTX-2.3 adapter descriptions** consumed by `MLXLTX2` (the LTX package in
mlx-engine-swift) and its host apps. The registry exists as its **own repo** so adapter schema and
entries can be shaped, reviewed, and verified **before** app integration — apps ingest adapters as
DATA; a new adapter must never require new request fields, bespoke UI, or per-adapter pipeline code.

Design context: `ltx-2-mlx-swift/IC-LORA-PLAN.md` (the integration plan this schema serves) and
`LORA-PLAN.md` (the plain/runtime LoRA capability, done).

## Schema v2 (additive over v1)

Top level: `schemaVersion`, `base` (the checkpoint family entries must match), `adapters[]`.

Per adapter — v1 fields unchanged (`id`, `displayName`, `repo`, `weightFile`, `defaultStrength`,
`trigger`, optional `input` for base-model x2v conditioning), plus:

| Field | Meaning |
|---|---|
| `kind` | `plain` (weight delta only) \| `ic` (needs reference conditioning) |
| `status` | `verified` · `verified-weights` (weights gate-passed, IC path pending) · `pending-gate-acceptance` · `eval-only` · `candidate` |
| `license` | per-adapter license id. **Non-permissive ⇒ `licenseGated: true`** — surfaced behind an explicit acknowledgment, never default-bundled |
| `gatedDownload` | HF repo requires accepted terms + auth token to download |
| `referenceDownscale` | from the safetensors header `reference_downscale_factor` (spatial position scale for reference tokens) |
| `stage2` | `skip` (ONE stage at target res — the community-blessed Ingredients config) · `clean` (detach LoRA + drop refs for a stage-2 refine — the oracle two-stage default) · `keep` |
| `surface` | which engine capability the adapter rides (`textToVideo`, later `videoUpscale`/`videoEdit`) |
| `promptConvention` | id of a prompting template the enhancer can apply (e.g. Ingredients' dual-part format) |
| `conditioning[]` | **declared attachment slots** — the generic surface. Each: `role` (free-form string the pipeline routes on), `media` (`video`/`audio`/`image`/`imageSet` + `maxCount`), `required`, `ingest` hint (`videoClip`/`audioTrack`/`loopedStillVideo`/`initImage`/`sheetBuilder`), optional `group` (slots sharing a group are alternatives — see `conditioningGroups`), `defaultStrength`, `note` (UI help text) |
| `conditioningGroups` | human-readable `oneOf` semantics for grouped slots (e.g. ingredients: a finished `reference_sheet` OR `subject_images` the app composes via the sheet-builder ingest) |
| `verification` | discovery/gate evidence: header dialect, rank, tensor/target counts, audio branch, `--lora-gate` result, date |

**The `conditioning` array is the contract with the app:** the UI renders one picker per declared
slot (by `media`), shows `note` as help text, and submits role-tagged attachments. The package
routes roles into the IC injection path. Adding an adapter = adding a JSON entry.

## Evaluation recipe (run before flipping any entry to `verified*`)

1. **Header probe** (no download of the full file needed beyond the header, but we cache the file
   anyway): parse the safetensors header — dialect (`diffusion_model.*` diffusers-PEFT expected),
   rank (min dim of any `lora_A`), block coverage (want 48), audio-branch keys (joint-AV LoRA),
   `__metadata__.reference_downscale_factor`.
2. **Shape/key fit:** `swift run -c release RunLTX2 --lora-gate <file>` in `ltx-2-mlx-swift` —
   want `targetsApplied` = all pairs, `finite=yes`, `detach cosine 1.0`. This proves the weight
   half on the real 22b distilled DiT regardless of `kind`.
3. **IC adapters additionally:** perceptual e2e once the injection path lands (IC-LORA-PLAN P3) —
   weights-verified entries carry `status: verified-weights` until then.
4. Record everything in the entry's `verification` block. Weight files cache at
   `/Volumes/DEV_ARCHIVE/models/loras/` (dev) — the app's `LoRACache` lazy-downloads per entry.

## Consumption model

- **Now:** `MLXLTX2` bundles a vendored copy (`Sources/MLXLTX2/Resources/ltx-lora-registry.json`).
  This repo is the editing/review surface; sync the vendored copy on change (script TBD).
- **Later (optional):** remote fetch with a pinned revision, so registry updates ship without a
  package release. Decide when entry churn justifies it.

## Current verification status (2026-07-08)

- `product-ad` (SOLRICKS) — **VERIFIED ✅** (2026-07-08): weights PASS (rank 16, 1632/1632 incl.
  full audio branch — first *plain* adapter with one) + live i2v perceptual pass (logo/label
  text preserved across 121f, studio product-hero style, audio branch fired).
  ⚠️ repo LICENSE file is empty (`license: other`) — clarification requested from author
  2026-07-08; treated as unspecified-community meanwhile.
- `sci-fi-cinema` (SOLRICKS) — **VERIFIED ✅** (2026-07-08): weights PASS (rank 16, 1632/1632
  incl. full audio branch, same recipe as product-ad) + live t2v perceptual pass (NEON TOKYO
  2077 prompt: strong cyberpunk/neon grading, coherent aerial motion, audio branch fired).
  Trigger `srx_scififilm` — the card table's `srx_scifilm` is a typo per training metadata.
  ⚠️ NO LICENSE file in repo (`license: other`) — same clarification posture as product-ad.

- `lipdub` — **VERIFIED ✅** (2026-07-03): weights PASS (rank 128, 1344/1344 incl. full audio branch) + live gate
  (lip-sync + identity + mux-path stt_verify WER 0.08; audio deliverable = actual dub per I7).
- `cameraman-v2` — weights **PASS** (rank 64, 480/480, video-only). ⚠️ research-only license ⇒
  eval-only, license-gated.
- `ingredients` — **VERIFIED ✅** (2026-07-03): weights PASS (rank 128, 480/480) + LIVE IC gate PASS
  (IC-P3-FIXTURE: identity transfer strong, no bleed, audio synced; dev-base-trained adapter confirmed working on distilled).
- Plain entries (`transition`, `omnicine`, `fantasy-anime`, `i2v-adapter`) — carried from v1,
  all previously gate-verified and live-validated in the test app.
