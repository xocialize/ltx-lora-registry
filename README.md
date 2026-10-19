# ltx-lora-registry

Two things live here:

1. **The adapter registry format, version 3**: one curated LoRA registry format for image and video
   packages (AB-D-0155, AB-A-0193). See [`FORMAT.md`](FORMAT.md) for the spec,
   [`schema/adapter-registry-3.schema.json`](schema/adapter-registry-3.schema.json) for the schema,
   and [`tools/adapter_registry.py`](tools/adapter_registry.py) for the validator, pinning and
   converters.
   - It merges this repo's v2 schema with FLUX.2 Klein's registry. Klein's format-3 registry lives with
     its package: `registry/flux2-klein-4b.json` in `xocialize/flux2-klein-swift` (mlx-image).
   - Every entry decodes as the engine's `AdapterDescriptor` (contract 1.53.0, AB-A-0189). From
     contract 1.55.0 (mlx-engine-swift v0.70.0) the engine also enforces `conditioningGroups`.
2. **The LTX-2 adapter registry**: [`registry.json`](registry.json) (namespace `ltx`). It is the source
   of truth for the adapters `MLXLTX2` and `ltx-features-swift` offer, and it is already in format 3.

Adapters are **data**: adding one means adding an entry, never new request fields, bespoke UI or
per-adapter pipeline code.

## Layout

| Path | What |
|---|---|
| `FORMAT.md` | The format spec, the legacy mapping, and how the engine uses it (AB-A-0189). |
| `schema/adapter-registry-3.schema.json` | JSON Schema 2020-12, strict (the authoring contract). |
| `registry.json` | LTX-2 adapters, format 3, every source pinned to a commit and a sha256. |
| `dist/registry.v2.json` | `registry.json` projected onto v2, for the vendored copies whose decoders haven't moved (see below). Generated; the tests fail if it is stale. |
| `tools/adapter_registry.py` | `validate [--remote]`, `pin`, `convert ltx-v2|klein-v1`, `export ltx-v2|klein-v1`. Standard library only. |
| `tools/migrations/` | The v2 → 3 migration, with every deliberate data change and its reason. |
| `tests/` | Offline tests: schema, rules, poison cases, the safetensors header check, the `AdapterDescriptor` shape, legacy round-trips (LTX v2 and Klein v1 fixtures), projection freshness. Set `ADAPTER_REGISTRIES` to check other format-3 files too. |

```bash
python3 -I tests/test_adapter_registry.py
python3 -I tools/adapter_registry.py validate --remote registry.json --local-dir /Volumes/Satechi/Models/ltx-lora-cache   # network; before a release
```

## Consumption

- **Packages vendor a copy.** `MLXLTX2` (`Sources/MLXLTX2/Resources/ltx-lora-registry.json`) and
  `ltx-features-swift` (`Sources/LTXFeatureCore/Resources/registry.json`) still decode **v2**.
  - Until they read format 3, sync them from `dist/registry.v2.json`, never from `registry.json`.
  - The projection carries no revision or hash, so those packages still download `resolve/main`.
    Pinned, hash-checked fetches arrive with the engine-managed materialisation of AB-A-0189.
- **The engine and runtime** (L1/L3) read format 3 directly: entries decode as `AdapterDescriptor`,
  and an `AdapterRef {id, strength}` resolves through the qualified id `ltx:<id>` to `source`.

## Status (2026-10-19)

All ten sources are pinned.
- **Eight** carry the hub's own LFS sha256, and it equals the copy we gated (`hashFrom: hub`).
- **Two** gated Lightricks repos (`ingredients`, `lipdub`) mask the oid without a token. They carry
  our verified copy's hash; the size matches the hub at the pinned commit (`hashFrom: local`).
  `pin --token-file` confirms them.

| id | kind | status | 2.3 | 2.5 | licence | notes |
|---|---|---|---|---|---|---|
| transition | plain | verified | perceptual | mechanical | Apache-2.0 | rank 32, partial audio branch |
| omnicine | plain | verified | perceptual | mechanical | Apache-2.0 | rank 128, audio branch |
| fantasy-anime | plain | verified | perceptual | mechanical | NOASSERTION | rank 32 |
| i2v-adapter | plain | verified | perceptual | mechanical | NOASSERTION | rank 256; built for the base model's init image (`baseInputs`) |
| product-ad | plain | verified | perceptual | mechanical | LTX-2 Community | rank 16, audio branch |
| sci-fi-cinema | plain | verified | perceptual | mechanical | LTX-2 Community | rank 16, audio branch; trigger `srx_scififilm` (card typo `srx_scifilm`) |
| lipdub | ic | verified | perceptual | mechanical | LTX-2 Community | **repo renamed upstream to `…-DubIt`** (see below); footprint owed |
| ingredients | ic | verified | perceptual | **perceptual** | LTX-2 Community | live IC fixture passed on 2.5 (2026-09-02); footprint ×1.64 activation |
| union-control | ic | gate-verified | perceptual (canny) | mechanical | LTX-2 Community | pose and depth fixtures owed (AB-T-0007); hub no longer gated; footprint owed |
| cameraman-v2 | ic | eval-only | perceptual | — | research only | licence-gated forever; footprint owed |

**IC footprints.** The engine registers an `ic` adapter only with a measured
`footprint.activationMultiplier`.
- **Ingredients** has one: ×1.64. It comes from the 2.5 fixture run against the same harness's
  no-adapter control arm (see `verification.footprint`).
- **LipDub, Union-Control and Cameraman** still need a matched run, and `validate` notes each one.

**Deliberate data changes in the migration** (`tools/migrations/2026-10-19-v2-to-v3.py`):
- **LipDub → DubIt.** On 2026-08-02 Lightricks renamed both the repo
  (`Lightricks/LTX-2.3-22b-IC-LoRA-LipDub` → `…-DubIt`) and the file (`…-lipdub-0.9` → `…-dubit-0.9`).
  - The v2 `resolve/main` URL no longer serves the weights, so a fresh install of the vendored v2
    registries can't fetch LipDub; existing caches are unaffected.
  - The dubit file is byte-for-byte the same size as our gated copy, and the card still names the old
    file. Its hash is unconfirmed because the repo is gated.
- **Transition's licence** is Apache-2.0: its card has declared it since the first upload. v2 recorded
  it as unspecified.
- **Union-Control** is no longer gated on the hub (`source.gated: false`).
- **Licences without an assertion** (`fantasy-anime`, `i2v-adapter`) are `NOASSERTION` /
  `commercialUse: unknown`: still offered, but a commercial-use policy refuses them.

## Evaluation recipe (before an entry's status becomes `verified*`)

1. **Header probe:** the safetensors header gives:
   - the dialect (`diffusion_model.*` PEFT expected → `format: diffusers-peft`);
   - the rank (min dim of any `lora_A`);
   - block coverage (want 48);
   - audio-branch keys;
   - `__metadata__.reference_downscale_factor`.

   `pin --local-dir` fills `format` and `weights` from a local copy.
2. **Shape and key fit:** run `swift run -c release RunLTX2 --lora-gate <file>` in `ltx-2-mlx-swift`.
   You want:
   - `targetsApplied` covering all pairs;
   - `finite=yes`;
   - detach cosine 1.0.

   That proves the weight half on the real DiT, whatever the `kind`. For 2.5, `--lora-gate25` earns
   `clearance` level `mechanical`.
3. **IC adapters** also need:
   - a perceptual end-to-end run, before level `perceptual`;
   - a `footprint`: run `--ic-spot25` with and without the adapter (`LTX_IC_BASE=1`) at one geometry.
     `activationMultiplier` is the ratio of the two `act` figures, rounded up.
4. **Record and pin.** Record the evidence in `verification`. Pin the source with `pin`, then run
   `validate --remote`.

Weights cache at `/Volumes/Satechi/Models/ltx-lora-cache/` (dev), id-named.

## History

- **v1:** flat entries, `resolve/main`.
- **v2:** IC slots, `clearedFamilies`; tag `v0.1.0`.
- **Format 3:** this tree, tag `v0.2.0`. The last v2 document is kept as
  `tests/fixtures/ltx-v2-2026-09-01.json`.
