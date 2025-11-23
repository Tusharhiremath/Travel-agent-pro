"""
app.py - Multi-agent Tourism System (Parent + Weather Agent + Places Agent)

APIs used (open-source / free):
- Nominatim (OpenStreetMap) for geocoding:
  https://nominatim.openstreetmap.org/search
- Open-Meteo for weather:
  https://api.open-meteo.com/v1/forecast
- Overpass API (OpenStreetMap) for places:
  https://overpass-api.de/api/interpreter

Run:
    streamlit run app.py
"""

import requests
import time
import math
from datetime import datetime, timezone
import streamlit as st
from typing import Optional, Tuple, List, Dict

# ----- CONFIG -----
USER_AGENT_EMAIL = "noxistepan2023@gmail.com"  # <- Replace with your email for Nominatim User-Agent/From header
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

HEADERS = {
    "User-Agent": f"TourismAgent/1.0 ({USER_AGENT_EMAIL})",
    "Accept-Language": "en"
}

# ----- UTILITIES -----
def haversine(lat1, lon1, lat2, lon2):
    # returns distance in km
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# ----- Child Agent: Geocoding (Nominatim) -----
def geocode_place(place: str, limit: int = 1) -> Optional[Dict]:
    """
    Returns dict containing lat, lon, display_name or None if not found.
    """
    params = {
        "q": place,
        "format": "json",
        "limit": limit,
        "addressdetails": 1
    }
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        item = data[0]
        return {
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "display_name": item.get("display_name", place),
            "raw": item
        }
    except Exception as e:
        # could be network error or API error
        return None

# ----- Child Agent: Weather Agent (Open-Meteo) -----
def get_weather(lat: float, lon: float) -> Optional[Dict]:
    """
    Uses Open-Meteo to fetch current weather and precipitation probability for the current hour.
    Returns:
      {
        "temperature": float (°C),
        "windspeed": float (m/s or km/h depending on API - Open-Meteo uses m/s),
        "time": iso-string,
        "precip_prob": int (0-100) or None,
        "raw": {...}
      }
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        # request hourly precipitation probability to estimate "chance of rain"
        "hourly": "precipitation_probability",
        "timezone": "auto"
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        curr = data.get("current_weather")
        if not curr:
            return None

        # attempt to get precipitation_probability for the current hour
        precip_prob = None
        try:
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            probs = hourly.get("precipitation_probability", [])
            if times and probs:
                # find index matching current_weather time
                curtime = curr.get("time")
                if curtime in times:
                    idx = times.index(curtime)
                    precip_prob = int(round(probs[idx]))
                else:
                    # fallback: pick nearest by time difference
                    # convert ISO times
                    def to_ts(s):
                        return datetime.fromisoformat(s).replace(tzinfo=None)
                    times_ts = [to_ts(t) for t in times]
                    cur_ts = to_ts(curtime)
                    diffs = [abs((cur_ts - t).total_seconds()) for t in times_ts]
                    idx = diffs.index(min(diffs))
                    precip_prob = int(round(probs[idx]))
        except Exception:
            precip_prob = None

        return {
            "temperature": curr.get("temperature"),
            "windspeed": curr.get("windspeed"),
            "time": curr.get("time"),
            "precip_prob": precip_prob,
            "raw": data
        }
    except Exception:
        return None

# ----- Child Agent: Places Agent (Overpass) -----
def find_places(lat: float, lon: float, radius_m: int = 20000, max_places: int = 5) -> List[Dict]:
    """
    Query Overpass API around (lat,lon) for common tourist features and return up to max_places entries.
    Each entry: {"name": name, "type": tag_key:tag_value, "lat":..., "lon":..., "distance_km":...}
    """
    # Compose an Overpass QL query that looks for tourism/historic/leisure nodes & ways
    # We'll collect nodes and ways and use 'center' for ways to get a coordinate
    overpass_q = f"""
    [out:json][timeout:25];
    (
      node(around:{radius_m},{lat},{lon})[tourism];
      way(around:{radius_m},{lat},{lon})[tourism];
      node(around:{radius_m},{lat},{lon})[historic];
      way(around:{radius_m},{lat},{lon})[historic];
      node(around:{radius_m},{lat},{lon})[leisure=park];
      way(around:{radius_m},{lat},{lon})[leisure=park];
    );
    out center 50;
    """
    try:
        resp = requests.post(OVERPASS_URL, data={"data": overpass_q}, timeout=30, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        elems = data.get("elements", [])

        places = []
        seen_names = set()
        for el in elems:
            tags = el.get("tags", {}) or {}
            name = tags.get("name")
            if not name:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)

            # get coordinates
            if el.get("type") == "node":
                plat = el.get("lat")
                plon = el.get("lon")
            else:
                # way or relation with center
                center = el.get("center") or {}
                plat = center.get("lat")
                plon = center.get("lon")
                # if still None, skip
                if plat is None or plon is None:
                    continue

            dist = haversine(lat, lon, plat, plon) if plat and plon else None

            # determine primary tag for type
            primary_tag = None
            for k in ("tourism", "historic", "leisure", "amenity"):
                if tags.get(k):
                    primary_tag = f"{k}={tags.get(k)}"
                    break

            places.append({
                "name": name,
                "tags": tags,
                "type": primary_tag or "other",
                "lat": plat,
                "lon": plon,
                "distance_km": dist
            })

        # sort by distance (closest first) if distance available
        places_sorted = sorted([p for p in places if p["distance_km"] is not None], key=lambda x: x["distance_km"])
        # if not enough with distance, append rest
        if len(places_sorted) < max_places:
            remaining = [p for p in places if p not in places_sorted]
            places_sorted.extend(remaining)

        return places_sorted[:max_places]
    except Exception as e:
        return []

# ----- Parent Agent: orchestrator -----
def plan_for_place(place_query: str):
    """
    High level orchestration: geocode -> weather + places -> return structured result
    """
    geocode = geocode_place(place_query)
    if not geocode:
        return {
            "ok": False,
            "error": f"I don't know this place exists: '{place_query}'. Please check spelling or try a different place."
        }

    lat = geocode["lat"]
    lon = geocode["lon"]
    display_name = geocode["display_name"]

    # call child agents
    weather = get_weather(lat, lon)
    places = find_places(lat, lon, radius_m=20000, max_places=5)  # radius 20km, up to 5 places

    return {
        "ok": True,
        "place": {
            "query": place_query,
            "display_name": display_name,
            "lat": lat,
            "lon": lon
        },
        "weather": weather,
        "places": places
    }

# ----- Streamlit UI -----
st.set_page_config(page_title="Tourism AI Agent", layout="centered")
st.title("Tourism AI Agent — Parent & Child Agents")
st.write("Enter a place (city / town / landmark). The app will geocode the place, fetch current weather (Open-Meteo) and suggest up to 5 nearby tourist places (Overpass).")

with st.form("place_form"):
    place_input = st.text_input("Where do you want to go?", placeholder="e.g., Bangalore or Paris")
    do_weather = st.checkbox("Show weather", value=True)
    do_places = st.checkbox("Show tourist places", value=True)
    submitted = st.form_submit_button("Plan my trip")

if submitted:
    if not place_input or place_input.strip() == "":
        st.error("Please enter a place name.")
    else:
        with st.spinner("Looking up the place and planning..."):
            result = plan_for_place(place_input.strip())

        if not result["ok"]:
            st.error(result["error"])
        else:
            place = result["place"]
            st.markdown(f"### Results for **{place['display_name']}**")
            st.write(f"Coordinates: {place['lat']:.6f}, {place['lon']:.6f}")

            if do_weather:
                weather = result.get("weather")
                if weather:
                    temp = weather.get("temperature")
                    precip = weather.get("precip_prob")
                    wtime = weather.get("time")
                    st.markdown("**Weather**")
                    if temp is not None:
                        text = f"Currently {temp}°C"
                        if precip is not None:
                            text += f" with a chance of {precip}% to rain."
                        else:
                            text += "."
                        st.success(text)
                        st.caption(f"Data time: {wtime}")
                    else:
                        st.warning("Weather data not available.")
                else:
                    st.warning("Weather data not available (Open-Meteo lookup failed).")

            if do_places:
                places = result.get("places", [])
                st.markdown("**Suggested places to visit**")
                if places:
                    for i, p in enumerate(places, start=1):
                        dist = p.get("distance_km")
                        dist_str = f" — {dist:.1f} km" if dist is not None else ""
                        st.write(f"{i}. **{p['name']}**{dist_str} — {p['type']}")
                        if st.expander("Details", expanded=False):
                            st.json({"name": p['name'], "type": p['type'], "lat": p['lat'], "lon": p['lon'], "tags": p['tags']})
                else:
                    st.info("No tourist places found nearby via Overpass API.")
