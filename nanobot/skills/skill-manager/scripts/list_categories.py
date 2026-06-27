#!/usr/bin/env python3
"""
List all skill categories currently in use.

Output: one category per line, sorted alphabetically.
Useful for the skill-manager to show the LLM what categories exist.
"""

import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


def _find_skill_dirs(roots: list[Path]) -> list[Path]:
    dirs: list[Path] = []
    for root in roots:
        if root.exists():
            for d in root.iterdir():
                if d.is_dir() and (d / "SKILL.md").exists():
                    dirs.append(d)
    return dirs


def _extract_category(skill_dir: Path) -> str | None:
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---", content, re.DOTALL)
    if not m:
        return None
    fm_text = m.group(1)

    if yaml is not None:
        try:
            parsed = yaml.safe_load(fm_text)
        except yaml.YAMLError as e:
            print(f"  [WARN] {skill_dir.name}: YAML parse error: {e}", file=sys.stderr)
            return None
        if isinstance(parsed, dict):
            cat = parsed.get("category")
            return str(cat) if cat else None

    # yaml not available — regex fallback
    cat_m = re.search(r"^category:\s*(.+?)(?:\s*#.*)?$", fm_text, re.MULTILINE)
    if cat_m:
        return cat_m.group(1).strip().strip('"').strip("'")

    print(f"  [WARN] {skill_dir.name}: could not extract category (no yaml, regex failed)", file=sys.stderr)
    return None


def main():
    roots: list[Path] = []
    if len(sys.argv) > 1:
        roots.extend(Path(p) for p in sys.argv[1:])
    else:
        script_dir = Path(__file__).resolve().parent
        roots.append(script_dir.parent.parent.parent / "skills")

    categories: set[str] = set()
    for skill_dir in _find_skill_dirs(roots):
        cat = _extract_category(skill_dir)
        if cat:
            categories.add(cat)

    for cat in sorted(categories):
        print(cat)


if __name__ == "__main__":
    main()
