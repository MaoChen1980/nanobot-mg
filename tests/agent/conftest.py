"""Shared test fixtures — stub out nanobot package to avoid Python 3.9 slots=True issue.

Covers: nanobot/__init__.py which uses @dataclass(slots=True) (Python 3.10+).
This conftest uses namespace-package approach: __path__ trick on the nanobot stub
so Python finds nanobot sub-packages in the filesystem without loading __init__.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_NANOBOT_ROOT = Path(__file__).resolve().parents[2]

# Create a namespace-package stub for nanobot.
# Setting __path__ makes it a namespace package: Python will search that directory
# for subpackages WITHOUT loading nanobot/__init__.py.
# But we need to avoid loading nanobot/__init__.py (slots=True fails on Python 3.9).
# Solution: DON'T create nanobot/__init__.py in sys.modules at all.
# Python will use the namespace package from _nanobot.__path__.
_nanobot = ModuleType("nanobot")
_nanobot.__path__ = [str(_NANOBOT_ROOT / "nanobot")]
_nanobot.__package__ = "nanobot"
_nanobot.__file__ = None  # Mark as namespace package (no __init__)
sys.modules["nanobot"] = _nanobot

# We don't stub nanobot.agent or nanobot.security as packages.
# Since nanobot is a namespace package with __path__, Python will find
# nanobot/agent and nanobot/security by searching __path__.
# However, we need to make sure the real modules are in sys.modules BEFORE
# any imports, so let's pre-load the ones we need.

def _load_file_module(name: str, file_path: Path) -> ModuleType:
    """Load a module directly from a file path, without triggering __init__.py."""
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Pre-load all modules that test files will need.
# These are loaded into sys.modules directly, bypassing __init__.py.
# Also register intermediate packages so attribute access works.
_network_mod = _load_file_module(
    "nanobot.security.network",
    _NANOBOT_ROOT / "nanobot" / "security" / "network.py",
)
# nanobot.security must be a package with 'network' as its submodule
_nanobot_security_pkg = ModuleType("nanobot.security")
_nanobot_security_pkg.__path__ = [str(_NANOBOT_ROOT / "nanobot" / "security")]
_nanobot_security_pkg.__package__ = "nanobot.security"
_nanobot_security_pkg.__file__ = None  # namespace package
_nanobot_security_pkg.network = _network_mod
sys.modules["nanobot.security"] = _nanobot_security_pkg

_danger_mod = _load_file_module(
    "nanobot.agent.tools.danger",
    _NANOBOT_ROOT / "nanobot" / "agent" / "tools" / "danger.py",
)
_shell_validators_mod = _load_file_module(
    "nanobot.agent.tools.shell_validators",
    _NANOBOT_ROOT / "nanobot" / "agent" / "tools" / "shell_validators.py",
)
# nanobot.agent.tools must be a package with 'danger' and 'shell_validators' as submodules
_nanobot_agent_tools_pkg = ModuleType("nanobot.agent.tools")
_nanobot_agent_tools_pkg.__path__ = [str(_NANOBOT_ROOT / "nanobot" / "agent" / "tools")]
_nanobot_agent_tools_pkg.__package__ = "nanobot.agent.tools"
_nanobot_agent_tools_pkg.__file__ = None
_nanobot_agent_tools_pkg.danger = _danger_mod
_nanobot_agent_tools_pkg.shell_validators = _shell_validators_mod
sys.modules["nanobot.agent.tools"] = _nanobot_agent_tools_pkg

# nanobot.agent must be a package with 'tools' as its submodule
_nanobot_agent_pkg = ModuleType("nanobot.agent")
_nanobot_agent_pkg.__path__ = [str(_NANOBOT_ROOT / "nanobot" / "agent")]
_nanobot_agent_pkg.__package__ = "nanobot.agent"
_nanobot_agent_pkg.__file__ = None  # namespace package
_nanobot_agent_pkg.tools = _nanobot_agent_tools_pkg
sys.modules["nanobot.agent"] = _nanobot_agent_pkg

# Point nanobot stub to nanobot.agent package
_nanobot.agent = _nanobot_agent_pkg
