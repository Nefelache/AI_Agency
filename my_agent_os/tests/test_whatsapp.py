"""
Tests for WhatsApp channel policy, phone normalization, and text chunking.
These tests run without network access (all LLM / DB calls are mocked).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from my_agent_os.api_gateway.routes.whatsapp import (
    _build_allowlist,
    _check_dm_policy,
    _chunk_text,
    _normalize_phone,
)


# ── Phone normalization ────────────────────────────────────────────────────────

class TestNormalizePhone:
    def test_chinese_number_no_plus(self):
        assert _normalize_phone("8613800138000") == "+8613800138000"

    def test_chinese_number_with_plus(self):
        assert _normalize_phone("+8613800138000") == "+8613800138000"

    def test_us_10_digit(self):
        assert _normalize_phone("5551234567") == "+15551234567"

    def test_us_11_digit(self):
        assert _normalize_phone("15551234567") == "+15551234567"

    def test_leading_zero_stripped(self):
        # 0861... → strip leading 0 → 861..., length ≥10, not starting with 1 → +861...
        assert _normalize_phone("08613800138000") == "+8613800138000"

    def test_strips_non_digits(self):
        assert _normalize_phone("+86-138-0013-8000") == "+8613800138000"


# ── Allowlist building ────────────────────────────────────────────────────────

_OPEN_CFG = {"enabled": True, "dm_policy": "open", "allow_from": []}
_ALLOWLIST_CFG = {"enabled": True, "dm_policy": "allowlist", "allow_from": []}


class TestBuildAllowlist:
    def test_env_allow_from_used(self):
        with (
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as mock_settings,
            patch("my_agent_os.config.channel_policies.get_whatsapp_config", return_value=_ALLOWLIST_CFG),
        ):
            mock_settings.WHATSAPP_ALLOW_FROM = "+8613800138000"
            mock_settings.SELF_CHAT_ONLY = ""
            result = _build_allowlist()
        assert "+8613800138000" in result

    def test_self_chat_only_auto_appended(self):
        with (
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as mock_settings,
            patch("my_agent_os.config.channel_policies.get_whatsapp_config", return_value=_ALLOWLIST_CFG),
        ):
            mock_settings.WHATSAPP_ALLOW_FROM = ""
            mock_settings.SELF_CHAT_ONLY = "+8613800138000"
            result = _build_allowlist()
        assert "+8613800138000" in result

    def test_self_chat_not_duplicated(self):
        with (
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as mock_settings,
            patch("my_agent_os.config.channel_policies.get_whatsapp_config", return_value=_ALLOWLIST_CFG),
        ):
            mock_settings.WHATSAPP_ALLOW_FROM = "+8613800138000"
            mock_settings.SELF_CHAT_ONLY = "+8613800138000"
            result = _build_allowlist()
        assert result.count("+8613800138000") == 1


# ── DM Policy ─────────────────────────────────────────────────────────────────

class TestCheckDmPolicy:
    def _patch(self, cfg):
        return patch("my_agent_os.config.channel_policies.get_whatsapp_config", return_value=cfg)

    def _patch_settings(self, allow="", self_chat=""):
        mock = patch("my_agent_os.api_gateway.routes.whatsapp.settings")
        return mock

    def test_open_policy_allows_all(self):
        """open policy must allow regardless of allow_from or env."""
        with (
            self._patch(_OPEN_CFG),
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as ms,
        ):
            ms.WHATSAPP_ALLOW_FROM = "+8613800138000"
            ms.SELF_CHAT_ONLY = ""
            allowed, err = _check_dm_policy("+9999999999", False)
        assert allowed is True
        assert err is None

    def test_open_policy_ignores_allow_from(self):
        """Bug fix: open policy with WHATSAPP_ALLOW_FROM set should still be open."""
        with (
            self._patch(_OPEN_CFG),
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as ms,
        ):
            ms.WHATSAPP_ALLOW_FROM = "+8613800138000"
            ms.SELF_CHAT_ONLY = ""
            allowed, _ = _check_dm_policy("+0000000000", False)
        assert allowed is True

    def test_allowlist_allows_matching_number(self):
        with (
            self._patch(_ALLOWLIST_CFG),
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as ms,
            patch("my_agent_os.config.channel_policies.is_paired", return_value=False),
        ):
            ms.WHATSAPP_ALLOW_FROM = "+8613800138000"
            ms.SELF_CHAT_ONLY = ""
            allowed, err = _check_dm_policy("8613800138000", False)
        assert allowed is True

    def test_allowlist_denies_unlisted(self):
        with (
            self._patch(_ALLOWLIST_CFG),
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as ms,
            patch("my_agent_os.config.channel_policies.is_paired", return_value=False),
        ):
            ms.WHATSAPP_ALLOW_FROM = "+8613800138000"
            ms.SELF_CHAT_ONLY = ""
            allowed, err = _check_dm_policy("+1234567890", False)
        assert allowed is False

    def test_self_chat_only_auto_allowed(self):
        """SELF_CHAT_ONLY number must pass allowlist check without manual allow_from config."""
        with (
            self._patch(_ALLOWLIST_CFG),
            patch("my_agent_os.api_gateway.routes.whatsapp.settings") as ms,
            patch("my_agent_os.config.channel_policies.is_paired", return_value=False),
        ):
            ms.WHATSAPP_ALLOW_FROM = ""
            ms.SELF_CHAT_ONLY = "+8613800138000"
            allowed, err = _check_dm_policy("8613800138000", False)
        assert allowed is True

    def test_disabled_policy(self):
        cfg = {"enabled": True, "dm_policy": "disabled", "allow_from": []}
        with self._patch(cfg):
            allowed, err = _check_dm_policy("+8613800138000", False)
        assert allowed is False

    def test_channel_disabled(self):
        cfg = {"enabled": False, "dm_policy": "open", "allow_from": []}
        with self._patch(cfg):
            allowed, err = _check_dm_policy("+8613800138000", False)
        assert allowed is False


# ── Text chunking ─────────────────────────────────────────────────────────────

class TestChunkText:
    def test_short_text_no_split(self):
        assert _chunk_text("hello world", 4000) == ["hello world"]

    def test_empty_text(self):
        assert _chunk_text("", 4000) == []

    def test_long_text_splits_by_paragraph(self):
        text = "\n\n".join(["word " * 20] * 50)
        chunks = _chunk_text(text, 200)
        assert len(chunks) > 1
        assert all(len(c) <= 200 for c in chunks)

    def test_length_mode(self):
        text = "x" * 500
        chunks = _chunk_text(text, 100, mode="length")
        assert len(chunks) == 5
        assert all(len(c) == 100 for c in chunks)
