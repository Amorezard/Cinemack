# Cinemack v8 design system (9 Nov 2026)

## Idea
A cinema at night: a charcoal room, the posters are the colour, and one warm light - the projector's tungsten amber -
marks what you can act on. The single bold moment is the home hero: tonight's film backdrop lit from one side by that
amber falloff, with the Ask box and Pick tonight sitting in the light. Everything else is quiet: flat surfaces, no
glass, no glow, no gradients except the hero light and poster scrims.

Rejected while planning: a red accent (reads as a streaming-service clone, and the references use it), cream
background (the current site; also a generic default), a neon / acid accent on black (generic), glassmorphism.

## Tokens (app.css :root)
| Token | Dark | Light | Use |
|---|---|---|---|
| --bg | #121316 | #F3F4F6 | page |
| --s1 / --s2 / --s3 | #1A1B1F / #222428 / #2C2E33 | #FFFFFF / #ECEDF0 / #E1E3E8 | surfaces, raised, pressed |
| --line | #34373D | #D3D6DC | dividers, outlines |
| --text / --muted / --faint | #EDEBE7 / #A6A4A0 / #93918D | #15161A / #565962 / #62656D | text levels (all >= 4.5:1 on --bg) |
| --accent | #F4B740 | #F4B740 | primary buttons, selected states (fill) |
| --accent-ink | #1C1404 | #1C1404 | text on accent |
| --accent-text | #F4B740 | #8A5A00 | accent used as text / links |
| --ok / --bad | #5CC28B / #F07167 | #1E7A4C / #B42318 | state, always with text or icon |
Radii: 6 (chips, inputs), 10 (cards, posters), 16 (sheets). One shadow, only on floating layers (sheets, toasts).
Spacing: 4, 8, 12, 16, 24, 32, 48. Breakpoints: 600 (tablet), 1024 (desktop).

## Type
Geist (Google Fonts) 400/500/600/700, system fallback. Scale 12 / 14 / 16 / 18 / 22 / 28 / 40, line-height 1.5 body,
1.15 headings, tracking -0.01em on 28+. Sentence case everywhere; no all-caps labels.

## Navigation
Desktop >= 1024: top bar (logo, Home, Discover, Pick tonight, Watchlist, Calendar | Ask field, alerts bell, profile).
Phone and tablet: slim top bar (logo, bell, profile) + bottom tab bar (Home, Discover, Pick tonight, Watchlist, Profile),
labels always shown, 56 px + safe-area inset. Calendar is a rail on Home and a link in Profile and the top bar.

## Components
Button (primary / secondary / ghost / icon), chip (toggle, aria-pressed), poster card (2:3, fixed size, lazy, bookmark
toggle), rail (horizontal scroll, snap, keyboard-scrollable, heading + "See all"), sheet (<dialog>: bottom sheet on
phones, centred panel on desktop; focus trapped, Esc closes), tabs (segmented, role=tablist), stars (radiogroup),
toast (role=status), skeleton (poster and line), empty state (one line + one action), chat message, film strip.
