# Cinemack v8 design system (9 Nov 2026; Merrimack colours 15:30; revised 19:00 after the user's review)

## Revision 19:00 (user: fonts and sizes not liked; bright yellow on dark blue hard to look at; overlapping posters)
- Type: Archivo (headings, 600-700) + Source Sans 3 (text) replace Libre Caslon + Montserrat; scale cut to
  15-16 / 19-22 / 26-34 / hero 30-44.
- Colour: background calmed to ink-navy #0D1B2A; buttons and selected chips are blue #2F64A3 with white text
  (hover #3A72B5); gold appears only as thin lines, dots, outlines, step-number rings, dimmed bulbs and the badge.
  Links use a softer gold #F0D66E on dark and Merrimack Blue on light.
- Hero: one poster at a time, stacked on one spot, cross-fading every 7 s (1.2 s fade); backdrop and "In theatres now"
  follow; pauses on hover/focus; dots to jump; still under reduced motion. Replaces the overlapping fan.
- Every control has a hover state (180 ms) and a press (scale .97, 140 ms); selected chips show a check mark.
- Chats: a Chats button in the top bar opens a side panel (new chat, previous chats with time, open, delete); the home
  banner offers "Continue: <last chat>".
- Watchlist page = a plan: Watch next (with reasons), Just landed, Last chance in theatres (estimate), Watch together
  (shared lists, taste match, friend loved), Everything (filters). No "leaving streaming" section: no source exists.

The sections below describe the 15:30 version; where they disagree, the revision above wins.


## Idea
A college cinema on a Friday night. The room is Merrimack Blue with the lights down; Merrimack Gold marks what you can
act on, the way the marquee bulbs and the exit signs do in a real theatre. The one bold moment is the home hero: a
marquee board with a row of gold bulbs top and bottom that chase once when the page loads. Everything else stays quiet,
and the posters supply the colour.

Rejected: the first v8 pass (charcoal + tungsten amber) was well-behaved but read as any streaming site; a red accent
(streaming-service clone); gradients as decoration; stock photography (none is used anywhere).

## Colour (from Merrimack's visual identity guidelines)
| Token | Dark (default) | Light | Use |
|---|---|---|---|
| --blue | #003767 Merrimack Blue | same | top bar, user chat bubbles, light-theme buttons |
| --gold | #FFDD00 Merrimack Gold | same | dark-theme buttons, bulbs, badges, selected chips, on-your-service tags, hover edge on posters |
| --gray | #82868A Merrimack Gray | same | reserved for rules and borders (3.7:1 on white, so never small text) |
| --bg / --s1 / --s2 / --s3 | #061A30 / #0B2442 / #12305A / #1A3D6E | #F4F6F9 / #FFF / #E8EDF3 / #DCE3EC | page and surfaces (navy family in dark) |
| --text / --muted / --faint | #F4F6FA / #B9C5D8 / #9AAAC2 | #0B1F35 / #4A5568 / #5B6472 | text levels |
| --primary / --primary-ink | gold / #001B33 | blue / #FFF | primary buttons and selected states |

Lowest text contrast: faint on the raised surface, 4.6:1 dark and 5.1:1 light. Navy text on gold: 12.9:1. White on
Merrimack Blue: 12.1:1.

## Type
Merrimack's typefaces are Gotham (primary) and Adobe Caslon (secondary); both are licensed, so the site uses the
closest free faces: **Libre Caslon Text** for headings (700, and the hero) and **Montserrat** for everything else
(400 to 700). Scale 12 / 14 / 15.5 / 19 / 22 / 28 / 32 / 44 / 54. Sentence case; no all-caps labels.

## Layout and breakpoints
Phone < 600: one column, 124 px posters, bottom tab bar. Tablet 600-1023: 148 px posters, sheets become centred panels.
Desktop >= 1024: top navigation, 160 px posters, hero gets a fan of three real now-playing posters on the right,
film pages get a side column for where-to-watch. Spacing 4 / 8 / 12 / 16 / 24 / 32 / 48; 16 px page gutter on phones,
32 px on desktop.

## Navigation
Top bar: Merrimack Blue band with a gold hairline; the current page is marked by a gold underline that slides in.
Phone tab bar: Home, Discover, **Pick tonight** (a raised gold ticket in the middle, with notched sides), Watchlist,
Profile; the current tab gets a gold dot above its icon.

## Motion (each answers something; all of it stops for prefers-reduced-motion)
Marquee bulbs chase once on load. Poster lifts 4 px with a gold edge on hover (pointer devices only). The bookmark pops
when a film is saved. Buttons and chips give way 1 px when pressed. The nav underline slides. Skeletons shimmer while
loading.

## Human touches
Hand-drawn line illustrations in the brand colours for empty states (popcorn: watchlist; ticket: history; reel:
calendar). A light film-grain texture on the hero only. Copy in Cinemack's own voice ("Describe the scene you
half-remember...", "Back on the big screen", "What everyone's watching").

## Home tells the evening in order
Pick up where you left off, Your list, On the big screen now, Because you liked..., Opening soon, What everyone's
watching, Back on the big screen. Each row has a serif title, one plain line saying what it is, and its data source and
date. Pick tonight numbers its five questions because they are a sequence.
