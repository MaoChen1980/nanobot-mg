---
name: weather
description: >
  查询天气信息。
  当用户询问天气、温度、降雨、降雪、风力、湿度、紫外线、空气质量、天气预报（今天/今晚/本周/周末）时，必须使用此 Skill。
  关键词：天气、温度、下雨、下雪、刮风、湿度、预报、℃、rain、temperature、forecast。
  即使用户没有明确说"查天气"，只要涉及某个城市或地区的自然条件，都应触发。
version: 0.1.0
category: domain-specific
---

# Weather — current conditions and forecasts

Two free services, no API keys required.

## When to Use

- User asks "what's the weather in ..." or "temperature in ..."
- User asks for a forecast or "will it rain today?"
- User asks about climate or weather conditions
- wttr.in is unreachable (fall back to Open-Meteo)

## Steps

### Method 1: wttr.in (primary)

1. **Quick one-liner** for current conditions:
   ```bash
   curl -s "wttr.in/London?format=3"
   # Output: London: ⛅️ +8°C
   ```

2. **Compact format** with humidity and wind:
   ```bash
   curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
   # Output: London: ⛅️ +8°C 71% ↙5km/h
   ```

3. **Full forecast**:
   ```bash
   curl -s "wttr.in/London?T"
   ```

4. **Format codes** for custom layouts:
   - `%c` — weather condition
   - `%t` — temperature
   - `%h` — humidity
   - `%w` — wind speed
   - `%l` — location
   - `%m` — moon phase

5. **Useful flags**:
   - URL-encode spaces as `+`: `wttr.in/New+York`
   - Airport codes work: `wttr.in/JFK`
   - Units: `?m` (metric), `?u` (US units)
   - Today only: `?1` · Current only: `?0`
   - PNG output: `curl -s "wttr.in/Berlin.png" -o /tmp/weather.png`

### Method 2: Open-Meteo (fallback, JSON)

1. **Find coordinates** for the location, then query:
   ```bash
   curl -s "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
   ```

2. Parse the JSON response for temperature, wind speed, and weather code.

3. **验证**: 对照 Verification 章节逐条检查。全部通过则完成；不通过则加载 skill-manager 修复此 skill。

## Verification

- wttr.in: confirm output contains location name and temperature (non-empty response)
- Open-Meteo: confirm JSON contains `current_weather` object with `temperature`, `windspeed`, `weathercode`
- If wttr.in returns an error or empty result, retry with Open-Meteo
- For forecasts, confirm the output spans the expected time range
- **Self-optimization**: 此 Skill 可自我进化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准

## Pitfalls

- wttr.in requires spaces to be encoded as `+` in URLs
- Open-Meteo needs latitude/longitude — geocode the city name first
- Airport codes only work with wttr.in, not Open-Meteo
- wttr.in may be rate-limited or blocked in some regions — fall back to Open-Meteo
- Open-Meteo documentation: https://open-meteo.com/en/docs
