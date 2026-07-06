---
name: summarize
description: '总结 URL、网页、本地文件、YouTube 视频的内容，提取关键点。

  当用户要求总结一个链接、网页、文件，或说"这个链接是什么"、"TLDR"时激活。'
version: 0.1.0
category: domain-specific
---

# Summarize Skill

Fast CLI for summarizing URLs, local files, and YouTube links.

## When to Use

- The user says "use summarize.sh" or "summarize this"
- The user asks "what's this link/video about?"
- The user says "summarize this URL/article"
- The user says "transcribe this YouTube/video" (extracts captions from the URL directly; no `yt-dlp` needed)

## Steps

1. **Install the CLI** if not present:
   ```bash
   brew install steipete/tap/summarize
   ```

2. **Pick a model** — default is `google/gemini-3-flash-preview` when no API key is set. Supported providers:
   - OpenAI (`OPENAI_API_KEY`)
   - Anthropic (`ANTHROPIC_API_KEY`)
   - xAI (`XAI_API_KEY`)
   - Google (`GEMINI_API_KEY`, alias: `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`)

3. **Set the API key** for your chosen provider as an environment variable.

4. **Run summarize** with the content source:
   ```bash
   summarize "https://example.com" --model google/gemini-3-flash-preview
   summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
   summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
   ```

5. **Handle YouTube content** — extract transcript vs. summarize:
   ```bash
   # Extract transcript only (no summary)
   summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
   ```
   If the transcript content is too large, return a condensed summary first, then ask which part or time range the user wants expanded.

6. **Useful flags** for controlling output:
   - `--length short|medium|long|xl|xxl|<chars>` — control summary length
   - `--max-output-tokens <count>` — limit token output
   - `--extract-only` — return raw content without summarization (URL sources only)
   - `--json` — machine-readable JSON output
   - `--firecrawl auto|off|always` — fallback extraction for blocked sites
   - `--youtube auto` — uses Apify as fallback if `APIFY_API_TOKEN` is set

7. **Optional configuration file** at `~/.summarize/config.json`:
   ```
   { "model": "openai/gpt-5.2" }
   ```

8. **验证**: 对照 Verification 章节逐条检查。全部通过则完成；不通过则加载 skill-manager 修复此 skill。

## Verification

- The command exits with code 0 and prints a summary or transcript to stdout
- For YouTube URLs, output contains either a transcript or a summarization of the video
- For URLs with `--extract-only`, output contains the full extracted text content
- When using `--json`, output is valid JSON
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Pitfalls

- **API key required**: Each provider requires its own API key set as an environment variable. If no key is set, the default model `google/gemini-3-flash-preview` is used (Google's free tier has rate limits)
- **Large content**: Very long pages or video transcripts may exceed token limits. Use `--length` or `--max-output-tokens` to control output size, or use `--extract-only` to get raw content for chunked processing
- **Firecrawl fallback**: For sites that block scraping, set `FIRECRAWL_API_KEY` and use `--firecrawl auto` or `--firecrawl always`
- **Apify for YouTube**: If YouTube caption extraction fails, the tool falls back to Apify when `APIFY_API_TOKEN` is set. Without it, YouTube extraction may return limited results
- **brew-only install**: The `summarize` CLI is only available via Homebrew. On Linux, install Homebrew first or use an alternative tool

