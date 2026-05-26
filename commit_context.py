import subprocess

result = subprocess.run(['git', '-C', 'E:/claude/nanobot', 'add', '-A'], capture_output=True, text=True)
print(result.stdout, result.stderr)

result = subprocess.run(
    ['git', '-C', 'E:/claude/nanobot', 'commit', '-m',
     'configurable context compression: context_max_turns, context_trim_batch'],
    capture_output=True, text=True
)
print(result.stdout, result.stderr)

result = subprocess.run(
    ['git', '-C', 'E:/claude/nanobot', 'status', '--short'],
    capture_output=True, text=True
)
print(result.stdout)