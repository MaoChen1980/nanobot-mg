---
name: weather
description: Checks current weather and forecasts for any city or location. Uses free APIs (wttr.in, Open-Meteo) — no API key required. Use when the user asks about temperature, rain, forecast, or climate.
version: 0.1.0
---

# Weather, tools from system

两个免费服务，无需 API 密钥。

## wttr.in (primary)

快速一行命令：
```bash
curl -s "wttr.in/London?format=3"
# Output: London: ⛅️ +8°C
```

紧凑格式：
```bash
curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
# Output: London: ⛅️ +8°C 71% ↙5km/h
```

完整预报：
```bash
curl -s "wttr.in/London?T"
```

格式代码：`%c` 天气状况 · `%t` 温度 · `%h` 湿度 · `%w` 风速 · `%l` 地点 · `%m` 月相

提示：
- URL 编码空格：`wttr.in/New+York`
- 机场代码：`wttr.in/JFK`
- 单位：`?m`（公制）`?u`（美制）
- 仅今日：`?1` · 仅当前：`?0`
- PNG：`curl -s "wttr.in/Berlin.png" -o /tmp/weather.png`

## Open-Meteo (fallback, JSON)

免费，无需密钥，适合程序化使用：
```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```

查找城市坐标后进行查询。返回包含温度、风速、天气代码的 JSON。

文档：https://open-meteo.com/en/docs

---

**自我优化**：使用此 skill 后，根据所学内容进行改进——修复 bug、简化步骤、添加边界情况、增强验证。frontmatter 中的触发条件和 description 由原作者设置，不得更改。
---

**Self-optimization**: After using this skill, improve it based on what you learned — fix bugs, simplify steps, add edge cases, enhance verification. The trigger conditions and description in the frontmatter are set by the original author and must NOT be changed.
