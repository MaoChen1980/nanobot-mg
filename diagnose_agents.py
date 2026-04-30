"""
Diagnostic: Compare AGENTS.md claims against actual framework code.
Run from: E:\claude\nanobot
"""
from pathlib import Path
import re

ROOT = Path("E:/claude/nanobot/nanobot")
AGENTS = Path("C:/Users/savyc/.nanobot/workspace/AGENTS.md")

def find_classes_and_funcs(root, pattern):
    """Find class and function definitions matching pattern."""
    results = []
    for f in root.rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8")
            for line in text.splitlines():
                if re.search(pattern, line):
                    results.append(f"{f.relative_to(root)}: {line.strip()}")
        except:
            pass
    return results

def grep_files(pattern, root=ROOT, extensions=["*.py"]):
    """Grep for pattern in files."""
    results = []
    for ext in extensions:
        for f in root.rglob(ext):
            try:
                text = f.read_text(encoding="utf-8")
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        results.append(f"{f.relative_to(root)}:{i}: {line.strip()}")
            except:
                pass
    return results

agents_text = AGENTS.read_text(encoding="utf-8")

print("=" * 60)
print("DIAGNOSTIC: AGENTS.md vs Framework Code")
print("=" * 60)

# --- Module tables in AGENTS.md ---
print("\n### Framework Modules table (AGENTS.md) ###")
modules = re.findall(r"\*\*(\w+)\*\*.*?`(.*?)`.*?—(.*)", agents_text)
for name, file, desc in modules:
    file_path = ROOT / file.replace("/", "\\")
    exists = file_path.exists()
    status = "✅" if exists else "❌ MISSING"
    print(f"  {name:20s} {file:30s} {status}")

# --- Capabilities ---
print("\n### Framework Capabilities (AGENTS.md) ###")
capabilities = re.findall(r"\| (.*?) \| (.*?) \|", agents_text)
for cap, tool in capabilities:
    cap = cap.strip()
    tool = tool.strip()
    # Check if tool exists in tools/registry
    tool_files = list((ROOT / "agent" / "tools").glob("*.py"))
    found = any(tool.lower() in f.name.lower() for f in tool_files)
    status = "✅" if found else "⚠️ verify"
    print(f"  {cap:20s} tool={tool:20s} {status}")

# --- Key modules mentioned in docs ---
print("\n### Key classes/functions referenced in docs ###")
key_refs = [
    ("SessionPersistHook", r"class SessionPersistHook"),
    ("ContextMonitorHook", r"class ContextMonitorHook"),
    ("HeartbeatService", r"class HeartbeatService"),
    ("SubagentManager", r"class SubagentManager"),
    ("AutoCompact", r"class AutoCompact"),
    ("ToolRegistry", r"class ToolRegistry"),
    ("ContextBuilder", r"class ContextBuilder"),
    ("AgentRunner", r"class AgentRunner"),
    ("AgentLoop", r"class AgentLoop"),
]
for name, pattern in key_refs:
    results = find_classes_and_funcs(ROOT, pattern)
    if results:
        print(f"  ✅ {name}: {results[0]}")
    else:
        print(f"  ❌ {name}: NOT FOUND")

# --- Limitations ---
print("\n### Framework Limitations (AGENTS.md) ###")
limitations = [
    ("concurrent_tools config", r"concurrent_tools"),
    ("max_iterations", r"max_iterations"),
    ("HEARTBEAT.md", r"HEARTBEAT"),
    ("session.messages", r"session\.messages"),
]
for desc, pattern in limitations:
    results = grep_files(pattern, ROOT)
    if results:
        print(f"  ✅ {desc}: found in {len(results)} places")
    else:
        print(f"  ❌ {desc}: NOT FOUND in code")

print("\n" + "=" * 60)
print("Done.")