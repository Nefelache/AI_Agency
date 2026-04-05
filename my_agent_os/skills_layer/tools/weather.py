"""
Weather — free Open-Meteo API (no key needed).
Geocodes city via Nominatim, then fetches current + 3-day forecast.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from my_agent_os.skills_layer.base import Skill
from my_agent_os.skills_layer.tools import register

_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Rain showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm+hail", 99: "Heavy thunderstorm+hail",
}


@register
class Weather(Skill):
    name = "weather"
    description = "Get current weather and 3-day forecast for any city. Params: city (str), units ('celsius'|'fahrenheit', optional)."
    skill_instructions = """
When to use: user asks for weather, forecast, 天气 for a place.
Required: city (non-empty place name).
Optional: units celsius|fahrenheit.
"""

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        city  = params.get("city", "").strip()
        units = params.get("units", "celsius")
        if not city:
            return {"success": False, "reason": "Missing 'city'."}

        geo = self._geocode(city)
        if not geo:
            return {"success": False, "reason": f"Could not geocode city: {city}"}

        lat, lon, display = geo
        return self._fetch_weather(lat, lon, display, units)

    def _geocode(self, city: str) -> tuple[float, float, str] | None:
        try:
            q   = urllib.parse.quote_plus(city)
            url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "AgentOS/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
            if not data:
                return None
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", city)
        except Exception:
            return None

    def _fetch_weather(self, lat: float, lon: float, display: str, units: str) -> dict[str, Any]:
        try:
            temp_unit = "fahrenheit" if units == "fahrenheit" else "celsius"
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
                f"&temperature_unit={temp_unit}&wind_speed_unit=mph&forecast_days=4&timezone=auto"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "AgentOS/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())

            cur     = data["current"]
            daily   = data["daily"]
            sym     = "°F" if temp_unit == "fahrenheit" else "°C"
            code    = int(cur.get("weather_code", 0))
            desc    = _WMO_CODES.get(code, "Unknown")
            temp    = cur.get("temperature_2m", "?")
            rh      = cur.get("relative_humidity_2m", "?")
            wind    = cur.get("wind_speed_10m", "?")

            lines  = [f"Weather for {display.split(',')[0]}"]
            lines += [f"Now: {desc}, {temp}{sym}, humidity {rh}%, wind {wind} mph"]
            lines += [""]
            for i in range(min(3, len(daily["time"]))):
                day   = daily["time"][i]
                hi    = daily["temperature_2m_max"][i]
                lo    = daily["temperature_2m_min"][i]
                wcode = int(daily["weather_code"][i])
                lines.append(f"{day}: {_WMO_CODES.get(wcode, '?')} — {lo}–{hi}{sym}")

            return {
                "success": True,
                "city":    display.split(",")[0],
                "current": {"description": desc, "temp": temp, "humidity": rh, "wind_mph": wind},
                "output":  "\n".join(lines),
            }
        except Exception as e:
            return {"success": False, "reason": str(e)}
