# #review Rules

Code review isn't gatekeeping — it's the fastest way to catch what the author missed because they were too close to the problem.

## What to Look For

- **Correctness** — Does the logic hold? Are edge cases handled? Are there hidden assumptions?
- **Consistency** — Does this fit the existing architecture and style? Inconsistent code is correct today and broken tomorrow when someone assumes the pattern.
- **Side effects** — What else does this change touch? Every change has a blast radius — understand it before approving.

## Beyond the Diff

- Trace the data flow: where does input come from, how is it transformed, where does output go? A change that looks right in isolation may break a downstream consumer.
- Think about failure modes: what happens when this code gets unexpected input? when it's called concurrently? when dependencies change?
- The best review feedback isn't "this is wrong" — it's "this is wrong, and here's why, and here's a way to think about it differently."
