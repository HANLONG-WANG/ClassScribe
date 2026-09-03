"""Stage 0 executable checks using only the Python standard library."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "product-contract.v1.json"
SCHEMA_PATH = ROOT / "protocol" / "schema" / "v1" / "product-contract.schema.json"
PRD_PATH = ROOT / "docs" / "product-requirements.md"
TRACE_PATH = ROOT / "docs" / "requirements-traceability.md"


class ProductContractTests(unittest.TestCase):
    contract: ClassVar[dict[str, Any]]
    schema: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_frozen_enumerations(self) -> None:
        self.assertEqual(
            self.contract["product_surfaces"],
            ["classroom_workbench", "ibus_dictation"],
        )
        self.assertEqual(self.contract["language_modes"], ["zh", "ja", "en", "auto_mixed"])
        self.assertEqual(
            self.contract["model_selection_modes"],
            ["auto_best", "manual_primary", "strict_single_model"],
        )
        self.assertEqual(self.contract["text_layers"], ["faithful", "corrected", "user"])

    def test_local_only_privacy_policy(self) -> None:
        privacy = self.contract["privacy"]
        self.assertIs(privacy["local_inference_only"], True)
        self.assertIs(privacy["telemetry"], False)
        self.assertEqual(
            privacy["network_operations"],
            ["user_initiated_model_install", "user_initiated_model_update"],
        )

    def test_language_specific_inaudible_markers(self) -> None:
        self.assertEqual(
            self.contract["inaudible_markers"],
            {
                "zh": "［听不清 {start}–{end}］",
                "ja": "［聞き取り不明 {start}–{end}］",
                "en": "[inaudible {start}–{end}]",
            },
        )

    def test_schema_freezes_the_example(self) -> None:
        properties = self.schema["properties"]
        for key in (
            "contract_version",
            "product_surfaces",
            "language_modes",
            "model_selection_modes",
            "text_layers",
        ):
            value = self.contract[key]
            self.assertEqual(properties[key]["const"], value)

        marker_schema = properties["inaudible_markers"]
        self.assertFalse(marker_schema["additionalProperties"])
        self.assertEqual(set(marker_schema["required"]), set(self.contract["inaudible_markers"]))
        for language, marker in self.contract["inaudible_markers"].items():
            self.assertEqual(marker_schema["properties"][language]["const"], marker)

        privacy_schema = properties["privacy"]
        self.assertFalse(privacy_schema["additionalProperties"])
        self.assertEqual(set(privacy_schema["required"]), set(self.contract["privacy"]))
        for key, value in self.contract["privacy"].items():
            self.assertEqual(privacy_schema["properties"][key]["const"], value)

        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["required"]), set(self.contract))

    def test_every_prd_requirement_is_in_traceability_matrix(self) -> None:
        prd = PRD_PATH.read_text(encoding="utf-8")
        trace = TRACE_PATH.read_text(encoding="utf-8")
        requirement_ids = set(re.findall(r"`(PRD-[A-Z]+-[0-9]{3})`", prd))
        self.assertEqual(len(requirement_ids), 46)
        missing = sorted(req for req in requirement_ids if f"`{req}`" not in trace)
        self.assertEqual(missing, [], f"Untraced requirements: {missing}")

    def test_public_copy_contains_required_limitations(self) -> None:
        copy = "\n".join(
            (
                (ROOT / "README.md").read_text(encoding="utf-8"),
                (ROOT / "docs" / "ui-copy-guidelines.md").read_text(encoding="utf-8"),
            )
        ).replace("**", "")
        for required_phrase in (
            "不承诺任何录音条件下都达到零 CER/WER",
            "不承诺重叠语音",
            "厂商",
            "不得为了让文本显得完整而自由改写忠实转录层",
        ):
            self.assertIn(required_phrase, copy)

    def test_required_stage_zero_artifacts_exist(self) -> None:
        paths = [
            ROOT / "README.md",
            ROOT / "docs" / "product-requirements.md",
            ROOT / "docs" / "glossary.md",
            ROOT / "docs" / "requirements-traceability.md",
            ROOT / "docs" / "acceptance-checklist.md",
            ROOT / "docs" / "ui-copy-guidelines.md",
            ROOT / "docs" / "adr" / "README.md",
            ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
        ]
        self.assertEqual([str(path) for path in paths if not path.is_file()], [])


if __name__ == "__main__":
    unittest.main()
