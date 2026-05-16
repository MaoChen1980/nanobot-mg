# Lessons Learned

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

## 2026-05-16: Put send queue in BaseProxyChannel, not per-channel

**Context**: After fixing DingTalk's ordering race with a per-channel `queue.Queue` + worker thread, the plan was to replicate the same pattern across 11 other channels.

**Correction**: Rather than adding a queue to every channel separately, put the infrastructure in `BaseProxyChannel`:
- `_send_queue: queue.Queue` + daemon `_send_worker_loop` thread in `__init__`
- `_enqueue_send(item)` thread-safe enqueue
- `_process_send(item)` abstract method — each channel overrides

This eliminates 12× boilerplate, ensures nobody forgets the queue, and makes the FIFO ordering a platform guarantee rather than a per-channel feature.

**Rule**: When infrastructure must be present in every subclass, put it in the base class. Don't let a "per-channel" fix become a pattern you copy-paste 11 times. The lesson from the DingTalk queue fix should have been "add queue to BaseProxyChannel" not "add queue to DingTalk."

**How to apply**: When identifying a pattern that multiple subclasses need, add it to the base class immediately — even if only one subclass currently demonstrates the need. The cost of abstracting is lower than the cost of retrofitting. Profile before optimizing: if the queue/thread overhead seems significant, measure it first (it's not — it's a near-zero cost for the daemon thread + unbounded queue).
