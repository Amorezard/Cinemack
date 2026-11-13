# Cinemack UX and motion system — screenshot-inspired specification

**Reference:** `reference/UX_20_PRINCIPLES_SCREENSHOT.jpeg` contains the following twenty UX and animation principles. It is not a screenshot of Cinemack, and does not specify an exact visual design to copy.

## Guiding rule

Polish should improve the clarity and *perceived responsiveness* of Home, Chat, Pick Tonight, Calendar, Watchlist and Profile while avoiding distractions, delays, motion sickness and layout instability. Reuse the previously approved page designs; change components where there is a concrete usability issue.

| # | Principle | Target Cinemack interaction | Must not do |
|---|---|---|---|
| 1 | Squash & stretch | Subtle button/favorite/watchlist confirmation | Bounce every poster continuously |
| 2 | Anticipation wind-up | Short, clear preparation for Pick Tonight reveal | Add latency to a fast operation |
| 3 | Staging focus | Clear best movie card, highlighted selected day, readable result | Competing flashes or hidden focus |
| 4 | Pose to pose | Defined states for modal, chip, card and panel transitions | Unpredictable layout jumps |
| 5 | Follow through | Stable settling of expandable detail panels | Excessive overshoot |
| 6 | Slow in/slow out | Consistent easing on panels, carousels and week transitions | Long easing that blocks input |
| 7 | Moving in arcs | Optional subtle spatial transition when conveying direction | Gratuitous spinning/arc motion |
| 8 | Secondary action | Supporting icon and shade changes during main state change | Competing simultaneous animations |
| 9 | Snappy timing | Instant chip/button reaction and skeleton/queue state | Hidden processing without feedback |
| 10 | Exaggerated feedback | Visible save/error and unavailable-state confirmation | Dramatic success/confetti on every action |
| 11 | Solid drawing in 3D | Consistent card depth if using perspective | Unreadable backfaces / 3D maze |
| 12 | Overall appeal | Coherent brand, typography, poster ratios, hierarchy | One-off inconsistent styling |
| 13 | Fitts's law | Larger frequent playback/save and carousel controls | Tiny far-apart targets |
| 14 | Bigger hit areas | Comfortable touch/click targets for arrows/chips/week controls | Visual controls with inaccessible bounds |
| 15 | Hick's law | Pick Tonight progressive disclosure and sensible defaults | Asking 10 questions before a movie choice |
| 16 | Miller's law | Group filters and detail info into meaningful sections | Rigid 7±2 rule presented as empirical magic |
| 17 | Doherty threshold | Respond quickly with local state/queue/progress | Treat 400ms as a universal benchmark for AI replies |
| 18 | Postel's law | Accept sensible title variants and spelling fuzziness at input boundary | Tolerating invalid internal JSON/security filters |
| 19 | Jakob's law | Familiar movie posters, rows, profile/watchlist progress | Copy another brand's protected layout/artwork |
| 20 | Frequency of use | One-click access to Watchlist, Pick Tonight, search | Prioritize obscure settings above everyday tasks |

## Proposed token defaults (tune on actual devices)

- `motion.instant`: 100 ms, keypress/button.
- `motion.select`: 150 ms, genre chips/checkmarks.
- `motion.card`: 220 ms, cards and menus.
- `motion.panel`: 300 ms, a major panel or week view.
- `motion.curve`: ease-out for entrances; ease-in-out for symmetric reversible motion; avoid unrelated bespoke curves.
- Reduce or disable nonessential transforms when `prefers-reduced-motion: reduce` is set; no auto-scrolling that cannot be paused.
- Prefer `transform` and `opacity`, avoiding expensive layout transitions and jank. Use JS state and CSS classes, not imperative canvas effects, unless justified.

## Per-page acceptance behaviors

**Home**
- Posters reserve a stable aspect-ratio box before images arrive; no content shifts.
- Multi-genre Any/All filters show selection immediately; result requests cancel/ignore stale responses and maintain stable deterministic order.
- Card actions and carousel controls have visible focus and appropriately sized hit areas.
- Avoid replacing sections with seemingly random titles when the user chooses all genres.

**Chat**
- Request submission is immediately acknowledged; show queue/generating/cancelled/error states and permit cancellation when feasible.
- Source-backed facts and movie cards have stable entity IDs and readable references. Stream should not jump scroll unexpectedly when the user is reading older content.
- No untrusted answer HTML injection.

**Pick Tonight**
- Brief optional mood/watching/time form; reveal the most suitable choice clearly.
- Exactly one 'Why I Picked This' element **per recommendation**; reasons derived from structured ranking output.
- If reroll is requested, keep original filters; disclose if no eligible unseen movies remain.
- Group mode should explain tradeoffs without exposing private profiles unprompted.

**Calendar**
- Preserve week/month, timezone, theatrical/digital/TV episodes/finale, followed-shows.
- Maintain chosen day when changing display mode, highlighted today, distinguish confirmed/tentative dates, consistent poster sizing.
- Directional transitions should preserve date context; mobile touch interactions and keyboard navigation tested.

**Watchlist/Profile**
- Optimistic save/unsave with rollback on failure, accessible labels, persistent progress after refresh.
- Clearly group continue watching, planned, upcoming, completed; avoid implying watched == loved.
- Sensitive friend/history/profile controls must retain privacy boundaries.

## Quality checks

- WCAG 2.2 AA targeted; visible focus, keyboard, screen readers, contrast, reduced motion and mobile 320px+ where feasible.
- Touch target design preference ~44×44 CSS px, acknowledging exact accessibility standard exceptions; check actual WCAG compliance.
- Core Web Vitals targets LCP ≤2.5 sec, INP ≤200ms, CLS ≤0.1 at p75 on sufficiently representative traffic, not fake local-only claims.
- Use `tools/accept_redesign.js` and current frontend fixture tooling; extend rather than replace existing tests.
- Screenshot/video before-after for changed pages, with measured page sizes and visual regression output where available.
