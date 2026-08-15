# Frontend

React + Tailwind. Two zones, one owner each.

```
src/
├── api.js                  agreed in hour 1, then frozen — both zones import from here
├── zones/
│   ├── Domestic.jsx        OWNER: frontend-domestic
│   └── International.jsx   OWNER: frontend-intl
└── components/             prefix by zone: Dom*, Intl*, or Shared*
```

Don't edit the other zone's files. Shared component styles get agreed once at the start so the
two halves don't look like different products.

Palette: deep blues, crisp whites, slate grays. Clinical and trust-building, not playful.

Backend base URL goes in `api.js` as a single constant so we can point at localhost or the
deployed URL by changing one line.

## Handoff between zones

Zone A passes to Zone B:

```js
{ session_id, procedure_name, user_deductible, acknowledged_international_risk: true }
```
