---
name: TenderIQ
description: Internal PDO/OQ tender intelligence dashboard for SSP's bidding team
colors:
  canvas: "#f0eee6"
  panel: "#faf9f5"
  surface: "#ffffff"
  surface-hover: "#eceada"
  surface-active: "#e0dccb"
  hairline: "rgba(31,30,29,0.09)"
  hairline-strong: "rgba(31,30,29,0.16)"
  ink: "#1f1e1d"
  ink-muted: "#5e5b53"
  ink-faint: "#6b665c"
  coral: "#d97757"
  coral-solid: "#b8542f"
  coral-deep: "#9c4828"
  positive: "#188a50"
  positive-deep: "#0f6b3e"
  caution: "#b45309"
  caution-deep: "#92400e"
  danger: "#dc2626"
  danger-deep: "#b91c1c"
  accent-purple: "#7c5cbf"
typography:
  body:
    fontFamily: "Inter, -apple-system, sans-serif"
    fontWeight: 400
  label:
    fontFamily: "Inter, -apple-system, sans-serif"
    fontWeight: 600
    letterSpacing: "0.06em"
  mono:
    fontFamily: "'SF Mono', 'Fira Code', monospace"
rounded:
  xs2: "2px"
  xs: "3px"
  sm: "4px"
  sm2: "6px"
  sm3: "7px"
  md: "8px"
  md2: "10px"
  lg: "12px"
  lg2: "16px"
  lg3: "18px"
  pill: "20px"
  circle: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "14px"
  lg: "18px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.coral-solid}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "9px 15px"
  button-primary-hover:
    backgroundColor: "{colors.coral-deep}"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.md}"
  chip-active:
    backgroundColor: "{colors.coral-solid}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.lg}"
---

# Design System: TenderIQ

## Overview

**Creative North Star: "The Claude-Warm Operations Desk"**

TenderIQ deliberately borrows Claude's own landing-page palette — warm ivory
canvas, coral-terracotta accent — and applies it to a dense, high-frequency
operations tool instead of a marketing surface. The effect it's going for:
an internal dashboard that feels considered and warm rather than the
default cold-gray enterprise-SaaS look, without spending any of that warmth
on decoration. Every screen is a fixed-viewport app shell (no page scroll;
independent scrolling panes for the list, detail, and market-intelligence
views) built for someone who has this open all day, not someone visiting
once. This is confirmed direction (see the palette's own code comment:
"Claude landing-page palette (warm ivory + coral)"), not an accidental
default.

Confirmed visual rejections: no gray-on-gray enterprise-dashboard coldness;
no shadows-as-default depth (flat, bordered, low-chrome instead).

**Key Characteristics:**
- Warm ivory canvas + pure-white/near-white panels, not blue-gray
- One real accent color (coral), used sparingly for actions and active state
- Flat surfaces with hairline borders; shadows reserved for true overlays
- Dense information display (score pills, status tags, meta grids) kept
  legible through consistent tinted-badge language, not heavier chrome
- Fixed desktop app-shell layout; not currently responsive (see Layout)

## Colors

Warm and restrained: one accent, a warm-neutral ramp instead of cool gray, and status colors used only as low-opacity tints on badges/pills rather than solid fills.

### Primary
- **Coral** (`#d97757`): the one real accent, used for non-text/graphical purposes only (focused input borders, selected-row indicator stripe, avatar fill, chart accents) — these only need to meet the 3:1 non-text contrast minimum, which this value clears.
- **Coral Solid** (`#b8542f`): Coral darkened for the one case Coral itself can't cover — a solid fill carrying white text at normal size (primary button, active filter chip). White-on-`#d97757` is only 3.1:1, short of the 4.5:1 body-text minimum; white-on-`#b8542f` is 4.8:1.
- **Coral Deep** (`#9c4828`): darker still — the button's hover fill, and every place Coral appears *as text* on a light background (AI-score label, "New" pill/count, the AI-score button label). `#c96442` (the original hover shade) was only 3.9:1 as text; `#9c4828` reaches 6.2:1.

**The Darker-For-Text Rule.** The closer a coral shade gets to carrying text, the darker it must be: Coral (graphical only) → Coral Solid (white text on a fill) → Coral Deep (coral text on a light background). Never use plain Coral behind or as body text.

### Secondary
- **Accent Purple** (`#7c5cbf`): appears only as the second stop in the AI-score card's diagonal gradient, alongside Coral. Not used as a standalone UI color elsewhere.

### Neutral (warm ramp, not cool gray)
- **Canvas** (`#f0eee6`): page background.
- **Panel** (`#faf9f5`): header bar, filter row, side-panel-style card backgrounds (`.mcard`, `.kpi-tile`, `.chart-card`, `.etag`).
- **Surface** (`#ffffff`): the list's row-hover background and form input backgrounds — the "closest to the user's cursor" layer.
- **Surface Hover** (`#eceada`): hover/track background (chip hover, progress-bar track).
- **Surface Active** (`#e0dccb`): active header-pill background, scrollbar thumb.
- **Hairline** (`rgba(31,30,29,0.09)`) / **Hairline Strong** (`rgba(31,30,29,0.16)`): the only depth cue on most surfaces — a 9%/16%-opacity near-black line, never a solid gray.
- **Ink** (`#1f1e1d`) / **Ink Muted** (`#5e5b53`) / **Ink Faint** (`#6b665c`): primary text, secondary text (labels, metadata), tertiary text (placeholders, counts). Ink Faint was originally `#8f8b80` — a 2026-09-27 audit found it failed WCAG AA (2.8–3.4:1) against every background it's used on; darkened to pass 4.5:1+ everywhere while keeping the warm-gray character.

### Status tints (positive / caution / danger)
- **Positive** (`#188a50` / deep `#0f6b3e`): high AI score, "tackled" state.
- **Caution** (`#b45309` / deep `#92400e`): medium score, urgent deadline.
- **Danger** (`#dc2626` / deep `#b91c1c`): low score, expired tender.

All three ship as a **deep** text color paired with the same hue at ~12–14% opacity as a badge background (e.g. `rgba(24,138,80,0.14)` text `#0f6b3e`), never as a solid fill. This keeps the dense badge/pill vocabulary legible without turning the list into a traffic-light wall.

### Named Rules
**The One Real Accent Rule.** Coral is the only color used to mean "this is interactive/primary/selected." Every other color in the palette means status (positive/caution/danger) or hierarchy (ink/panel/surface). Nothing else competes with Coral for attention.

**The Tint-Not-Fill Rule.** Status colors (positive/caution/danger) always appear as a deep text color on a ~12–14%-opacity tint of the same hue, never as a solid badge fill. This is what keeps a list full of score pills and status tags from reading as noisy.

## Typography

**Body Font:** Inter (weights 300–700 loaded), falling back to `-apple-system, sans-serif`.
**Label/Mono Font:** `'SF Mono', 'Fira Code', monospace` — used only for the tender reference-number tag (`.ref-mono`).

**Character:** A single, well-hinted UI sans across the whole product — no display face, no editorial serif. The personality comes from color and density, not type contrast.

### Hierarchy
- **Title** (600, 21px, 1.35 line-height, −0.3px tracking): the tender detail view's headline.
- **Body** (400–500, 12–14px, 1.45–1.8 line-height): list item titles, reasoning rows, long-form summary text (`.sbody` uses 1.8 for readability in paragraph form).
- **Label** (600–700, 10–11px, uppercase, 0.06–0.08em tracking): section labels (`.slabel`, `.kpi-lbl`, `.mcard-lbl`) and score/status pill text.
- **Numeric emphasis:** the AI score number is set at 38px/700 — the single largest text on the page, deliberately, since the score is the one number this tool exists to help you act on.

### Named Rules
**The Loud Number Rule.** Exactly one number per surface is allowed to be large and bold: the AI relevance score. Every other number (KPI tiles, meta-grid values) stays at 14–26px — noticeably smaller than the score.

## Layout

Fixed-viewport app shell: `html, body { height:100%; overflow:hidden }` — there is no page scroll. Instead, the list pane, the detail pane, and the market-intelligence view each scroll independently within a fixed 58px header. This is a tool meant to stay open in one browser tab all day, not a page someone scrolls through once.

Two structural grids exist: a `repeat(auto-fill, minmax(148px, 1fr))` meta-grid for tender detail metadata, and a fixed 4-column KPI row (`repeat(4,1fr)`) for market-intelligence summary tiles.

**Not currently responsive.** There are zero `@media` queries anywhere in the stylesheet, and the fixed 4-column KPI grid and 230px-wide horizontal-bar labels will not adapt to a narrow viewport. This is consistent with an internal desktop-only tool used at a workstation, not a gap introduced by accident — but it means the dashboard would break, not just look plain, on a tablet or narrow window. Flagged in the audit for a decision on whether that's acceptable long-term.

## Elevation & Depth

Flat by default, confirmed as deliberate. Nearly every panel, card, and tile (`.mcard`, `.kpi-tile`, `.chart-card`, `.etag`) is distinguished purely by a hairline border against a slightly different neutral background — never a shadow. Depth appears only for genuine overlays and one state indicator:

### Shadow Vocabulary
- **Overlay** (`box-shadow: 0 16px 36px rgba(31,30,29,0.18)`): the user-account dropdown menu — the only true floating/overlapping element in the UI.
- **Action glow** (`box-shadow: 0 2px 10px rgba(217,119,87,0.3)`, hover `0 3px 14px rgba(217,119,87,0.4)`): the primary accent button only — a colored glow, not a neutral drop shadow, reinforcing that Coral means "the action to take."
- **Selection stripe** (`box-shadow: inset 2px 0 0 var(--accent)`): a 2px inset accent bar on the selected list row — depth used as a state indicator, not literal elevation.
- **Focus ring** (`box-shadow: 0 0 0 2px var(--bg2), 0 0 0 4px var(--border2)`): keyboard-focus indicator on interactive chart bars.
- **Tooltip** (`box-shadow: 0 4px 16px rgba(0,0,0,0.18)`): the Market Intelligence chart hover tooltip — a floating overlay like the account dropdown, so it earns the same treatment.

### Named Rules
**The Flat-By-Default Rule.** Shadows never indicate "this card is above that card." They indicate one of exactly three things: a floating overlay, the primary action, or the current selection/focus. A new component that reaches for a shadow to look "elevated" is working against this system.

## Shapes

Three deliberate radius tiers, used consistently by role rather than by component type:
- **8–18px** (`--radius2`/`--radius` plus a couple of one-off larger values) on anything box-like: buttons, inputs, cards, tiles, panels, and a few larger containers (the empty-state icon at 18px, the Data Sources modal icon well at 16px).
- **20px pill / 50% circle** on anything status- or identity-like: filter chips at rest, score pills, status pills, portal tag, the user avatar, and small state dots (the "scoring in progress" pulse dot).
- **2–7px** on small inline elements nested inside a larger card: the scrollbar thumb (2px), the score bar track/fill (3px), legend swatches and hover-state chips (6–7px), the mono reference tag and spill status pills (4px).

No hard corners appear anywhere in the interface; the softest tier (2px) is still visibly rounded.

## Components

### Buttons
- **Shape:** 8px radius (`--radius2`).
- **Ghost/ Default** (`.btn`): white surface, hairline border, muted text; hovers to the Surface Hover background with full-strength ink text. This is the default for secondary actions (Refresh, Market Intel toggle).
- **Primary** (`.btn-accent`): solid Coral fill, white text, colored glow shadow; deepens to Coral Deep with a stronger glow on hover. Reserved for the single most important action on a surface (Score all).
- **Icon-only header controls** (user menu button) follow the ghost treatment with an avatar chip instead of a label.

### Chips / Pills
- **Filter chips** (`.fchip`): transparent at rest, muted text; solid Coral fill + white text when active. Binary on/off, never a third state.
- **Score & status pills** (`.score-pill`, `.spill`): the Tint-Not-Fill rule (see Colors) — 10px, 700-weight (score) or 600-weight uppercase (status) text on a tinted rounded-pill or 4px-radius background depending on role.
- **Info tags** (`.etag`, `.portal-tag`, `.ref-mono`): neutral Panel-background pills/tags for metadata that isn't a state — reference numbers, portal name, category label.

### Cards / Containers
- **Corner style:** 12px (`--radius`) for card-level containers (`.kpi-tile`, `.chart-card`, `.ai-card`), 8px for smaller nested cards (`.mcard`).
- **Background:** Panel (`#faf9f5`) or the dedicated `--chart-surface` alias (currently the same value) — never pure white, which is reserved for form controls and row-hover states.
- **Shadow strategy:** none (see Elevation & Depth) — a hairline border is the only separation from the canvas.
- **Border:** 1px hairline (`--border`) on every card.
- **Internal padding:** 12–20px depending on card density; the AI-score card gets the most (20px) as the visual anchor of the detail view.
- **Signature component — AI Score Card:** the one card that breaks flatness slightly, via a diagonal Coral→Purple gradient at very low opacity (8%/6%) plus a Coral-tinted border, marking it as the single most important panel on the detail view without resorting to a shadow.

### Inputs / Fields
- **Style:** white surface, hairline border, 8px radius, 32px left padding to make room for a leading icon.
- **Focus:** border color shifts to Coral — no glow, no ring, matching the flat system.

### Navigation
- **Header pills** (`.hpill`): a segmented-control pattern — transparent pills inside a bordered, rounded container; active state gets the Surface Active background. Used for the top-level Live/Market view switch.
- **Filter row:** a horizontal chip row beneath the header, not a sidebar — keeps the list pane maximally tall in the fixed-viewport layout.

## Do's and Don'ts

### Do:
- **Do** use Coral for exactly one meaning per screen: the primary action or the active/selected state. Never decorative.
- **Do** express status (positive/caution/danger) as a tinted badge (deep text on a ~12–14%-opacity fill of the same hue), never a solid-color badge.
- **Do** keep new cards and panels flat with a hairline border; reach for the Panel background before reaching for a shadow.
- **Do** keep the largest, boldest number on any given surface reserved for the thing the user is meant to act on (the AI score) — don't let a KPI tile or meta-grid value compete with it in size.

### Don't:
- **Don't** introduce a cool gray — every neutral in this system, including borders, is warm (a near-black at low opacity, not `#ccc`-style gray).
- **Don't** add a drop shadow to a card to "lift" it. The only shadows in the system are the account-menu overlay, the primary-button glow, the inset selection stripe, and keyboard focus rings.
- **Don't** assume this layout reflows for mobile — it currently doesn't (zero `@media` queries, fixed-height shell, fixed 4-column grid), so a new addition that only looks right at desktop width is consistent with today's system but should be flagged, not silently shipped, if wider device support becomes a goal.
