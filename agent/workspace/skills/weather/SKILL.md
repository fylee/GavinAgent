---
name: weather
description: Get current weather and today's forecast for one or more cities. Returns temperature, conditions, wind, humidity, and UV index. Always use this skill instead of web_read for weather queries.
allowed-tools: Bash
compatibility: Requires internet access to Open-Meteo API
metadata:
  examples: "what's the weather in Taipei? | current temperature in Tokyo | weather forecast for New York | is it raining in London? | humidity in Hong Kong today | wind speed in Kaohsiung | UV index in Tainan | is it hot in Seoul? | will it rain today in Paris?"
  triggers: "weather | forecast | temperature | humidity | wind | UV index | rain | raining | sunny | cloudy | snow | precipitation | hot | cold | climate | conditions"
  version: "1"
  approval_required: "false"
---

## Overview / Key conventions

- Accepts one or more city names as a comma-separated string in the `input` parameter.
- Fetches current conditions and today's forecast from the Open-Meteo API via geocoding.
- Returns for each city: temperature (°C), weather conditions, wind speed (km/h), humidity (%), and UV index.
- If a city name is ambiguous or not found, the handler will return an error message for that city; other cities in the list will still be processed.
- Only today's forecast is returned — this skill does not provide multi-day forecasts.
- Units: temperature in Celsius, wind in km/h, UV index as a numeric value (0–11+).

## Standard query patterns

Single city:
```
run_skill(skill_name="weather", input="Taipei")
```

Multiple cities:
```
run_skill(skill_name="weather", input="Taipei, Tokyo, New York")
```

Example output per city:
```
Taipei: 28°C, Partly Cloudy, Wind 15 km/h, Humidity 72%, UV Index 6
```

## Do NOT use

- Do NOT use `web_read` to fetch weather from any website.
- Do NOT use `api_get` or raw HTTP tools to call Open-Meteo or any weather API directly.
- Do NOT use `web_search` and parse results — structured data will be missing or inconsistent.
- Do NOT hardcode latitude/longitude — the handler geocodes city names automatically.

## Search strategy

1. Identify all city names mentioned in the user's request.
2. Call `run_skill` with `skill_name="weather"` and `input` as a comma-separated list of those cities.
3. Present the returned data for each city clearly, grouped by city.
4. If a city returns an error (not found), note it to the user and suggest checking the spelling or using a more specific name (e.g., "Springfield, IL" instead of "Springfield").
5. Do not make additional tool calls to verify or supplement the weather data — trust the skill output.
