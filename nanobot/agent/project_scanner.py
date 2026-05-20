"""Project scanner: generates an explicit project_card.md from the actual filesystem.

The scanner reads the real project directory (not docs, not training data) and
produces a structured summary that the LLM MUST read before making changes.
This replaces the LLM's tendency to rely on outdated training-data "knowledge"
with explicit, current, filesystem-derived facts about the project.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProjectInfo:
    name: str = ""
    root: str = ""
    languages: list[dict[str, Any]] = field(default_factory=list)
    build_system: str = ""
    test_framework: str = ""
    linter: str = ""
    formatter: str = ""
    ci_cd: list[str] = field(default_factory=list)
    project_type: str = ""  # app, library, service, tool, etc.
    deps_total: int = 0
    structure: list[str] = field(default_factory=list)
    config_files: list[dict[str, Any]] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    has_readme: bool = False
    has_claudemd: bool = False
    has_license: bool = False
    has_docs_dir: bool = False
    file_counts: dict[str, int] = field(default_factory=dict)
    loc_estimate: int = 0
    scan_time: str = ""


_LANG_DETECTORS: list[tuple[str, set[str], str]] = [
    ("Python", {"py", "pyi", "pyx"}, "Python"),
    ("TypeScript", {"ts", "tsx"}, "TypeScript"),
    ("JavaScript", {"js", "jsx", "mjs", "cjs"}, "JavaScript"),
    ("Rust", {"rs"}, "Rust"),
    ("Go", {"go"}, "Go"),
    ("Java", {"java"}, "Java"),
    ("Kotlin", {"kt", "kts"}, "Kotlin"),
    ("Swift", {"swift"}, "Swift"),
    ("Ruby", {"rb"}, "Ruby"),
    ("PHP", {"php"}, "PHP"),
    ("C/C++", {"c", "h", "cpp", "hpp", "cc", "cxx"}, "C/C++"),
    ("C#", {"cs"}, "C#"),
    ("Zig", {"zig"}, "Zig"),
    ("Dart", {"dart"}, "Dart"),
    ("Lua", {"lua"}, "Lua"),
    ("Shell", {"sh", "bash", "zsh"}, "Shell"),
    ("SQL", {"sql"}, "SQL"),
    ("R", {"r", "R"}, "R"),
]

_BUILD_DETECTORS: list[tuple[str, str, str | None]] = [
    ("Pixi", "pyproject.toml", None),
    ("Poetry", "pyproject.toml", None),
    ("Pip", "requirements.txt", "text"),
    ("Setuptools", "setup.py", "text"),
    ("npm", "package.json", "json"),
    ("yarn", "yarn.lock", "text"),
    ("pnpm", "pnpm-lock.yaml", "text"),
    ("Cargo", "Cargo.toml", "toml"),
    ("Go Modules", "go.mod", "text"),
    ("Bun", "bun.lockb", None),
    ("Maven", "pom.xml", "text"),
    ("Gradle", "build.gradle", "text"),
    ("Gradle Kotlin", "build.gradle.kts", "text"),
    ("CMake", "CMakeLists.txt", "text"),
    ("Make", "Makefile", "text"),
    ("Pipenv", "Pipfile", "text"),
]

_TEST_DETECTORS: list[tuple[str, str | list[str], str | None]] = [
    ("pytest", "conftest.py", None),
    ("pytest", ["pytest.ini", "pyproject.toml", "setup.cfg"], None),
    ("Jest", "jest.config.*", None),
    ("Vitest", "vitest.config.*", None),
    ("Mocha", ".mocharc.*", None),
]

_LINTER_DETECTORS: list[tuple[str, str, str | None]] = [
    ("ruff", ".ruff.toml", None),
    ("ruff", "ruff.toml", None),
    ("flake8", ".flake8", None),
    ("pylint", ".pylintrc", None),
    ("eslint", ".eslintrc.*", None),
    ("prettier", ".prettierrc*", None),
    ("biome", "biome.json", None),
    ("clippy", "clippy.toml", None),
    ("golangci-lint", ".golangci.yml", None),
]

_CI_DETECTORS: list[str] = [
    ".github/workflows",
    ".gitlab-ci.yml",
    ".circleci",
    "Jenkinsfile",
    ".travis.yml",
]

CONFIG_KEYS: dict[str, str] = {
    ".editorconfig": "text",
    ".gitignore": "text",
    "Dockerfile": "text",
    "docker-compose.yml": "text",
    "Makefile": "text",
    "tsconfig.json": "json",
    ".env.example": "text",
    "CONTRIBUTING.md": "text",
    "CHANGELOG.md": "text",
    "CLAUDE.md": "text",
    ".pre-commit-config.yaml": "text",
}

MAX_STRUCTURE_DEPTH = 3
MAX_STRUCTURE_ENTRIES = 120
MAX_CONFIG_VALUE_LINES = 40
SCAN_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".DS_Store", "target", "build", "dist", ".next", ".turbo",
    "coverage", ".idea", ".vscode", "*.egg-info",
    ".claude", ".nanobot",
}


def _parse_toml_key(text: str, key_path: str) -> str | None:
    """Naive TOML key parser."""
    parts = key_path.split(".")
    in_table = ""
    current_idx = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            in_table = line.strip("[]").strip()
            current_idx = 0
            continue
        if not line or line.startswith("#"):
            continue
        for i, part in enumerate(parts):
            if i != current_idx:
                continue
            if line.startswith(part + " =") or line.startswith(part + "="):
                current_idx = i + 1
                if current_idx == len(parts):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    return val
    return None


def _detect_python_build(pyproject: Path | None, req: Path | None, setup: Path | None) -> str:
    if pyproject and pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[tool.poetry]" in text:
            return "Poetry"
        if "[project]" in text:
            return "pip (pyproject.toml)"
    if req and req.exists():
        return "pip (requirements.txt)"
    if setup and setup.exists():
        return "Setuptools"
    return ""


def _detect_test_framework(project_root: Path, langs: set[str]) -> str:
    root = Path(project_root)
    if root.joinpath("conftest.py").exists():
        return "pytest"
    for cfg in ("pytest.ini", "tox.ini", "setup.cfg"):
        p = root.joinpath(cfg)
        if p.exists() and "pytest" in p.read_text(encoding="utf-8", errors="replace"):
            return "pytest"
    pyproject = root.joinpath("pyproject.toml")
    if pyproject.exists() and "[tool.pytest" in pyproject.read_text(encoding="utf-8", errors="replace"):
        return "pytest"
    for pattern in ("jest.config.*", "vitest.config.*"):
        for p in root.glob(pattern):
            if p.exists():
                return "vitest" if "vitest" in p.name.lower() else "jest"
    if "Go" in langs:
        go_files = list(root.rglob("*.go"))
        test_files = [f for f in go_files if f.name.endswith("_test.go")]
        if test_files:
            return "go test"
    if "Rust" in langs and root.joinpath("Cargo.toml").exists():
        return "cargo test"
    if "Python" in langs:
        for td in (root / "tests", root / "test"):
            if td.is_dir() and any(td.rglob("test_*.py")):
                return "unittest"
    if root.joinpath("tests").is_dir():
        return "unknown (tests/ directory detected)"
    return ""


def _detect_linter(project_root: Path) -> str:
    root = Path(project_root)
    for name, filename in [
        ("ruff", ".ruff.toml"), ("ruff", "ruff.toml"),
        ("flake8", ".flake8"), ("pylint", ".pylintrc"),
        ("biome", "biome.json"),
    ]:
        if root.joinpath(filename).exists():
            return name
    pyproj = root.joinpath("pyproject.toml")
    if pyproj.exists() and "[tool.ruff]" in pyproj.read_text(encoding="utf-8", errors="replace"):
        return "ruff"
    for pat in (".eslintrc.*", ".eslintrc"):
        for p in root.glob(pat):
            if p.exists():
                return "eslint"
    for pat in (".prettierrc*",):
        for p in root.glob(pat):
            if p.exists():
                return "prettier"
    return ""


def _detect_ci_cd(project_root: Path) -> list[str]:
    found: list[str] = []
    root = Path(project_root)
    wf_dir = root / ".github" / "workflows"
    if wf_dir.is_dir():
        names = []
        for wf in wf_dir.iterdir():
            if wf.suffix in (".yml", ".yaml"):
                m = re.search(r'^name:\s*(.+)$', wf.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
                if m:
                    names.append(m.group(1).strip())
        label = "GitHub Actions"
        if names:
            label += f" ({', '.join(names[:3])})"
        found.append(label)
    for ci in _CI_DETECTORS:
        if ci.startswith("."):
            continue
        if root.joinpath(ci).exists():
            found.append(ci)
    return found


def _count_files(root: Path) -> dict[str, int]:
    counter: Counter = Counter()
    for f in root.rglob("*"):
        if f.is_file() and not any(part.startswith(".") or part in SCAN_EXCLUDE_DIRS for part in f.parts):
            ext = f.suffix.lstrip(".") or "(no ext)"
            counter[ext] += 1
    return dict(counter.most_common(20))


def _estimate_loc(root: Path, exts: set[str]) -> int:
    total = 0
    count = 0
    for f in root.rglob("*"):
        if count > 5000:
            break
        if f.is_file() and f.suffix.lstrip(".") in exts:
            if any(part.startswith(".") or part in SCAN_EXCLUDE_DIRS for part in f.parts):
                continue
            try:
                total += len(f.read_bytes().splitlines())
                count += 1
            except Exception:
                pass
    return total


def _build_structure_tree(root: Path, depth: int = MAX_STRUCTURE_DEPTH) -> list[str]:
    lines: list[str] = []
    lines.append(f"{root}/")

    def _walk(dir_path: Path, prefix: str = "", current_depth: int = 0):
        if current_depth > depth or len(lines) > MAX_STRUCTURE_ENTRIES:
            return
        entries: list[Path] = []
        try:
            for p in dir_path.iterdir():
                name = p.name
                if name.startswith(".") or name in SCAN_EXCLUDE_DIRS:
                    continue
                entries.append(p)
        except PermissionError:
            return
        entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
        for i, entry in enumerate(entries):
            if i >= 50:
                lines.append(f"{prefix}  ... ({len(entries) - i} more)")
                break
            if entry.is_dir():
                lines.append(f"{prefix}  {entry.name}/")
                _walk(entry, prefix + "  ", current_depth + 1)
            else:
                try:
                    size = entry.stat().st_size
                    size_str = f" ({size}B)" if size < 1024 else f" ({size / 1024:.0f}KB)"
                except OSError:
                    size_str = ""
                lines.append(f"{prefix}  {entry.name}{size_str}")

    _walk(root)
    return lines


def _read_config_snippet(path: Path) -> str | None:
    try:
        if path.stat().st_size > 50_000:
            return "[file too large]"
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > MAX_CONFIG_VALUE_LINES:
            return "\n".join(lines[:MAX_CONFIG_VALUE_LINES]) + f"\n... ({len(lines) - MAX_CONFIG_VALUE_LINES} more lines)"
        return text if text.strip() else None
    except Exception:
        return None


def scan_project(project_root: str | Path, use_real_paths: bool = True) -> ProjectInfo:
    """Scan a project directory and return structured ProjectInfo."""
    root = Path(project_root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"Project root not found: {root}")

    info = ProjectInfo()
    info.root = str(root) if use_real_paths else str(root)
    info.name = root.name

    # Language detection
    file_counts = _count_files(root)
    info.file_counts = file_counts
    source_exts: set[str] = set()
    lang_details: list[dict[str, Any]] = []
    for lang_name, exts, _ in _LANG_DETECTORS:
        count = sum(file_counts.get(ext, 0) for ext in exts)
        if count >= 5:
            source_exts.update(exts)
            loc = _estimate_loc(root, exts)
            lang_details.append({"name": lang_name, "files": count, "loc_approx": loc})
            info.loc_estimate += loc

    lang_details.sort(key=lambda x: -x["files"])
    info.languages = lang_details

    # Build system
    pyproject = root / "pyproject.toml"
    req = root / "requirements.txt"
    setup = root / "setup.py"
    package = root / "package.json"
    cargo = root / "Cargo.toml"
    gomod = root / "go.mod"

    detected_builds: list[str] = []
    if pyproject.exists():
        bs = _detect_python_build(pyproject, req, setup)
        if bs:
            detected_builds.append(bs)
    elif req.exists():
        detected_builds.append("pip (requirements.txt)")
    elif setup.exists():
        detected_builds.append("Setuptools")

    if package.exists():
        try:
            json.loads(package.read_text(encoding="utf-8"))
            if root.joinpath("pnpm-lock.yaml").exists():
                detected_builds.append("pnpm")
            elif root.joinpath("yarn.lock").exists():
                detected_builds.append("yarn")
            elif root.joinpath("package-lock.json").exists():
                detected_builds.append("npm")
            else:
                detected_builds.append("npm (package.json)")
        except json.JSONDecodeError:
            detected_builds.append("npm (package.json)")

    if cargo.exists():
        detected_builds.append("Cargo")
    if gomod.exists():
        detected_builds.append("Go Modules")
    if root.joinpath("CMakeLists.txt").exists():
        detected_builds.append("CMake")
    if root.joinpath("Makefile").exists():
        detected_builds.append("Make")

    info.build_system = " + ".join(detected_builds) if detected_builds else ""

    # Project type
    primary_lang = lang_details[0]["name"] if lang_details else "Unknown"
    has_entry = any(root.glob("main.*")) or any(root.glob("__main__.*"))
    if has_entry and (root.joinpath("setup.py").exists() or pyproject.exists()):
        info.project_type = f"{primary_lang} CLI Application"
    elif pyproject.exists() or setup.exists():
        info.project_type = f"{primary_lang} Library"
    elif root.joinpath("src").is_dir():
        info.project_type = f"{primary_lang} Application"
    else:
        info.project_type = f"{primary_lang} Project"

    # Test framework
    lang_set = {ld["name"] for ld in lang_details}
    info.test_framework = _detect_test_framework(root, lang_set)

    # Linter
    info.linter = _detect_linter(root)

    # CI/CD
    info.ci_cd = _detect_ci_cd(root)

    # Dependencies count
    if package.exists():
        try:
            pkg = json.loads(package.read_text(encoding="utf-8"))
            info.deps_total = len(pkg.get("dependencies", {})) + len(pkg.get("devDependencies", {}))
        except json.JSONDecodeError:
            pass
    elif pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            # Count lines that look like deps under [tool.poetry.dependencies] or [project.dependencies]
            m = re.search(r'\[(?:tool\.poetry\.|project\.)dependencies\](.*?)(?:\[|$)', text, re.DOTALL)
            if m:
                info.deps_total = len([l for l in m.group(1).splitlines() if l.strip() and not l.strip().startswith("#") and "=" in l])
        except Exception:
            pass

    # Entry points
    if package.exists():
        try:
            pkg = json.loads(package.read_text(encoding="utf-8"))
            bin_entry = pkg.get("bin", {})
            if bin_entry:
                info.entry_points = list(bin_entry.keys()) if isinstance(bin_entry, dict) else [str(bin_entry)]
        except json.JSONDecodeError:
            pass
    for pattern in ("cli/*.py", "__main__.py", "main.py"):
        for f in root.glob(pattern):
            if f.exists() and re.search(r'(typer\.run|app\(\)|click\.)', f.read_text(encoding="utf-8", errors="replace")):
                info.entry_points.append(f.name)
                break

    # Config files
    configs: list[dict[str, Any]] = []
    for pattern, read_as in CONFIG_KEYS.items():
        for f in root.glob(pattern):
            snippet = _read_config_snippet(f) if read_as else None
            configs.append({"path": str(f.relative_to(root)), "type": read_as or "binary", "content": snippet})
    for bc in (pyproject, package, cargo, gomod):
        if bc and bc.exists() and not any(c["path"] == bc.name for c in configs):
            snippet = _read_config_snippet(bc)
            if snippet:
                configs.append({"path": bc.name, "type": "text", "content": snippet})
    info.config_files = configs

    # Directory structure
    info.structure = _build_structure_tree(root)

    # Flags
    info.has_readme = root.joinpath("README.md").exists() or root.joinpath("README.rst").exists()
    info.has_claudemd = root.joinpath("CLAUDE.md").exists()
    info.has_license = any(root.glob("LICENSE*"))
    info.has_docs_dir = root.joinpath("docs").is_dir()
    info.scan_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    return info


def format_project_card(info: ProjectInfo, include_config_content: bool = True) -> str:
    """Format ProjectInfo as a markdown project_card."""
    lines: list[str] = []
    lines.append(f"# Project Card: {info.name}")
    lines.append("")
    lines.append(f"Last scanned: {info.scan_time}")
    lines.append(f"Project root: `{info.root}`")
    lines.append("")

    # Overview
    lines.append("## Overview")
    lines.append("")
    if info.languages:
        primary = info.languages[0]
        others = [lang["name"] for lang in info.languages[1:3]]
        label = primary["name"]
        if others:
            label += f" (primary), {', '.join(others)}"
        lines.append(f"- **Languages**: {label}")
    if info.build_system:
        lines.append(f"- **Build System**: {info.build_system}")
    if info.test_framework:
        lines.append(f"- **Test Framework**: {info.test_framework}")
    if info.linter:
        lines.append(f"- **Linter**: {info.linter}")
    if info.ci_cd:
        lines.append(f"- **CI/CD**: {' | '.join(info.ci_cd)}")
    lines.append(f"- **Type**: {info.project_type}")
    if info.deps_total > 0:
        lines.append(f"- **Dependencies**: ~{info.deps_total}")
    if info.loc_estimate > 0:
        lines.append(f"- **Approx LOC**: ~{info.loc_estimate:,}")
    lines.append("")

    # Language breakdown
    if info.languages:
        lines.append("### Language Breakdown")
        lines.append("")
        lines.append("| Language | Files | Approx LOC |")
        lines.append("|----------|-------|------------|")
        for lang in info.languages:
            lines.append(f"| {lang['name']} | {lang['files']} | ~{lang['loc_approx']:,} |")
        lines.append("")

    # Directory structure
    lines.append("## Directory Structure")
    lines.append("")
    lines.extend(info.structure)
    lines.append("")

    # Config files
    lines.append("## Key Configuration")
    lines.append("")
    if info.config_files:
        build_section = [c for c in info.config_files if c["path"] in (
            "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
            "requirements.txt", "setup.py",
        )]
        other_configs = [c for c in info.config_files if c not in build_section]

        for label, cfgs in [("Build Config", build_section), ("Other Config", other_configs)]:
            shown = [c for c in cfgs if c.get("content") and include_config_content]
            listed = [c for c in cfgs if not c.get("content") or not include_config_content]
            if shown:
                for cfg in shown:
                    lines.append(f"### {cfg['path']}")
                    lines.append("")
                    lang = cfg["type"] if cfg["type"] in ("json", "toml", "yaml", "text") else ""
                    lines.append(f"```{lang}")
                    lines.append(cfg["content"])
                    lines.append("```")
                    lines.append("")
            if listed:
                for cfg in listed:
                    lines.append(f"- {cfg['path']} {'(binary/large)' if not cfg.get('content') else ''}")

    # Assets
    assets = []
    if info.has_readme:
        assets.append("README.md")
    if info.has_claudemd:
        assets.append("CLAUDE.md")
    if info.has_license:
        assets.append("LICENSE")
    if info.has_docs_dir:
        assets.append("docs/")
    if assets:
        lines.append(f"**Project assets**: {', '.join(assets)}")
        lines.append("")

    lines.append("---")
    lines.append("*This project card is generated from the actual filesystem. "
                 "If it differs from documentation or your training data, "
                 "trust the code — not the docs, not your memory.*")

    return "\n".join(lines)


def write_project_card(project_root: str | Path, output_path: str | Path | None = None) -> Path:
    """Scan a project and write project_card.md."""
    info = scan_project(project_root)
    card = format_project_card(info)
    if output_path is None:
        output_path = Path(project_root) / "project_card.md"
    else:
        output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(card, encoding="utf-8")
    return output_path
