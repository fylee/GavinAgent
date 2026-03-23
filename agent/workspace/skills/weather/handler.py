"""
Weather skill handler — uses Open-Meteo (free, no API key required).
Geocoding via Open-Meteo geocoding API.
"""
from __future__ import annotations

import httpx

WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

WIND_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _wind_direction(degrees: float) -> str:
    return WIND_DIRS[round(degrees / 45) % 8]


def _geocode(city: str) -> tuple[float, float, str] | None:
    """Return (lat, lon, resolved_name) or None."""
    resp = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        return None
    r = results[0]
    name = f"{r['name']}, {r.get('country', '')}"
    return r["latitude"], r["longitude"], name


def _fetch_weather(lat: float, lon: float) -> dict:
    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "uv_index",
            ],
            "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
            "forecast_days": 1,
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def handle(input: str) -> str:
    cities = [c.strip() for c in input.split(",") if c.strip()]
    if not cities:
        return "Please provide at least one city name."

    lines: list[str] = []
    for city in cities:
        geo = _geocode(city)
        if geo is None:
            lines.append(f"**{city}**: Could not find this location.")
            continue

        lat, lon, resolved = geo
        try:
            data = _fetch_weather(lat, lon)
        except Exception as e:
            lines.append(f"**{city}**: Weather fetch failed — {e}")
            continue

        cur = data["current"]
        daily = data["daily"]

        code = cur.get("weather_code", 0)
        condition = WMO_DESCRIPTIONS.get(code, f"Code {code}")
        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        humidity = cur.get("relative_humidity_2m", "?")
        wind_spd = cur.get("wind_speed_10m", "?")
        wind_dir = _wind_direction(cur.get("wind_direction_10m", 0))
        uv = cur.get("uv_index", "?")
        high = daily.get("temperature_2m_max", ["?"])[0]
        low = daily.get("temperature_2m_min", ["?"])[0]
        precip = daily.get("precipitation_sum", ["?"])[0]

        lines.append(
            f"**{resolved}**\n"
            f"- Conditions: {condition}\n"
            f"- Temperature: {temp}°C (feels like {feels}°C)\n"
            f"- High / Low: {high}°C / {low}°C\n"
            f"- Humidity: {humidity}%\n"
            f"- Wind: {wind_spd} km/h {wind_dir}\n"
            f"- UV Index: {uv}\n"
            f"- Precipitation today: {precip} mm"
        )

    return "\n\n".join(lines)
