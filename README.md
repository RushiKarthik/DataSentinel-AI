# DataSentinel AI

A local CSV data-quality dashboard powered by FastAPI, Pandas, and the existing deterministic quality engine.

## Run locally

Start the API first in PowerShell:

```powershell
cd "C:\Users\rushi\OneDrive\Desktop\AIML PROJECT\DatasentinelAI\backend"
.\venv313\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, start the frontend:

```powershell
cd "C:\Users\rushi\OneDrive\Desktop\AIML PROJECT\DatasentinelAI\frontend"
npm run dev
```

Open **http://localhost:5173**. The Vite development server forwards `/api/*` requests to `http://127.0.0.1:8000`, keeping the browser and API on one origin. The backend must remain running while you use the dashboard.

Check the API directly at **http://127.0.0.1:8000/api/health**. Interactive API documentation is at **http://127.0.0.1:8000/docs**.

## Verify

Backend tests:

```powershell
cd "C:\Users\rushi\OneDrive\Desktop\AIML PROJECT\DatasentinelAI\backend"
.\venv313\Scripts\python.exe -m pytest tests -q
```

Frontend production build:

```powershell
cd "C:\Users\rushi\OneDrive\Desktop\AIML PROJECT\DatasentinelAI\frontend"
npm run build
```

## API endpoints used by the dashboard

- `GET /api/health` — connection status
- `POST /api/upload` — analyze a multipart CSV upload (form field: `file`)
- `POST /api/analyze-sample` — analyze the built-in demonstration dataset

The frontend uses same-origin `/api` requests in development. Set `VITE_API_BASE_URL` only when the frontend and API are hosted separately. To change the development proxy target, set `VITE_API_PROXY_TARGET` in a frontend environment file.

Uploaded data and results are stored in memory for the running API process and are cleared when it restarts.
