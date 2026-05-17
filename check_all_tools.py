import sys
sys.path.insert(0, 'E:/claude/nanobot')

# Register all tools as done in loop.py
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell.shell import ExecTool
from nanobot.agent.tools.filesystem.filesystem_read import ReadFileTool
from nanobot.agent.tools.filesystem.filesystem_write import WriteFileTool
from nanobot.agent.tools.filesystem.filesystem_delete import DeleteFileTool
from nanobot.agent.tools.filesystem.filesystem_move import MoveFileTool
from nanobot.agent.tools.filesystem.filesystem_glob import GlobTool
from nanobot.agent.tools.filesystem.filesystem_grep import GrepTool
from nanobot.agent.tools.filesystem.filesystem_list_dir import ListDirTool
from nanobot.agent.tools.filesystem.filesystem_explore_module import ExploreModuleTool
from nanobot.agent.tools.web.web_fetch import WebFetchTool
from nanobot.agent.tools.web.web_search import WebSearchTool
from nanobot.agent.tools.web.web_inspect_text import InspectTextTool
from nanobot.agent.tools.web.web_search_text import SearchTextTool
from nanobot.agent.tools.web.web_analyze import AnalyzeTool
from nanobot.agent.tools.git.git_inspect import GitInspectTool
from nanobot.agent.tools.goal_event import (
    WriteGoal, ListGoals, ListEvents, WriteEvent,
    DeclareGoal, DeclareCheckpoint, DeclareAssumption, VerifyAssumption,
)
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.notebook import NotebookEditTool
from nanobot.agent.tools.session import SessionManageTool, RecallTool, SearchMemoryTool
from nanobot.agent.tools.recipe import RecipeTool
from nanobot.agent.tools.diagnose import DiagnoseTool
from nanobot.agent.tools.spawn import SpawnTool, CheckSubagentTool
from nanobot.agent.tools.loop.my import MyTool
from nanobot.agent.tools.ask import AskUserTool

reg = ToolRegistry()
for cls in [ExecTool, ReadFileTool, WriteFileTool, DeleteFileTool, MoveFileTool,
            GlobTool, GrepTool, ListDirTool, ExploreModuleTool,
            WebFetchTool, WebSearchTool, InspectTextTool, SearchTextTool, AnalyzeTool,
            GitInspectTool, WriteGoal, ListGoals, ListEvents, WriteEvent,
            DeclareGoal, DeclareCheckpoint, DeclareAssumption, VerifyAssumption,
            MessageTool, NotebookEditTool, SessionManageTool, RecallTool,
            SearchMemoryTool, RecipeTool, DiagnoseTool, SpawnTool, CheckSubagentTool,
            MyTool, AskUserTool]:
    try:
        reg.register(cls())
    except Exception as e:
        print(f'Failed to register {cls.__name__}: {e}')

missing_all = []
for name, tool in reg._tools.items():
    schema = getattr(tool, '_tool_parameters_schema', None)
    if not schema:
        schema = getattr(tool, 'parameters', {})
    props = schema.get('properties', {}) if isinstance(schema, dict) else {}
    for k, v in props.items():
        d = v.get('description', '') if isinstance(v, dict) else ''
        if not d:
            missing_all.append(f'{name}.{k}')

if missing_all:
    print('MISSING DESCRIPTIONS:')
    for m in missing_all:
        print(f'  {m}')
else:
    print('All parameter descriptions present.')
print(f'Total tools registered: {len(reg._tools)}')