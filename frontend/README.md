# Docflow frontend

Next.js review UI: PDF on the left, extracted fields on the right. Clicking a field
highlights its source quote in the PDF; edited values are saved as a human review
(`PATCH /extractions/{id}`), which re-runs the template rules.

```bash
npm install
npm run dev          # http://localhost:3000, expects the backend on :8000
```

`NEXT_PUBLIC_API_URL` overrides the backend URL. Checks: `npm run lint`, `npx tsc --noEmit`, `npm run build`.

**Public demo** (`NEXT_PUBLIC_DEMO=1`, deployed on Vercel): no backend; the API layer
(`src/lib/demo.ts`) serves `public/demo/data.json`, real model output precomputed by
`backend/scripts/export_demo.py`. Uploads, extraction and edits are disabled there.
Deploy: `vercel --prod` from this folder.

