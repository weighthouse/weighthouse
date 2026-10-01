# Development

## Backend

Python 3.13 is the baseline; the CI image and the production image both use it.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH="$PWD/backend" MODEL_STORAGE="$PWD/models" DATA_DIR="$PWD/data" \
  .venv/bin/uvicorn app.main:app --app-dir backend --reload --port 7860
```

## Frontend

Node.js 22 or newer; the lockfile is maintained with npm 10.9.8. The Vite dev server proxies `/api` to port 7860.

```bash
cd frontend
npm install
npm run dev
```

## Tests and build

```bash
PYTHONPATH="$PWD/backend" .venv/bin/pytest backend/tests
cd frontend && npm test && npm run build
```

CI and the published image are maintained by the workflows in `.github/workflows/`.
