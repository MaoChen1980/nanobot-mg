---
name: summarize
description: Summarizes web pages, local files, and YouTube videos. Extracts key content and produces transcripts via the summarize CLI. Use when the user asks to summarize, transcribe, or get key points from content.
version: 0.1.0
---

# Summarize, tools from system

快速 CLI，用于总结 URL、本地文件和 YouTube 链接。

## Install

```bash
brew install steipete/tap/summarize
```

## When to use (trigger phrases)

当用户提出以下任何请求时，立即使用此 skill：
- "use summarize.sh"
- "what's this link/video about?"
- "summarize this URL/article"
- "transcribe this YouTube/video"（尽力提取字幕；无需 `yt-dlp`）

## Quick start

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube: summary vs transcript

尽力提取字幕（仅 URL）：

```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

如果用户要求字幕但内容太大，先返回精炼摘要，然后询问展开哪个部分/时间范围。

## Model + keys

为所选提供商设置 API 密钥：
- OpenAI: `OPENAI_API_KEY`
- Anthropic: `ANTHROPIC_API_KEY`
- xAI: `XAI_API_KEY`
- Google: `GEMINI_API_KEY`（别名：`GOOGLE_GENERATIVE_AI_API_KEY`、`GOOGLE_API_KEY`）

未设置时默认模型为 `google/gemini-3-flash-preview`。

## Useful flags

- `--length short|medium|long|xl|xxl|<chars>`
- `--max-output-tokens <count>`
- `--extract-only`（仅 URL）
- `--json`（机器可读）
- `--firecrawl auto|off|always`（回退提取）
- `--youtube auto`（如设置 `APIFY_API_TOKEN` 则使用 Apify 回退）

## Config

可选配置文件：`~/.summarize/config.json`

```json
{ "model": "openai/gpt-5.2" }
```

可选服务：
- `FIRECRAWL_API_KEY` 用于被屏蔽站点
- `APIFY_API_TOKEN` 用于 YouTube 回退

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
