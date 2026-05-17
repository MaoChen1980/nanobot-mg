import os, re

base = 'E:/claude/nanobot/nanobot/agent/tools'
results = []
for root, dirs, files in os.walk(base):
    for f in sorted(files):
        if not f.endswith('.py') or f == '__init__.py':
            continue
        path = os.path.join(root, f)
        lines = open(path, encoding='utf-8').readlines()
        for i, line in enumerate(lines, 1):
            # Match p('type', '') or p("type", '')
            m = re.search(r'p\s*\(\s*["\']([^"\']+)["\'],\s*""\s*\)', line)
            if m:
                results.append(f'{os.path.relpath(path, base)}:{i} | {line.rstrip()}')
print('\n'.join(results) if results else 'None found')