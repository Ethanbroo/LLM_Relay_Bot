"""Tests for orchestration/prompt_registry.py.

Covers: PromptVersion construction and hash computation, idempotent
registration, re-registration rejection, get/get_latest/list_versions,
and hash stability across repeated instantiations.
"""

import pytest

from orchestration.prompt_registry import (
    PromptVersion, PromptRegistry, _compute_prompt_hash,
)
from orchestration.errors import PromptRegistryError


class TestPromptVersionConstruction:
    def test_hash_computed_at_construction(self):
        pv = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="hello")
        assert pv.prompt_hash != ""
        assert len(pv.prompt_hash) == 64

    def test_hash_is_deterministic(self):
        h1 = PromptVersion(prompt_id="p", version="1", template="t").prompt_hash
        h2 = PromptVersion(prompt_id="p", version="1", template="t").prompt_hash
        assert h1 == h2

    def test_different_template_different_hash(self):
        h1 = PromptVersion(prompt_id="p", version="1", template="foo").prompt_hash
        h2 = PromptVersion(prompt_id="p", version="1", template="bar").prompt_hash
        assert h1 != h2

    def test_different_version_different_hash(self):
        h1 = PromptVersion(prompt_id="p", version="1.0.0", template="t").prompt_hash
        h2 = PromptVersion(prompt_id="p", version="2.0.0", template="t").prompt_hash
        assert h1 != h2

    def test_different_id_different_hash(self):
        h1 = PromptVersion(prompt_id="a", version="1.0.0", template="t").prompt_hash
        h2 = PromptVersion(prompt_id="b", version="1.0.0", template="t").prompt_hash
        assert h1 != h2

    def test_explicit_hash_arg_is_overwritten(self):
        """Passing prompt_hash="wrong" must be silently replaced by computed hash."""
        pv = PromptVersion(
            prompt_id="p", version="1", template="t", prompt_hash="wrong_hash"
        )
        expected = _compute_prompt_hash("p", "1", "t")
        assert pv.prompt_hash == expected

    def test_to_dict_has_sorted_keys(self):
        pv = PromptVersion(prompt_id="p", version="1", template="t")
        d = pv.to_dict()
        keys = list(d.keys())
        assert keys == sorted(keys)

    def test_to_dict_contains_expected_fields(self):
        pv = PromptVersion(prompt_id="p", version="1.0.0", template="hello")
        d = pv.to_dict()
        assert d["prompt_id"] == "p"
        assert d["version"] == "1.0.0"
        assert d["template"] == "hello"
        assert len(d["prompt_hash"]) == 64


class TestPromptRegistryRegister:
    def test_register_and_retrieve(self):
        reg = PromptRegistry()
        pv = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="generate code")
        reg.register(pv)
        retrieved = reg.get("dev.gen", "1.0.0")
        assert retrieved is pv

    def test_idempotent_registration_same_hash(self):
        reg = PromptRegistry()
        pv = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="generate code")
        reg.register(pv)
        reg.register(pv)  # must not raise
        assert reg.get("dev.gen", "1.0.0") is pv

    def test_reregistration_different_template_raises(self):
        reg = PromptRegistry()
        pv1 = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="original")
        pv2 = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="changed")
        reg.register(pv1)
        with pytest.raises(PromptRegistryError, match="different template"):
            reg.register(pv2)

    def test_get_nonexistent_raises(self):
        reg = PromptRegistry()
        with pytest.raises(PromptRegistryError, match="not found"):
            reg.get("nonexistent", "1.0.0")

    def test_register_multiple_versions(self):
        reg = PromptRegistry()
        pv1 = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="v1")
        pv2 = PromptVersion(prompt_id="dev.gen", version="2.0.0", template="v2")
        reg.register(pv1)
        reg.register(pv2)
        assert reg.get("dev.gen", "1.0.0") is pv1
        assert reg.get("dev.gen", "2.0.0") is pv2


class TestPromptRegistryGetLatest:
    def test_get_latest_single_version(self):
        reg = PromptRegistry()
        pv = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="v1")
        reg.register(pv)
        assert reg.get_latest("dev.gen") is pv

    def test_get_latest_multiple_versions(self):
        reg = PromptRegistry()
        pv1 = PromptVersion(prompt_id="dev.gen", version="1.0.0", template="v1")
        pv2 = PromptVersion(prompt_id="dev.gen", version="2.0.0", template="v2")
        reg.register(pv1)
        reg.register(pv2)
        # "2.0.0" > "1.0.0" lexicographically and semantically
        latest = reg.get_latest("dev.gen")
        assert latest is pv2

    def test_get_latest_nonexistent_raises(self):
        reg = PromptRegistry()
        with pytest.raises(PromptRegistryError, match="No versions registered"):
            reg.get_latest("nonexistent")


class TestPromptRegistryListVersions:
    def test_list_versions_empty(self):
        reg = PromptRegistry()
        assert reg.list_versions("dev.gen") == []

    def test_list_versions_returns_sorted(self):
        reg = PromptRegistry()
        for v in ["2.0.0", "1.0.0", "3.0.0"]:
            reg.register(PromptVersion(prompt_id="dev.gen", version=v, template=f"t{v}"))
        versions = reg.list_versions("dev.gen")
        assert versions == sorted(versions)

    def test_list_versions_only_for_requested_id(self):
        reg = PromptRegistry()
        reg.register(PromptVersion(prompt_id="a", version="1.0.0", template="ta"))
        reg.register(PromptVersion(prompt_id="b", version="1.0.0", template="tb"))
        assert reg.list_versions("a") == ["1.0.0"]
        assert reg.list_versions("b") == ["1.0.0"]


class TestComputePromptHash:
    def test_compute_is_stable(self):
        h1 = _compute_prompt_hash("p", "1.0.0", "template text")
        h2 = _compute_prompt_hash("p", "1.0.0", "template text")
        assert h1 == h2

    def test_compute_returns_64_char_hex(self):
        h = _compute_prompt_hash("p", "1.0.0", "t")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
