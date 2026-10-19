#!/usr/bin/env python3
"""adapter_registry.py: validate, pin, convert and export adapter registries (format 3).

Stdlib only, so it runs anywhere `python3` does:

    python3 -I tools/adapter_registry.py validate registry.json [--remote]
    python3 -I tools/adapter_registry.py pin registry.json --local-dir <cache> --write
    python3 -I tools/adapter_registry.py convert klein-v1 old.json --namespace flux2-klein -o new.json
    python3 -I tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json

The format is FORMAT.md; the authoring contract is schema/adapter-registry-3.schema.json, which
`validate` interprets with a small built-in JSON Schema subset (it refuses a schema that uses a
keyword it does not implement, so the two cannot silently drift apart).
"""

import argparse
import collections
import hashlib
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schema", "adapter-registry-3.schema.json")
HUB = "https://huggingface.co"


# ---------------------------------------------------------------------------------------------
# JSON Schema subset

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "default", "examples", "$defs"}
_SUPPORTED = _ANNOTATIONS | {
    "$ref", "type", "enum", "const", "pattern", "required", "properties", "additionalProperties",
    "items", "minItems", "uniqueItems", "minimum", "maximum", "exclusiveMinimum", "minLength",
}


def _json_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _json_equal(a, b):
    # Python says True == 1; JSON does not.
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


class SchemaValidator:
    def __init__(self, schema):
        self.root = schema
        self._audit(schema, "#")

    def _audit(self, node, where):
        if not isinstance(node, dict):
            raise ValueError(f"schema node at {where} is not an object")
        for key in node:
            if key not in _SUPPORTED:
                raise ValueError(f"schema uses unsupported keyword {key!r} at {where}")
        for container in ("properties", "$defs"):
            for name, sub in node.get(container, {}).items():
                self._audit(sub, f"{where}/{container}/{name}")
        if "items" in node:
            self._audit(node["items"], f"{where}/items")
        if isinstance(node.get("additionalProperties"), dict):
            self._audit(node["additionalProperties"], f"{where}/additionalProperties")

    def _resolve(self, ref):
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        node = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def errors(self, instance):
        out = []
        self._check(instance, self.root, "$", out)
        return out

    def _check(self, v, s, path, out):
        if "$ref" in s:
            self._check(v, self._resolve(s["$ref"]), path, out)
        t = s.get("type")
        if t is not None:
            actual = _json_type(v)
            ok = actual == t or (t == "number" and actual == "integer") or (
                t == "integer" and actual == "number" and float(v).is_integer())
            if not ok:
                out.append(f"{path}: expected {t}, got {actual}")
                return
        if "const" in s and not _json_equal(v, s["const"]):
            out.append(f"{path}: must be {json.dumps(s['const'])}")
        if "enum" in s and not any(_json_equal(v, e) for e in s["enum"]):
            out.append(f"{path}: {json.dumps(v)} is not one of {json.dumps(s['enum'])}")
        if isinstance(v, str):
            if "minLength" in s and len(v) < s["minLength"]:
                out.append(f"{path}: shorter than {s['minLength']}")
            if "pattern" in s and not re.search(s["pattern"], v):
                out.append(f"{path}: {json.dumps(v)} does not match {s['pattern']}")
        if _json_type(v) in ("integer", "number"):
            if "minimum" in s and v < s["minimum"]:
                out.append(f"{path}: {v} < minimum {s['minimum']}")
            if "maximum" in s and v > s["maximum"]:
                out.append(f"{path}: {v} > maximum {s['maximum']}")
            if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
                out.append(f"{path}: {v} <= exclusiveMinimum {s['exclusiveMinimum']}")
        if isinstance(v, list):
            if "minItems" in s and len(v) < s["minItems"]:
                out.append(f"{path}: fewer than {s['minItems']} items")
            if s.get("uniqueItems"):
                seen = [json.dumps(x, sort_keys=True) for x in v]
                if len(seen) != len(set(seen)):
                    out.append(f"{path}: items are not unique")
            if "items" in s:
                for i, item in enumerate(v):
                    self._check(item, s["items"], f"{path}[{i}]", out)
        if isinstance(v, dict):
            for key in s.get("required", []):
                if key not in v:
                    out.append(f"{path}: missing required '{key}'")
            props = s.get("properties", {})
            extra = s.get("additionalProperties", True)
            for key, sub in v.items():
                if key in props:
                    self._check(sub, props[key], f"{path}.{key}", out)
                elif extra is False:
                    out.append(f"{path}: unknown key '{key}'")
                elif isinstance(extra, dict):
                    self._check(sub, extra, f"{path}.{key}", out)


def load_schema():
    with open(SCHEMA_PATH) as f:
        return SchemaValidator(json.load(f))


# ---------------------------------------------------------------------------------------------
# Rules the schema cannot express

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def semantic_errors(doc):
    out = []
    ids = collections.Counter(a.get("id") for a in doc.get("adapters", []))
    for dup, n in ids.items():
        if n > 1:
            out.append(f"adapters: id '{dup}' appears {n} times")
    for i, a in enumerate(doc.get("adapters", [])):
        where = f"adapters[{i}] ({a.get('id')})"
        slots = a.get("conditioning", [])
        roles = [s.get("role") for s in slots]
        groups = a.get("conditioningGroups", {})

        # AdapterDescriptor.validationProblems: slots exactly when the kind is ic.
        if a.get("kind") == "ic" and not slots:
            out.append(f"{where}: kind 'ic' needs at least one conditioning slot")
        if a.get("kind") == "plain" and slots:
            out.append(f"{where}: only an ic adapter declares conditioning slots (a base-model input is baseInputs)")
        if groups and not slots:
            out.append(f"{where}: conditioningGroups without conditioning slots")
        for role, n in collections.Counter(roles).items():
            if n > 1:
                out.append(f"{where}: slot role '{role}' appears {n} times")
        for s in slots:
            if s.get("group") is not None and s["group"] not in groups:
                out.append(f"{where}: slot '{s.get('role')}' names undeclared group '{s['group']}'")
        for name, g in groups.items():
            members = {s.get("role") for s in slots if s.get("group") == name}
            if not members:
                out.append(f"{where}: group '{name}' has no member slots")
            covered = set()
            for alt in g.get("alternatives", []):
                alt_roles = set(alt.get("requires", [])) | set(alt.get("allows", []))
                for r in sorted(alt_roles - members):
                    out.append(f"{where}: group '{name}' alternative names '{r}', which is not a slot in the group")
                if set(alt.get("requires", [])) & set(alt.get("allows", [])):
                    out.append(f"{where}: group '{name}' alternative both requires and allows the same role")
                covered |= alt_roles
            for r in sorted(members - covered):
                out.append(f"{where}: slot '{r}' is in group '{name}' but no alternative uses it")
            if any(s.get("required") for s in slots if s.get("group") == name):
                out.append(f"{where}: grouped slots must be required: false; the group decides")
        inputs = [b.get("input") for b in a.get("baseInputs", [])]
        if len(inputs) != len(set(inputs)):
            out.append(f"{where}: a baseInputs input is listed twice")

        rng = a.get("strengthRange")
        if rng and {"min", "max"} <= rng.keys():
            if rng["min"] > rng["max"]:
                out.append(f"{where}: strengthRange min {rng['min']} > max {rng['max']}")
            elif "defaultStrength" in a and not rng["min"] <= a["defaultStrength"] <= rng["max"]:
                out.append(f"{where}: defaultStrength {a['defaultStrength']} is outside strengthRange "
                           f"[{rng['min']}, {rng['max']}]")

        bases = set(a.get("baseModels", []))
        cleared = set(a.get("clearance", {}))
        for k in sorted(bases - cleared):
            out.append(f"{where}: base model '{k}' has no clearance entry")
        for k in sorted(cleared - bases):
            out.append(f"{where}: clearance names '{k}', which is not in baseModels")

        terms = a.get("licenseTerms", {})
        if a.get("license") == "NOASSERTION" and terms.get("commercialUse") not in (None, "unknown"):
            out.append(f"{where}: license NOASSERTION must have commercialUse 'unknown'")
        if terms.get("commercialUse") == "prohibited" and terms.get("gated") is False:
            out.append(f"{where}: a non-commercial licence must be gated (operator accepts terms)")
        if terms.get("commercialUse") == "conditional" and not terms.get("notice"):
            out.append(f"{where}: commercialUse 'conditional' needs a notice stating the conditions")

        src = a.get("source", {})
        if src.get("hashFrom") == "local" and src.get("gated") is False:
            out.append(f"{where}: only a gated source may carry a local (hub-unconfirmed) hash")

        tmpl = a.get("promptTemplate")
        if tmpl:
            for ph in _PLACEHOLDER.findall(tmpl):
                if ph not in ("prompt", "references"):
                    out.append(f"{where}: promptTemplate placeholder '{{{ph}}}' is not {{prompt}} or {{references}}")
            if "{references}" in tmpl and not any(s.get("describable") for s in slots):
                out.append(f"{where}: promptTemplate uses {{references}} but no slot is describable")
    return out


def semantic_warnings(doc):
    """Valid format 3, but something a consumer will trip on."""
    out = []
    for a in doc.get("adapters", []):
        if a.get("kind") == "ic" and "footprint" not in a:
            out.append(f"{a.get('id')}: ic adapter without a measured footprint; the engine (contract 1.53) "
                       f"refuses to register it until one is declared")
    return out


def validate_doc(doc, schema=None):
    schema = schema or load_schema()
    errs = schema.errors(doc)
    return errs + semantic_errors(doc) if not errs else errs


# ---------------------------------------------------------------------------------------------
# Hub access. Unauthenticated unless the operator passes --token-file: a source that resolves
# only with our token is not reachable for users (AB-L-0055: resolution is not reachability).

class Hub:
    def __init__(self, token=None):
        self.token = token

    def _request(self, url, data=None):
        headers = {"User-Agent": "adapter-registry/3"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None

    def repo_info(self, repo):
        return self._request(f"{HUB}/api/models/{repo}")

    def commit(self, repo, revision):
        status, info = self._request(f"{HUB}/api/models/{repo}/revision/{urllib.parse.quote(revision, safe='')}")
        return status, (info or {}).get("sha")

    def path_info(self, repo, revision, path):
        status, rows = self._request(f"{HUB}/api/models/{repo}/paths-info/{revision}",
                                     {"paths": [path], "expand": "true"})
        if status == 200 and rows:
            return status, rows[0]
        # paths-info is 401 on a gated repo without a token; the tree listing still answers,
        # with the size but the LFS oid masked.
        folder = os.path.dirname(path)
        tstatus, tree = self.tree(repo, revision, folder)
        row = next((r for r in tree or [] if r.get("path") == path), None)
        return (tstatus if row else status), row

    def tree(self, repo, revision, folder=""):
        url = f"{HUB}/api/models/{repo}/tree/{revision}" + (f"/{urllib.parse.quote(folder)}" if folder else "")
        status, rows = self._request(url)
        return status, rows if isinstance(rows, list) else None


def _hub_oid(row):
    oid = ((row or {}).get("lfs") or {}).get("oid")
    return oid if oid and re.fullmatch(r"[0-9a-f]{64}", oid) else None


def remote_errors(doc, hub):
    """Check every pinned source against the hub: repo not renamed, file present at the pinned
    commit, size and sha256 equal, gating as recorded."""
    out, notes = [], []
    for a in doc.get("adapters", []):
        src, where = a["source"], f"{a['id']} ({a['source']['repo']})"
        status, info = hub.repo_info(src["repo"])
        if status != 200 or not info:
            out.append(f"{where}: repo info HTTP {status}")
            continue
        if info.get("id") != src["repo"]:
            out.append(f"{where}: repo now resolves to '{info.get('id')}' (renamed upstream)")
        hub_gated = info.get("gated") not in (False, None)
        if hub_gated != src["gated"]:
            out.append(f"{where}: source.gated is {src['gated']} but the hub says gated={info.get('gated')}")
        status, row = hub.path_info(src["repo"], src["revision"], src["file"])
        if row is None:
            out.append(f"{where}: '{src['file']}' not found at {src['revision'][:12]} (HTTP {status})")
            continue
        size = (row.get("lfs") or {}).get("size") or row.get("size")
        if size != src["bytes"]:
            out.append(f"{where}: hub size {size} != recorded bytes {src['bytes']}")
        oid = _hub_oid(row)
        if oid is None:
            if src["hashFrom"] == "hub":
                out.append(f"{where}: hashFrom 'hub' but the hub did not return the LFS oid")
            else:
                notes.append(f"{where}: sha256 is our local copy's; hub oid masked (gated); size matches")
        elif oid != src["sha256"]:
            out.append(f"{where}: hub sha256 {oid[:16]}… != recorded {src['sha256'][:16]}…")
        elif src["hashFrom"] == "local":
            notes.append(f"{where}: hub oid now readable and equal; hashFrom can become 'hub'")
    return out, notes


# ---------------------------------------------------------------------------------------------
# Pinning

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_copy(adapter, local_dirs, size):
    for d in local_dirs:
        for name in (f"{adapter['id']}.safetensors", adapter["source"]["file"]):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.path.getsize(p) == size:
                return p
    return None


def pin_adapter(adapter, hub, local_dirs, log=print):
    """Fill source.revision/sha256/bytes/gated/hashFrom from the hub, resolving a branch or a
    missing revision to its commit. A masked oid (gated repo, no token) falls back to a local
    copy whose size matches the hub's."""
    src = adapter["source"]
    status, info = hub.repo_info(src["repo"])
    if status != 200 or not info:
        raise RuntimeError(f"{adapter['id']}: repo {src['repo']} HTTP {status}")
    if info.get("id") != src["repo"]:
        raise RuntimeError(f"{adapter['id']}: {src['repo']} now resolves to {info.get('id')}; update repo/file by hand")
    rev = src.get("revision") or "main"
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        status, sha = hub.commit(src["repo"], rev)
        if not sha:
            raise RuntimeError(f"{adapter['id']}: cannot resolve revision '{rev}' (HTTP {status})")
        rev = sha
    status, row = hub.path_info(src["repo"], rev, src["file"])
    if row is None:
        raise RuntimeError(f"{adapter['id']}: '{src['file']}' not at {rev[:12]} (HTTP {status})")
    size = (row.get("lfs") or {}).get("size") or row.get("size")
    oid = _hub_oid(row)
    if oid:
        sha, origin = oid, "hub"
    else:
        local = _local_copy(adapter, local_dirs, size)
        if not local:
            raise RuntimeError(f"{adapter['id']}: hub masks the oid and no local copy of {size} bytes was found")
        sha, origin = _sha256_file(local), "local"
        log(f"  {adapter['id']}: hub oid masked; hashed local {local}")
    src.update(revision=rev, sha256=sha, bytes=size, hashFrom=origin,
               gated=info.get("gated") not in (False, None))
    lic = adapter.get("licenseTerms")
    if lic is not None and ("url" not in lic or "notice" not in lic):
        status, rows = hub.tree(src["repo"], rev)
        names = {r.get("path") for r in rows or [] if r.get("size")}   # an empty LICENSE says nothing
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
            if name in names and "url" not in lic:
                lic["url"] = f"{HUB}/{src['repo']}/blob/{rev}/{urllib.parse.quote(name)}"
                break
        if "NOTICE" in names and "notice" not in lic:
            lic["notice"] = (f"Keep this repo's NOTICE file (the original author's attribution) with any "
                             f"redistributed copy: {HUB}/{src['repo']}/blob/{rev}/NOTICE")
    return adapter


def read_safetensors_header(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    header.pop("__metadata__", None)
    return header


def weights_from_header(header):
    """`format` (the key dialect) and the typed weights block from a safetensors header. Only
    LTX's dialect is detected from keys; image dialects come from the registry's declared
    headerDialect."""
    keys = list(header)
    if not (keys and all(k.startswith("diffusion_model.") for k in keys) and any(".lora_A." in k for k in keys)):
        return None, {}
    ranks = {min(v["shape"]) for k, v in header.items() if ".lora_A." in k and len(v["shape"]) == 2}
    w = {}
    if len(ranks) == 1:
        w["rank"] = ranks.pop()
    w["audioBranch"] = any(".audio" in k or "audio_" in k for k in keys)
    return "diffusers-peft", w


# ---------------------------------------------------------------------------------------------
# Legacy dialects

LTX_NOTICE = ("Derivative of LTX-2 under the LTX-2 Community License Agreement (Lightricks). Entities with "
              "annual revenue of US$10M or more need a separate licence from Lightricks; the use "
              "restrictions in its Attachment A apply. Carry the licence with the weights and outputs.")

# legacy licence id -> (spdx, commercialUse, notice)
LICENSES = {
    "apache-2.0": ("Apache-2.0", "allowed", None),
    "LTX-2-community": ("LicenseRef-LTX-2-Community", "conditional", LTX_NOTICE),
    "unspecified-community": ("NOASSERTION", "unknown", None),
    "research-only-noncommercial": ("LicenseRef-research-only-noncommercial", "prohibited",
                                    "The author releases these weights for research only; not for commercial use."),
}
LICENSES_BACK = {v[0]: k for k, v in LICENSES.items()}

# legacy verification.headerDialect -> format (AdapterFormat). Klein's names stay its own until
# mlx-image maps them onto the engine's constants.
DIALECTS = {
    "diffusers-PEFT (diffusion_model.*)": "diffusers-peft",
    "bfl-fused": "bfl-fused",
    "bfl-fused-peft": "bfl-fused-peft",
    "diffusers-split": "diffusers-split",
}

LTX_BASE = "LTX-2.3-22b-distilled"
_LIVE_KEYS = ("liveGate", "perceptualPass", "functionalEval", "cannyLiveFixture")
INIT_IMAGE_ROLE = "init_image"


def _license(legacy_id, gated):
    if legacy_id not in LICENSES:
        raise ValueError(f"unknown legacy licence id '{legacy_id}'; add it to LICENSES")
    spdx, commercial, notice = LICENSES[legacy_id]
    terms = {"commercialUse": commercial, "gated": bool(gated)}
    if notice:
        terms["notice"] = notice
    return spdx, terms


def parse_group_rule(text):
    """'oneOf: a | (b [+ c])' -> alternatives [{requires:[a]}, {requires:[b], allows:[c]}]."""
    body = text.split(":", 1)[1] if text.strip().startswith("oneOf") else text
    alts = []
    for part in body.split("|"):
        part = part.strip().strip("()").strip()
        allows = re.findall(r"\[\+\s*([a-z0-9_]+)\s*\]", part)
        requires = [r.strip() for r in re.sub(r"\[\+[^\]]*\]", "", part).split("+") if r.strip()]
        alt = {"requires": requires}
        if allows:
            alt["allows"] = allows
        alts.append(alt)
    return alts


def render_group_rule(alternatives):
    parts = []
    for alt in alternatives:
        s = " + ".join(alt["requires"]) + "".join(f" [+ {r}]" for r in alt.get("allows", []))
        parts.append(f"({s})" if len(alt["requires"]) + len(alt.get("allows", [])) > 1 else s)
    return "oneOf: " + " | ".join(parts)


def _slot_from_legacy(s):
    slot = {k: v for k, v in s.items() if k != "slot" and v is not None}
    if slot.get("media") == "imageSet":
        if "maxCount" not in slot:
            raise ValueError(f"imageSet slot '{slot.get('role')}' has no maxCount")
        slot["media"] = "image"
    return slot


def _slot_to_ltx_v2(s):
    slot = dict(s)
    if slot.get("maxCount", 1) > 1:
        slot["media"] = "imageSet"
    return slot


def from_ltx_v2(old, namespace="ltx", title="LTX-2 adapters"):
    if old.get("schemaVersion") != 2:
        raise ValueError("expected an ltx-lora-registry schemaVersion 2 document")
    base_key = "ltx-" + re.match(r"LTX-(\d+\.\d+)", old["base"]).group(1)
    adapters = []
    for e in old["adapters"]:
        ver = e.get("verification") or {}
        live = e.get("status") == "verified" or any(k in ver for k in _LIVE_KEYS)
        bases = e.get("clearedFamilies") or [base_key]
        spdx, terms = _license(e["license"], e.get("licenseGated", False))
        slots = e.get("conditioning", [])
        a = {
            "id": e["id"],
            "title": e["displayName"],
            "kind": e.get("kind", "plain"),
            "format": DIALECTS.get(ver.get("headerDialect", "")),
            "baseModels": bases,
            "capabilities": [e.get("surface", "textToVideo")],
            "source": {"host": "huggingface", "repo": e["repo"], "file": e["weightFile"],
                       "gated": bool(e.get("gatedDownload", False))},
            "license": spdx,
            "licenseTerms": terms,
            "defaultStrength": e["defaultStrength"],
        }
        if a["format"] is None:
            del a["format"]   # filled from the safetensors header by `pin --local-dir`
        ic_slots = [_slot_from_legacy(s) for s in slots if s.get("ingest") != "initImage"]
        if ic_slots:
            a["conditioning"] = ic_slots
        if e.get("conditioningGroups"):
            a["conditioningGroups"] = {name: {"rule": "exactlyOne", "required": True,
                                              "alternatives": parse_group_rule(text)}
                                       for name, text in e["conditioningGroups"].items()}
        a["status"] = e["status"]
        a["clearance"] = {k: {"level": "perceptual" if k == base_key and live else "mechanical"} for k in bases}
        if e.get("trigger"):
            a["trigger"] = e["trigger"]
        if e.get("promptConvention"):
            a["promptConvention"] = e["promptConvention"]
        init = [s for s in slots if s.get("ingest") == "initImage"]
        if init:
            if len(init) > 1 or init[0]["role"] != INIT_IMAGE_ROLE:
                raise ValueError(f"{e['id']}: an initImage slot must be the single role '{INIT_IMAGE_ROLE}'")
            b = {"input": "initImage", "required": init[0]["required"]}
            if "note" in init[0]:
                b["note"] = init[0]["note"]
            a["baseInputs"] = [b]
        weights = {k: ver[k] for k in ("rank", "audioBranch") if k in ver}
        if weights:
            a["weights"] = weights
        runtime = {k: e[k] for k in ("referenceDownscale", "stage2", "audioReference") if k in e}
        if runtime:
            a["runtime"] = runtime
        if ver:
            a["verification"] = ver
        adapters.append(a)
    return {"format": "adapter-registry", "schemaVersion": 3, "namespace": namespace,
            "title": title, "adapters": adapters}


def to_ltx_v2(doc):
    """Project format 3 onto ltx-lora-registry v2, the shape MLXLTX2.LoRARegistry and
    ltx-features-swift's AdapterRegistry decode, for vendored copies that have not moved yet."""
    out = []
    for a in doc["adapters"]:
        src, terms = a["source"], a["licenseTerms"]
        e = {"id": a["id"], "displayName": a["title"], "kind": a["kind"], "status": a["status"],
             "license": LICENSES_BACK.get(a["license"], a["license"])}
        if terms["gated"]:
            e["licenseGated"] = True
        if src["gated"]:
            e["gatedDownload"] = True
        e.update(repo=src["repo"], weightFile=src["file"], defaultStrength=a["defaultStrength"],
                 trigger=a.get("trigger", ""))
        slots = [_slot_to_ltx_v2(s) for s in a.get("conditioning", [])]
        for b in a.get("baseInputs", []):
            if b["input"] == "initImage":
                slot = {"role": INIT_IMAGE_ROLE, "media": "image", "required": b["required"], "ingest": "initImage"}
                if "note" in b:
                    slot["note"] = b["note"]
                slots.append(slot)
                if a["kind"] == "plain":
                    e["input"] = "image"
        rt = a.get("runtime", {})
        for k in ("referenceDownscale", "stage2"):
            if k in rt:
                e[k] = rt[k]
        if a["kind"] == "ic" or a["capabilities"] != ["textToVideo"]:
            e["surface"] = a["capabilities"][0]
        if "promptConvention" in a:
            e["promptConvention"] = a["promptConvention"]
        if slots:
            e["conditioning"] = slots
        if "conditioningGroups" in a:
            e["conditioningGroups"] = {n: render_group_rule(g["alternatives"])
                                       for n, g in a["conditioningGroups"].items()}
        if "verification" in a:
            e["verification"] = a["verification"]
        if "audioReference" in rt:
            e["audioReference"] = rt["audioReference"]
        if a["baseModels"] != ["ltx-2.3"]:
            e["clearedFamilies"] = list(a["baseModels"])
        out.append(e)
    return {"schemaVersion": 2, "base": LTX_BASE, "adapters": out}


def _klein_base(base):
    m = re.fullmatch(r"FLUX\.2-klein-(\w+)", base)
    if not m:
        raise ValueError(f"unexpected Klein base '{base}'")
    return f"flux2-klein-{m.group(1).lower()}"


def from_klein_v1(old, namespace="flux2-klein", title="FLUX.2 Klein adapters"):
    if old.get("schemaVersion") != 1:
        raise ValueError("expected a klein-lora-registry schemaVersion 1 document")
    base = _klein_base(old["base"])
    adapters = []
    for e in old["adapters"]:
        ver = e.get("verification") or {}
        spdx, terms = _license(e.get("license", "unspecified-community"), False)
        a = {
            "id": e["id"],
            "title": e["displayName"],
            "kind": e["kind"],
            "format": DIALECTS.get(ver.get("headerDialect", "")),
            "baseModels": [base],
            "capabilities": [e["surface"]],
            "source": {"host": "huggingface", "repo": e["repo"], "file": e["weightFile"],
                       "gated": bool(e.get("gatedDownload", False))},
            "license": spdx,
            "licenseTerms": terms,
            "defaultStrength": e["defaultStrength"],
        }
        if a["format"] is None:
            del a["format"]
        if e.get("source"):
            a["source"]["upstream"] = e["source"]
        if e.get("conditioning"):
            a["conditioning"] = [_slot_from_legacy(s) for s in sorted(e["conditioning"], key=lambda s: s["slot"])]
        a["status"] = e.get("status", "candidate")
        a["clearance"] = {base: {"level": "perceptual" if "E2E" in ver.get("gate", "") else "mechanical"}}
        if e.get("trigger"):
            a["trigger"] = e["trigger"]
        if e.get("promptTemplate"):
            a["promptTemplate"] = e["promptTemplate"]
        weights = {k: ver[k] for k in ("rank", "alpha") if k in ver}
        if weights:
            a["weights"] = weights
        if ver:
            a["verification"] = ver
        adapters.append(a)
    return {"format": "adapter-registry", "schemaVersion": 3, "namespace": namespace,
            "title": title, "adapters": adapters}


def to_klein_v1(doc):
    """Project format 3 onto the shape MLXKlein.LoRARegistry decodes today."""
    bases = {b for a in doc["adapters"] for b in a["baseModels"]}
    if len(bases) != 1 or not next(iter(bases)).startswith("flux2-klein-"):
        raise ValueError("klein-v1 holds adapters for exactly one FLUX.2 Klein base")
    size = next(iter(bases))[len("flux2-klein-"):]
    out = []
    for a in doc["adapters"]:
        src = a["source"]
        e = {"id": a["id"], "displayName": a["title"], "repo": src["repo"], "weightFile": src["file"],
             "trigger": a.get("trigger", ""), "defaultStrength": a["defaultStrength"],
             "kind": a["kind"], "surface": a["capabilities"][0], "status": a["status"],
             "license": LICENSES_BACK.get(a["license"], a["license"]), "gatedDownload": src["gated"]}
        if "upstream" in src:
            e["source"] = src["upstream"]
        if "promptTemplate" in a:
            e["promptTemplate"] = a["promptTemplate"]
        if a.get("conditioning"):
            e["conditioning"] = [dict({"slot": i + 1}, **s) for i, s in enumerate(a["conditioning"])]
        if "verification" in a:
            e["verification"] = a["verification"]
        out.append(e)
    return {"schemaVersion": 1, "base": f"FLUX.2-klein-{size.upper()}", "adapters": out}


CONVERTERS = {"ltx-v2": from_ltx_v2, "klein-v1": from_klein_v1}
EXPORTERS = {"ltx-v2": to_ltx_v2, "klein-v1": to_klein_v1}


# ---------------------------------------------------------------------------------------------
# CLI

def _read(path):
    with open(path) as f:
        return json.load(f)


_ENTRY_ORDER = ["id", "title", "kind", "format", "baseModels", "capabilities", "source", "license",
                "licenseTerms", "defaultStrength", "strengthRange", "conditioning", "conditioningGroups",
                "footprint", "status", "clearance", "trigger", "promptTemplate", "promptConvention",
                "baseInputs", "weights", "runtime", "verification"]
_SOURCE_ORDER = ["host", "repo", "revision", "file", "sha256", "bytes", "gated", "hashFrom", "upstream", "note"]


def _ordered(d, order):
    rank = {k: i for i, k in enumerate(order)}
    return dict(sorted(d.items(), key=lambda kv: rank.get(kv[0], len(order))))


def canonical(doc):
    """Format 3 in a stable key order: AdapterDescriptor's fields first, then the registry's own."""
    if doc.get("format") != "adapter-registry":
        return doc
    out = _ordered(doc, ["$schema", "format", "schemaVersion", "namespace", "title", "description", "adapters"])
    out["adapters"] = []
    for a in doc["adapters"]:
        a = _ordered(a, _ENTRY_ORDER)
        if "source" in a:
            a["source"] = _ordered(a["source"], _SOURCE_ORDER)
        out["adapters"].append(a)
    return out


def _write(doc, path):
    text = json.dumps(canonical(doc), indent=2, ensure_ascii=False) + "\n"
    if path in (None, "-"):
        sys.stdout.write(text)
    else:
        with open(path, "w") as f:
            f.write(text)


def _token(args):
    if not getattr(args, "token_file", None):
        return None
    with open(os.path.expanduser(args.token_file)) as f:
        return f.read().strip() or None


def _fill_from_local(doc, local_dirs):
    """Fill `format` and `weights` from a local copy's safetensors header where the registry
    does not already say."""
    for a in doc["adapters"]:
        if "format" in a and "rank" in a.get("weights", {}):
            continue
        size = a["source"].get("bytes")
        local = _local_copy(a, local_dirs, size) if size else None
        if local is None:
            for d in local_dirs:
                p = os.path.join(d, f"{a['id']}.safetensors")
                if os.path.isfile(p):
                    local = p
                    break
        if not local:
            continue
        fmt, weights = weights_from_header(read_safetensors_header(local))
        if fmt and "format" not in a:
            a["format"] = fmt
        for k, v in weights.items():
            a.setdefault("weights", {}).setdefault(k, v)


def cmd_validate(args):
    schema = load_schema()
    hub = Hub(_token(args)) if args.remote else None
    failed = False
    for path in args.files:
        doc = _read(path)
        errs = validate_doc(doc, schema)
        notes = []
        if hub and not errs:
            remote, notes = remote_errors(doc, hub)
            errs += remote
        for n in notes + (semantic_warnings(doc) if not errs else []):
            print(f"note  {path}: {n}")
        if errs:
            failed = True
            for e in errs:
                print(f"FAIL  {path}: {e}")
        else:
            n = len(doc.get("adapters", []))
            print(f"ok    {path}: {n} adapters{' (hub-checked)' if hub else ''}")
    return 1 if failed else 0


def cmd_pin(args):
    doc = _read(args.file)
    hub = Hub(_token(args))
    for a in doc["adapters"]:
        pin_adapter(a, hub, args.local_dir, log=lambda m: print(m, file=sys.stderr))
    _fill_from_local(doc, args.local_dir)
    _write(doc, args.file if args.write else "-")
    return 0


def cmd_convert(args):
    old = _read(args.input)
    kwargs = {k: v for k, v in (("namespace", args.namespace), ("title", args.title)) if v}
    doc = CONVERTERS[args.dialect](old, **kwargs)
    if not args.no_pin:
        hub = Hub(_token(args))
        for a in doc["adapters"]:
            pin_adapter(a, hub, args.local_dir, log=lambda m: print(m, file=sys.stderr))
        _fill_from_local(doc, args.local_dir)
        errs = validate_doc(doc)
        for e in errs:
            print(f"FAIL  converted: {e}", file=sys.stderr)
        if errs:
            _write(doc, args.output)
            return 1
    _write(doc, args.output)
    return 0


def cmd_export(args):
    doc = _read(args.input)
    errs = validate_doc(doc)
    if errs:
        for e in errs:
            print(f"FAIL  {args.input}: {e}", file=sys.stderr)
        return 1
    _write(EXPORTERS[args.dialect](doc), args.output)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="schema + semantic checks; --remote also checks pins against the hub")
    v.add_argument("files", nargs="+")
    v.add_argument("--remote", action="store_true")
    v.add_argument("--token-file", help="HF token file, for gated repos (default: unauthenticated)")
    v.set_defaults(func=cmd_validate)

    pn = sub.add_parser("pin", help="resolve revisions to commits and record sha256 + bytes")
    pn.add_argument("file")
    pn.add_argument("--local-dir", action="append", default=[], help="where verified copies live (for gated repos)")
    pn.add_argument("--token-file")
    pn.add_argument("--write", action="store_true", help="rewrite the file in place (default: stdout)")
    pn.set_defaults(func=cmd_pin)

    c = sub.add_parser("convert", help="legacy registry -> format 3 (pinned unless --no-pin)")
    c.add_argument("dialect", choices=sorted(CONVERTERS))
    c.add_argument("input")
    c.add_argument("-o", "--output")
    c.add_argument("--namespace")
    c.add_argument("--title")
    c.add_argument("--local-dir", action="append", default=[])
    c.add_argument("--token-file")
    c.add_argument("--no-pin", action="store_true")
    c.set_defaults(func=cmd_convert)

    e = sub.add_parser("export", help="format 3 -> a legacy shape for decoders that have not moved yet")
    e.add_argument("dialect", choices=sorted(EXPORTERS))
    e.add_argument("input")
    e.add_argument("-o", "--output")
    e.set_defaults(func=cmd_export)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
