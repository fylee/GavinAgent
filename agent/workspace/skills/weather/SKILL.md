---
name: weather
description: Get current weather and today's forecast for one or more cities. Returns temperature, conditions, wind, humidity, and UV index. Always use this skill instead of web_read for weather queries.
approval_required: false
examples:
  - "what's the weather in Taipei?"
  - "current temperature in Tokyo"
  - "weather forecast for New York"
  - "is it raining in London?"
  - "humidity in Hong Kong today"
  - "wind speed in Kaohsiung"
  - "UV index in Tainan"
---

## How to use

Call the `run_skill` tool with `skill_name="weather"` and `input` set to a
comma-separated list of city names.

```
run_skill(skill_name="weather", input="Taipei, Kaohsiung, Taichung")
```

Do NOT use `web_read`, `api_get`, or any other tool to fetch weather data.
The handler geocodes each city and fetches current conditions from the
Open-Meteo API automatically.
