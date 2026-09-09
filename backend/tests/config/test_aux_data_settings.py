"""
Tests for AuxDataSettings' nested news/fundamentals/options env-var binding.

Regression coverage: NewsAuxSettings/FundamentalsAuxSettings/OptionsAuxSettings
are nested BaseSettings fields on AuxDataSettings. A nested BaseSettings field
does NOT inherit its parent's env_prefix or env_file — each nested class
resolves its own env vars independently, per its own model_config. Without an
explicit env_prefix/env_file on the nested classes themselves, they read with
env_prefix="" (looking for a bare ENABLED, not AUX_NEWS_ENABLED) and
env_file=None (not reading .env at all) — so AUX_NEWS_ENABLED=true in .env
had zero effect and every /api/aux-data/* endpoint 503'd regardless of .env
or how many times the backend restarted (found live 2026-09-09).

These tests construct the settings classes directly against a real
environment (via unittest.mock.patch.dict(os.environ)), the same way
pydantic-settings actually resolves them at process startup — unlike
test_manager.py/test_router.py, which patch is_enabled()/the manager
directly and never exercise the real env-var-to-settings path at all
(which is exactly why this bug went uncaught).
"""
import os
import unittest
from unittest.mock import patch

from backend.config.settings import (
    AuxDataSettings,
    FundamentalsAuxSettings,
    NewsAuxSettings,
    OptionsAuxSettings,
)


class TestAuxDataSettingsEnvBinding(unittest.TestCase):

    def test_news_enabled_defaults_false(self):
        """Isolated from both process env AND the real .env file (via
        _env_file=None) — this project's real .env has AUX_NEWS_ENABLED=true,
        which the .env-file source would otherwise correctly supply even
        with the process env var popped (env_file is read directly off
        disk, independent of os.environ, and .env values apply whether or
        not the corresponding process env var is set)."""
        self.assertFalse(NewsAuxSettings(_env_file=None).enabled)

    def test_news_enabled_reads_correct_env_var(self):
        with patch.dict(os.environ, {"AUX_NEWS_ENABLED": "true"}):
            self.assertTrue(NewsAuxSettings().enabled)

    def test_fundamentals_enabled_reads_correct_env_var(self):
        with patch.dict(os.environ, {"AUX_FUNDAMENTALS_ENABLED": "true"}):
            self.assertTrue(FundamentalsAuxSettings().enabled)

    def test_options_enabled_reads_correct_env_var(self):
        with patch.dict(os.environ, {"AUX_OPTIONS_ENABLED": "true"}):
            self.assertTrue(OptionsAuxSettings().enabled)

    def test_bare_enabled_without_prefix_is_NOT_read(self):
        """The exact shape of the original bug: a bare ENABLED (no AUX_NEWS_
        prefix) must NOT flip this on — confirms the fix binds to the
        correctly-prefixed var, not just any 'ENABLED' in the environment.
        Isolated from the real .env file (see test_news_enabled_defaults_false)
        so only the bare process env var is in play."""
        with patch.dict(os.environ, {"ENABLED": "true"}):
            self.assertFalse(NewsAuxSettings(_env_file=None).enabled)

    def test_news_reads_primary_provider_and_rate_limit(self):
        with patch.dict(os.environ, {
            "AUX_NEWS_PRIMARY_PROVIDER": "some_other_provider",
            "AUX_NEWS_RATE_LIMIT_PER_MINUTE": "42",
        }):
            cfg = NewsAuxSettings()
            self.assertEqual(cfg.primary_provider, "some_other_provider")
            self.assertEqual(cfg.rate_limit_per_minute, 42)

    def test_aux_data_settings_nested_access_reflects_env(self):
        """The actual access path the app uses: settings.aux_data.news.enabled."""
        with patch.dict(os.environ, {
            "AUX_NEWS_ENABLED": "true",
            "AUX_FUNDAMENTALS_ENABLED": "true",
            "AUX_OPTIONS_ENABLED": "true",
        }):
            cfg = AuxDataSettings()
            self.assertTrue(cfg.news.enabled)
            self.assertTrue(cfg.fundamentals.enabled)
            self.assertTrue(cfg.options.enabled)

    def test_module_singleton_matches_a_fresh_read_of_the_real_env_file(self):
        """The module-level `settings` singleton (loaded once at import time)
        must agree with a brand-new instance constructed right now — both
        read the same real .env file, so they should never disagree on
        enabled/disabled regardless of whatever that file's current values
        happen to be. Comparing against a fresh read (instead of a
        hardcoded True) avoids the test going stale every time .env's
        AUX_*_ENABLED values are toggled for local testing — found live
        2026-09-09: this test originally hardcoded True and went red the
        moment .env's aux flags were flipped back to false, even though
        the settings binding itself was working correctly."""
        from backend.config.settings import settings, AuxDataSettings
        fresh = AuxDataSettings()
        self.assertEqual(settings.aux_data.news.enabled, fresh.news.enabled)
        self.assertEqual(settings.aux_data.fundamentals.enabled, fresh.fundamentals.enabled)
        self.assertEqual(settings.aux_data.options.enabled, fresh.options.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
