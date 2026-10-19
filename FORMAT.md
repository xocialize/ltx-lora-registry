# Adapter registry format 3

One curated adapter (LoRA) registry format for image and video packages (AB-D-0155, AB-A-0193). It
merges ltx-lora-registry v2 with FLUX.2 Klein's registry. Packages, the runtime adapter catalog
(mlx-image-runtime), the media engines and their consoles read it.

**Every entry is a superset of `MLXToolKit.AdapterDescriptor`** (contract 1.53.0, AB-A-0189;
mlx-engine-swift v0.68.0) and decodes as one. The runtime catalog can hand the engine a registry's `adapters` array with no mapping
layer. The registry adds what the engine doesn't need: status, clearance, prompting, licence terms,
evidence, and the source's provenance flags.

- **Schema:** [`schema/adapter-registry-3.schema.json`](schema/adapter-registry-3.schema.json)
  (JSON Schema 2020-12).
- **Validator and converters:** [`tools/adapter_registry.py`](tools/adapter_registry.py) (Python
  standard library only).
- **Registries in this format:** [`registry.json`](registry.json) (LTX-2, namespace `ltx`) and
  [`examples/flux2-klein-4b.json`](examples/flux2-klein-4b.json) (FLUX.2 Klein, converted; mlx-image
  owns that data).

Status: 3.0, 2026-10-19. The engine-facing names are the ones shipped in contract 1.53.0
(mlx-engine-swift v0.68.0). Contract 1.55.0 (v0.70.0) added `conditioningGroups` in this format's
shape and enforces it in the pre-flight. Both registries here decode against v0.70.0: 18/18 entries,
with the Ingredients group cases refused and admitted as specified. If the contract renames a field,
the registry follows as a 3.x minor change, noted in the changelog.

## Versioning

- `schemaVersion` is the major version: **3**. A change that breaks an existing reader means 4.
- Additive fields are minor versions (3.1, 3.2): the schema file is updated in place and listed in
  the changelog.
- **Readers ignore unknown keys** (Swift's `Codable` does). The schema is strict
  (`additionalProperties: false`) because it is the authoring contract: a typo fails validation
  instead of being silently ignored.

## Document

```json
{
  "format": "adapter-registry",
  "schemaVersion": 3,
  "namespace": "ltx",
  "title": "LTX-2 adapters",
  "description": "…",
  "adapters": [ … ]
}
```

| Field | Meaning |
|---|---|
| `format` | Always `"adapter-registry"`. |
| `namespace` | Slug naming this registry. An adapter's **qualified id** is `<namespace>:<id>`, e.g. `ltx:ingredients`, `flux2-klein:tryon`. Generic ids (`outpaint`, `object-remove`) will collide once more families join. So a catalog that merges registries should register each adapter under its qualified id, which is what `AdapterRef.id` then names. |
| `adapters` | The entries, in display order. |

## Entry

These fields are the ones `AdapterDescriptor` decodes, with its names and types:

| Field | Req. | Meaning |
|---|---|---|
| `id` | yes | Slug, unique within the registry. It also names the adapter's cache file, so renaming it forks caches; don't. |
| `title` | yes | Display name. |
| `kind` | yes | `AdapterKind`: `plain` (a weight delta) or `ic` (reference-conditioned). Conditioning slots exist **exactly** when the kind is `ic`. |
| `format` | yes | `AdapterFormat`: the safetensors key dialect a surface's `AdapterSupport.formats` matches. The engine's constants are `diffusers-peft`, `kohya` and `bfl-fused`. Klein's registry also uses `bfl-fused-peft` and `diffusers-split`; mlx-image maps them. |
| `baseModels` | yes | Base-model keys `<family>-<version>`: `ltx-2.3`, `ltx-2.5`, `flux2-klein-4b`. Matched against `AdapterSupport.baseModel`. The LTX keys equal `LTXFamily` raw values and v2's `clearedFamilies`. |
| `capabilities` | yes | Adapter-carrying `Capability` raw values: `textToImage`, `imageEdit`, `textToVideo`, `videoEdit`. |
| `source` | yes | `AdapterSource`, pinned. See [Source](#source). |
| `license` | yes | An `SPDXLicense` identifier: an SPDX id, `LicenseRef-*` for non-SPDX licences (`LicenseRef-LTX-2-Community` is the engine's own constant), or `NOASSERTION` when the author states none. |
| `defaultStrength` | yes | The adapter's strength when a request gives none. |
| `strengthRange` | no | `{min, max}`, the range the adapter was validated over. The engine admits the intersection with the surface's range and refuses (never clamps) outside it. Omit it when unknown; the surface's range then applies. |
| `conditioning` | `ic` | The ordered slots. See [Conditioning](#conditioning). |
| `footprint` | `ic` | `AdapterFootprint` `{residentBytes, activationMultiplier}`, measured with the package that applies it. Omit it for a plain adapter (the engine charges `source.bytes`). **The engine refuses to register an `ic` adapter without one**, so `validate` warns about each missing one. Record the measurement in `verification.footprint`. |

These fields are the registry's own:

| Field | Req. | Meaning |
|---|---|---|
| `licenseTerms` | yes | See [Licence](#licence). |
| `status` | yes | `verified` · `gate-verified` · `verified-weights` (weights gate passed, IC path pending) · `pending-gate-acceptance` · `eval-only` · `candidate`. |
| `clearance` | yes | One entry per key in `baseModels`. See [Clearance](#clearance). |
| `trigger` | no | Text the prompt must contain for the adapter to fire. Omitted when there is none. |
| `promptTemplate` | no | See [Prompting](#prompting). |
| `promptConvention` | no | Id of a prompting convention an enhancer profile applies (e.g. `ingredients-dual-part`). |
| `conditioningGroups` | no | Alternatives among slots. See [Conditioning](#conditioning). |
| `baseInputs` | no | The base model's own request inputs the adapter is built for, e.g. `[{"input": "initImage", "required": false}]` for an i2v adapter. These are **not** adapter conditioning: the image rides `T2VRequest.initImage`. |
| `weights` | no | `{rank?, alpha?, audioBranch?}` from the safetensors header. `audioBranch`: a joint audio-video adapter carries audio-branch targets. |
| `runtime` | no | Family-specific parameters the package reads. See [Runtime](#runtime). |
| `verification` | no | Gate, perceptual and footprint evidence, open-ended. Consumers must not branch on it. |

### Clearance

```json
"baseModels": ["ltx-2.3", "ltx-2.5"],
"clearance": {
  "ltx-2.3": {"level": "perceptual", "variant": "distilled"},
  "ltx-2.5": {"level": "mechanical", "variant": "distilled",
              "note": "--lora-gate25: targets resolve, output moves, detach exact; effect not judged on 2.5."}
}
```

- **`level`:**
  - `perceptual`: output generated and judged on this base.
  - `mechanical`: the weight gate only. The adapter's targets resolve, it changes the output, and it
    detaches exactly. A host may offer it, but must not claim the effect matches the perceptual base.
  - `declared`: upstream says it is compatible; we have not measured it.
- **`variant`:** the checkpoint the clearance was established on.
- **Model choice.** The adapter id implies its model. When a request names no model, the engine
  chooses among the enabled base models, preferring the strongest clearance and then its own default.
  Order in `baseModels` carries no meaning.

### Conditioning

```json
"conditioning": [
  {"role": "person", "media": "image", "required": true, "ingest": "clothing-agnostic", "note": "…"},
  {"role": "top",    "media": "image", "required": true, "ingest": "raw"},
  {"role": "bottom", "media": "image", "required": true, "ingest": "raw"}
]
```

- **Order is the contract.** Array order is the slot order. `AdapterDescriptor.orderedConditioning`
  sorts a request's role-tagged `ConditioningInput`s into it. Klein's v1 `slot` numbers are gone; the
  array says the same thing with one less way to disagree. For RefControl and try-on, swapping two
  slots swaps their meaning.
- **`AdapterConditioningSlot` fields:**
  - `role`: a `snake_case` name the package routes on.
  - `media`: `image`, `video` or `audio`.
  - `required`.
  - `maxCount`: how many inputs the role takes; default 1. v2's `imageSet` became `image` with `maxCount`.
  - `note`: help text for the UI.
- **Registry-only slot fields:**
  - `ingest`: the package's preparation hint (`videoClip`, `loopedStillVideo`, `sheetBuilder`,
    `openpose-skeleton`, `raw`, …).
  - `defaultStrength`: reference strength when the `ConditioningInput` gives none.
  - `describable` / `describeNote`: the slot takes a caller-written description that feeds
    `{references}`.
  - `group`.
- **Groups.** These are alternatives, e.g. Ingredients takes a finished sheet *or* subject images
  that the host composes into one:

```json
"conditioningGroups": {
  "sheet": {"rule": "exactlyOne", "required": true, "alternatives": [
    {"requires": ["reference_sheet"]},
    {"requires": ["subject_images"], "allows": ["location_image"]}
  ]}
}
```

  - An alternative is satisfied when every `requires` role is bound and every bound role of the
    group is in `requires ∪ allows`.
  - `exactlyOne` means one alternative is satisfied; `required` means the request must satisfy the
    group.
  - Grouped slots are `required: false`; the group decides.
  - **The engine enforces groups from contract 1.55.0** (mlx-engine-swift v0.70.0;
    `AdapterDescriptor.conditioningGroups`, same shape). Its pre-flight refuses a required group with
    nothing bound (Ingredients with no sheet) and bound roles that match no alternative, or more than
    one. It registers only `exactlyOne`. The slot-level `group` key is registry-only: the engine
    derives membership from the alternatives, so the two must agree (`validate` checks it).
  - Against an engine older than 1.55.0, the package or host must enforce groups itself.

### Prompting

- **`trigger`**: the activation text. A host may prepend it when the prompt lacks it.
- **`promptTemplate`**: the canonical prompt.
  - With no placeholder, it is the default prompt for a request that carries none (Klein's edit
    adapters).
  - Placeholders:
    - `{prompt}`: the caller's prompt.
    - `{references}`: the caller's descriptions of `describable` slots.
  - Example (Ingredients): `"Reference sheet: {references}\n\nGenerated video: {prompt}"`.
- **`promptConvention`**: names a prompting convention for prompt-enhance-kit profiles.

### Licence

`license` is the SPDX string the engine classifies against its `LicensePolicy`. `licenseTerms` carries
what that string alone can't:

```json
"license": "LicenseRef-LTX-2-Community",
"licenseTerms": {
  "commercialUse": "conditional",
  "gated": false,
  "notice": "Derivative of LTX-2 under the LTX-2 Community License Agreement (Lightricks). …",
  "url": "https://huggingface.co/Lightricks/LTX-2.3/blob/3c6a4e66…/LICENSE"
}
```

- **`commercialUse`** is what a `policy.license: commercial-use` check reads:

  | value | meaning | commercial-use policy |
  |---|---|---|
  | `allowed` | permissive | admit |
  | `conditional` | allowed under terms the `notice` states (revenue cap, use restrictions) | admit, and carry the notice |
  | `prohibited` | research or non-commercial only | refuse |
  | `unknown` | no licence asserted (`NOASSERTION`) | refuse |

- **`gated`**: the operator must accept the terms before the adapter is enabled (the engine's
  `adapter:<id>` enablement). It is required for `prohibited`.
- **`notice`**: attribution or terms a host shows and carries in provenance. It is required for
  `conditional`. Apache-2.0 mirrors with a NOTICE file point at it (§4(d)).
- **`url`**: the licence text, pinned to a commit.

> `licenseTerms.gated` is about **terms**. `source.gated` is about **downloading**. A repo can be
> either without the other.

### Source

```json
"source": {
  "host": "huggingface",
  "repo": "Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients",
  "revision": "dcc403d92aa0f5a983c5dbc7282503ca52f73a08",
  "file": "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
  "sha256": "515e4e139001ac6282357a5b35372e42e98b3affd5fcc886a52242abeed19559",
  "bytes": 1308778338,
  "gated": true,
  "hashFrom": "local"
}
```

`AdapterSource` decodes `repo`, `revision`, `file`, `sha256` and `bytes`; the rest are registry-only.

- **`revision` is a full 40-hex commit.** Branch names are not pins: the v2 registries downloaded
  `resolve/main`, and LipDub's `main` stopped serving our file when upstream renamed it (2026-08-02).
- **`file`** is a relative path inside the repo: no leading `/`, no empty or `..` components.
- **`sha256`** is checked after every download. A mismatch is refused, never cached.
- **`bytes`** feeds the materialisation preview (consent) and admission.
- **`gated`**: the hub repo is gated. Downloading needs a token from an account that accepted its terms.
- **`hashFrom`**:
  - `hub`: `sha256` is the hub's own LFS oid at `revision`.
  - `local`: the hub masks the oid without a token, so `sha256` is the hash of our verified copy,
    whose size matches the hub at `revision`. A differing download fails closed.
  - Only a gated source may say `local`. `pin --token-file` upgrades it to `hub`.
- **`upstream`**: where the bytes originate, when `repo` is a mirror.
- **`note`**: anything a reader needs, such as a rename.

### Runtime

These are parameters a family's package reads. Adding a key is a 3.x minor change.

| Key | Family | Meaning |
|---|---|---|
| `referenceDownscale` | ltx | Spatial position scale for reference tokens (safetensors `reference_downscale_factor`). It also sets the IC activation cost: at 2, the reference is a quarter of the target's tokens. |
| `stage2` | ltx | IC stage policy: `skip` (one stage at target resolution), `clean`, `keep`. |
| `audioReference` | ltx | The IC adapter also appends a reference audio stream (LipDub). |

## Checks

```bash
python3 -I tools/adapter_registry.py validate registry.json examples/*.json
python3 -I tools/adapter_registry.py validate --remote registry.json
python3 -I tests/test_adapter_registry.py
```

**`validate`** checks two things:
- **Schema:** it interprets the JSON Schema with a built-in subset. It refuses a schema that uses a
  keyword it does not implement, so the two can't drift. The tests check it against the
  `jsonschema` package when that package is installed.
- **Rules the schema can't express:**
  - unique ids and roles;
  - slots exactly for `ic`;
  - groups are consistent;
  - a clearance for every base model;
  - the default strength is inside its range;
  - the licence rules above;
  - `local` hashes only on gated sources;
  - only known template placeholders.

  It also notes, without failing, each `ic` adapter that still lacks a footprint.

**`--remote`** checks every pin against the hub, **unauthenticated by design**: a source that resolves
only with our token isn't reachable for users (AB-L-0055). It checks that:
- the repo isn't renamed;
- the file exists at the pinned commit;
- the size and sha256 match;
- gating is as recorded.

Run it before a release and after any source change. It needs the network, so it isn't in the
offline tests.

**`pin`** resolves a branch, or a missing revision, to its commit and records `sha256`, `bytes`,
`gated` and `hashFrom`:
- it uses the hub's oid when the hub returns one;
- otherwise it hashes a size-matched local copy (`--local-dir`);
- it fills `format` and `weights` from a local copy's header;
- it links the repo's own LICENSE and NOTICE files.

It refuses a repo that now resolves under another name.

## From the legacy registries

`convert` maps them, and `export` projects format 3 back for decoders that haven't moved. Both are
lossless: the tests round-trip both legacy fixtures exactly.

```bash
python3 -I tools/adapter_registry.py convert klein-v1 klein-lora-registry.json --namespace flux2-klein -o flux2-klein-4b.json
python3 -I tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json
python3 -I tools/adapter_registry.py export klein-v1 flux2-klein-4b.json -o klein-lora-registry.json
```

| ltx-lora-registry v2 | Klein v1 | format 3 |
|---|---|---|
| top-level `base` | top-level `base` | `baseModels[]` per entry |
| `displayName` | `displayName` | `title` |
| `repo`, `weightFile` | `repo`, `weightFile` | `source.repo`, `source.file` + `revision`, `sha256`, `bytes`, `hashFrom` |
| `gatedDownload` | `gatedDownload` | `source.gated` |
| — | `source` (origin URL) | `source.upstream` |
| `license` (free id) | `license` | `license` (SPDX) + `licenseTerms.commercialUse` |
| `licenseGated` | — | `licenseTerms.gated` |
| `defaultStrength` | `defaultStrength` | `defaultStrength` (+ `strengthRange` when validated) |
| `surface` (absent ⇒ textToVideo) | `surface` | `capabilities[]` |
| `clearedFamilies` | — | `baseModels` + `clearance` |
| `input: "image"`, `initImage` slots | — | `baseInputs: [{input: initImage}]` |
| `conditioning[]`, `imageSet` | `conditioning[]` with `slot` | `conditioning[]` in array order, `image` + `maxCount` |
| `conditioningGroups` (prose) | — | `conditioningGroups` (`exactlyOne`, alternatives) |
| `promptConvention` | `promptTemplate` | both kept |
| `referenceDownscale`, `stage2`, `audioReference` | — | `runtime.*` |
| `verification.headerDialect`, `rank` | same | also typed: `format`, `weights` |

Legacy licence ids:

| legacy id | `license` | `commercialUse` |
|---|---|---|
| `apache-2.0` | `Apache-2.0` | allowed |
| `LTX-2-community` | `LicenseRef-LTX-2-Community` | conditional |
| `unspecified-community` | `NOASSERTION` | unknown |
| `research-only-noncommercial` | `LicenseRef-research-only-noncommercial` | prohibited |

## How the engine uses it (contracts 1.53.0 and 1.55.0, AB-A-0189)

- **Requests.** A request carries `AdapterRef {id, strength?, conditioning}`. The engine looks the id
  up in its catalog (`AdapterDescriptor`s, i.e. registry entries), then:
  - fits it against the surface's `AdapterSupport` (`capabilities`, `baseModels`, `kind`, `format`,
    strength ranges);
  - orders the conditioning into the slots and checks the groups (1.55.0);
  - materialises `source` at the pinned revision, checks its `sha256`, and uses a token from the host's
    store when `source.gated`.
- **Admission.** It charges `footprint` (or `source.bytes`) and the largest `activationMultiplier`.
- **What the engine ignores.** The registry-only fields serve hosts and consoles: licence terms for
  the commercial-use policy and provenance, clearance for model choice, prompting for the enhancer,
  and status and evidence for review.

## Changelog

- **3.0** (2026-10-19): first release. It merges ltx-lora-registry v2 and Klein v1. Entries decode as
  `AdapterDescriptor` (contract 1.53.0; groups enforced from 1.55.0). Adds pinned sources, typed licence
  terms, per-base clearance, structured groups, base-model inputs, and namespaces.
