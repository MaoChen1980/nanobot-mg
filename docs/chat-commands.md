# In-Chat Commands

These commands work inside chat channels and interactive agent sessions:

| Command | Description |
|---------|-------------|
| `/new` | Stop current task and start a new conversation |
| `/stop` | Stop the current task |
| `/restart` | Restart the bot |
| `/status` | Show bot status |
| `/dream` | Run Dream memory consolidation now |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-log <sha>` | Show a specific Dream memory change |
| `/dream-restore` | List recent Dream memory versions |
| `/dream-restore <sha>` | Restore memory to the state before a specific change |
| `/help` | Show available in-chat commands |

## Periodic Tasks (Heartbeat)

The gateway wakes up every 30 minutes and queries active goals from the DB. If there are in-progress goals, the agent receives them via heartbeat message and can advance them, deliver results to your most recently active chat channel.

**Setup:** use `write_goal` tool to create goals — the heartbeat will pick them up automatically:

```
write_goal(action="upsert", id="g1", title="Check weather forecast", status="in_progress")
```

The agent can also manage goals itself — ask it to "add a periodic task" and it will create a goal via `write_goal`.

> **Note:** The gateway must be running (`nanobot gateway`) and you must have chatted with the bot at least once so it knows which channel to deliver to.
