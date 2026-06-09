---
name: summarize
description: Trigger when the user asks to summarize a URL, web page, or local file; transcribe a YouTube video; or extract key points from content. Also use for "what's this link about?" or similar requests.
version: 0.1.0
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
   ```json
   { "model": "openai/gpt-5.2" }
   ```

## Verification

- The command exits with code 0 and prints a summary or transcript to stdout
- For YouTube URLs, output contains either a transcript or a summarization of the video
- For URLs with `--extract-only`, output contains the full extracted text content
- When using `--json`, output is valid JSON

## Pitfalls

- **API key required**: Each provider requires its own API key set as an environment variable. If no key is set, the default model `google/gemini-3-flash-preview` is used (Google's free tier has rate limits)
- **Large content**: Very long pages or video transcripts may exceed token limits. Use `--length` or `--max-output-tokens` to control output size, or use `--extract-only` to get raw content for chunked processing
- **Firecrawl fallback**: For sites that block scraping, set `FIRECRAWL_API_KEY` and use `--firecrawl auto` or `--firecrawl always`
- **Apify for YouTube**: If YouTube caption extraction fails, the tool falls back to Apify when `APIFY_API_TOKEN` is set. Without it, YouTube extraction may return limited results
- **brew-only install**: The `summarize` CLI is only available via Homebrew. On Linux, install Homebrew first or use an alternative tool

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification.
