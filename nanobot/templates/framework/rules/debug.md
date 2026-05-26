# #debug Rules

Debugging is a process of elimination, not a guessing game. Every failed attempt is data — use it.

## Narrow Before You Dig

- The fastest fix is the one where you've proven exactly where the problem is before touching anything. Always start by isolating: reduce the problem space until you can point at the exact line or data point.
- A stack trace tells you where the program crashed, not why. Trace backward from the crash: what state led to this? What assumptions were violated?
- Before blaming "a bug", blame your understanding. The code was working (or was written deliberately). What don't you know yet?

## Learn From History

- Every commit tells a story. Before changing code someone else wrote, `git_inspect` the relevant history. Was this line a fix for something? A workaround? A deliberate trade-off? Understanding why it's there is faster than rediscovering the same lesson.
- If you're about to change something that was clearly a workaround: great — you may have found the right place to fix the root cause. But first understand what the workaround was working around.

## Escalate Deliberately

- If you've tried two fundamentally different approaches and neither worked: stop and gather more information before trying a third. Read the docs, search for similar issues, ask for context. The third attempt with the same level of understanding won't be any more successful.
- Same error three times from similar approaches means you're in a loop. The loop isn't in your code — it's in your thinking. Step back, re-read the problem, change your model.
