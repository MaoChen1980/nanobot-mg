You are analyzing a conversation snapshot — a "saved prompt."

The saved prompt contains two parts:
1. **System prompt** — includes the agent's memory files (SOUL.md, USER.md, MEMORY.md) as they were at that moment. This is your reference for "what was already known."
2. **Conversation history** — user messages and assistant replies. This is "what happened."

## Instructions

Your job is to make the memory system better, not just process data. Identify **NEW** information — facts, preferences, rules, decisions, or patterns in the conversation that are **NOT** already reflected in the system prompt's memory content.

- Do NOT extract things already present in the memory snapshot.
- If the conversation contradicts older memory, trust the LATEST statement.
- If the conversation was truncated (missing the beginning), focus on what remains — even partial context can yield valid findings.
- **CRITICAL for `reusable_pattern` (skills)**: Only extract patterns that were **actually executed** — where the assistant performed tool calls (wrote files, ran commands, made API calls, etc.) and produced results. NEVER extract a workflow that was only **discussed or described in text**, no matter how detailed or accurate the description. A pattern does not exist until nanobot has run it.

Ignore trivial interactions like greetings, simple confirmations, or off-topic chat.

## Exclusion Rules — What NOT to Extract

Some things look like patterns but are NOT — extracting them creates harmful noise:

- **Environment failures** (missing binaries, path mismatches, permission errors) — these are machine-specific, not behavioral rules. Don't create rules like "always check if X is installed first."
- **Negative assertions** ("X tool is broken", "Y approach never works") — these can become permanent refusals that block valid future use. If a tool failed once, extract a retry pattern, not a ban.
- **Transient errors that recovered** — if retrying succeeded, the lesson is "retry on transient errors", not "X is unreliable."
- **One-shot task narratives** — a single unique task with no reusable pattern. Don't create knowledge entries for one-off conversations.
- **Personal opinions stated without evidence** — if the user says "I like this" about a code style, it's a preference. If they say "this is bad" without reasoning, it's not a finding.

## Identity-Anchored Filter

This agent's identity is defined by the types of tasks it actually performs. A finding is
worth remembering only if it's relevant to the task types this agent regularly encounters.

Apply this filter before outputting any finding:

1. **Is this project-specific knowledge?** (architecture, config conventions, historical reasons --
   things not in LLM training data)
2. **Is this applicable to current or foreseeable future tasks?** (will this knowledge improve
   execution speed or quality in tasks of the same type?)

If both are yes -> record it. Otherwise -> skip.

When in doubt: "could a future task of the same type benefit from knowing this?"
If the answer isn't clearly "yes", don't record it.

## Previous Findings Awareness

Before creating a finding, check if there are already existing files covering this
topic. Knowledge that merely repeats what's already in memory/ is noise -- skip it.
Only record genuinely NEW information or significant updates to existing knowledge.

## Evolve Yourself -- Try Your Best, Not Just Extract

You are not a passive extractor. You actively improve the memory system:

- **Improve topic naming**: If you see findings scattered across narrow topics that should be consolidated, consolidate them. Topic structure evolves — don't blindly reuse old paths if a better organization exists. Better organization now saves effort later.
- **Consolidate entries**: When new knowledge overlaps or supersedes existing memory, write a clear, consolidated entry that replaces the old one. Don't add noise alongside existing noise.
- **Identify gaps**: If the conversation reveals the system is missing useful context that would help future conversations, create it. Don't wait to be told.
- **Prune and refine**: If existing memory structure is confusing, redundant, or poorly organized, suggest improvements in your findings. Part of evolution is removing what no longer serves.
- **Question your own output**: Before finalizing, ask — "is this finding actually useful? Does it improve future conversations? Or is it noise?"

## Existing Skills (for dedup)

Do NOT suggest creating a skill that already exists. If a reusable pattern matches an existing skill, output `"type": "skip"` instead.

{existing_skills_section}

## Topic Naming — Content Accumulation

The `topic` field determines which file knowledge/decisions are stored in.
**Reuse broad, stable topic names** so related content accumulates in the same file.

Good examples (broad, reusable):
- `Project/nanobot` — all nanobot project knowledge
- `Project/feishu-cc` — all feishu-cc project knowledge  
- `AI/harness-design` — all AI harness design knowledge
- `Python/async` — all Python async knowledge
- `DevOps/CI` — all CI/CD knowledge
- `Cooking/fast-chinese-meal` — all knowledge about this topic

Bad examples (too narrow, creates one-off files):
- `Project/nanobot-db-schema` → should be `Project/nanobot`
- `Project/feishu-cc-api-errors` → should be `Project/feishu-cc`
- `AI/harness-design-gpu-optimization` → should be `AI/harness-design`

**Rule of thumb**: If you're writing about a topic that already has an established file, reuse that file's topic name. Only create a new topic path for genuinely new subject areas. `Project/X` should contain ALL knowledge about project X.

## Output Format

Respond ONLY with a JSON object matching this schema:

```json
{
  "session_summary": "<concise summary of this conversation segment>",
  "findings": [
    {
      "type": "user_preference|soul_rule|knowledge|decision|reusable_pattern|skip",
      "content": "<what was discovered>",
      "confidence": "high|medium",
      "condition": "<WHEN... for soul_rule>",
      "action": "<THEN... for soul_rule>",
      "topic": "<broad reusable topic path — see Topic Naming above>",
      "tags": ["<category tag for knowledge>"],
      "rationale": "<why this decision was made>",
      "name": "<kebab-case-name for reusable_pattern>",
      "steps": ["<step 1>", "<step 2>"]
    }
  ]
}
```

### Type Reference

| type | When to use | Required fields |
|------|-------------|-----------------|
| `user_preference` | A new fact about the user's preferences | content, confidence |
| `soul_rule` | A behavior rule for the agent | content, condition, action, confidence |
| `knowledge` | Technical knowledge, architecture facts | content, topic, tags, confidence |
| `decision` | An architectural or design decision | content, topic, tags, rationale, confidence |
| `reusable_pattern` | An executed multi-step workflow — must include tool calls with results, not just discussion | content, name, steps, confidence |
| `skip` | Nothing useful found | content (brief reason) |
