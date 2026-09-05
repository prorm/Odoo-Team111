# Demo Script Builder

## Why this matters
Judges watch dozens of demos back to back. A tight, rehearsed 5-7 minute walkthrough that explicitly names your architectural decisions scores higher than a longer, meandering tour of every feature. This skill turns your finished feature list into an exact script — what to click, what to say, and what to do if something breaks live.

## Architecture of a winning demo
```
0:00–0:30   Problem framing
0:30–1:30   Architecture in one breath
1:30–4:30   Live walkthrough across roles
4:30–5:15   The "break it" moment (required)
5:15–6:30   Nice-to-haves, rapid-fire
6:30–7:00   Close
```
Every second maps to a judging criterion — must-have or nice-to-have. Nothing else survives the cut.

## Step-by-step script template

**0:00–0:30 — Problem framing**
> "[Product name] solves [specific problem] for [specific user]. Judges already know the brief, so we won't re-explain it — let's go straight to the system."

**0:30–1:30 — Architecture in one breath**
> "We're running React, Node/Express, and PostgreSQL with Prisma. The one decision we're proudest of: booking conflicts are prevented with a Postgres exclusion constraint — not an if-check in code, an actual database-level guarantee that holds even under concurrent requests."

Show the schema/constraint on screen for 5 seconds — a single SQL snippet, not a wall of code.

**1:30–4:30 — Live walkthrough across roles**
Exact click sequence (fill in with your real screens):
1. Log in as **User** (tab 1, already open) → perform the core action (e.g. "Book Court 3, 6-7pm") → submit
2. Switch to **Admin** (tab 2, already open, pre-logged-in) → show the booking appear live with no refresh
3. As Admin, approve/reject → switch back to tab 1 → show the User's view update live

Say explicitly: "Notice neither tab was refreshed — that's a websocket event fired from our service layer the moment the database transaction commits."

**4:30–5:15 — The "break it" moment (do not skip)**
> "Now let's try to break it — I'll attempt to book this exact same slot again from a second tab."

Submit the conflicting request on screen → show the clean `409` error message, not a crash or stack trace.
> "That's not a client-side check — the database itself is refusing the second insert."

Script this word-for-word; it's the highest-leverage 45 seconds of the whole demo, don't improvise it live.

**5:15–6:30 — Nice-to-haves, rapid-fire (10-15 sec each)**
Only include what's actually built:
- "We added rate limiting on the login endpoint — 5 attempts per minute, backed by Redis."
- "Reports export to PDF and Excel from this button — generated server-side with Puppeteer/ExcelJS."
- "Every state change is captured in an append-only audit log — here's the full history of this one booking."
- "If offline, mutations queue locally and sync automatically on reconnect — [show the offline banner]."

**6:30–7:00 — Close**
> "With more time we'd add [one honest, specific thing — not a vague 'more features']. Thanks."

## Judge Q&A — common questions and how to answer them
- **"What happens if two people click at the exact same time?"** → Point to the exclusion constraint; offer to demo it live if not already shown.
- **"Why Postgres over Mongo?"** → "We needed relational integrity and native support for exclusion constraints — Mongo would've pushed that logic into application code, which is a weaker guarantee."
- **"How do you handle auth?"** → "JWT access tokens, 15-minute expiry, rotated refresh tokens in httpOnly cookies, role checked server-side on every protected route."
- **"Is this actually deployed / would this scale?"** → Be honest. "It's hackathon-scoped, but the constraint layer, rate limiting, and audit log are genuinely production patterns, not shortcuts."

## Fallback plans
- **If the live demo breaks**: have a 30-second pre-recorded screen capture of the exact same flow ready as backup — don't try to debug live in front of judges.
- **If websockets misbehave**: flip the `REALTIME_MODE=poll` feature flag (see WebSocket skill) before presenting — a 4-second poll delay is invisible in a demo, a broken live update isn't.
- **If wifi is unreliable at the venue**: run the whole stack locally (`localhost`) rather than depending on a deployed URL.

## Rules for a strong demo
- Every screen already open in tabs before recording/presenting starts — no live navigation to bookmarks.
- Rehearse the "break it" line word-for-word at least twice before presenting.
- Cut anything that doesn't map to a judging criterion — a nice animation nobody asked about wastes seconds you don't have.
- Record 2-3 takes if submitting a video; use the one where the live demo doesn't stutter.
- Time yourself with a stopwatch during rehearsal — 7 minutes goes faster than it sounds.

## Checklist before presenting
- [ ] All tabs/accounts pre-logged-in and pre-opened in the right order
- [ ] The "break it" moment rehearsed word-for-word
- [ ] Fallback recording ready in case of live failure
- [ ] Realtime feature flag set to whichever mode is more reliable on venue wifi
- [ ] Script fits comfortably in 7 minutes with rehearsal, not just on paper

## Common mistakes to avoid
- Narrating code instead of the product — judges want to see it work, not read your source live.
- Skipping the "break it" moment because it feels risky — it's explicitly what judges are told to look for; skipping it looks like something to hide.
- Running over time — a strong 6-minute demo beats an unfinished 8-minute one when judges cut you off.

## Integration with the rest of the stack
Pulls its strongest beats directly from the **State Machine + Constraint Generator** (break-it moment), **WebSocket Real-Time Sync** (live cross-role update), and whichever of the enterprise skills (Reporting, Audit Log, Rate Limiting, Offline-First) your team actually finished — only script what's real.
