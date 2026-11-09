# Cinemack website audit — Mon 9 Nov 2026

Method: the committed code (git 5a8e4f6) run locally in test mode (`MOVIE_BUFF_MOCK=1`, no GPU, no facts.db, so
posters fall back to coloured placeholders), driven with Playwright at 375, 768 and 1440 px: home, an answered
question, filters, saved chats, sign-in, keyboard focus. Screenshots: `docs/audit/`. References: the two images the
user sent (a dark, poster-led desktop board with genre chips and rails; dark phone screens with a bottom tab bar,
library rails and a wishlist list).

## What exists (keep)
- FastAPI server (`web/server.py`) + one page (`web/index.html`, 150 KB, inline CSS + one vanilla-JS script, no build
  step). Streaming answers (SSE), film cards with taste buttons, film panel (`/api/film/{id}`), saved chats, accounts,
  settings + onboarding, memory notepad, filter bar (genre / era / runtime / certificate / services), Letterboxd import,
  How it works / About pages. Light theme with a `prefers-color-scheme: dark` variant.
- Working and worth keeping: the streaming chat, "films found" cards tied to the answer, "Not the one you meant?"
  alternatives, visible keyboard focus rings, reduced-motion handling, private chats, the filter bar's code-enforced
  limits, honest fine print.

## Problems, by priority
| # | Problem | Evidence | Severity |
|---|---|---|---|
| 1 | Horizontal overflow: the header does not fit at tablet and phone widths | 768 px: page 126 px wider than the screen (blank strip on the right); 375 px: 14 px, "New chat" cut off | High |
| 2 | Chat is the only thing on the home page: no posters, no watchlist, no "for you", no coming soon | home_1440 / home_375 | High |
| 3 | Mobile header takes two rows, with developer toggles (Reasoning, Details) and a status pill in prime space | home_375 | High |
| 4 | Small tap targets | 11 of 23 controls under 44 px at 375 px (taste buttons, toggles, links) | High |
| 5 | Bottom composer + fine print cover ~190 px of a 812 px phone; the welcome-back card collides with "Personalise" | chat_375, focus_1440 | Medium |
| 6 | Visual identity: cream background, cartoon popcorn/curtain illustration, purple-pink gradients - friendly but not the premium, poster-led look of the references | all | Medium |
| 7 | Film cards: four taste buttons + "Details" per card crowd mobile; cards scroll sideways inside the chat on phones | chat_375 | Medium |
| 8 | No watchlist page, profile page, history, friends, calendar or "Pick tonight" - the API has `/api/watchlist` and `/api/taste` but nothing shows them | routes list | Medium |
| 9 | Saved chats open as a full-height empty drawer on phones; the empty state gives no next step | chats_375 | Low |
| 10 | One 150 KB file holds every style and script: hard to reuse components or test | index.html | Low (maintainability) |

No console errors or page errors were logged in any flow (Playwright).

## Direction (from the references, without copying them)
- Dark, near-black base with layered surfaces; posters and backdrops carry the colour; one accent.
- Desktop: top bar (logo, Home, Discover, Pick tonight, Watchlist, Calendar, search, profile). Phone: bottom tab bar
  (Home, Discover, Pick tonight, Watchlist, Profile) with Ask Cinemack reachable from every screen.
- Home = useful rails from real data only: Continue (last chats), For you (with a reason), Your watchlist,
  Coming soon, New and trending; genre chips; empty states that say how to fill them.
- Detail page with backdrop, poster, facts, the four actions, where to watch, cast, similar films, Ask about this film.
- Keep the old page reachable (`/classic`) until the new one passes the success criteria.
