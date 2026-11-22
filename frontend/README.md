# SearchFlow UI

React 19 islands preserve the existing request/auth/idempotency/download handlers.
The active presentation system is **shadcn/ui neutral + Vercel AI Elements Task**.
Lucide supplies the React icons. React Aria supplies only accessible table collection
and keyboard behavior. All assets compile locally; no runtime CDN.

## Build

```sh
npm ci --prefix frontend
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

The build writes JS, CSS, notices and the exact source-input inventory to
`realtime/assets/component-ui*`. `realtime/component_ui.py` embeds the assets.
Production is a static HTML artifact; do not overwrite production `web.py` or
`serp_page.py` from this checkout. Regeneration must include these assets and hook.

## Sources and adaptations

- shadcn-ui/ui (MIT), commit `dd41d19e2f80ff6891951f7ced74c2c9a650b065`:
  actual Button, Badge, Card, Tabs, Collapsible source in `src/vendor/shadcn`.
  Imports are adjusted to local `cn`; business adapters are in `components.tsx`.
- vercel/ai-elements (Apache-2.0), commit
  `ca8be600bfd01b3131f9bf6134b51602a71f4374`: actual Task source in
  `src/vendor/ai-elements`; imports adjusted and the default trigger changed to a
  native button for keyboard access. Used for real request status, stages and queue.
- No AI backend, model calls, fictitious reasoning or token counters are added.
- BoardUI, Solid Glass and AffectUI source directories remain as prior-version
  references. They and RemixIcon are absent from the active runtime bundle,
  confirmed by `component-ui-inputs.json`; their previous documentation is historical.

The Maps bridge only publishes public display fields, preserving null/0, and clears
prior results on a new submission. All legacy business forms and workflow IDs stay
in place; the rest of the workbench shares the neutral CSS tokens.
