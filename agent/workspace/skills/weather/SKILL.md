---
name: weather
description: Get current weather and today's forecast for one or more cities. Returns temperature, conditions, wind, humidity, and UV index. Always use this skill instead of web_read for weather queries.
approval_required: false
---

Call the handler with a comma-separated list of city names as input.
Example input: "Taichung, Hsinchu"

The handler geocodes each city and fetches current conditions from the Open-Meteo API (no API key required). Return the structured data directly to the user in a readable format.
