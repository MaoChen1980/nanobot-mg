# Lessons Learned

## 2026-05-26: Hook lifecycle granularity — per-iteration vs per-turn

**Context**: SelfReflectHook originally wrote a reflection trigger file in `after_iteration` (fires per LLM call), which the NEXT `before_iteration` would pick up and fire an LLM reflection call. This caused:
- Per-iteration file I/O for every single LLM call in a multi-iteration turn
- Nested LLM calls inside the agent loop (deferred to next iteration to avoid, but still messy)
- The "pending file" mechanism was fragile — dict vs object attribute bug (`entry._reflected` doesn't work on dicts)
- Self-edit infinite loops weren't detected until the runner guard caught them at 4 iterations

**Correction**: The user asked "我看他就一直在改某个文件，就是改不完" — the agent can't see its own repetitive behavior because there was no per-turn lifecycle hook.

**Fix**: 
1. Added `after_turn()` to the hook lifecycle (`hook.py`, `loop_hook.py`, `loop.py`) — fires once per user message, after all tool-call cycles finish
2. Rewrote SelfReflectHook to accumulate metrics in memory during `after_iteration`, then fire ONE LLM reflection in `after_turn`
3. Added repeated-tool and same-file-edit detection to the reflection prompt
4. Removed the fragile pending-file mechanism entirely

**Rule**: Hook methods must match their granularity to the event they observe:
- `after_iteration` → per-LLM-call (accumulate data, but don't fire expensive operations)
- `after_turn` → per-user-message (synthesize, persist, reflect)
- Never write file-based IPC between hook methods — use instance state
- Always check types: dict keys vs object attributes are different

**How to apply**: When designing hook behavior, ask "what's the natural scope?" If you're collecting per-call data to report per-turn, use in-memory accumulation in `after_iteration` and flush in `after_turn`. Never defer work to the next `before_iteration` — use the correct lifecycle method.

## 2026-05-17: MiniMax 2013 = multiple system messages

**Context**: MiniMax returned 2013 "invalid chat setting" for every request after a session had accumulated error messages. I spent a long time debugging thinking/reasoning parameters, message content, and API call format.

**Root cause**: The context assembler in `context.py` was emitting TWO messages with `role: "system"` — the main system prompt (`sys_static`) and a dynamic section (memory + timeline + state). MiniMax rejects requests with multiple system messages.

**Fix**: Merge dynamic parts into the first system message instead of appending a second one. Always safe — semantically equivalent.

**Rule**: When debugging API errors, think about what's UNIQUE about this provider's requirements, not just the error message. MiniMax is a Chinese LLM with its own API conventions. Test the simplest possible request first, then add complexity until it breaks.

**How to apply**: When a new provider returns a cryptic validation error (like "invalid chat setting (2013)"), narrow down by testing: (1) baseline simple request, (2) add one feature at a time. Consider "multiple system messages" as a likely cause for any non-OpenAI provider.

## 2026-05-13: exec is not "last resort"

**Context**: I was optimizing tool descriptions and system prompts to make LLMs use workspace tools instead of exec. I framed exec as "LAST RESORT — only use when no other tool can do the job."

**Correction**: The user pointed out that exec is the RIGHT tool for data processing (e.g., writing a Python script to process 30MB CSV). Reading it line by line with read_file would be terrible.

**Rule**: Don't frame exec as "last resort." Instead, distinguish by task type:
- **exec** → computation: data processing, scripts, builds, batch operations
- **workspace tools** → interaction: reading/writing/searching files, listing dirs, fetching URLs

The nudge (suggesting tools for cat/grep/sed/curl in exec results) is still correct — those ARE workspace interaction patterns. But the blanket "avoid exec" framing was wrong.

**How to apply**: When describing tool selection strategy, always split by task type, not by priority. exec and workspace tools are peers for different jobs.

## 2026-05-16: Thread-pool delivery races with sync callback replies

**Context**: Added `_handle_deliver` to proxy channels for tool/think push events. Used `asyncio.to_thread` for DingTalk's sync HTTP call, thinking it would preserve ordering since the background reader `await`s the result.

**Correction**: The reply (`_send_reply`, called from the DingTalk SDK thread after `send_to_hub` returns) could race ahead of `_send_deliver` in the thread pool. The thread pool executor doesn't guarantee execution order relative to another thread's synchronous call.

**Rule**: When outbound messages from different code paths (async background reader vs sync callback) need FIFO ordering, use a shared `queue.Queue` with a dedicated worker thread. Both `_handle_deliver` and `_send_reply` become non-blocking enqueue operations. Never rely on `asyncio.to_thread` scheduling to guarantee ordering against a different thread's synchronous HTTP call.

**How to apply**: For any proxy channel where `_handle_deliver` and `_send_reply` send to the same API, funnel all outbound messages through a single `queue.Queue` worker. Don't block the conn_loop with sync HTTP calls either — that's trading one problem for another.

## 2026-05-17: Don't extract file content into user message — save to workspace silently

**Context**: When a user sends a file (txt, pdf, image) via chat, the system was extracting the file content and injecting it into the user message as text. For images, it was base64-encoding them as vision input. This caused the LLM to treat file content as user instructions, hallucinating actions ("guess" behavior).

**Correction**: The LLM should only know what files were received, not process their content automatically. All media files are saved to the workspace directory; the user message only contains a simple reference like `[用户发送了: aaa.txt]` — no content extraction, no vision encoding. The LLM only acts when the user gives explicit instructions, at which point it can use file tools to read from workspace.

**Rule**: Never extract/inject file content into the user message for the LLM. File content should only be accessed via tool calls when the LLM has a specific reason (user instruction) to do so. Images should not be auto-encoded as vision input unless the user explicitly asks for visual processing. The message to the LLM should indicate WHAT was received, not WHAT the content says.

**How to apply**: When handling inbound media, always: (1) save the file to workspace with deduplicated name, (2) only add a simple `[用户发送了: filename]` to the message content, (3) strip media from the message so downstream layers don't auto-process it. Don't use `extract_documents()` or `detect_image_mime()` for automatic processing — leave files for the LLM to discover on demand.

## 2026-05-16: Put send queue in BaseProxyChannel, not per-channel

**Context**: After fixing DingTalk's ordering race with a per-channel `queue.Queue` + worker thread, the plan was to replicate the same pattern across 11 other channels.

**Correction**: Rather than adding a queue to every channel separately, put the infrastructure in `BaseProxyChannel`:
- `_send_queue: queue.Queue` + daemon `_send_worker_loop` thread in `__init__`
- `_enqueue_send(item)` thread-safe enqueue
- `_process_send(item)` abstract method — each channel overrides

This eliminates 12× boilerplate, ensures nobody forgets the queue, and makes the FIFO ordering a platform guarantee rather than a per-channel feature.

**Rule**: When infrastructure must be present in every subclass, put it in the base class. Don't let a "per-channel" fix become a pattern you copy-paste 11 times. The lesson from the DingTalk queue fix should have been "add queue to BaseProxyChannel" not "add queue to DingTalk."

**How to apply**: When identifying a pattern that multiple subclasses need, add it to the base class immediately — even if only one subclass currently demonstrates the need. The cost of abstracting is lower than the cost of retrofitting. Profile before optimizing: if the queue/thread overhead seems significant, measure it first (it's not — it's a near-zero cost for the daemon thread + unbounded queue).
