from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from geopy.distance import distance
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import json
import os

STORAGE_DIR = "data"
USERS_FILE = os.path.join(STORAGE_DIR, "users.json")
ALERTS_FILE = os.path.join(STORAGE_DIR, "alerts.json")
CACHE_FILE = os.path.join(STORAGE_DIR, "cache.json")
LAST_ALERT_FILE = os.path.join(STORAGE_DIR, "last_alert.json")

os.makedirs(STORAGE_DIR, exist_ok=True)

USERS: Dict[str, Dict[str, Any]] = {}
ALERTS: List[Dict[str, Any]] = []
LAST_ALERT: Dict[str, str] = {}
CACHE: Dict[str, Any] = {
    "last_update": None,
    "events": [],
    "consecutive_failures": 0
}

RISK_RADIUS = {
    "Hurricane": 100,
    "Flood": 50,
    "Wildfire": 75,
    "Tornado": 25,
    "Earthquake": 60,
}

scheduler: Optional[BackgroundScheduler] = None

def load_data() -> None:
    global USERS, ALERTS, LAST_ALERT, CACHE
    
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r') as f:
                USERS = json.load(f)
            print(f"Loaded {len(USERS)} users from disk")
    except Exception as e:
        print(f"Failed to load users: {e}")
        USERS = {}
    
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, 'r') as f:
                ALERTS = json.load(f)
            print(f"Loaded {len(ALERTS)} alerts from disk")
    except Exception as e:
        print(f"Failed to load alerts: {e}")
        ALERTS = []
    
    try:
        if os.path.exists(LAST_ALERT_FILE):
            with open(LAST_ALERT_FILE, 'r') as f:
                LAST_ALERT = json.load(f)
    except Exception as e:
        print(f"Failed to load last_alert: {e}")
        LAST_ALERT = {}
    
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                CACHE = json.load(f)
            if "consecutive_failures" not in CACHE:
                CACHE["consecutive_failures"] = 0
            print(f"Loaded cache with {len(CACHE.get('events', []))} events")
    except Exception as e:
        print(f"Failed to load cache: {e}")
        CACHE = {"last_update": None, "events": [], "consecutive_failures": 0}


def save_users() -> None:
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(USERS, f, indent=2)
    except Exception as e:
        print(f"Failed to save users: {e}")


def save_alerts() -> None:
    try:
        with open(ALERTS_FILE, 'w') as f:
            json.dump(ALERTS, f, indent=2)
    except Exception as e:
        print(f"Failed to save alerts: {e}")


def save_last_alert() -> None:
    try:
        with open(LAST_ALERT_FILE, 'w') as f:
            json.dump(LAST_ALERT, f, indent=2)
    except Exception as e:
        print(f"Failed to save last_alert: {e}")


def save_cache() -> None:
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(CACHE, f, indent=2, default=str)
    except Exception as e:
        print(f"Failed to save cache: {e}")


class SubscribeRequest(BaseModel):
    user_id: str
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None


class PushNotificationRequest(BaseModel):
    user_id: str
    message: str


def map_gdacs_type(gdacs_title: str) -> str:
    title = gdacs_title.lower()
    if "fire" in title or "wildfire" in title:
        return "Wildfire"
    if "storm" in title or "cyclone" in title or "hurricane" in title or "typhoon" in title:
        return "Hurricane"
    if "flood" in title:
        return "Flood"
    if "volcano" in title:
        return "Volcano"
    if "earthquake" in title:
        return "Earthquake"
    return gdacs_title


def fetch_recent_gdacs_events(since: datetime) -> List[Dict[str, Any]]:
    url = "https://www.gdacs.org/gdacsapi/api/events"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"GDACS resync fetch failed: {e}")
        return []

    events: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        data = data.get("results", [])
    
    for ev in data:
        lat = ev.get("latitude")
        if lat is None:
            lat = ev.get("lat")
        lon = ev.get("longitude")
        if lon is None:
            lon = ev.get("lon")
        if lon is None:
            lon = ev.get("lng")
        if lat is None or lon is None:
            continue

        try:
            lat_float = float(lat)
            lon_float = float(lon)
        except (ValueError, TypeError):
            continue

        disaster_type = ev.get("eventtype") or ev.get("title") or ev.get("eventType") or "Disaster"
        mapped_type = map_gdacs_type(disaster_type)
        
        event_timestamp = None
        for ts_field in ["fromdate", "eventdate", "publisheddate", "date", "fromDate", "eventDate"]:
            ts_value = ev.get(ts_field)
            if ts_value:
                try:
                    if isinstance(ts_value, str):
                        event_timestamp = datetime.fromisoformat(ts_value.replace('Z', '+00:00')).isoformat()
                    elif isinstance(ts_value, (int, float)):
                        if ts_value > 1e10:
                            event_timestamp = datetime.fromtimestamp(ts_value / 1000.0, tz=timezone.utc).isoformat()
                        else:
                            event_timestamp = datetime.fromtimestamp(ts_value, tz=timezone.utc).isoformat()
                    break
                except (ValueError, TypeError, OSError):
                    continue
        
        if event_timestamp is None:
            event_timestamp = datetime.now(timezone.utc).isoformat()
        
        try:
            event_dt = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            if event_dt >= since:
                events.append({
                    "id": str(ev.get("eventid") or ev.get("id") or ev.get("eventId", "")),
                    "type": mapped_type,
                    "coordinates": [lat_float, lon_float],
                    "severity": ev.get("alertlevel") or ev.get("alertLevel") or "unknown",
                    "timestamp": event_timestamp,
                })
        except (ValueError, TypeError):
            events.append({
                "id": str(ev.get("eventid") or ev.get("id") or ev.get("eventId", "")),
                "type": mapped_type,
                "coordinates": [lat_float, lon_float],
                "severity": ev.get("alertlevel") or ev.get("alertLevel") or "unknown",
                "timestamp": event_timestamp,
            })

    return events


def fetch_gdacs_events() -> List[Dict[str, Any]]:
    url = "https://www.gdacs.org/gdacsapi/api/events"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"GDACS fetch failed: {e}")
        return []

    events: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        data = data.get("results", [])
    
    for ev in data:
        lat = ev.get("latitude")
        if lat is None:
            lat = ev.get("lat")
        lon = ev.get("longitude")
        if lon is None:
            lon = ev.get("lon")
        if lon is None:
            lon = ev.get("lng")
        if lat is None or lon is None:
            continue

        try:
            lat_float = float(lat)
            lon_float = float(lon)
        except (ValueError, TypeError):
            continue

        disaster_type = ev.get("eventtype") or ev.get("title") or ev.get("eventType") or "Disaster"
        mapped_type = map_gdacs_type(disaster_type)
        
        event_timestamp = None
        for ts_field in ["fromdate", "eventdate", "publisheddate", "date", "fromDate", "eventDate"]:
            ts_value = ev.get(ts_field)
            if ts_value:
                try:
                    if isinstance(ts_value, str):
                        event_timestamp = datetime.fromisoformat(ts_value.replace('Z', '+00:00')).isoformat()
                    elif isinstance(ts_value, (int, float)):
                        if ts_value > 1e10:
                            event_timestamp = datetime.fromtimestamp(ts_value / 1000.0, tz=timezone.utc).isoformat()
                        else:
                            event_timestamp = datetime.fromtimestamp(ts_value, tz=timezone.utc).isoformat()
                    break
                except (ValueError, TypeError, OSError):
                    continue
        
        if event_timestamp is None:
            event_timestamp = datetime.now(timezone.utc).isoformat()

        events.append({
            "id": str(ev.get("eventid") or ev.get("id") or ev.get("eventId", "")),
            "type": mapped_type,
            "coordinates": [lat_float, lon_float],
            "severity": ev.get("alertlevel") or ev.get("alertLevel") or "unknown",
            "timestamp": event_timestamp,
        })

    return events


def fetch_disaster_data() -> List[Dict[str, Any]]:
    return fetch_gdacs_events()


def safe_update_events() -> None:
    global CACHE
    
    try:
        events = fetch_disaster_data()
        
        if CACHE.get("consecutive_failures", 0) > 1:
            print("Network restored - resyncing last 24 hours...")
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            recent_events = fetch_recent_gdacs_events(since)
            
            existing_ids = {e.get("id") for e in CACHE.get("events", [])}
            for event in recent_events:
                if event.get("id") not in existing_ids:
                    events.append(event)
            
            print(f"Resynced {len(recent_events)} events from last 24 hours")
        
        CACHE["events"] = events
        CACHE["last_update"] = datetime.now(timezone.utc).isoformat()
        CACHE["consecutive_failures"] = 0
        save_cache()
        print(f"[{datetime.now(timezone.utc)}] events updated: {len(events)}")
    except Exception as e:
        CACHE["consecutive_failures"] = CACHE.get("consecutive_failures", 0) + 1
        
        if CACHE["consecutive_failures"] > 1:
            print(f"[{datetime.now(timezone.utc)}] Network outage - switching to offline mode (failure #{CACHE['consecutive_failures']})")
        else:
            print(f"[{datetime.now(timezone.utc)}] error updating events - using cached events: {e}")
        
        save_cache()


def get_user_location_from_ip(ip: str) -> Optional[Dict[str, float]]:
    if ip in ["127.0.0.1", "localhost", "::1"] or ip.startswith("192.168.") or ip.startswith("10."):
        return None
    
    try:
        url = f"https://ipapi.co/{ip}/json/"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        
        lat = data.get("latitude")
        lon = data.get("longitude")
        
        if lat is not None and lon is not None:
            city = data.get("city", "Unknown")
            region = data.get("region", "Unknown")
            country = data.get("country_name", "Unknown")
            print(f"Detected location for IP {ip}: {city}, {region}, {country} ({lat}, {lon})")
            return {"lat": float(lat), "lon": float(lon)}
    except Exception as e:
        print(f"IP geolocation failed for {ip}: {e}")
    
    return None


def calculate_distance_miles(user_coords: tuple, disaster_coords: tuple) -> float:
    return distance(user_coords, disaster_coords).miles


def categorize_risk(user_dist: float, disaster_type: str) -> Optional[str]:
    base_radius = RISK_RADIUS.get(disaster_type, 50)
    if user_dist <= 0.5 * base_radius:
        return "High"
    elif user_dist <= base_radius:
        return "Moderate"
    elif user_dist <= 1.5 * base_radius:
        return "Low"
    return None


def classify_proximity(risk_level: str) -> str:
    if risk_level == "High":
        return "Immediate Danger"
    elif risk_level == "Moderate":
        return "Caution Zone"
    elif risk_level == "Low":
        return "Informational"
    return "Unknown"


def classify_alert_level(event_timestamp: str) -> Tuple[str, str]:
    try:
        if isinstance(event_timestamp, str):
            if event_timestamp.endswith('Z'):
                event_timestamp = event_timestamp.replace('Z', '+00:00')
            event_dt = datetime.fromisoformat(event_timestamp)
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        else:
            return ("Emergency", "Unknown time")
        
        now = datetime.now(timezone.utc)
        time_diff = event_dt - now
        hours_until = time_diff.total_seconds() / 3600.0
        
        if hours_until > 72:
            days = int(hours_until / 24)
            return ("Reminder", f"{days} days away")
        elif hours_until > 24:
            days = int(hours_until / 24)
            return ("Warning", f"{days} days away")
        else:
            if hours_until < 0:
                return ("Emergency", "Past event")
            else:
                hours = int(hours_until)
                return ("Emergency", f"Within {hours}h")
    except (ValueError, TypeError, AttributeError):
        return ("Emergency", "Unknown time")


def should_send_alert(user_id: str, min_hours: int = 6) -> bool:
    now = datetime.now(timezone.utc)
    last_str = LAST_ALERT.get(user_id)
    
    if not last_str:
        LAST_ALERT[user_id] = now.isoformat()
        save_last_alert()
        return True
    
    try:
        last = datetime.fromisoformat(last_str.replace('Z', '+00:00'))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last) > timedelta(hours=min_hours):
            LAST_ALERT[user_id] = now.isoformat()
            save_last_alert()
            return True
    except (ValueError, TypeError):
        LAST_ALERT[user_id] = now.isoformat()
        save_last_alert()
        return True
    
    return False


def send_alert(user: Dict[str, Any], event: Dict[str, Any], risk_level: str) -> None:
    alert_level, time_desc = classify_alert_level(event.get("timestamp"))
    proximity_label = classify_proximity(risk_level)
    
    payload = {
        "user_id": user["user_id"],
        "user_name": user["name"],
        "disaster_type": event["type"],
        "risk_level": risk_level,
        "alert_level": alert_level,
        "proximity_level": proximity_label,
        "coords": event["coordinates"],
        "severity": event.get("severity", "unknown"),
        "event_id": event.get("id", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ALERTS.append(payload)
    save_alerts()
    
    print(f"[{alert_level}][{proximity_label}] alert -> {user['name']} | {event['type']} | {risk_level} risk | ({time_desc})")


def process_events_for_users() -> None:
    if not CACHE["events"]:
        return

    for event in CACHE["events"]:
        if "coordinates" not in event or not isinstance(event["coordinates"], (list, tuple)) or len(event["coordinates"]) != 2:
            continue
        
        event_type = event["type"]
        event_coords = tuple(event["coordinates"])

        for user_id, user in USERS.items():
            try:
                user_lat = user.get("lat")
                user_lon = user.get("lon")
                if user_lat is None or user_lon is None:
                    continue
                
                try:
                    float(event_coords[0])
                    float(event_coords[1])
                    float(user_lat)
                    float(user_lon)
                except (ValueError, TypeError):
                    continue
                
                user_coords = (user_lat, user_lon)
                dist = calculate_distance_miles(user_coords, event_coords)
                risk = categorize_risk(dist, event_type)

                if risk and should_send_alert(user_id):
                    send_alert(user, event, risk)
            except Exception as e:
                print(f"Error processing event {event.get('id', 'unknown')} for user {user_id}: {e}")
                continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    
    print("Loading data from disk...")
    load_data()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(safe_update_events, "interval", minutes=30, id="fetch_job")
    scheduler.add_job(process_events_for_users, "interval", minutes=5, id="process_job")
    try:
        scheduler.start()
        print("Scheduler started")
        safe_update_events()
    except Exception as e:
        print(f"Failed to start scheduler: {e}")
    
    yield
    
    print("Saving data to disk...")
    save_users()
    save_alerts()
    save_last_alert()
    save_cache()
    
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=True)
        print("Scheduler stopped")


app = FastAPI(title="Disaster AI Sentinel", version="1.0", lifespan=lifespan)


@app.post("/alerts/subscribe")
def subscribe_user(req: SubscribeRequest, request: Request):
    client_ip = request.client.host if request.client else None
    
    lat = req.lat
    lon = req.lon
    
    if lat is None or lon is None:
        location = None
        
        if client_ip:
            location = get_user_location_from_ip(client_ip)
        
        if location:
            if lat is None:
                lat = location["lat"]
            if lon is None:
                lon = location["lon"]
        else:
            if lat is None:
                lat = 39.8283
                print(f"Using default latitude for user {req.user_id} (IP detection failed or unavailable)")
            if lon is None:
                lon = -98.5795
                print(f"Using default longitude for user {req.user_id} (IP detection failed or unavailable)")
    
    USERS[req.user_id] = {
        "user_id": req.user_id,
        "name": req.name,
        "lat": lat,
        "lon": lon,
        "subscribed_on": datetime.now(timezone.utc).isoformat(),
    }
    save_users()
    return {"message": "user subscribed", "user": USERS[req.user_id]}


@app.get("/alerts")
def get_alerts(limit: int = 20):
    if limit <= 0:
        return []
    if limit > 1000:
        limit = 1000
    return ALERTS[-limit:]


@app.get("/alerts/history")
def get_alert_history(user_id: Optional[str] = None):
    if user_id:
        filtered = [alert for alert in ALERTS if alert.get("user_id") == user_id]
        return {"user_id": user_id, "count": len(filtered), "alerts": filtered}
    return {"count": len(ALERTS), "alerts": ALERTS}


@app.post("/alerts/push")
def push_notification(req: PushNotificationRequest):
    user = USERS.get(req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {req.user_id} not found")
    
    alert_payload = {
        "user_id": req.user_id,
        "user_name": user.get("name", req.user_id),
        "disaster_type": "Manual",
        "risk_level": "Unknown",
        "alert_level": "Warning",
        "proximity_level": classify_proximity("Unknown"),
        "coords": [user.get("lat", 0), user.get("lon", 0)],
        "severity": "unknown",
        "event_id": "manual",
        "message": req.message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    ALERTS.append(alert_payload)
    save_alerts()
    
    print(f"Notification sent to {user.get('name', req.user_id)}: {req.message}")
    
    return {
        "message": "Notification sent",
        "alert": alert_payload
    }


@app.get("/alerts/test-process")
def manual_process():
    safe_update_events()
    process_events_for_users()
    return {"message": "processed", "alerts": ALERTS[-10:]}


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "last_update": CACHE.get("last_update"),
        "user_count": len(USERS),
        "alerts_count": len(ALERTS),
        "cached_events": len(CACHE.get("events", [])),
        "offline_mode": CACHE.get("consecutive_failures", 0) > 1,
    }
