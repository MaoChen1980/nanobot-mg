# Tool Usage Notes

Tool Usage Notes for assistant using in function call or tool call
Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## General

- Answer all pending user questions in one response if possible. If tool results are available, use them.
- If a task was cancelled or interrupted, inform the user explicitly.
- Do not leave questions unanswered if you have the information to answer them.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## exec — Chinese Characters & URLs

- On Windows, `cmd.exe` uses GBK encoding by default, which corrupts Chinese characters in URLs
- For Chinese city names in URLs, use `powershell -Command` instead of bare `curl`, or URL-encode the city name
- Example: `powershell -Command "curl -s 'https://wttr.in/%E8%A5%BF%E5%AE%89?format=3'"` (西安 URL-encoded)
- Better alternative for weather: use `web_search` to find the weather, or use Open-Meteo with coordinates (no Chinese in URL)

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across results
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## recall — Memory Search

- Use `recall` to search memories before answering questions about prior work, decisions, dates, people, preferences, or facts.
- After calling recall, absorb the results into your context — do NOT simply forward the raw memory output to the user.
- If recall returns relevant memories, incorporate that context into your response naturally.
