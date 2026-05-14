---
name: memory
description: Two-layer memory system with Dream-managed knowledge files.
always: false
---

# Memory, tools from system

## Structure

- `SOUL.md` — Bot personality and communication style. **Managed by Dream.** Do NOT edit.
- `USER.md` — User profile and preferences. **Managed by Dream.** Do NOT edit.
- `memory/MEMORY.md` — Long-term facts (project context, important events). **Managed by Dream.** Do NOT edit.
- SQLite database — append-only conversation history. Use the `recall` tool to search it.

## Search Past Events

Use the `recall` tool to search conversation history:

- **First check**: Use a broad keyword (or no keyword) to see if relevant memories exist
- **Then narrow**: Use start/end dates or more specific keywords for context
- **Always synthesize results** into your answer — do not dump raw output

## Important

- **Do NOT edit SOUL.md, USER.md, or MEMORY.md.** They are automatically managed by Dream.
- If you notice outdated information, it will be corrected when Dream runs next.
- Users can view Dream's activity with the `/dream-log` command.
