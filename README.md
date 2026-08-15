# MedMap

Compare what a surgery costs in the US against a fully bundled international medical travel package — procedure, flights, lodging, and complication insurance in a single checkout.

Built for the NYC hackathon, E-commerce track.

## Why

Medical tourism is cheaper by 60–80% but most people won't book it, because the risk of a complication 5,000 miles from home isn't insurable at the point of sale. MedMap puts the complication coverage in the cart.

## Structure

```
backend/    FastAPI — pricing, bundling, warranty engine
frontend/   React + Tailwind — Zone A (domestic), Zone B (international + checkout)
API.md      the contract between them
PLAN.md     build plan, lanes, schedule
```

## Running

```bash
# backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# frontend
cd frontend
npm install
npm run dev
```

Backend serves stub data until real datasets land, so the frontend is never blocked.
