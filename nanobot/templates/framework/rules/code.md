# #code Rules

You're building something that will be read, maintained, and depended on. Write code you'd be proud to explain six months from now.

## Design First

- A bug is rarely a typo — it's almost always a misunderstanding of how the system works. Before fixing, trace the full chain: what was the original design intent? What assumption turned out wrong? Fix the design, not the line.
- When you're about to edit a file, ask: do I understand the full context of this function/module? If the answer is no, read more first. A precise edit based on understanding is faster than a guess that needs three follow-up fixes.
- New code doesn't exist in isolation. Read the surrounding patterns before writing — consistency with existing code is a form of correctness.

## Verification Is Your Safety Net

- The most expensive bug is the one you don't catch yourself. Run linter + tests after every change. Not because a rule says so — because catching it now costs seconds, catching it later costs hours.
- For any non-trivial change: write a quick test or script that proves the new behavior works and the old behavior didn't break. This isn't bureaucracy — it's the difference between "I think it works" and "I know it works."

## Craft Over Speed

- Good code is a side effect of caring about the details: naming, error messages, edge cases, the shape of the API. These aren't polish — they're what makes code maintainable.
- If a piece of code feels wrong (too complex, too fragile, too clever), it probably is. Trust that feeling and simplify before moving on. The time to fix it is now, not in three months when someone else is debugging it.
