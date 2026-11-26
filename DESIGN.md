---
name: "Agent Teams Board"
description: "A calm site office for the owner of a team of AI specialists, who wants the state of the work and never the machinery."
colors:
  # Light theme (the default). The comment names the stylesheet token.
  paper: "#F2F2F0"                  # --canvas
  porcelain: "#FFFFFF"              # --surface
  mist: "#F7F7F5"                   # --surface-2
  ink: "#1D1D1F"                    # --text
  slate: "#5F5F64"                  # --text-2
  quartz: "#6B6B70"                 # --text-3
  flint: "#86868B"                  # --gray: not started
  shell: "rgba(0, 0, 0, 0.06)"      # --shell
  hairline: "rgba(0, 0, 0, 0.08)"   # --hairline
  signal-blue: "#007AFF"            # --accent: a fill or a ring, never text
  signal-blue-tint: "rgba(0, 122, 255, 0.12)"      # --blue-soft
  cobalt: "#0060CC"                 # --blue: the ink of "in work"
  amber-ink: "#9A4A00"              # --yellow
  amber-tint: "rgba(255, 149, 0, 0.16)"             # --yellow-soft
  moss: "#176F2F"                   # --green
  moss-tint: "rgba(52, 199, 89, 0.16)"              # --green-soft
  brick: "#BC1A10"                  # --red
  brick-tint: "rgba(255, 59, 48, 0.12)"             # --red-soft
  neutral-tint: "rgba(0, 0, 0, 0.05)"               # --gray-soft
  # Dark theme ([data-theme="dark"]): zinc-graphite, never black.
  graphite: "#1C1C1E"               # --canvas
  charcoal: "#2C2C2E"               # --surface
  basalt: "#232325"                 # --surface-2
  chalk: "#F5F5F7"                  # --text
  pewter: "#AFAFB4"                 # --text-2
  quartz-dark: "#98989D"            # --text-3
  zinc: "#98989D"                   # --gray
  shell-dark: "rgba(255, 255, 255, 0.08)"           # --shell, --gray-soft
  hairline-dark: "rgba(255, 255, 255, 0.1)"         # --hairline
  night-blue: "#0A84FF"             # --accent: a fill or a ring, never text
  night-blue-tint: "rgba(10, 132, 255, 0.2)"        # --blue-soft
  sky: "#6CB3FF"                    # --blue: the ink of "in work"
  lantern: "#FFB340"                # --yellow
  lantern-tint: "rgba(255, 159, 10, 0.2)"           # --yellow-soft
  spring: "#30D158"                 # --green
  spring-tint: "rgba(48, 209, 88, 0.18)"            # --green-soft
  coral: "#FF8578"                  # --red
  flame-tint: "rgba(255, 69, 58, 0.18)"             # --red-soft
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.03em"
  glyph:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "19px"
    fontWeight: 700
    lineHeight: 1
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "19px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "17px"
    fontWeight: 600
    letterSpacing: "-0.02em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  row-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "14px"
    fontWeight: 500
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "13px"
    fontWeight: 500
  sheet-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "18px"
    fontWeight: 600
    letterSpacing: "-0.02em"
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
  note:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "11.5px"
    fontWeight: 400
  tick:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "11px"
    fontWeight: 400
  reading:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
  node-tech:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "10.5px"
    fontWeight: 400
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  sm: "12px"      # --r-sm: inner tiles, feed rows, history rows
  md: "16px"      # --r-md: inner panels, specialist cards, attention items
  lg: "24px"      # --r-lg: cards
  core: "18px"    # calc(--r-lg - 6px): the face of a card inside its shell
  pill: "999px"   # buttons, status pills, chips, inputs, the header
  chip: "6px"     # file chips
components:
  action-button:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "8px 14px"
  action-button-danger:
    backgroundColor: "{colors.brick-tint}"
    textColor: "{colors.brick}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "8px 14px"
  icon-button:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    width: "34px"
    height: "34px"
  tag:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.slate}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "5px 11px"
  avatar:
    backgroundColor: "{colors.signal-blue}"
    textColor: "{colors.porcelain}"
    typography: "{typography.glyph}"
    rounded: "{rounded.pill}"
    width: "38px"
    height: "38px"
  chip:
    textColor: "{colors.slate}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "5px 11px"
  chip-on:
    backgroundColor: "{colors.porcelain}"
    textColor: "{colors.ink}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "5px 11px"
  search-field:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "6px 12px"
  state-pill-busy:
    backgroundColor: "{colors.signal-blue-tint}"
    textColor: "{colors.signal-blue}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  state-pill-attention:
    backgroundColor: "{colors.amber-tint}"
    textColor: "{colors.amber-ink}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  state-pill-done:
    backgroundColor: "{colors.moss-tint}"
    textColor: "{colors.moss}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  state-pill-problem:
    backgroundColor: "{colors.brick-tint}"
    textColor: "{colors.brick}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  state-pill-idle:
    backgroundColor: "{colors.neutral-tint}"
    textColor: "{colors.slate}"
    rounded: "{rounded.pill}"
    padding: "4px 10px"
  card:
    backgroundColor: "{colors.mist}"
    rounded: "{rounded.lg}"
    padding: "6px"
  card-core:
    backgroundColor: "{colors.porcelain}"
    rounded: "{rounded.core}"
    padding: "20px 22px"
  tile:
    backgroundColor: "{colors.mist}"
    rounded: "{rounded.sm}"
    padding: "12px"
  attention-item:
    backgroundColor: "{colors.amber-tint}"
    rounded: "{rounded.md}"
    padding: "16px 18px"
  attention-item-stopped:
    backgroundColor: "{colors.brick-tint}"
    rounded: "{rounded.md}"
    padding: "16px 18px"
  file-chip:
    backgroundColor: "{colors.porcelain}"
    textColor: "{colors.slate}"
    rounded: "{rounded.chip}"
    padding: "2px 8px"
---

# Design System: Agent Teams Board

## Overview

**Creative North Star: "The Site Office"**

The reader is the owner of a team of AI specialists: a manager, an
analyst, a lead, a person who is good at their own job and has no reason
to learn the engine's. The board is a quiet site office they walk into.
One wall carries the plan and the clock. Another shows the people and
what each of them is doing right now. One tray holds what needs their
signature. The machinery stays outside the window: identifiers, commits,
branches and the engine's vocabulary are not on these walls at all, and
appear only when the reader asks for the technical level.

The room is furnished with the restraint of a system interface, not the
excitement of a landing page. One type family, which is the one the
reader's own system uses. Warm zinc neutrals with a single soft shadow
and corners that nest concentrically. Five state colours that keep their
meaning everywhere: grey has not started, blue is alive, amber waits for
the owner and nothing is broken, green is done, red cannot go on without
a decision. Density is a newspaper, not a cockpit: whole sentences, wide
margins between cards, and no number without the words that say what it
counts. The owner was shown a reference of restraint (Apple's system
interfaces) and accepted it; the names of the colours and of the whole
system are this document's proposal and can be renamed.

The texts are Russian today, in the words of the reader (a "specialist",
a "stage", a "check"), and they take their endings from their numbers
(1 проверка, 2 проверки, 5 проверок). The language of the board is an
open decision of the owner; nothing in the visual system depends on it.

**Key Characteristics:**
- One page, read top to bottom: state, plan, people, money, problems,
  then the long activity feed at the bottom.
- Cards in two equal columns that balance by measured height; a full
  change of one card redraws that card alone.
- Every state is a word, an icon and a colour, in that order of
  importance.
- One shadow, one type family, one accent, five state colours.
- System settings honoured: the dark theme follows the reader's, motion
  stops when the reader asks the system to reduce it, the keyboard focus
  is always visible.
- Nothing is loaded from the network: no web font, no icon font, no
  image. Icons are a small inline set drawn with a 2px stroke.
- Every text keeps a contrast of 4.5:1 or more in both themes (the
  owner's bar is WCAG AA), and on a phone every control keeps a side of
  32px or more.
- Before the first answer the page shows quiet bars where its lists will
  be and draws no progress: nothing is shown that the engine did not say.

## Colors

The palette is a warm zinc grey scale with five saturated colours that
each own one meaning; no colour is used for decoration. The source of
truth is the token block at the top of the board's stylesheet (one block
for the light theme, one for the dark); the values in the frontmatter
above are those tokens.

### Primary

- **Signal Blue** (#007AFF; dark: Night Blue #0A84FF): the only accent.
  It is a fill or a ring and never text: a specialist's avatar, the
  keyboard focus ring, the edge of a row under the pointer, a bar of the
  spend, the sparkline. As text on its own tint it cannot reach 4.5:1.

### Secondary

The four state colours that speak. Each is a text-safe ink on its own
soft tint, and as a solid fill only for small marks (a dot, an icon of
16px, a stroke, a bar of 8 to 18px).

- **Cobalt** (#0060CC; dark: Sky #6CB3FF): in work. The text of the "in
  work" pill, the dot of a counter, the icon of a stage at work, a link
  of the graph and a span of the timeline that come from a stage at
  work. Its tint is Signal Blue at 12% over the surface (dark: 20%).
- **Amber Ink** (#9A4A00; dark: Lantern #FFB340): waiting for the owner,
  nothing is broken. A stage on check, a remark of the checker, an
  attention item, the warning band of the budget, the line of a graph
  node that waits for a decision. Its tint is 16% over the surface.
- **Moss** (#176F2F; dark: Spring #30D158): done. An accepted stage, a
  checkpoint reached, a passed check, the progress ring, a settled
  problem.
- **Brick** (#BC1A10; dark: Coral #FF8578): work that cannot go on
  without a decision. A stage stopped after failed checks, a failed
  integration, a defect found in a result, an action that has
  consequences and asks for a confirmation first, the "connection lost"
  banner.

### Neutral

- **Paper** (#F2F2F0; dark: Graphite #1C1C1E): the canvas behind
  everything. The dark theme is graphite, never pure black: black
  surfaces and pure white text together are what makes a dark interface
  glare.
- **Porcelain** (#FFFFFF; dark: Charcoal #2C2C2E): the face of a card,
  the background of a chip that is switched on, of a file chip.
- **Mist** (#F7F7F5; dark: Basalt #232325): the shell of a card, and
  every tile inside a card: a stage row, a specialist card, a figure
  tile, a record block.
- **Ink** (#1D1D1F; dark: Chalk #F5F5F7): the main text.
- **Slate** (#5F5F64; dark: Pewter #AFAFB4): the second line of text: a
  hint, a detail, a caption that has to be read.
- **Quartz** (#6B6B70; dark #98989D): the third line: a time stamp, a
  note under a figure, a step not reached, the placeholder of a search.
- **Flint** (#86868B; dark: Zinc #98989D): the "not started" state, as
  a mark and never as text: an empty checkpoint, a free specialist, a
  span of waiting.
- **Shell** (black at 6%; dark: white at 8%) and **Hairline** (black at
  8%; dark: white at 10%): the two borders. The shell draws the outer
  edge of a card, the hairline the edge of everything inside it.
- **Neutral tint** (black at 5%; dark: white at 8%): the track of a bar
  that is empty, the mark of a stage nobody started, the idle pill.

### Measured contrast

Measured with the WCAG formula on the tint over its surface, November
2026, after the polish pass; the script repeats the same count on the
built page in both themes and on a team of a hundred stages. Every ink
that is written as text keeps 4.5:1 or more on the worst surface it
lands on:

- Ink 13.4:1 (dark 8.7:1), Slate 5.3:1 (5.0:1), Quartz 4.9:1 (4.9:1).
- Cobalt on its tint 4.8:1 (Sky 5.0:1), Amber Ink 5.2:1 (Lantern
  5.3:1), Moss 5.2:1 (Spring 4.9:1), Brick 5.1:1 (Coral 4.8:1).
- The letter of an avatar is large text (19px at 700) and needs 3:1:
  it keeps 4.0:1 in light and 3.7:1 in dark. The focus ring keeps 3.6:1
  and 3.8:1 against what surrounds it.

**On a tint the quiet text is the main ink, thinned.** A grey cannot
keep its contrast on every tint and never looks at home on one: inside
an attention item and inside a problem that waits, Slate and Quartz are
Ink at 72% (dark: Chalk at 80%). The one line the owner acts on is set
in Ink at 600 and is never the quiet one.

### The report for a manager

The report the owner can download is a document of the same system and
now takes the board's tokens exactly (decided in the polish pass of
November 2026): the same canvas, the same five state colours as inks on
their tints, the same three lines of text, the same corners (16px for a
section, 12px for a tile inside it, a pill for a state). It stays a
document: 16px body for reading and printing, one column of 62rem, and
light only.

### Named Rules

**The Colour Never Speaks Alone Rule.** Every state is a word and an
icon first; the colour only repeats them. A reader who cannot tell Moss
from Brick loses nothing.

**The Amber Waits, Red Stops Rule.** Amber means the owner's turn and
nothing is broken. Red is reserved for work that cannot continue
without a decision and for actions with consequences. A problem that
was dealt with is never red.

**The Blue Is Alive Rule.** Blue marks what is alive or reachable: work
in progress, the focus, a person. It is never a decoration, a background
of a section, or the colour of a heading.

**The Graphite, Not Black Rule.** In the dark theme the canvas is
#1C1C1E and the card face #2C2C2E; pure black and pure white are not
used.

## Typography

**Display Font:** the system UI font (SF Pro on Apple, Segoe UI on
Windows, the generic sans-serif elsewhere)
**Body Font:** the system UI font
**Label/Mono Font:** the system UI font for labels; the system monospace
(SF Mono, Menlo) only in the technical blocks

**Character:** the reader's own system font, used plainly: no display
face, no second family, no italics. Weight carries the hierarchy (400,
500, 600, 700) together with size; tight tracking only on the large
numbers and headings.

### Hierarchy

- **Display** (700, 30px, line-height 1, tracking -0.03em): one number
  on the whole board, the percent inside the progress ring.
- **Headline** (600, 19px, tracking -0.01em): the sentence of the
  progress card ("Команда работает. 1 задача в работе."), the statement
  of the comparison of teams.
- **Title** (600, 17px, tracking -0.02em): the heading of a card.
- **Sheet title** (600, 18px, tracking -0.02em): the title of the open
  sheet, one step above a card because the sheet is a page of its own.
- **Body** (400, 15px, line-height 1.5): everything the reader reads as
  a sentence, in cards and in the sheet.
- **Row title** (500, 14px): the title of a stage, of a feed line, of a
  history line; a state hint under it is 12.5px in Slate.
- **Label** (500, 13px): buttons, chips, pills, inputs, the second
  line of a figure.
- **Glyph** (700, 19px, line-height 1): the one letter of an avatar.
- **Caption** (400, 12.5px, Slate): a hint, a detail, a note under a
  figure; time stamps are 12px in Quartz with tabular numerals.
- **Reading** (400, 13.5px, line-height 1.5): a sentence inside a tile:
  what a specialist is doing, the lines of an attention item, of a
  problem.
- **Note** (400, 11.5px, Quartz): a note under a figure, the small line
  of a legend, the state of a graph node at 600.
- **Tick** (400, 11px, Quartz): the hour on the axis of the timeline, the
  state under the name of a row and the count of what a row does not
  draw.
- **Node tech** (400, 10.5px, the mono face, Quartz): the identifier and
  the branch under a node of the graph, at the technical level only; the
  smallest step of the ramp and the only one in mono besides Mono.
- **Mono** (400, 12px, line-height 1.55): the technical block of a card
  at the technical level, in Slate; never in the simple level. The
  whole ramp is 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 15, 17, 18, 19
  and 30px: a size outside it is a new step and belongs here first.

### Named Rules

**The System Font Rule.** No web font is ever loaded. The board works
offline, loads nothing from the network, and looks native because it is
set in the font the reader already reads.

**The Words First Rule.** A figure never stands alone: it comes with
the word that says what it counts ("передано на проверку: 2") and the
word takes its ending from the figure.

## Layout

One page, 1180px wide at most, centred, with 20px of body padding on
each side (12px on a phone). From the top: the floating header, a row of
anchor chips (the main places, reachable in one click), then the cards.

The first two cards span the full width (the progress and the plan
graph). Everything else lives in two equal columns of 20px gaps. The
cards are distributed between the columns by their measured height, so
the two columns end level and neither is "the narrow one"; the reader's
place in the page is kept when they move. The activity feed sits at the
bottom of its column and never pushes a card out of view.

A card is a double bezel: a 6px shell of Mist inside a 24px rounded
corner with the one shadow, and a face of Porcelain inside it with
20px 22px of padding and an 18px corner. Inside the face the rhythm is
4, 8, 12, 14, 16, 20: a tile has 12px, a specialist card 14px 16px, an
attention item 16px 18px.

The page adapts at three widths:

- Below 900px the two columns become one, in the order of the page:
  actions, attention, what happens now, specialists, plan, time, feed,
  quality, spend, problems, the integration, the comparison of teams.
- Below 640px (a phone) the header becomes a rounded card of two short
  rows (the name and the goal, then the state, the mode and the theme):
  it is sticky, and a taller one covered a quarter of the screen. The
  body padding is 12px, the face of a card 16px, a stage row two
  columns, lists of specialists one column. A phone is touched, not
  pointed at: a chip, a button of the switch, an icon button, a search
  field and a select each keep a side of 32px or more, and a group of
  chips that wraps stops pretending to be one pill (each chip is a pill
  of its own).
- The card of "what happens now" follows its own width, not the
  window's (a container query at 560px): narrow, its state moves under
  the title.

Wide content scrolls inside its card, never stretches the page: the
timeline and the graph pan sideways inside a clipped panel of 16px
corners. The tools of the feed (a search, two selects, the chips, two
toggles) take rows of their own under the heading: the three fields
share a row, the two toggles travel together and never leave one of
them alone on a row.

### Named Rules

**The One Page Rule.** There is no separate screen for a metric. A
detail opens as a sheet over the page and the page stays where it was.

**The Balanced Columns Rule.** Cards move between the two columns to
balance them by measured height; a column is never defined as the wide
or the narrow one.

## Elevation & Depth

The system is a hybrid with a single shadow. A card at rest carries the
one soft shadow (`0 20px 40px -15px rgba(0,0,0,0.08), 0 1px 2px
rgba(0,0,0,0.04)`; in dark `0 20px 40px -15px rgba(0,0,0,0.5), 0 1px 2px
rgba(0,0,0,0.3)`) under its 6px shell. Everything inside a card is flat
and separated by tone (Porcelain, Mist, Paper) and by the hairline. A
specialist card or a stage row under the pointer stays flat: it takes
the accent on its edge. No second, deeper level exists. The header
floats above the page as a
translucent pill (the surface at 72%, a 20px backdrop blur with 180%
saturation). The sheet opens over a backdrop of black at 28%.

### Named Rules

**The One Shadow Rule.** There is one shadow value. Depth inside a card
is made with tone and hairlines, never with more shadows.

**The Tone Before The Line Rule.** Separate with a change of surface
first; draw a hairline only where two surfaces of one tone meet.

## Shapes

Corners nest concentrically: a card is 24px, its face 18px (24 minus
the 6px shell between them), a panel inside the face 16px, a tile 12px,
a file chip 6px. Everything that is a status, a control or a piece of
meta is a full pill (999px): buttons, chips, the status pill, the
search field, the header, the connection banner, the legend swatch.
Solid forms are small and round: the progress ring, the 8px state dot,
the 10px checkpoint, the 12px distribution bar, the 18px time span with
9px ends. Borders are 1px: the shell and the hairline in the neutrals,
a 35% or 50% mix of a state colour only where the state has to be seen
from across the room (the attention card's shell, the connection
banner). An empty state is a 16px rounded box with a dashed hairline.

### Named Rules

**The Concentric Corner Rule.** A radius inside a shape equals the
outer radius minus the padding between them. A 24px card with a 6px
shell has an 18px face.

**The Pill For Anything That Has A State Rule.** If it shows a state,
filters, or can be pressed, it is a pill. Rectangles with small corners
are for containers only.

## Components

### Buttons
- **Shape:** a full pill (999px).
- **The action button** ("Что можно сделать"): quiet and factual. Mist
  background, Ink text at 13px 500, a hairline, 8px 14px. Under the
  pointer it takes the shell tone; pressed, it scales to 0.98.
- **With consequences:** the same pill in Brick on its tint, so the
  owner sees before reading that it will ask first; under the pointer
  the tint deepens to 24%.
- **Icon buttons:** 34px circles (30px small, 34px on a phone), Mist, a
  hairline, a 16px icon; under the pointer the shell tone, pressed they
  scale to 0.94. Used for the theme, the zoom and the fit of the graph,
  the close of the sheet.
- **Not available now:** every control (an action, an icon button, a
  chip, a part of the switch, the "earlier" button of the feed) takes
  one and the same look when it cannot be used: 55% opaque, no press,
  no change under the pointer.
- **Focus:** every button shows the focus ring (2px Signal Blue, 2px
  away from the edge).

### Chips
- **Style:** a filter group is a pill of Mist with a hairline and 3px
  inside; each chip is a pill of 5px 11px, 12.5px 500, Slate on a
  transparent background. A chip that stands outside a group (the two
  toggles of the feed) carries the look of the track itself: Mist and a
  hairline.
- **State:** the chip that is on sits on Porcelain in Ink with a
  `0 1px 3px rgba(0,0,0,0.12)` shadow; under the pointer an off chip
  takes Ink and the shell tone; pressed, it scales to 0.97. The state
  is also written (`aria-pressed`), for the reader who does not see
  the contrast.
- **A label is not a switch.** The kinds of problems with their counts
  are tags: a pill of Mist with a hairline in Slate, never the look of
  a chip that is pressed on.

### Cards / Containers
- **Corner Style:** 24px outside, 18px inside.
- **Background:** a Mist shell around a Porcelain face.
- **Shadow Strategy:** the one shadow (see Elevation and Depth).
- **Border:** a 1px shell edge; the attention card's edge is Amber at
  35%.
- **Internal Padding:** 6px of shell, then 20px 22px on the face
  (16px on a phone).

### Inputs / Fields
- **Style:** a pill of Mist with a hairline, 13px, 6px 12px: the search
  of the graph and of the feed, the two selects of the feed. Under the
  pointer the edge takes Flint. The placeholder of a search is Quartz.
- **Focus:** the focus ring, 1px from the edge.
- **The browser's own parts follow the theme** (`color-scheme`): the
  list of a select, the clear button of a search and the scrollbars are
  dark in the dark theme; scrollbars are thin and Flint.
- **Error / Disabled:** not used as a form: the board has one number
  field (the new budget), asked for in the system's own dialog and
  checked in words.

### Navigation
- **Anchors:** a row of pills (13px 500, Slate on Porcelain with a
  hairline, 6px 12px) for the main places of the page; under the
  pointer, Ink on Mist. An anchor lands 84px from the top, under the
  floating header.
- **The header:** a sticky glass pill 12px from the top with the name
  and the goal (15px 600 and 12px Slate, each cut with an ellipsis on
  one line), the state pill, the start and the elapsed time as quiet
  pills, a two-part switch for the level (Простой / Технический) and
  the theme button. A part of the switch that is off takes Ink and the
  shell tone under the pointer; the part that is on sits on Porcelain in
  Ink. On a phone, two rows.

### Icons
A set of thirteen inline icons drawn with a 2px round stroke in the
colour of the text: a circle (not started), a clock (in work), a
magnifier (on check), a check mark (done), a triangle (a decision or a
problem), a minus (cancelled), an hourglass (waiting), two arrows
(recovered), a sun (the theme), a cross (close), and for the graph a
minus (smaller), a plus (larger) and four corners (the whole plan). 16px in a row, 13px in the feed; each is decorative
next to a word, and the word carries the meaning for assistive
technology. A sign is never a character of the font standing in for an
icon.

### The status pill
The signature of the board: an 8px dot, an icon and a word in a tinted
pill of 12.5px 600 (4px 10px). Five kinds: busy (Cobalt on the tint of
Signal Blue), attention (Amber Ink), done (Moss), problem (Brick), idle
(Neutral tint with Slate). The same five kinds drive the mark of a stage row, of a
specialist card, of a graph node, of a feed line and of a time span, so
a reader learns them once.

### Specialist card
A tile of 16px corners with a 38px avatar: a Signal Blue circle with
the first letter of the role (never of an identifier), 19px at 700 in
Porcelain. Name 15px 600, the responsibility in Slate 12.5px, what it
is doing now 13.5px, the figures 12.5px, the status pill at the end.
Under the pointer the edge takes Signal Blue; pressed, 0.99. Opens the
sheet.

### Stage row
A tile of 12px corners in three columns: a 26px round mark with the
state's icon, the title (14px 500) with a sentence of state and the
lines "после" and "дальше" in Slate 12.5px, and the role at the right.
Under the title: the checkpoint track, three 10px points joined by
22px lines, filled in Moss as the engine records them (started, handed
over, accepted); then the files of the result as file chips. Under the
pointer the edge takes Signal Blue, like every row that opens
something (a specialist card, a graph node, a file button); pressed,
0.99. Opens the sheet.

### File chip
A 6px chip of Porcelain with a hairline, 12px, 2px 8px, Slate; a file
written outside its zone is Brick on its tint at 600. A long name moves
to the next line whole and breaks inside itself only when it cannot
fit a line.

### Progress ring
132px, a 10px track in the neutral tint and a Moss bar with round ends,
the one big number (30px 700) and a caption inside, the sentence next
to it (19px 600) and the counters as pills with an 8px dot.

### Attention item
A 16px panel in Amber Ink on its tint with a 25% amber edge: the title
with an icon, then five labelled lines (what happened, why it matters,
what the system tried, what is needed, what happens otherwise) and the
checker's remark in a quoted panel. The line "what is needed" is set at
600: it is the one the owner acts on. The quiet text on the tint is Ink
thinned (see Colors). A stage that is stopped, or a failed integration,
is the same panel in Brick on its tint.

### Activity feed
A column that scrolls inside 520px, a row of three columns: the time
(40px, 12px Quartz, tabular), a 22px round mark with the state's icon,
and the text (14px 500 title, 12.5px Slate details). A new line rises
8px and fades in over 500ms. A row about a stage opens that stage; so
does the keyboard (Enter, Space).

### Plan graph
Stages as rounded boxes of 196 by 64px (16px corners, Porcelain face, a
hairline that becomes 2px Signal Blue under the pointer or the focus),
a 6px state mark, the state in 11.5px 600 and the title in 13px 500
cut after two lines; curved links of 2px in the colour of the stage
they come from. What a filter hides stays in place at 16% opacity, so
the shape does not jump. The reader zooms (25% to 250%) and drags; the
position survives refreshes.

The drawing has no viewBox and its box is set in pixels: a unit is a
pixel, and the plan is drawn at the scale the reader chose and only
that one. On arrival the whole plan is shown across the width, never
larger than life and never under 75%: what does not fit at 75% is
panned, on a phone as on a desk. (With a viewBox the browser fitted the
box a second time, and a plan of a hundred stages came out with its
words at a third of their size.)

### Timeline
A row per stage (46px high, the whole of it one target: an invisible
plate answers the pointer between the letters and the bars): the title
(12.5px 500, cut after 26 signs),
the state (11px, Quartz), the spans of what the stage was doing (18px
high, 9px ends, at 85% opacity: work in blue, check and rework in amber,
wait in grey, recovery in the blue tint with a dashed blue stroke, done
in green, stopped in red) and an 8px dot per recorded event in the
colour of its kind, outlined in 1.5px of Porcelain. Drawn at real size;
a narrow window scrolls it sideways. What is not drawn is said in words
("ещё 37").

### Figures
A tile of 12px corners with a number (19px 600) and its word in Slate
(12.5px) and a note in Quartz (11.5px): the six figures of the spend,
the four of quality, the rows of the comparison. Bars are 8px pills in
Signal Blue on the neutral tint; the budget gauge is a 10px bar in Moss
(Amber near the limit, Brick at it) with a 2px Ink mark where the
warning starts; the spend over time is a sparkline in Signal Blue, 56px
high; the distribution of results is one 12px bar in Moss, Amber Ink,
Signal Blue and Quartz, always with its legend in words; the checks in
their order are 14px squares (the 6px corner of a chip) in Moss and
Brick, one per check.

### Problem list
Under the sentence of the recovery centre stand the kinds of problems
with their counts, as tags. Then a tile per problem (16px corners, 10px
12px, 13.5px): the ones that wait for the owner come first. A problem
that waits is Brick on its tint with a 25% brick edge and a Brick icon;
one that was dealt with is Mist with a Moss icon. Each says what
happened and when, what the system did, what cannot be lost, how it
ended, and in 600 weight in Ink what is needed from the owner when
something is; the rest of its quiet text is Ink thinned.

### Record blocks
A block per model (technical level) or per compared team: a tile of
12px corners (10px 12px, 12.5px) with a 13px 600 heading and a list of
terms in Quartz with their values in tabular numerals. Every measure is
visible at any width: what would not fit a table is never cut off.

### Integration steps
Three rows (16px corners, 10px 12px, 14px): a circle that becomes a Moss
check mark when the engine recorded the step, the name of the step and
its state in Slate; a step not reached is Quartz.

### Detail sheet
A native dialog of 560px at most (86% of the height; on a phone the
full width less 16px and 92% of the height), 24px corners, the one
shadow, over black at 28%: a sticky head (18px title, a close
button) and the body with sections of 13px 600 headings in Slate. It
closes with Escape, with its button and with a click on the backdrop;
it stays open across refreshes and follows the data. While it is open
the page under it does not scroll.

### Loading
Before the first answer nothing is shown that the engine did not say:
the ring is an empty track beside "0%", and where the lists will be
drawn there are quiet bars (a tile of Mist with a hairline, or a line
62% wide). The bars appear after 180ms, so an answer that comes at once
never shows them; the first picture replaces them as a whole. If the
first answer fails, the bars go and the banner says so.

### The parts the browser draws
Text selection is the accent at 26% over the surface; the caret of a
search is Signal Blue; scrollbars are thin and Flint; in the dark theme
the browser's own parts are dark.

### Connection banner
A pill fixed at the bottom (9px 16px): stale updates in Amber, a lost
connection in Brick, in words, with the time of the last known state.

### Motion
One easing (`cubic-bezier(0.32, 0.72, 0, 1)`) and one duration (220ms)
for colours and transforms, the theme change included: a reader in flow
is not made to wait for a hover. The ring fills over 700ms and fades in
over 300ms, a bar grows over 500ms, a new feed line rises over 500ms,
the refresh dot pulses over 900ms, the loading bars fade in over 240ms. With `prefers-reduced-motion: reduce` every animation and
transition is switched off. A block of the page is written again only
when its markup changed: a refresh that moved nothing is perfectly
still.

## Do's and Don'ts

### Do:
- **Do** give every state a word, an icon and then a colour
  (The Colour Never Speaks Alone Rule).
- **Do** keep Amber for "your turn, nothing is broken" and Brick for
  "cannot go on without a decision" (The Amber Waits, Red Stops Rule).
- **Do** use the system font at the sizes of the hierarchy and set
  times in tabular numerals.
- **Do** put the word that counts next to every figure, with the ending
  that belongs to the number.
- **Do** show the keyboard focus as a 2px Signal Blue ring, 2px away
  from the edge, and make every row that opens something reachable by
  Enter and Space.
- **Do** nest corners (24 outside, 18 inside a 6px shell) and use the
  one shadow only.
- **Do** let wide content scroll inside its card and keep the reader's
  place (scroll, filters, the graph's position, the open sheet, the
  open file) across refreshes.
- **Do** cut a single line with an ellipsis and a multi-line text by
  breaking inside long words, so nothing ever stretches the page.
- **Do** add a new control to the nearest family (the action pill, the
  chip, the icon button) rather than inventing a new shape for it.
- **Do** check a new ink where it is written, on the tint over its
  surface, in both themes, before adding it to the palette; 4.5:1 is
  the floor for text.
- **Do** give a control its hover, press and unavailable looks from its
  family, and a side of 32px on a phone.

### Don't:
- **Don't** load a web font, an icon font, an image or any other
  resource from the network; the page loads nothing and must keep
  loading nothing (The System Font Rule).
- **Don't** write a style or a script inside the markup: the page's
  policy refuses both, and a bar gets its width through the style
  object of the page.
- **Don't** use a long dash in a text of the board: the owner does not
  want them, in sentences and in strings alike.
- **Don't** show an identifier, a commit, a branch or a term of the
  engine at the simple level; those belong to the technical level and
  to the technical block of a card.
- **Don't** use blue, amber, green, red or grey to mean anything other
  than their one state.
- **Don't** add a gradient, a glow, a neon edge or a second shadow:
  none exist in the system, and depth is made with tone.
- **Don't** animate to show that the page is alive: motion answers a
  change (a new line, a filled bar), nothing else.
- **Don't** show a colour without its word and icon in a new component
  "because there is no room": make room or leave the colour out.
- **Don't** write Signal Blue as text, and don't write Slate or Quartz
  on a tint: on a tint the quiet text is Ink thinned.
- **Don't** let a label wear the look of a switch that is pressed on,
  and don't let one thing that opens a sheet signal it differently from
  the others.
- **Don't** fit the plan graph a second time or draw it under 75%: the
  reader pans and zooms, and never squints.
