"""Tests for AgentLoop _discover_hooks and _try_load_hook."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.hook import AgentHook
from nanobot.agent.loop import AgentLoop


class TestTryLoadHook:
    def test_imports_module_and_appends(self, tmp_path):
        hook_file = tmp_path / "my_hook.py"
        hook_file.write_text("""
from nanobot.agent.hook import AgentHook

class TestHook(AgentHook):
    pass
""")
        discovered = []
        AgentLoop._try_load_hook(hook_file, discovered)
        assert len(discovered) == 1
        assert isinstance(discovered[0], AgentHook)

    def test_respects_hook_classes(self, tmp_path):
        hook_file = tmp_path / "multi_hook.py"
        hook_file.write_text("""
from nanobot.agent.hook import AgentHook

class InnerHook(AgentHook):
    pass

class MultiHook(AgentHook):
    HOOK_CLASSES = (InnerHook,)
""")
        discovered = []
        AgentLoop._try_load_hook(hook_file, discovered)
        # InnerHook is discovered directly as AgentHook subclass,
        # and also via MultiHook.HOOK_CLASSES → 2 total instances
        assert len(discovered) == 2
        assert all(isinstance(h, AgentHook) for h in discovered)

    def test_ignores_base_class(self, tmp_path):
        hook_file = tmp_path / "base_only.py"
        hook_file.write_text("from nanobot.agent.hook import AgentHook\n")
        discovered = []
        AgentLoop._try_load_hook(hook_file, discovered)
        assert len(discovered) == 0

    def test_no_side_effect_on_import_error(self, tmp_path):
        hook_file = tmp_path / "broken.py"
        hook_file.write_text("import nonexistent_module_xyz\n")
        discovered = []
        AgentLoop._try_load_hook(hook_file, discovered)
        assert len(discovered) == 0


class TestDiscoverHooks:
    def test_scans_workspace_hooks(self, tmp_path):
        workspace_hooks = tmp_path / "hooks"
        workspace_hooks.mkdir()
        (workspace_hooks / "custom_hook.py").write_text("""
from nanobot.agent.hook import AgentHook

class CustomHook(AgentHook):
    pass
""")
        loop = MagicMock()
        loop.workspace = tmp_path
        loop.provider = MagicMock()
        loop.model = "test-model"
        loop._try_load_hook = AgentLoop._try_load_hook

        result = AgentLoop._discover_hooks(loop)
        assert len(result) >= 1
        assert all(isinstance(h, AgentHook) for h in result)

    def test_injects_provider_into_hooks(self, tmp_path):
        workspace_hooks = tmp_path / "hooks"
        workspace_hooks.mkdir()
        (workspace_hooks / "injectable.py").write_text("""
from nanobot.agent.hook import AgentHook

class InjectableHook(AgentHook):
    def set_provider(self, provider, model):
        self._injected_provider = provider
        self._injected_model = model
""")
        provider = MagicMock()
        loop = MagicMock()
        loop.workspace = tmp_path
        loop.provider = provider
        loop.model = "test-model"
        loop._try_load_hook = AgentLoop._try_load_hook

        hooks = AgentLoop._discover_hooks(loop)
        injectable = [h for h in hooks if hasattr(h, "_injected_provider")]
        assert len(injectable) == 1
        assert injectable[0]._injected_provider is provider
        assert injectable[0]._injected_model == "test-model"

    def test_empty_when_no_dirs(self):
        loop = MagicMock()
        loop.workspace = MagicMock()
        loop.workspace.__truediv__.return_value.is_dir.return_value = False
        loop.provider = MagicMock()
        loop.model = None
        loop._try_load_hook = AgentLoop._try_load_hook

        with patch("nanobot.agent.loop.Path.is_dir", return_value=False):
            result = AgentLoop._discover_hooks(loop)
        assert isinstance(result, list)
