import sys
sys.path.insert(0, 'E:/claude/nanobot')
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell.shell import ExecTool
from nanobot.agent.tools.filesystem.filesystem_read import ReadFileTool
from nanobot.agent.tools.filesystem.filesystem_write import WriteFileTool
import json

reg = ToolRegistry()
reg.register(ExecTool())
reg.register(ReadFileTool())
reg.register(WriteFileTool())

for defn in reg.get_definitions():
    fn = defn['function']
    print(f'=== {fn["name"]} ===')
    d = fn['description']
    print(f'Description: {d[:80]}...' if len(d) > 80 else f'Description: {d}')
    props = fn['parameters'].get('properties', {})
    for k, v in props.items():
        desc = v.get('description', '*** NO DESCRIPTION ***')
        if not desc or desc == '':
            desc = '*** EMPTY DESCRIPTION ***'
        print(f'  {k}: {desc[:80]}')
    print()