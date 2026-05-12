# Lessons Learned

## 2026-05-13: exec is not "last resort"

**Context**: I was optimizing tool descriptions and system prompts to make LLMs use workspace tools instead of exec. I framed exec as "LAST RESORT — only use when no other tool can do the job."

**Correction**: The user pointed out that exec is the RIGHT tool for data processing (e.g., writing a Python script to process 30MB CSV). Reading it line by line with read_file would be terrible.

**Rule**: Don't frame exec as "last resort." Instead, distinguish by task type:
- **exec** → computation: data processing, scripts, builds, batch operations
- **workspace tools** → interaction: reading/writing/searching files, listing dirs, fetching URLs

The nudge (suggesting tools for cat/grep/sed/curl in exec results) is still correct — those ARE workspace interaction patterns. But the blanket "avoid exec" framing was wrong.

**How to apply**: When describing tool selection strategy, always split by task type, not by priority. exec and workspace tools are peers for different jobs.
