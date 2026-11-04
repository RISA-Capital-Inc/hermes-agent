"""Model-keyed auxiliary routing (``auxiliary.<task>.model_map``).

The map exists so an auxiliary override written for one session model cannot follow a session onto
another: the compressor sizes its trigger from the auxiliary window, so a stale pin silently
compacts a long-context session early. See RISA-Capital-Inc/risa-platform#241.
"""

import unittest
from unittest import mock

from agent.auxiliary_task_config import (
    apply_aux_model_map,
    aux_model_map_context_length,
    aux_model_map_entry,
)

AUX_BASE = {
    "provider": "auto",
    "model": "",
    "base_url": "",
    "timeout": 240,
    "reasoning_effort": "none",
    "model_map": {
        "risa-27b": {
            "provider": "custom",
            "model": "risa-local-aux",
            "base_url": "https://risa-inference.tail98021e.ts.net/aux",
            "context_length": 196608,
            "timeout": 900,
        },
    },
}


class AuxModelMapEntryTests(unittest.TestCase):
    def test_entry_matches_session_model(self):
        entry = aux_model_map_entry("compression", "risa-27b", AUX_BASE)
        self.assertEqual(entry["model"], "risa-local-aux")

    def test_match_is_case_and_whitespace_insensitive(self):
        self.assertTrue(aux_model_map_entry("compression", "  RISA-27B ", AUX_BASE))

    def test_absent_model_has_no_entry(self):
        self.assertEqual(aux_model_map_entry("compression", "deepseek/deepseek-v4.1", AUX_BASE), {})

    def test_blank_model_has_no_entry(self):
        for main_model in ("", None, "   "):
            self.assertEqual(aux_model_map_entry("compression", main_model, AUX_BASE), {})

    def test_missing_or_malformed_map_is_not_fatal(self):
        self.assertEqual(aux_model_map_entry("compression", "risa-27b", {"provider": "auto"}), {})
        self.assertEqual(aux_model_map_entry("compression", "risa-27b", {"model_map": []}), {})
        self.assertEqual(
            aux_model_map_entry("compression", "risa-27b", {"model_map": {"risa-27b": "not-a-dict"}}), {}
        )

    def test_non_dict_entry_does_not_shadow_a_later_valid_one(self):
        cfg = {"model_map": {"risa-27b": "junk", "RISA-27B": {"model": "second"}}}
        self.assertEqual(aux_model_map_entry("compression", "risa-27b", cfg), {"model": "second"})


class ApplyAuxModelMapTests(unittest.TestCase):
    def test_matching_entry_layers_over_the_block(self):
        merged = apply_aux_model_map("compression", "risa-27b", AUX_BASE)
        self.assertEqual(merged["provider"], "custom")
        self.assertEqual(merged["model"], "risa-local-aux")
        self.assertEqual(merged["base_url"], "https://risa-inference.tail98021e.ts.net/aux")
        self.assertEqual(merged["timeout"], 900)

    def test_keys_the_entry_does_not_set_are_preserved(self):
        merged = apply_aux_model_map("compression", "risa-27b", AUX_BASE)
        self.assertEqual(merged["reasoning_effort"], "none")

    def test_other_models_keep_the_block_unchanged(self):
        merged = apply_aux_model_map("compression", "deepseek/deepseek-v4.1", AUX_BASE)
        self.assertEqual(merged["provider"], "auto")
        self.assertEqual(merged["model"], "")
        self.assertEqual(merged["base_url"], "")
        self.assertEqual(merged["timeout"], 240)

    def test_map_itself_is_not_forwarded_as_a_route_field(self):
        self.assertNotIn("model_map", apply_aux_model_map("compression", "risa-27b", AUX_BASE))

    def test_mutating_the_result_does_not_mutate_the_config(self):
        merged = apply_aux_model_map("compression", "risa-27b", AUX_BASE)
        merged["model"] = "tampered"
        self.assertEqual(AUX_BASE["model_map"]["risa-27b"]["model"], "risa-local-aux")

    def test_non_dict_config_is_empty_not_fatal(self):
        self.assertEqual(apply_aux_model_map("compression", "risa-27b", "nope"), {})


class AuxModelMapContextLengthTests(unittest.TestCase):
    def test_returns_the_declared_window(self):
        self.assertEqual(aux_model_map_context_length("compression", "risa-27b", AUX_BASE), 196608)

    def test_returns_none_for_other_models(self):
        self.assertIsNone(aux_model_map_context_length("compression", "risa-27b-2", AUX_BASE))

    def test_rejects_unusable_values(self):
        for raw in ("abc", None, 0, -5, ""):
            cfg = {"model_map": {"risa-27b": {"context_length": raw}}}
            self.assertIsNone(aux_model_map_context_length("compression", "risa-27b", cfg))

    def test_accepts_numeric_strings(self):
        cfg = {"model_map": {"risa-27b": {"context_length": "196608"}}}
        self.assertEqual(aux_model_map_context_length("compression", "risa-27b", cfg), 196608)


class ResolveTaskProviderModelTests(unittest.TestCase):
    """The map is applied where the route is actually resolved, not only when read directly."""

    def _resolve(self, main_model, task_config=AUX_BASE, runtime_model=None):
        from agent import auxiliary_client as ac

        config_patch = mock.patch.object(ac, "_get_auxiliary_task_config", return_value=task_config)
        model_patch = mock.patch.object(ac, "_read_main_model_for_aux", return_value=main_model)
        with config_patch, model_patch:
            provider, model, base_url, _api_key, _api_mode = ac._resolve_task_provider_model(
                "compression", main_model=runtime_model
            )
        return provider, model, base_url

    def test_pinned_model_gets_the_mapped_route(self):
        provider, model, base_url = self._resolve("risa-27b")
        self.assertEqual(provider, "custom")
        self.assertEqual(model, "risa-local-aux")
        self.assertEqual(base_url, "https://risa-inference.tail98021e.ts.net/aux")

    def test_another_model_gets_no_mapped_route(self):
        _provider, model, base_url = self._resolve("deepseek/deepseek-v4.1")
        self.assertNotEqual(model, "risa-local-aux")
        self.assertNotEqual(base_url, "https://risa-inference.tail98021e.ts.net/aux")

    def test_runtime_model_beats_the_configured_default(self):
        # A session switched to another model must not inherit the pin written for the default.
        _provider, model, base_url = self._resolve(
            "risa-27b", runtime_model="deepseek/deepseek-v4.1"
        )
        self.assertNotEqual(model, "risa-local-aux")
        self.assertNotEqual(base_url, "https://risa-inference.tail98021e.ts.net/aux")


if __name__ == "__main__":
    unittest.main()
