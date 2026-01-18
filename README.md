# 🚨 Disaster Sentinel

Disaster Sentinel is a backend service designed to detect, classify, and respond to disaster events in near real time using user location data. Built during the BE Smart Hackathon (Top 5 of 64 teams), the system focuses on delivering timely, location-aware alerts instead of broad, generic warnings.

This repository reflects the backend implementation and experimental features I developed during the hackathon under extreme time constraints.

---

## 🧠 Problem Statement

During disaster events, alerts are often delayed, overly broad, or irrelevant to a user’s actual location. This reduces trust and slows response time. Disaster Sentinel addresses a core question:

How can we deliver accurate disaster alerts that are specific to where a user actually is?

---

## ⚙️ What Disaster Sentinel Does

- Ingests user location data during subscription
- Fetches live disaster events from the GDACS API
- Caches disaster events locally for resiliency and offline tolerance
- Computes distance-based risk levels per user
- Classifies proximity into actionable alert categories
- Runs scheduled background jobs for event refresh and alert processing
- Exposes REST endpoints for frontend or mobile integration

---

## 🧩 My Contribution (Backend)

This repository contains my individual backend contribution, including:

- User subscription and location ingestion logic
- IP-based geolocation fallback when coordinates are not provided
- Proximity and distance calculations using geodesic distance
- Risk tier classification based on disaster type and radius thresholds
- Scheduled background processing using APScheduler
- Lightweight JSON-based persistence for users, alerts, and cached events
- Rate-limited alert delivery to prevent notification spam

Due to rapid iteration and late-stage pivots during the hackathon, not all backend components were merged into the final team submission. This repository preserves the backend work as developed and tested during the event.

---

## 🛠️ Tech Stack

- Python
- FastAPI
- APScheduler
- Geopy (distance calculations)
- GDACS Disaster Events API
- RESTful APIs
- JSON file-based persistence

---

## 🧪 Running the Project Locally

```bash
git clone https://github.com/gasthecreator/disaster-sentinel.git
cd disaster-sentinel

python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

pip install -r requirements.txt
uvicorn main:app --reload

