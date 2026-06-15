"""Shared compression logic for session history.

Public API (async, one-call):
  ``compress_turns(to_compress, keep, ...)``
    Summarise old turns + create synthetic pair in a single call.
    Use when you have a flat message list and no Session object.

  ``compress_session(session, history, ...)``
    Pure session-level compression: split → summarise → return
    ``(updated_history, CompressEvent)``.  Does **not** persist or
    mutate.  Callers use ``apply_compress_event`` for side effects.

  ``split_history_by_budget(session_messages, formatted, limit)``
    Pure splitter — determine what to keep vs compress.

  ``apply_compress_event(session, event, db)``
    Persist a ``CompressEvent`` to DB and update ``session.messages``.
"""

from __future__ import annotations

import asyncio
import json
import re

from loguru import logger

from nanobot.agent.compressor import CompressEvent, Compressor
from nanobot.agent.llm_context import chat_stream_with_retry
from nanobot.agent.loop_utils import strip_think
from nanobot.session.manager import Session
from nanobot.utils.helpers import estimate_message_tokens

# Minimum number of turns to keep after compression (code constant, not configurable)
MIN_KEEP_TURNS = 1

# Progressive compression: batch size and future context window
COMPRESS_BATCH_SIZE = 35
FUTURE_TURNS = 10


# ---------------------------------------------------------------------------
# Summary prompt (shared with MessagePipe)
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT_TEMPLATE = (
    "你正在总结即将被裁剪的旧对话 turns。\n"
    "task：你**后面**的对话（附在后面）是当前正在进行的上下文。\n"
    "请根据后面的对话来判断：前面的对话中，哪些信息对后面的对话仍然重要？\n"
    "\n"
    "## 一些可参考的方向（由你判断是否适用）\n"
    "- 后面还在引用的现实细节，比如： 地址、坐标、金额、文件路径、参数名、配置值、API 签名、接口约定、消息格式\n"
    "- 后面还在依赖的架构决策、选择理由\n"
    "- 后续步骤依赖的前置条件、状态、配置\n"
    "- 明确的选型决定（我们决定用 X 不选 Y，因为……）\n"
    "- 排除掉的替代方案及其排除原因\n"
    "- 达成共识的方案、配置、参数值\n"
    "- 后面还在讨论的话题、瓶颈、下一步计划\n"
    "- 当前 task 的目标和进度\n"
    "- 已尝试但未完成的下一步\n"
    "- 未解决的阻塞点\n"
    "- 踩过的坑和解决方案\n"
    "- 已验证不可行的路径及原因\n"
    "\n"
    "## ⚠️ 必须保留的——重要且无法重新推导的信息\n"
    "核心判断标准：**如果这个信息在未来还有用，且后面无法通过工具调用或推理重新得到，就必须保留。**\n"
    "常见例子：\n"
    "- 项目目录结构、文件布局、文件之间的引用关系（如：哪些文件是模块入口、测试文件在哪）\n"
    "- glob_tool / list_directory_tool / read_file_tool 返回的文件路径列表和目录结构\n"
    "- 已确认的文件绝对路径（不要只写「项目中有个配置文件」，要写全路径）\n"
    "- 代码库中的关键符号位置（函数 X 在哪个文件的哪一行）\n"
    "- 工具调用的返回样例（输出格式、结构），以便后面知道如何解析\n"
    "- 需要外部系统返回才能知道的值：API 响应状态码、报错信息、实时的外部数据\n"
    "- 用户明确提供的配置值、需求细节、偏好\n"
    "\n"
    "## 一些可以考虑丢弃的方向（由你判断）\n"
    "- 后面的对话已不再使用的试错过程\n"
    "- 已被后续值替代的旧值\n"
    "- 寒暄\n"
    "\n"
    "## 最重要的原则\n"
    "以上方向仅供参考。你的判断比这些建议更重要。\n"
    "如果你觉得某个信息在后面还有用，不管它属于哪类，都保留。\n"
    "如果你觉得某个信息后面已经用不上了，不管它看似多重要，都丢弃。\n"
    "\n"
    "关键原则：同一类信息，只保留最晚的那个版本。\n"
    "\n"
    "## ⛔ 摘要退化防护（极其重要）\n"
    "每次压缩都是一次**提炼合并**，不是重新总结。必须防止「抽象化退化」：\n"
    "- 具体信息保持具体：文件路径、参数名、配置值、函数名、错误码等专有名词在新摘要中要保持同样的具体程度，不要模糊成「相关配置」「某些文件」「一些参数」\n"
    "- 旧摘要中已有的具体信息必须保留在新摘要中，不要因为它在旧摘要中出现过就认为「可以省略」\n"
    "- 新摘要的信息密度不得低于旧摘要：如果旧摘要写了 5 个要点，新摘要不能只剩 3 个。宁可冗余，不要丢失\n"
    "- 如果旧摘要中有某个信息你判断不再需要了，用**后面的对话**来验证，而不是因为它「旧」就丢弃\n"
    "\n"
    "## 输出要求\n"
    "你的输出是一个**新摘要**，不是对旧对话的简单罗列。\n"
    "- 提取有用的事实、决策、配置、约定\n"
    "- 把完整的推理链、试错过程**压缩成结论**（如「已确认方案 X 可行」而非每一步尝试）\n"
    "- 按主题分组，简洁的要点列表，不要按 turn 顺序\n"
    "- 除非专用名词（命令、路径、参数等），都用自然语言表述，不要包含任何 XML、代码、或工具调用格式\n"
    "\n"
    "{previous_summary}"
    "以下是即将被裁剪的旧对话：\n\n"
    "{turns_text}\n\n"
    "---\n"
    "以下是后面（会保留的）对话，请参考它们来判断上面的旧对话中哪些信息仍然重要：\n\n"
    "{future_text}"
)


# ---------------------------------------------------------------------------
# Public: split history by token budget
# ---------------------------------------------------------------------------

def split_history_by_budget(
    session_messages: list[dict],
    formatted: list[dict],
    limit: int,
    min_keep_turns: int = MIN_KEEP_TURNS,
) -> tuple[list[list[dict]], list[list[dict]], list[list[dict]]]:
    """Split raw session into ``(keeps_raw, to_compress_fmt, keeps_fmt)``.

    *keeps_raw* — RAW message turns (from ``session_messages``), for write-back.
    *to_compress_fmt* — formatted turns to feed to the LLM summarizer.
    *keeps_fmt* — formatted turns to keep in the compressed LLM input.

    Alignment: ``format_history`` may trim the front (orphan tool results,
    first-user alignment).  The two turn lists are aligned from the **tail**
    so that corresponding turns share the same index offset.
    """
    raw_turns = Session._split_turns_by_assistant(
        [m for m in session_messages if m.get("status") != "excluded"]
    )
    fmt_turns = Session._split_turns_by_assistant(formatted)

    n = min(len(raw_turns), len(fmt_turns))
    offset = len(raw_turns) - n
    # Align: only use the last n fmt_turns (the trim happens at the front)
    if n < len(fmt_turns):
        fmt_turns = fmt_turns[-n:]

    # Walk from the tail accumulating tokens
    keep_start = n  # default: keep nothing
    used = 0
    for i in range(n - 1, -1, -1):
        turn_tokens = sum(estimate_message_tokens(m) for m in fmt_turns[i])
        if keep_start < n and used + turn_tokens > limit:
            break
        used += turn_tokens
        keep_start = i

    # Enforce min_keep_turns
    keep_start = min(keep_start, n - min_keep_turns)
    if keep_start < 0:
        keep_start = 0

    keeps_raw = raw_turns[offset + keep_start:]
    to_compress_fmt = fmt_turns[:keep_start]
    keeps_fmt = fmt_turns[keep_start:]
    return keeps_raw, to_compress_fmt, keeps_fmt


# ---------------------------------------------------------------------------
# Shared helpers (used by both compress_session and MessagePipe._compress)
# ---------------------------------------------------------------------------


def _take_future_turns(
    all_turns: list[list[dict]],
    batch_start: int,
    batch_size: int,
    n_future: int,
    keep: list[list[dict]],
) -> list[dict]:
    """取 batch 后面 n_future 轮作为 future context."""
    future_start = batch_start + batch_size
    src = all_turns[future_start:future_start + n_future]
    if len(src) < n_future:
        need = n_future - len(src)
        src = src + keep[:need]
    return [m for turn in src for m in turn]


# Matches MiniMax dict format: {tool => "name", args => { ... }}
_TOOL_DICT_RE = re.compile(
    r'\{tool\s*=>\s*"[^"]+"\s*,\s*args\s*=>\s*\{[^}]*\}\s*\}',
    re.DOTALL,
)
# Matches XML invoke: <invoke name/tool="...">...</invoke>
_TOOL_INVOKE_RE = re.compile(
    r'<invoke\s+(?:name|tool)\s*=\s*["\'][^"\']+["\']\s*>.*?</invoke>',
    re.DOTALL,
)
# Matches [TOOL_CALL] wrapper markers
_TOOL_TC_RE = re.compile(r'\[/?TOOL_CALL\]')


def _strip_xml_tool_calls(text: str) -> str:
    """Strip residual XML tool call patterns from summary text.

    Safety net: if the LLM ignored the natural-language instruction and
    emitted tool-call XML/dict/args formats in the summary, remove them.
    """
    result = _TOOL_INVOKE_RE.sub("", text)
    result = _TOOL_DICT_RE.sub("", result)
    result = _TOOL_TC_RE.sub("", result)
    return result.strip()


# ---------------------------------------------------------------------------
# Public: async summarise via LLM
# ---------------------------------------------------------------------------

async def summarize_turns(
    turns: list[dict],
    future_context: list[dict] | None = None,
    previous_summary: str | None = None,
) -> str:
    """Summarise *turns* (flat messages) via the LLM.

    *future_context* — flat messages from the retained history, used so the
    LLM can judge which information in the old turns is still relevant.

    *previous_summary* — summary from the last compression round. Included
    in the prompt so the LLM can merge new information into it rather than
    rewriting from scratch.

    Returns the summary text (empty string on failure).
    Never raises: all exceptions are caught and logged.

    The caller is responsible for stripping ``<think>`` blocks and further
    processing.
    """
    if not turns:
        return ""

    current_turns: list[dict] = list(turns)
    current_future: list[dict] = list(future_context) if future_context else []

    for attempt in range(6):
        prompt = _build_prompt(current_turns, current_future, previous_summary)
        t_tok = sum(estimate_message_tokens(m) for m in current_turns)
        f_tok = sum(estimate_message_tokens(m) for m in current_future)
        logger.info(
            "CT_DBG: summarize_turns attempt {}/6: {} turns ({} tok), {} future ({} tok)",
            attempt + 1, len(current_turns), t_tok, len(current_future), f_tok,
        )

        try:
            resp = await asyncio.wait_for(
                chat_stream_with_retry(
                    [{"role": "user", "content": prompt}],
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.warning("Summary attempt {}/6 timed out (120s)", attempt + 1)
            if attempt < 9:
                await asyncio.sleep(10)
                continue
            return ""
        except Exception as e:
            logger.exception("Summary attempt {}/6 failed (network): {}", attempt + 1, e)
            if attempt < 9:
                await asyncio.sleep(10)
                continue
            return ""

        from nanobot.agent.message_pipe import _is_overflow

        if _is_overflow(resp):
            logger.warning("Summary overflow on attempt {}/6, reducing content", attempt + 1)
            if attempt < 9 and len(current_turns) > 1:
                mid = len(current_turns) // 2
                current_turns = current_turns[mid:]
                if current_future and len(current_future) > 1:
                    mid_future = len(current_future) // 2
                    current_future = current_future[mid_future:]
                continue
            if attempt < 9 and len(current_turns) == 1:
                msg = current_turns[0]
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 200:
                    half = len(content) // 2
                    current_turns = [{"role": msg["role"], "content": content[:half] + "\n...(truncated)"}]
                    if current_future and len(current_future) == 1:
                        fc_msg = current_future[0]
                        fc_content = fc_msg.get("content", "")
                        if isinstance(fc_content, str) and len(fc_content) > 200:
                            fc_half = len(fc_content) // 2
                            current_future = [{"role": fc_msg["role"], "content": fc_content[:fc_half] + "\n...(truncated)"}]
                        else:
                            current_future = []
                    else:
                        current_future = []
                    continue
            return ""

        if resp.finish_reason == "error":
            logger.warning(
                "Summary attempt {}/6 failed (LLM error): {}",
                attempt + 1,
                (resp.content or "")[:200],
            )
            if attempt < 9:
                await asyncio.sleep(10)
                continue
            return ""

        if not resp.content:
            logger.warning(
                "Summary attempt {}/6 returned empty content (finish_reason={})",
                attempt + 1,
                resp.finish_reason,
            )
            if attempt < 9:
                await asyncio.sleep(10)
                continue
            return ""

        summary = resp.content.strip()
        logger.info("Summarized {} turns ({} chars)", len(current_turns), len(summary))
        return summary

    return ""


# ---------------------------------------------------------------------------
# Public: one-shot compress → pair (no session)
# ---------------------------------------------------------------------------

async def compress_turns(
    to_compress: list[dict],
    keep: list[dict],
    previous_summary: str | None = None,
    timestamp: str | None = None,
) -> tuple[str | None, list[dict]]:
    """Summarise *to_compress*, create synthetic pair — one async call.

    *keep* — turns retained as future context (helps LLM judge relevance).
    *previous_summary* — summary from the last compression round.
    *timestamp* — optional ISO timestamp for the synthetic messages.

    Returns ``(summary_text, synthetic_pair)`` where *summary_text* is
    ``None`` when summarisation fails (empty pair in that case).
    """
    if not to_compress:
        return None, []

    summary = await summarize_turns(
        to_compress, future_context=keep, previous_summary=previous_summary,
    )
    summary = strip_think(summary).strip() if summary else ""
    summary = _strip_xml_tool_calls(summary) if summary else ""
    if not summary:
        return None, []
    return summary, make_summary_pair(summary, timestamp)


# ---------------------------------------------------------------------------
# Public: full session compression (reads session, no side effects)
# ---------------------------------------------------------------------------

async def compress_session(
    session: Session,
    history: list[dict],
    *,
    limit: int,
    min_keep_turns: int = MIN_KEEP_TURNS,
) -> tuple[list[dict], CompressEvent]:
    """Compress session history: split, summarise, return updated history + event.

    *session* — session object (read-only here; caller mutates via
    ``apply_compress_event``).
    *history* — formatted message list for LLM input.
    *limit* — token budget for kept turns.
    *min_keep_turns* — minimum turns to retain after compression.

    Returns ``(updated_history, compress_event)``.  Does **not** mutate
    *session* or write to DB — callers handle persistence via
    ``apply_compress_event``.
    """
    keeps_raw, to_compress_fmt, keeps_fmt = split_history_by_budget(
        session.messages, history, limit=limit, min_keep_turns=min_keep_turns,
    )
    logger.info(
        "CT_DBG: split done — to_compress={} turns, keep={} turns",
        len(to_compress_fmt), len(keeps_fmt),
    )

    event = CompressEvent()
    if to_compress_fmt:
        prev = getattr(session, "_last_summary", None)
        logger.info(
            "CT_DBG: Compressor.compress start (to_compress={} turns, keep={} turns, prev_summary={})",
            len(to_compress_fmt), len(keeps_fmt), bool(prev),
        )
        event = await Compressor.compress(to_compress_fmt, keeps_fmt, previous_summary=prev)
        logger.info(
            "CT_DBG: Compressor.compress done (summary={}, synthetic_pair={})",
            bool(event.summary), len(event.synthetic_pair),
        )

    # Find replaced raw messages using message object identity
    if keeps_raw:
        flat_kept = [m for turn in keeps_raw for m in turn]
        kept_ids = {id(m) for m in flat_kept}
        split_point = 0
        for i, m in enumerate(session.messages):
            if id(m) in kept_ids:
                split_point = i
                break
        else:
            split_point = len(session.messages)
        event.replaced_raw = session.messages[:split_point]
    else:
        event.replaced_raw = []

    if event.synthetic_pair:
        result = list(event.synthetic_pair)
        for turn in keeps_fmt:
            result.extend(turn)
    else:
        result = [m for turn in keeps_fmt for m in turn]

    return result, event


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_turns(msgs: list[dict]) -> str:
    """Format a flat message list into the prompt text block."""
    lines: list[str] = []
    for msg in msgs:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            lines.append(f"<{role}>\n{content}\n</{role}>")
        elif isinstance(content, list):
            texts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "\n".join(texts)
            if joined:
                lines.append(f"<{role}>\n{joined}\n</{role}>")
    return "\n".join(lines)


def _build_prompt(
    turns: list[dict],
    future_context: list[dict],
    previous_summary: str | None = None,
) -> str:
    """Build the summary prompt for the LLM."""
    turns_text = _format_turns(turns)
    future_text = _format_turns(future_context) if future_context else ""

    prev_section = ""
    if previous_summary:
        prev_section = (
            "## 已有摘要（只更新不裁剪，必须包含全部已有内容）\n"
            f"{previous_summary}\n\n"
        )

    return _SUMMARY_PROMPT_TEMPLATE.format(
        turns_text=turns_text,
        future_text=future_text,
        previous_summary=prev_section,
    )


def apply_compress_event(session: Session, event: CompressEvent, db=None) -> None:
    """Persist *event* to DB and update ``session.messages``.

    Side-effectful: writes to DB history table, mutates ``session.messages``.
    This is the explicit counterpart to the pure ``compress_session`` —
    callers invoke this when they are ready to commit the compression.
    """
    if not event.replaced_raw:
        return
    if db is not None:
        try:
            db.append_history(
                content=json.dumps(event.replaced_raw, ensure_ascii=False),
                summary=event.summary or "",
            )
        except Exception:
            logger.exception("Failed to persist compressed history to DB")
    kept = session.messages[len(event.replaced_raw):]
    session.messages[:] = kept
    if event.summary:
        session._last_summary = event.summary
        session.metadata.pop("_summary_injected_key", None)
    logger.info(
        "Compressed session {}: dropped {} messages, kept {}",
        session.key, len(event.replaced_raw), len(kept),
    )


_COMPRESSION_NOTICE = (
    "\n\n---\n[Context compressed: earlier parts of the conversation were "
    "summarized to fit the context window. Precise information such as "
    "file paths, directory structures, tool outputs, or external data "
    "may have been lost. If you need details to proceed, use the "
    "appropriate input tools (read, search, exec, web, etc.) "
    "to re-discover before acting.]\n---"
)


def make_summary_pair(summary: str, timestamp: str | None = None) -> list[dict]:
    """Create a synthetic user message carrying the compressed summary.

    A single user message ensures the conversation always starts with user
    (``user → assistant → user → assistant → …``).

    A compression notice is prepended so the LLM knows context was reduced
    and precise information (paths, structure) may need re-discovery.
    """
    msg = {
        "role": "user",
        "content": summary + _COMPRESSION_NOTICE,
        "status": "synthetic",
    }
    if timestamp:
        msg["timestamp"] = timestamp
    return [msg]


def _prepend_summary(keeps_fmt: list[list[dict]], summary: str) -> list[dict]:
    """Prepend a synthetic summary message before the kept formatted turns.

    Returns a flat message list suitable for LLM input.  Does **not** touch
    the session object.
    """
    result = make_summary_pair(summary)
    for turn in keeps_fmt:
        result.extend(turn)
    return result
