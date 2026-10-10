# Antigravity Prompt: Stage P1 (consistency and polish)

Run this after P0 is merged. Paste everything below this line into Antigravity. `StockPro_UIUX_Audit.md` should still be in the repo root.

---

You are a senior product designer and front-end engineer continuing the UI/UX overhaul of **StockPro Analytics**, an NSE pattern-scanner dashboard for retail momentum traders. Stage P0 (routing, auth, accessibility, layout bugs) is done. This is **P1: make the app feel like one coherent, finished product.** The rule of this stage is to build shared pieces once and reuse them everywhere.

## Context you must respect

- Vanilla HTML/CSS/JS, no framework, no build step. Main app is `index.html`. Styles in `apple-theme.css` plus inline `<style>`. Shared JS from P0 lives near the top of the inline script or in `/js/ui-core.js`.
- Apple-inspired design system: light `#F5F5F7`, accent `#0071E3`, dark navy `#0b0f19`, Inter, radius 10/16/20px. Keep the direction. Refine within it.
- **Don't change API contracts.** Don't touch `admin.html`.
- Data is end-of-day NSE. Users are Indian traders who think in lakh and crore.
- Finding IDs in brackets refer to `StockPro_UIUX_Audit.md`.

## Part A: shared foundations (do these first, then apply them)

### A1. Formatting utilities [G8, G9, G10]

Add one `fmt` module and replace every ad-hoc formatting call with it:

- `fmt.date(d)` gives `08 Oct 2026`. `fmt.dateShort(d)` gives `08 Oct`. Never M/D/Y, never ISO in visible UI (CSV exports can stay ISO).
- `fmt.price(n)` gives `₹1,178.00` (en-IN grouping, 2 decimals).
- `fmt.pct(n)` gives `+2.34%` / `−1.49%` (true minus sign U+2212, always signed).
- `fmt.qty(n)` gives Indian short scale: `12,152,819` → `1.22 Cr`, `845,000` → `8.45 L`, under 1 lakh → `84,500`.
- `fmt.inrLarge(n)` for fundamentals: `₹2.37 L Cr`, `₹529.95 K Cr`. Pick one convention (L Cr / K Cr), apply it consistently, and explain it in a tooltip on the column header.
- `fmt.ticker(sym)` strips `.NS`/`.BO` for display. `fmt.tickerLabel(sym, name)` renders `RELIANCE` with "Reliance Industries" as muted secondary text. Index symbols (`^NSEI`) map to their names ("Nifty 50"). Build the symbol to name map from whatever the backend already returns. If no name field exists, show the ticker only and leave a TODO.

Apply these to chart axes too: the volume axis currently shows "25M" while the legend says "1.39 Cr".

### A2. One symbol picker component [G11, C1, AL2, F1]

There are currently four different pickers: a 3,162-option native `<select>` (Chart, Stock History), a native `<datalist>` (Fundamentals), a custom search (Watchlist), and plain text (Alerts). Replace all of them with one `SymbolCombobox` (`createSymbolCombobox(inputEl, { onSelect })`):

- An input with a search icon. Placeholder "Search stock or company…".
- Filters on ticker prefix and company name substring. Shows the top 8 matches, each as ticker (bold), company name (muted) and exchange tag.
- With an empty query on focus, it shows "Recent" (last 6 picks, from localStorage) and then popular indices.
- Keyboard: ↑ ↓ to move, Enter to select, Escape to close. ARIA combobox pattern (`role="combobox"`, `aria-expanded`, `aria-activedescendant`, `role="listbox"`/`option`).
- Pressing `/` anywhere (outside inputs) focuses the page's primary symbol picker.
- Debounce 120ms. Never render more than 50 options.

### A3. Page header component [G14, G18]

Every section uses the same header:

```
[H1 page title]                         [secondary actions] [primary action]
[one-line description]   ·   As of close, 08 Oct 2026
```

- H1 22px/700. Description 14px muted. "As of" is a small muted chip.
- Export CSV always lives here as a secondary button, disabled (with tooltip "Nothing to export yet") while there's no data.
- Remove emoji and the icons before the page titles. Chart, Stock History, Scanner, Fundamentals and Scan Builder currently put their title inside the card. Move it out so it matches Watchlist, Alerts, Heatmap and Pattern screens.

### A4. Data table standard [G19, S2, T3, AL4, F2]

Create a `.data-table` class (and a small `renderTable(container, columns, rows, opts)` helper if useful) and apply it to every table in the app:

- Row height 40px (compact) / 48px (comfortable, via a density toggle stored in localStorage). Cell padding 8px 12px.
- Header: 12px/600 uppercase, letter-spacing 0.04em, muted, **single line** (`white-space: nowrap`), sticky top. Sortable columns show ↑/↓ and set `aria-sort`.
- Numbers: right-aligned, tabular-nums, formatted through `fmt`. Text left-aligned.
- First column (symbol or date) sticky left when the table scrolls horizontally.
- **One scroll container per table.** Remove nested scroll boxes that create double scrollbars.
- Positive and negative values: green/red text only, no filled pills, except status chips.
- Boolean signal columns render as a filled dot (true) or an empty dot (false), never "✓ True" / 0 / 1. If every row is true for a column (the scanned signal itself), hide that column.
- Don't repeat the symbol on every row when the table is for one symbol (Stock History).
- Footer: "Showing 500 of 1,284" plus "Export all" when results are capped.
- Empty state inside the table body: icon, one line, one action.

### A5. Dark mode and theme gaps [G17, G6]

- `:root { color-scheme: light; } .dark-mode { color-scheme: dark; }` so native date pickers, selects and scrollbars follow the theme.
- Themed scrollbars: `scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent;` plus WebKit equivalents.
- Fix unthemed components: Alerts filter chips (All/Active/Triggered), Heatmap "By Sector / By Industry" toggle, Pattern screen market-cap toggle. All become one `.segmented` component using theme tokens.

### A6. Icon and copy pass [G13, G15, G16]

- Remove all emoji used as icons (📊 📈 🚀 ⚡ and so on). Use Font Awesome or nothing.
- Remove icons from form labels. Keep them on nav items and buttons only.
- Replace native validation bubbles with inline errors: a 13px red message under the field, red border, `aria-invalid`, `aria-describedby`.
- Remove implementation leakage: "via yfinance", "Company Info (.info)" → "Company profile", "Latest News" → "News", `==` → "is", and `SIGNAL1` to `SIGNAL5`, `PC`, `STD`, `NR` → human names (ask, or leave a TODO comment if the meaning isn't clear from the code).
- Expand acronyms on first use and in tooltips: RS (Relative Strength), RCS (Relative Comparative Strength vs Nifty 500), PCS (Price Compression Setup), APB (All-time Peak Breakout).

## Part B: apply per screen

### B1. Sidebar and header [G2, G6]

- Brand "StockPro" only. Header shows the current page title (already set in P0).
- Nav icons are mono (current text color). Only the active item gets the accent color and background. Watchlist heart and Alerts bell are no longer red/yellow.
- Pattern screen names lead with the full name and the acronym as a muted suffix: "Price Compression Setup · PCS". Make the "Pattern screens" group collapsible, with state remembered.
- Footer: "© 2026 StockPro". Remove the duplicate theme toggle (now in the avatar menu).

### B2. Dashboard [D4]

- Add Widget modal: the whole row is the click target (not the small "+"), "Added" shown as a muted check that still meets contrast, and today's match count next to each signal.

### B3. Watchlist [W1]

- Neutral count chip ("0 saved", not a red pill). Shorter placeholder via the combobox. Export and Refresh disabled when empty. Subtitle drops "real-time".

### B4. Alerts [AL1, AL2, AL4]

- The form comes first. At 1024px and up it's one row: Stock (combobox), Condition (segmented: Above / Below), Target ₹, Note (optional), Save. The three KPI cards become one summary line: "0 active · 0 triggered · 25 slots left".
- Table: fits the width, single-line headers, a row-actions column (Edit, Delete with undo toast).
- One sentence stating how the user is notified (use whatever is true in the code: in-app, email, or both).

### B5. Stock chart [C2, C3, C4, C6, C7, C8]

- **Toolbar:** one row. Symbol combobox, then a Candles/Line/Bar/Area icon segmented control, then Linear/Log/% segmented, then an "Indicators" button opening a popover (EMA overlay, sub-panel RSI/MACD, signal markers), then Fit, Snapshot and Fullscreen as icon buttons with tooltips. On mobile: the symbol on its own row, everything else in one horizontally scrollable row.
- **Legend:** one line, Inter tabular-nums, no monospace: `RELIANCE  ₹1,178.00  −29.70 (−2.46%)   O 1,207.70  H 1,208.00  L 1,173.00  Vol 1.39 Cr`. Change colored by sign. On mobile only symbol, price and change show, and tapping toggles the rest. It must never overlap the price scale.
- **Volume** moves into the price pane as a histogram series (`priceScaleId: ''`, `scaleMargins: { top: 0.8, bottom: 0 }`, up/down colors at 50% opacity). Remove the separate volume chart so the crosshair and time axis are shared.
- **Confluence score colors:** 0 to 39 neutral grey, 40 to 59 amber, 60 to 79 light green, 80+ green. Red only when bearish warnings outnumber bullish signals.
- **RSI:** replace the broken arc gauge with a horizontal 0 to 100 bar, shaded zones under 30 and over 70, and a marker at the value.
- Section label "Indicators (as of close)". Raise the EMA50 legend color contrast to at least 3:1 against the chart background.

### B6. Stock history (formerly "Stock Scanner" / "Stock Data Analysis") [S1, S2, S3]

- Rename it in the nav, page title and route to **Stock History**.
- Replace the 81-column dump with column presets as tabs: **Price** (Date, O, H, L, C, Volume, % chg), **Candles**, **Moving averages**, **Breakout & base**, **Delivery**, **RSI**, plus a "Columns" chooser for custom sets (persisted). The date column stays sticky.
- Rows with zero volume and identical OHLC are muted and tagged "No trade".

### B7. Technical signal scanner [T1, T2, T3]

- Signal dropdown grouped with `<optgroup>` using the **same 7 categories** as the Add Widget modal (Candlestick patterns, Composite signals, Delivery signals, Narrow range, Price extremes, RSI signals, Volume & ADX).
- Date range: quick chips **Last session** (default) · 1W · 1M · Custom. Use the same defaults on Stock History and Scan Builder.
- Results use the table standard, with a capped-results footer.

### B8. Fundamentals [F1, F2]

- Symbol via the combobox. Remove "AAPL" and the ".NS suffix" help text.
- Values through `fmt.inrLarge`. A **₹ / $** segmented control replaces the "Show in $ USD" button.
- Column headers as fiscal labels ("FY26", "FY25"). The line-item column is sticky. Export moves to the page header.

### B9. Scan builder [SB1, SB2]

- Operators by signal type. Boolean: "is true / is false". Numeric: "above / below / between", with a value input (two for "between") that appears when needed.
- Hint box in Inter, not monospace. 16px gap between the description and the first field. "Save scan" as a secondary outline button that looks enabled.
- Saved scans: name, a one-line condition summary ("Delivery > 80% AND RS rank > 80"), "42 matches · 08 Oct", Run as primary, Edit and Delete inside a "⋯" menu, and delete with an undo toast (soft delete for 5 seconds before the API call).

### B10. Heatmap [H3, H4, H5]

- "As of close, 08 Oct 2026" chip. Themed segmented toggle.
- Replace the five KPI cards with one breadth bar (advancing / unchanged / declining, proportional, with counts) plus "Turnover ₹1.1 L Cr" beside it. On mobile it's a single compact block.
- **Treemap colors:** a diverging scale clamped at ±3% (for example, −3% `#C9372C`, 0% neutral `#3A3F4B` in dark or `#E5E5EA` in light, +3% `#2E9E5B`), interpolated, so −0.2% looks very different from −3%. Add a small legend.
- Remove the decorative wave graphic and the two-letter badges from tiles. Hide text on tiles under 60 × 40px (the tooltip carries it). Tiles must not be clipped at the right edge. Hide the tooltip on `mouseleave` and on scroll.
- Sector-list sparklines colored by their own net change.

### B11. Pattern screens [P3, P4, P6]

- The stage selectors (Forming / Fresh breakouts / Climbing, and "Latest Date / Last 5 Days" on Stage 2) become real tabs (`role="tablist"`): label plus count badge, with a filled selected state. The one-line description sits below the tabs, not inside them. Rename vague labels ("Latest Date" → "Broke out today", "Last 5 Days" → "Last 5 sessions").
- Market-cap toggle: "All caps" / "> ₹500 Cr", no wrapping.
- FA icons instead of emoji. Regular-weight muted text instead of italics. Hide the orphan first x-axis tick.

## Verification before you finish

At **375, 768, 1024 and 1440px**, light and dark:

- [ ] No date anywhere in M/D/Y or ISO (except CSV). No `.NS` visible. Volumes in L/Cr.
- [ ] The same combobox on Chart, Stock History, Fundamentals, Watchlist and Alerts. Keyboard and `/` shortcut work.
- [ ] Every page uses the same header. Export is disabled when empty.
- [ ] Every table: single-line headers, right-aligned tabular numbers, one scrollbar, sticky first column.
- [ ] No emoji icons. No white chips, toggles or scrollbars in dark mode.
- [ ] Chart legend never overlaps the price scale. Volume is in the same pane. No `00:00:00`.
- [ ] Treemap shows graded colors with a legend.
- [ ] Light and dark contrast still at least 4.5:1 for body and muted text.
- [ ] No console errors. P0 behaviors (routing, modals, focus ring) still work.

Finish with a summary of files changed, new shared helpers, and any TODOs left for missing data fields.
