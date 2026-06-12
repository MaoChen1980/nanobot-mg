"""Skills loader for agent capabilities."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import yaml
from loguru import logger

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Opening ---, YAML body (group 1), closing --- on its own line; supports CRLF.
_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)

_SELF_OPTIMIZATION_FOOTER = (
    "\n"
    "- **Self-optimization**: 此 Skill 可自我进化。\n"
    "  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug\n"
    "  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准\n"
)


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None, disabled_skills: set[str] | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.disabled_skills = disabled_skills or set()
        self._skill_cache: dict[str, tuple[float, str, str]] = {}
        # list_skills cache: invalidated when skills directory mtimes change
        self._list_cache: list[dict[str, str]] | None = None
        self._list_cache_sig: object = None

    def _skills_dir_signature(self) -> int:
        """Return a hash of skills directory state for cache invalidation."""
        sig = 0
        for root in (self.workspace_skills, self.builtin_skills):
            if root and root.exists():
                try:
                    sig ^= hash(root.stat().st_mtime)
                    for d in root.iterdir():
                        if d.is_dir() and (d / "SKILL.md").exists():
                            sig ^= hash(d.name)
                except OSError:
                    logger.debug("Failed to stat skills directory")
        return sig

    def _refresh_skills_list(self) -> list[dict[str, str]]:
        """Scan skills directories and build the raw (unfiltered) list."""
        skills = self._skill_entries_from_dir(self.workspace_skills, "workspace")
        workspace_names = {entry["name"] for entry in skills}
        if self.builtin_skills and self.builtin_skills.exists():
            skills.extend(
                self._skill_entries_from_dir(self.builtin_skills, "builtin", skip_names=workspace_names)
            )
        if self.disabled_skills:
            skills = [s for s in skills if s["name"] not in self.disabled_skills]
        return skills

    @staticmethod
    def _ensure_self_optimization_footer(skill_file: Path) -> None:
        """Append the self-optimization footer to SKILL.md if missing."""
        try:
            content = skill_file.read_text(encoding="utf-8")
            if "**Self-optimization**" in content:
                return  # Footer already present
            skill_file.write_text(
                content.rstrip("\n") + _SELF_OPTIMIZATION_FOOTER, encoding="utf-8"
            )
            logger.debug("SkillsLoader: added self-optimization footer to {}", skill_file)
        except OSError:
            logger.warning("SkillsLoader: failed to update {}", skill_file)

    def _skill_entries_from_dir(self, base: Path, source: str, *, skip_names: set[str] | None = None) -> list[dict[str, str]]:
        if not base.exists():
            return []
        entries: list[dict[str, str]] = []
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            if skip_names is not None and name in skip_names:
                continue
            self._ensure_self_optimization_footer(skill_file)
            entries.append({"name": name, "path": str(skill_file), "source": source})
        return entries

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        sig = self._skills_dir_signature()
        if self._list_cache is None or sig != self._list_cache_sig:
            self._list_cache = self._refresh_skills_list()
            self._list_cache_sig = sig
            logger.info(
                "Skills loaded: {} total (builtin={}, workspace={})",
                len(self._list_cache),
                sum(1 for s in self._list_cache if s.get("source") == "builtin"),
                sum(1 for s in self._list_cache if s.get("source") == "workspace"),
            )
            for s in self._list_cache:
                logger.info("  SKILL: {} ({})", s["name"], s.get("source", "?"))

        if filter_unavailable:
            return [skill for skill in self._list_cache if self._check_requirements(self._get_skill_meta(skill["name"]))]
        return self._list_cache

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name (cached by file mtime).

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        cached = self._skill_cache.get(name)
        if cached is not None:
            try:
                path = Path(cached[1])  # stored as str for pickling compatibility
                if path.exists() and path.stat().st_mtime == cached[0]:
                    return cached[2]
            except OSError:
                logger.debug("Failed to stat cached skill path")

        roots = [self.workspace_skills]
        if self.builtin_skills:
            roots.append(self.builtin_skills)
        for root in roots:
            path = root / name / "SKILL.md"
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                    content = path.read_text(encoding="utf-8")
                    content = content.replace("{baseDir}", path.parent.as_posix())
                    self._skill_cache[name] = (mtime, str(path), content)
                    return content
                except OSError as e:
                    logger.warning("Failed to read skill {}: {}", name, e)
        logger.warning(f"[SKILL] Not found: {name}")
        return None

    def format_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        loaded: list[str] = []
        parts: list[str] = []
        for name in skill_names:
            markdown = self.load_skill(name)
            if markdown:
                loaded.append(name)
                parts.append(
                    f"### Skill: {name}\n\n{self._strip_frontmatter(markdown)}\n\n--- End: {name} ---"
                )
        if loaded:
            logger.info("Skills injected into context: {}", loaded)
        return "\n\n".join(parts)

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Args:
            exclude: Set of skill names to omit from the summary.

        Returns:
            Markdown-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        lines: list[str] = []
        for entry in all_skills:
            skill_name = entry["name"]
            if exclude and skill_name in exclude:
                continue
            meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(meta)
            desc = self._get_skill_description(skill_name)
            if available:
                lines.append(f"- **{skill_name}** — {desc}  `{entry['path']}`")
            else:
                missing = self._get_missing_requirements(meta)
                suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
                lines.append(f"- **{skill_name}** — {desc}{suffix}  `{entry['path']}`")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"CLI: {command_name}" for command_name in required_bins if not shutil.which(command_name)]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    def _parse_nanobot_metadata(self, raw: object) -> dict:
        """Extract nanobot/openclaw metadata from a frontmatter field.

        ``raw`` may be a dict (already parsed by yaml.safe_load) or a JSON str.
        """
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("nanobot", data.get("openclaw", {}))
        return payload if isinstance(payload, dict) else {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(shutil.which(cmd) for cmd in required_bins) and all(
            os.environ.get(var) for var in required_env_vars
        )

    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        raw_meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(raw_meta.get("metadata"))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self.get_skill_metadata(entry["name"]) or {})
            and (
                self._parse_nanobot_metadata(meta.get("metadata")).get("always")
                or meta.get("always")
            )
        ]

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict):
            return None
        # yaml.safe_load returns native types (int, bool, list, etc.);
        # keep values as-is so downstream consumers get correct types.
        metadata: dict[str, object] = {}
        for key, value in parsed.items():
            metadata[str(key)] = value
        return metadata
