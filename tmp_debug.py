"""Debug heartbeat tick test with session_key=None."""
import tempfile, json, pathlib, time, sys
sys.path.insert(0, 'E:/claude/nanobot-mg')

from unittest.mock import MagicMock, AsyncMock
from nanobot.heartbeat.service import HeartbeatService
from nanobot.heartbeat.state import HeartbeatState

tmp = pathlib.Path(tempfile.mkdtemp())
print(f"tmp = {tmp}")

# Write tree.json
tasks_dir = tmp / "tasks"
tasks_dir.mkdir(parents=True, exist_ok=True)
items = [{"id": "check", "name": "check", "status": "pending", "parent": None}]
(tasks_dir / "tree.json").write_text(json.dumps({"items": items}), encoding="utf-8")
print(f"Wrote tree.json: {(tasks_dir / 'tree.json').read_text(encoding='utf-8')}")

# Create service with session_key=None
loop = MagicMock()
loop.workspace = tmp
loop._session_dispatch = {}
loop.dispatch_manager = MagicMock()
loop.process_direct = AsyncMock(return_value=MagicMock(content="HEARTBEAT_OK"))

svc = HeartbeatService(agent_loop=loop, enabled=True, session_key=None)
svc._state = HeartbeatState(tmp / "tasks" / ".heartbeat_state.json")

# Debug
print(f"svc.session_key = {svc.session_key!r}")
print(f"_tree_path: {svc._tree_path()}")
print(f"_tree_path exists: {svc._tree_path().exists()}")
if svc._tree_path().exists():
    print(f"File content: {svc._tree_path().read_text(encoding='utf-8')}")
else:
    # Check if any json exists
    for f in tasks_dir.glob("*.json"):
        print(f"Found: {f.name} -> {f.read_text(encoding='utf-8')[:100]}")

pending = svc._find_pending_tasks()
print(f"_find_pending_tasks() returned: {pending}")
