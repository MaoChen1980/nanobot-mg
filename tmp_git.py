"""Check git diff for heartbeat service."""
import subprocess
result = subprocess.run(
    ["git", "diff", "HEAD", "--", "nanobot/heartbeat/service.py"],
    capture_output=True, text=True, cwd="E:/claude/nanobot-mg"
)
print(result.stdout[:3000])
