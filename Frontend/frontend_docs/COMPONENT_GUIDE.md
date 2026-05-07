# Component Guide — Quant Trading Console Frontend

This document is the authoritative reference for frontend presentation-layer patterns. All changes are local, reversible, and reviewable — no backend, API, or data model modifications are permitted.

---

## Design Tokens

Tokens live in `src/styles/index.css` under `:root`. Tailwind consumes them via `tailwind.config.js`. Never hardcode hex values for semantic colors — use the token system.

### Surface Scale (background density)
```css
--surface-1: #0b0f19  /* deepest — page background */
--surface-2: #111827  /* panels */
--surface-3: #1a2234  /* cards */
--surface-4: #1e293b  /* elevated */
--surface-5: #243447  /* highest */
```
Usage: `className="bg-surface-3"` for card backgrounds.

### Accent Scale (border/text neutral)
```css
--accent-1: #334155  /* subtle borders */
--accent-2: #475569  /* muted borders */
--accent-3: #64748b  /* secondary text */
--accent-4: #94a3b8  /* primary text */
--accent-5: #cbd5e1  /* emphasis text */
```
Usage: `className="text-accent-3"` for secondary labels, `className="border-accent-1"` for subtle dividers.

### Status Colors (locked by contract — DO NOT MODIFY)
```css
--status-healthy: #22c55e   /* green — operational */
--status-degraded: #f59e0b /* amber — degraded */
--status-down: #ef4444      /* red — down/failed */
--status-stale: #6b7280     /* gray — stale/no data */
--status-blocked: #dc2626   /* red — blocked by risk */
```
Usage: `bg-status-healthy`, `text-status-degraded`. Never invent new status semantics.

### Severity Colors (locked by contract — DO NOT MODIFY)
```css
--severity-low: #3b82f6      /* blue */
--severity-medium: #f59e0b   /* amber */
--severity-high: #f97316     /* orange */
--severity-critical: #ef4444 /* red */
```
Usage: `bg-severity-critical`, `text-severity-medium`.

### Semantic Tokens
```css
--bg-page: #111827
--bg-panel: #1f2937
--bg-card: rgba(31, 41, 55, 0.4)
--bg-input: #111827
--text-primary: #f3f4f6
--text-secondary: #e5e7eb
--text-muted: #9ca3af
--border-default: #374151
--focus-ring-color: #3b82f6
```
These are currently unused in Tailwind — apply directly via `style={{ }}` if needed for semantic consistency, or prefer token classes.

---

## Layout Components

### AppShell
Root layout wrapper. Provides sidebar, topbar, and global status ribbon. All page content renders inside `AppShell` children.

```tsx
import { AppShell } from '@/components/layout'

function MyPage() {
  return (
    <AppShell>
      <MyContent />
    </AppShell>
  )
}
```

**Sidebar collapse:** `AppShell` stores `sidebarCollapsed` state in `localStorage`. No prop needed — toggle via Topbar button.

**Mobile behavior:** On screens `< 768px`, sidebar renders as a fixed overlay drawer with a backdrop. Toggle opens/closes it. Desktop sidebar always visible (or collapsed to icon-only).

### PageHeader
Creates a consistent page header with title and optional right-side controls.

```tsx
import { PageHeader } from '@/components/layout'

<PageHeader title="System Monitor">
  <button onClick={refetch} className="...">Refresh</button>
</PageHeader>
```

### Sidebar
Navigation rail. Accepts `collapsed?: boolean` (icon-only on desktop) and `mobileOpen?: boolean` with `onCloseMobile?: () => void` for the mobile drawer.

**Adding a new nav item:**
```tsx
{
  path: '/my-page',
  label: 'My Page',
  phase: 'P0' | 'P1' | 'P2',  // phase color dot indicator
  icon: (  // SVG icon from Heroicons outline set
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path ... />
    </svg>
  ),
}
```

### Topbar
Fixed header. Left side shows toggle + page title. Right side shows:
- Env indicator (hidden on `sm:`)
- Connection status dot + label (hidden on `md:`)
- Last update timestamp (hidden on `lg:`)
- KillSwitch mini badge (always)
- HealthBadge (always)

KillSwitch badge and HealthBadge always visible — they are critical risk metadata.

---

## UI Primitives

### ConfirmDialog
Used for all destructive/strategic actions (start, stop, pause, resume, unload, kill-switch, delete, replay, budget update).

```tsx
import { ConfirmDialog } from '@/components/ui'

<ConfirmDialog
  isOpen={confirmAction !== null}
  title="Stop Strategy"
  message={<div>...impact scope...</div>}
  confirmLabel="Stop"
  cancelLabel="Cancel"
  variant="danger" // 'danger' | 'warning' | 'info'
  isLoading={isPending}
  onConfirm={handleConfirm}
  onCancel={() => setConfirmAction(null)}
/>
```

Dialog animates in via `animate-dialog-enter` (scale 0.95→1, opacity 0→1, 75ms).
Escape key closes. Focus returns to cancel button on open.

### EmptyState
For lists/tables with no data. Shows title, message, and optional action button.
```tsx
<EmptyState
  title="No Audit Entries"
  message="No audit entries match the current filters."
  action={{ label: 'Clear Filters', onClick: clearFilters }}
/>
```

### ErrorState
For initial load failures. Shows title, message, and retry button.
```tsx
<ErrorState
  title="Failed to load strategies"
  message={formatAPIError(error)}
  onRetry={refetch}
/>
```

### LoadingState
For initial data loads. Shows spinner and message.
```tsx
<LoadingState message="Loading strategies..." />
```

### LoadingSpinner
Standalone spinner for inline use.
```tsx
import { LoadingSpinner } from '@/components/ui'
<LoadingSpinner size="md" />  // 'sm' | 'md' | 'lg'
```

### StatusBadge
Four badge types — use the correct one:
- `HealthBadge` — system health (healthy/degraded/stale/down)
- `AdapterStatusBadge` — adapter-level health
- `SeverityBadge` — alert severity (low/medium/high/critical)
- `KillSwitchBadge` — KillSwitch level (0–3)

All are pure presentational. Memoized — safe to use in lists without re-render concern.

---

## Accessibility Patterns

### Input aria-label
Every `<input>`, `<select>`, and `<textarea>` without a visible `<label>` must have `aria-label`.

**Pattern — no visible label:**
```tsx
<input
  type="text"
  value={strategyId}
  onChange={e => setStrategyId(e.target.value)}
  aria-label="Strategy ID"
  placeholder="strategy_id"
  className="..."
/>
```

**Pattern — wrapped in label:**
```tsx
<label className="block">
  <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Strategy ID</span>
  <input ... />
</label>
```
`aria-label` not needed when `<label>` wraps the input — the text content serves as the accessible name.

### Table scope
All `<th>` cells must have `scope="col"`.

```tsx
<thead>
  <tr>
    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-accent-3">Name</th>
    ...
  </tr>
</thead>
```

### Focus ring
The global `*:focus-visible` rule applies a `2px + 4px` double ring in blue. Do not override with `outline-none` without providing equivalent focus feedback.

### Keyboard navigation
- Tab reaches all interactive elements
- Enter/Space activates buttons
- Escape closes dialogs
- Arrow keys navigate within select/tab groups (native behavior)

### Reduced motion
`@media (prefers-reduced-motion: reduce)` in `index.css` disables all animations. Components must not rely on animation for correctness.

---

## Responsive Patterns

### Table horizontal scroll
Every table must be wrapped in `overflow-x-auto` to enable horizontal scroll on narrow viewports.

```tsx
<div className="overflow-x-auto">
  <table className="min-w-full ...">
    ...
  </table>
</div>
```

This is a no-op on wide screens. On narrow screens (< 768px) tables horizontally scroll without breaking layout.

### Sidebar mobile drawer
On `md:` and above: sidebar is static (or collapsed to icon-only). On smaller screens: sidebar is a fixed overlay opened/closed via Topbar toggle button.

```tsx
// In AppShell/Sidebar — mobile overlay pattern
{mobileOpen && (
  <div
    className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm md:hidden"
    onClick={onCloseMobile}
    aria-hidden="true"
  />
)}
<aside className="fixed ... -translate-x-full md:translate-x-0 ...">
```

### Topbar progressive hiding
Use `hidden sm:` / `hidden md:` / `hidden lg:` to progressively hide secondary metadata.
KillSwitch badge and HealthBadge always visible.

```tsx
<span className="hidden sm:inline-flex ...">env</span>
<span className="hidden md:inline-flex ...">connection</span>
<span className="hidden lg:inline-flex ...">last update</span>
```

### Grid breakpoints
Use Tailwind breakpoints for responsive layouts:
- `sm:` (640px) — 2 columns
- `md:` (768px) — 3–4 columns
- `lg:` (1024px) — 5+ columns

---

## Performance Patterns

### React.memo on pure leaf components
Use `React.memo` on components that:
- Receive stable props (not frequently changing state)
- Render the same output for the same props
- Are used in lists (renders frequently without structural change)

**Already memoized:**
- `HealthBadge`, `AdapterStatusBadge`, `SeverityBadge`, `KillSwitchBadge` — memoized in `StatusBadge.tsx`
- `SessionListItem` — memoized in `Chat.tsx`
- `MessageBubble` — memoized in `Chat.tsx`

**Do NOT memoize:**
- Page-level components that receive TanStack Query state (queryClient updates invalidate cached data frequently — memo provides no benefit and adds overhead)
- Components that own `useEffect` / `useState` for frequently-changing data (Monitor, CryptoRiskOps)

### Suspense boundary
`<Suspense>` wraps all routes in `App.tsx` with a `PageFallback` loading spinner. This enables React's built-in loading state for code-split chunks.

### No virtual scrolling
The largest lists are ~200 items (Audit, Replay). This is well within React's rendering performance envelope. Virtual scrolling adds complexity and is not warranted.

### No heavy frameworks
No animation libraries (Framer Motion), no state management libraries (Redux, Zustand), no CSS-in-JS. Only: React 18 + Tailwind CSS + TanStack Query + React Router.

---

## Do's and Don'ts

### DO
- Use `surface-*`, `accent-*`, `status-*`, `severity-*` token classes for all semantic colors
- Wrap every table in `overflow-x-auto`
- Add `aria-label` to every input without a visible label
- Add `scope="col"` to all `<th>` elements
- Preserve all confirmation dialogs with full impact scope and success/failure feedback
- Keep diagnostic info visible: `request_id`, `blocked_reason`, `drift_type`, `KillSwitch level`, `stale timestamp`
- Keep all 7 risk semantics: loading/empty/error/stale/degraded/blocked/killed/halted/reconciling/drifted/pending/approved/rejected
- Use `React.memo` on pure presentational components in lists
- Apply `animate-page-enter` to route containers
- Apply `table-row-hover` to table row `className`

### DON'T
- Hardcode hex color values for semantic purposes
- Modify backend, API endpoints, DTOs, or TanStack Query semantics
- Simplify risk control plane semantics to plain text
- Remove confirmation dialogs or reduce their information content
- Hide request_id, blocked_reason, drift type, KillSwitch level, or stale timestamp
- Add virtual scrolling, animation libraries, or heavy state management
- Use `outline-none` without replacing the focus ring with equivalent feedback
- Override `status-*` or `severity-*` tokens with ad-hoc colors

---

## CSS Utility Reference

| Class | Purpose |
|-------|---------|
| `.surface-1` – `.surface-5` | Background density scale |
| `.accent-1` – `.accent-5` | Border/text neutral scale |
| `.status-healthy`, `down`, `degraded`, `stale`, `blocked` | Status semantics |
| `.severity-low`, `medium`, `high`, `critical` | Severity semantics |
| `.card-hover` | Subtle shadow lift on hover |
| `.table-row-hover` | Row background on hover |
| `.badge` | Pill-shaped badge base |
| `.dialog-backdrop` | Modal backdrop overlay |
| `.dialog-panel` | Centered modal panel |
| `.animate-page-enter` | Page fade-slide transition |
| `.animate-dialog-enter` | Dialog scale-in transition |
| `.status-pulse` | Stale indicator pulse animation |
| `.btn-press` | Button press micro-interaction |