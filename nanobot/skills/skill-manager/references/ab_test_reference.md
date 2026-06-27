# A/B Test Reference

## Getting the Provider

The `AgentLoop` requires a `provider` (LLM backend). When nanobot runs inside a conversation, the provider is already initialized. To get it:

```python
# From within a tool execute() method, access via self._loop
loop = self._loop
provider = loop.provider

# Then pass it to the new AgentLoop in your test script:
loop = AgentLoop(workspace=workspace, provider=provider, disabled_skills=disabled_skills)
```

The `SpawnTool` has the same pattern — it accesses `self._manager` which holds the loop with the provider.

## Writing the A/B Test Script

1. Copy `scripts/ab_test_template.py` content
2. Fill in the placeholders:
   - `provider` — get from `self._loop.provider` in a tool
   - `task` — the task description string to test
   - `skill` — the skill name under test
3. Write the filled script to workspace with `write_file`
4. Run with `exec`
5. Read results from `ab_test_result.json`

## Interpreting Results

```
{
  "task": "...",
  "skill": "...",
  "without": {
    "success": true,
    "tool_events": [...],
    "stop_reason": "completed",
    "tools_used": ["grep", "read_file"]
  },
  "with": { ... },
  "diff": {
    "better": true,
    "reason": "Skill improved task success"
  }
}
```

| Field | Meaning |
|---|---|
| `success` | Whether the task completed successfully (stop_reason == "completed") |
| `tool_events` | List of tool calls made — check if the right tools were invoked |
| `stop_reason` | "completed" / "max_iterations" / "error" / "empty_final_response" |
| `tools_used` | Unique tool names called |
| `better` | true if WITH skill succeeded and WITHOUT failed |

## Decision Guide

- **better = true** → skill helped achieve the task
- **both success, fewer tokens** → skill is more efficient
- **both fail** → task is hard for both; skill didn't help, but may not have hurt either
- **success without > success with** → skill caused interference; investigate or discard

## Limitations

- A/B test validates behavior change, not correctness of the changed behavior
- If both conditions fail the task, the skill may need refinement, not necessarily discard
- Multiple runs recommended for non-deterministic LLM behavior
