# 语音转文字

NanoBot 支持将语音消息转写为文字，由用户自行配置后生效。

## 支持的提供商

NanoBot 内置两个转录提供商，均基于 Whisper 模型：

| 提供商 | 默认模型 | 特点 |
|--------|----------|------|
| **OpenAI Whisper** | `whisper-1` | 标准方案，通用性强 |
| **Groq Whisper** | `whisper-large-v3` | 转写速度快，免费额度慷慨 |

## 配置方式

语音转文字使用环境变量配置，不占用 LLM provider 配置位。

### OpenAI Whisper

```bash
# 必需
export OPENAI_API_KEY="sk-..."

# 可选：自定义 API 地址（兼容接口的第三方服务）
export OPENAI_TRANSCRIPTION_BASE_URL="https://api.openai.com/v1/audio/transcriptions"
```

### Groq Whisper

```bash
# 必需
export GROQ_API_KEY="gsk-..."

# 可选：自定义 API 地址
export GROQ_BASE_URL="https://api.groq.com/openai/v1/audio/transcriptions"
```

## 指定语言

通过环境变量 `NANOBOT_TRANSCRIPTION_LANGUAGE` 可指定转写语言（ISO 639-1 代码），例如：

```bash
export NANOBOT_TRANSCRIPTION_LANGUAGE="zh"   # 中文
export NANOBOT_TRANSCRIPTION_LANGUAGE="en"   # 英文
export NANOBOT_TRANSCRIPTION_LANGUAGE="ja"   # 日文
```

不设置时由模型自动检测语言。

## 工作原理

1. 用户发送语音消息（通过支持的聊天通道）
2. NanoBot 将音频文件保存至 media 目录
3. 根据配置自动选择 `GroqTranscriptionProvider` 或 `OpenAITranscriptionProvider`
4. 调用对应 API 将音频转为文字
5. 转写结果作为用户消息文本进入对话流程

## 注意事项

- 两个提供商可同时配置，NanoBot 会优先使用 Groq（若已配置 API key）
- 音频文件大小限制和格式支持由各提供商决定
- 未配置 API key 时，语音消息会被静默忽略
- 不支持本地离线转写，需联网调用 API
