import sys
sys.path.insert(0, '.')
from nanobot.command.router import CommandRouter
from nanobot.command.builtin import register_builtin_commands

r = CommandRouter()
register_builtin_commands(r)
print('exact keys:', list(r._exact.keys()))
print('/new in _exact:', '/new' in r._exact)
print('is_dispatchable_command /new:', r.is_dispatchable_command('/new'))
print('is_dispatchable_command /help:', r.is_dispatchable_command('/help'))
print('prefix:', list(r._prefix))
