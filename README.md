# Defence Logistics Management System (AI-powered predictive logistics prototype)
Fictional demo data only. Olive/khaki command-centre UI; no official insignia.

## Structure
- `frontend/index.html` – single-page UI (Leaflet + OpenStreetMap, Chart.js via CDN; needs internet for tiles/CDN)
- `backend/main.py` – FastAPI REST API (auth, CRUD, forecast, routes, weather, alerts, reports, resupply)
- `ml/forecast.py` – demand model (trend + weekly seasonality regression, weather uplift, shortage probability, explanations)
- `database/schema.sql` – 11 tables (SQLite file `database/dlms.db` auto-created and seeded on first start)
- `docs/` – API docs are auto-generated at `/docs`

## Run
```
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```
Open http://localhost:8000. Run the ML model alone: `python ml/forecast.py` (it also retrains on the DB history at every request).

## Demo credentials
admin/admin123 · officer/officer123 · viewer/viewer123 (read-only) · or click Demo Login.
Settings or Dashboard → LOAD DEMO DATA reseeds 10 locations, 30 items, 20 vehicles, 1,350 consumption records, weather and alerts.

## Key endpoints
`POST /api/login` · `/api/crud/{inventory|vehicles|locations}` · `GET /api/forecast?inventory_id=&days=` · `GET /api/resupply` · `POST /api/resupply/plan` · `POST /api/route` · `POST /api/vehicles/{id}/assign` · `GET/PUT /api/weather` · `GET /api/alerts` · `GET /api/report/{kind}` · `GET /api/analytics`

## Notes
PostgreSQL: swap `sqlite3` calls in `db()/q()/ex()` for psycopg2 and run `schema.sql` (change AUTOINCREMENT-style `INTEGER PRIMARY KEY` to `SERIAL`). Passwords use salted PBKDF2; sessions are in-memory tokens (use JWT + HTTPS in production).
