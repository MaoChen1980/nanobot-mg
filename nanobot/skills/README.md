# nanobot Skills

This directory contains built-in skills that extend nanobot's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

When skills reference large local documentation or logs, prefer nanobot's built-in
`grep` / `glob` tools to narrow the search space before loading full files.
Use `grep(output_mode="count")` / `files_with_matches` for broad searches first,
use `head_limit` / `offset` to page through large result sets,
and `glob(entry_type="dirs")` when discovering directory structure matters.

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Available Skills

| Skill | Description |
|-------|-------------|
| `clawhub` | Search and install skills from ClawHub registry |
| `cron` | Schedule reminders and recurring tasks |
| `github` | Interact with GitHub using the `gh` CLI |
| `intent-alignment` | Intelligent intent detection and requirement clarification |
| `memory` | Two-layer memory system with auto-managed knowledge files |
| `multi-question-answering` | Answer multiple choice questions directly |
| `config` | Check and set the agent's own runtime state |
| `skill-manager` | Full lifecycle skill management (create, validate, optimize, maintain) |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `weather` | Get weather info using wttr.in and Open-Meteo |