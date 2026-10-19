#!/usr/bin/env python3
"""Offline tests for the adapter registry format: python3 -I tests/test_adapter_registry.py

The hub-side check (`validate --remote`) needs the network and is run by hand before a release.
"""

import copy
import glob
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import adapter_registry as ar  # noqa: E402


def load(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return json.load(f)


REGISTRIES = ["registry.json"] + sorted(
    os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "examples", "*.json")))


class Registries(unittest.TestCase):
    def test_every_registry_validates(self):
        schema = ar.load_schema()
        for rel in REGISTRIES:
            with self.subTest(rel):
                self.assertEqual(ar.validate_doc(load(rel), schema), [])

    def test_namespaces_are_distinct(self):
        spaces = [load(rel)["namespace"] for rel in REGISTRIES]
        self.assertEqual(len(spaces), len(set(spaces)))

    def test_projection_is_current(self):
        # dist/registry.v2.json is what the vendored MLXLTX2 / ltx-features copies sync from.
        self.assertEqual(ar.to_ltx_v2(load("registry.json")), load("dist/registry.v2.json"),
                         "regenerate: tools/adapter_registry.py export ltx-v2 registry.json -o dist/registry.v2.json")

    def test_legacy_dialects_round_trip_exactly(self):
        for rel, conv, back in (("tests/fixtures/ltx-v2-2026-09-01.json", ar.from_ltx_v2, ar.to_ltx_v2),
                                ("tests/fixtures/klein-v1-2026-10-19.json", ar.from_klein_v1, ar.to_klein_v1)):
            with self.subTest(rel):
                old = load(rel)
                self.assertEqual(back(conv(old)), old)

    def test_example_conversion_is_reproducible(self):
        # The example is the fixture converted and then pinned; pinning only fills source and the
        # licence url/notice.
        converted = ar.from_klein_v1(load("tests/fixtures/klein-v1-2026-10-19.json"))
        example = load("examples/flux2-klein-4b.json")
        for a, b in zip(converted["adapters"], example["adapters"]):
            strip = lambda e: {k: v for k, v in e.items() if k not in ("source", "licenseTerms")}  # noqa: E731
            self.assertEqual(strip(a), strip(b))
            self.assertEqual({k: v for k, v in b["source"].items()
                              if k in ("host", "repo", "file", "upstream")},
                             {k: v for k, v in a["source"].items() if k != "gated"})

    def test_gated_sources_are_the_only_local_hashes(self):
        for rel in REGISTRIES:
            for a in load(rel)["adapters"]:
                if a["source"]["hashFrom"] == "local":
                    self.assertTrue(a["source"]["gated"], a["id"])


# MLXToolKit.AdapterDescriptor (contract 1.53.0): the fields it decodes and their JSON types. A
# format-3 entry must decode as one, so the runtime catalog needs no mapping layer.
DESCRIPTOR_FIELDS = {
    "id": (str, True), "title": (str, False), "kind": (str, False), "format": (str, True),
    "baseModels": (list, True), "capabilities": (list, True), "source": (dict, True),
    "license": (str, True), "defaultStrength": ((int, float), False), "strengthRange": (dict, False),
    "conditioning": (list, False), "footprint": (dict, False),
}
CAPABILITIES = {"textToImage", "imageEdit", "textToVideo", "videoEdit"}


class DescriptorShape(unittest.TestCase):
    def test_entries_decode_as_adapter_descriptors(self):
        for rel in REGISTRIES:
            for a in load(rel)["adapters"]:
                with self.subTest(f"{rel}:{a['id']}"):
                    for key, (typ, required) in DESCRIPTOR_FIELDS.items():
                        if required:
                            self.assertIn(key, a)
                        if key in a:
                            self.assertIsInstance(a[key], typ, key)
                            self.assertFalse(isinstance(a[key], bool), key)
                    self.assertTrue(all(isinstance(b, str) for b in a["baseModels"]))
                    self.assertTrue(set(a["capabilities"]) <= CAPABILITIES)
                    src = a["source"]
                    for k in ("repo", "revision", "file", "sha256"):
                        self.assertIsInstance(src[k], str)
                    self.assertIsInstance(src["bytes"], int)
                    for slot in a.get("conditioning", []):
                        self.assertIn(slot["media"], ("image", "video", "audio"))
                    # validationProblems: slots exactly when the kind is ic.
                    self.assertEqual(a["kind"] == "ic", bool(a.get("conditioning")))
                    if "strengthRange" in a:
                        r = a["strengthRange"]
                        self.assertTrue(r["min"] <= a["defaultStrength"] <= r["max"])
                    if "footprint" in a:
                        self.assertGreaterEqual(a["footprint"]["activationMultiplier"], 1)


class GroupRules(unittest.TestCase):
    def test_parse_and_render(self):
        text = "oneOf: reference_sheet | (subject_images [+ location_image])"
        alts = ar.parse_group_rule(text)
        self.assertEqual(alts, [{"requires": ["reference_sheet"]},
                                {"requires": ["subject_images"], "allows": ["location_image"]}])
        self.assertEqual(ar.render_group_rule(alts), text)


def _by_id(doc, aid):
    return next(a for a in doc["adapters"] if a["id"] == aid)


def _ingredients(doc):
    return _by_id(doc, "ingredients")


def _plain(doc):
    return _by_id(doc, "transition")


# (name, mutation, substring the error must contain). A validator that passes all of these is not
# a validator: each case must fail, and fail for its own reason.
POISON = [
    ("branch is not a pin", lambda d: _plain(d)["source"].update(revision="main"), "revision"),
    ("short commit", lambda d: _plain(d)["source"].update(revision="685118b10f"), "revision"),
    ("upper-case sha256", lambda d: _plain(d)["source"].update(sha256=_plain(d)["source"]["sha256"].upper()), "sha256"),
    ("missing sha256", lambda d: _plain(d)["source"].pop("sha256"), "missing required 'sha256'"),
    ("path traversal", lambda d: _plain(d)["source"].update(file="../weights.safetensors"), "does not match"),
    ("bool is not an integer", lambda d: _plain(d)["source"].update(bytes=True), "expected integer"),
    ("unknown top-level key", lambda d: d.update(base="LTX-2.3-22b-distilled"), "unknown key 'base'"),
    ("legacy field on an entry", lambda d: _plain(d).update(repo="x/y"), "unknown key 'repo'"),
    ("unknown runtime key", lambda d: _plain(d).setdefault("runtime", {}).update(steps=8), "unknown key 'steps'"),
    ("wrong schemaVersion", lambda d: d.update(schemaVersion=2), "schemaVersion"),
    ("bad id", lambda d: _plain(d).update(id="Transition_1"), "does not match"),
    ("duplicate id", lambda d: d["adapters"].append(copy.deepcopy(_plain(d))), "appears 2 times"),
    ("licence as an object", lambda d: _plain(d).update(license={"spdx": "Apache-2.0"}), "expected string"),
    ("bad base-model key", lambda d: _plain(d).update(baseModels=["LTX-2.3"]), "does not match"),
    ("repeated base model", lambda d: _plain(d)["baseModels"].append("ltx-2.3"), "not unique"),
    ("base without clearance", lambda d: _plain(d)["clearance"].pop("ltx-2.5"), "has no clearance entry"),
    ("clearance for a stranger", lambda d: _plain(d)["clearance"].update({"ltx-9.9": {"level": "declared"}}),
     "not in baseModels"),
    ("unknown capability", lambda d: _plain(d).update(capabilities=["tts"]), "not one of"),
    ("empty capabilities", lambda d: _plain(d).update(capabilities=[]), "fewer than 1"),
    ("ic without slots", lambda d: _ingredients(d).pop("conditioning"), "kind 'ic' needs"),
    ("plain with slots", lambda d: _plain(d).update(conditioning=copy.deepcopy(_ingredients(d)["conditioning"][:1])),
     "only an ic adapter"),
    ("default outside range", lambda d: _ingredients(d).update(strengthRange={"min": 0.0, "max": 1.0}), "outside"),
    ("inverted range", lambda d: _plain(d).update(strengthRange={"min": 1.5, "max": 0.5}), "> max"),
    ("negative default", lambda d: _plain(d).update(defaultStrength=-1.0), "minimum"),
    ("multiplier below one", lambda d: _ingredients(d)["footprint"].update(activationMultiplier=0.9), "minimum"),
    ("footprint without bytes", lambda d: _ingredients(d)["footprint"].pop("residentBytes"),
     "missing required 'residentBytes'"),
    ("undeclared group", lambda d: _ingredients(d)["conditioning"][0].update(group="nope"), "undeclared group"),
    ("alternative names a stranger",
     lambda d: _ingredients(d)["conditioningGroups"]["sheet"]["alternatives"][0]["requires"].append("ghost"),
     "not a slot in the group"),
    ("grouped slot required", lambda d: _ingredients(d)["conditioning"][0].update(required=True), "must be required: false"),
    ("imageSet is not a media kind", lambda d: _ingredients(d)["conditioning"][1].update(media="imageSet"), "not one of"),
    ("duplicate role", lambda d: _ingredients(d)["conditioning"].append(copy.deepcopy(_ingredients(d)["conditioning"][0])),
     "appears 2 times"),
    ("base input twice", lambda d: _by_id(d, "i2v-adapter")["baseInputs"].append(
        copy.deepcopy(_by_id(d, "i2v-adapter")["baseInputs"][0])), "listed twice"),
    ("unknown base input", lambda d: _by_id(d, "i2v-adapter")["baseInputs"][0].update(input="initAudio"), "not one of"),
    ("NOASSERTION claims commercial", lambda d: (_plain(d).update(license="NOASSERTION"),
                                                 _plain(d)["licenseTerms"].update(commercialUse="allowed")),
     "NOASSERTION"),
    ("non-commercial left ungated", lambda d: _plain(d)["licenseTerms"].update(commercialUse="prohibited", gated=False),
     "must be gated"),
    ("conditional without notice", lambda d: (_plain(d)["licenseTerms"].update(commercialUse="conditional"),
                                              _plain(d)["licenseTerms"].pop("notice", None)), "needs a notice"),
    ("local hash on a public repo", lambda d: _plain(d)["source"].update(hashFrom="local"), "only a gated source"),
    ("unknown placeholder", lambda d: _plain(d).update(promptTemplate="{subject} walks"), "placeholder"),
    ("references without describable",
     lambda d: [s.pop("describable", None) for s in _ingredients(d)["conditioning"]], "no slot is describable"),
]


class Poison(unittest.TestCase):
    def test_each_mutation_fails_for_its_reason(self):
        base = load("registry.json")
        schema = ar.load_schema()
        self.assertEqual(ar.validate_doc(base, schema), [], "the unmutated registry must pass")
        for name, mutate, expect in POISON:
            with self.subTest(name):
                doc = copy.deepcopy(base)
                mutate(doc)
                errs = ar.validate_doc(doc, schema)
                self.assertTrue(any(expect in e for e in errs), f"expected '{expect}' in {errs}")

    def test_schema_refuses_unimplemented_keywords(self):
        with self.assertRaises(ValueError):
            ar.SchemaValidator({"type": "object", "oneOf": [{"type": "object"}]})


class AgainstReferenceImplementation(unittest.TestCase):
    """When the `jsonschema` package is importable, the built-in subset must agree with it."""

    def test_agrees_with_jsonschema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(ar.SCHEMA_PATH) as f:
            schema = json.load(f)
        ref = jsonschema.Draft202012Validator(schema)
        ours = ar.SchemaValidator(schema)
        base = load("registry.json")
        docs = [load(rel) for rel in REGISTRIES]
        for name, mutate, _ in POISON:
            doc = copy.deepcopy(base)
            mutate(doc)
            docs.append(doc)
        for i, doc in enumerate(docs):
            with self.subTest(i):
                self.assertEqual(bool(ours.errors(doc)), bool(list(ref.iter_errors(doc))))


if __name__ == "__main__":
    unittest.main(verbosity=1)
