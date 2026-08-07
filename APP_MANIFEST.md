# App repo manifest — `.dashboard-app.json`

The dashboard's **Apps** tab pulls objective status (review state, TestFlight,
screenshots/metadata) from **App Store Connect** automatically. It cannot know
your *plans*, though — so each app repo may carry a small `.dashboard-app.json`
at its root with planning/intent fields the dashboard renders on the app card.

All fields are optional; include only what's useful.

| Field | Type | Shown as |
|-------|------|----------|
| `tagline` | string | Subtitle under the app name (replaces the bundle id) |
| `priority` | `"high"` \| `"medium"` \| `"low"` | A pill; also sorts higher-priority apps first |
| `target` | `"YYYY-MM"` or `"YYYY-MM-DD"` | 🎯 target date with a live countdown ("~8 wks left", "overdue") |
| `next_step` | string | The single next concrete action to ship (green "→" line) |
| `blocked_on` | string | What's blocking progress (amber "⚠" line) |
| `bundle_id` | string | Overrides the `.xcodeproj` bundle id for ASC lookup (use if they differ) |

### Example

```json
{
  "tagline": "Ice-time queue manager for rinks",
  "priority": "high",
  "target": "2026-09",
  "next_step": "Record 3 iPhone screenshots, then submit for review",
  "blocked_on": "waiting on final icon from designer"
}
```

Do **not** track objective status here (review state, whether screenshots exist,
etc.) — that comes from App Store Connect. Keep this to planning fields only.

---

## Snippet to paste into each app repo's `CLAUDE.md`

```markdown
## Dashboard manifest (`.dashboard-app.json`)

This repo is tracked by the Vibe Dashboard's Apps tab. Keep a
`.dashboard-app.json` file at the repo root current so the dashboard shows
accurate planning info, and update it whenever the plan changes — a new next
step, the target shifts, priority changes, or it becomes blocked/unblocked.

Optional fields: `tagline` (one-line description), `priority`
("high"/"medium"/"low"), `target` (ship date "YYYY-MM" or "YYYY-MM-DD"),
`next_step` (the single next action to ship), `blocked_on` (what's blocking),
`bundle_id` (only if the .xcodeproj bundle id differs from the App Store
Connect registration).

Objective status — review state, TestFlight, screenshots/metadata — comes from
App Store Connect automatically; do not track those here.
```
