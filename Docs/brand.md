# Sinergy Flow — Brand Guidelines

Distilled from `Presentation brand book.pdf` (33 pages, by Carbonara Agency, London; Italian original). This file is the working reference for building anything that carries the brand, including the SynergyScan UI. If it disagrees with the PDF, the PDF wins.

## 1. Brand essence

Sinergy Flow is a startup building **redox flow battery** energy storage. The brand is meant to read as **advanced technology** and **professionalism**, with **energy**, **renewability** and **sustainability** as the underlying story.

Branding "is more than a logo and a colour palette; it is the reflection of a company's values, mission and identity." The goal of the guidelines is that every product is meaningful, consistent and memorable.

## 2. Logo

### Concept
- The key idea is the **infinity symbol**, recalling redox flow battery technology.
- The symbol is built by splitting the letter **"O"** of *Flow* into two arcs.
- **Arrows** added to the arc ends signal renewability and sustainability.

### Logotype
- Two-line wordmark: **SINERGY** on top, **FLOW** below.
- The type goes **from Bold to Thin**: SINERGY is bold, wide and technical; FLOW is thin and widely letter-spaced. This represents the energy transition.
- The **mark replaces the "O"** in FLOW.
- **Yellow** is the colour of sulphur, and stands for energy and the sun. In the full-colour logo only the mark is yellow; the lettering is Off Black.

### Mark
Two interlocking half-rings (a "U" on top, an inverted "U" below offset to the right), each ending in a small arrow notch. It can be used **alone** for:
- Favicon
- Texture / pattern
- Product labels

### Versions
| Version | Notes |
|---|---|
| Horizontal and vertical (stacked) logo | Both provided in the book |
| Full colour | Off Black lettering, Main Yellow mark, on white/light |
| Black / white | Pure black on white, and white on black; yellow mark dropped |
| Reversed (derived) | White lettering with the yellow mark on Off Black / Grey |

### Sizes
The book shows the logo at heights of **30, 45, 60, 80, 90, 100, 120 and 150 px**. 30 px is the smallest shown; do not go below it.

### Do / don't (derived, not stated in the book)
- Don't redraw or re-space the mark; use the supplied artwork.
- Don't put the yellow mark on a yellow or pale-yellow background.
- Don't recolour the lettering outside Off Black / white.

## 3. Typography

Two faces, chosen to say "advanced technology" and "professionalism".

| Role | Typeface | Weight | Notes |
|---|---|---|---|
| Headlines, display | **Conthrax** | SemiBold ("Conthrax SB") | Wide, technical display face. Titles, labels, large numerals |
| Body, UI text, long copy | **Proxima Nova** | Regular | Neutral sans |

Both are licensed commercial fonts and are **not bundled**. Where unavailable, fall back as follows:

```css
--font-head: "Conthrax SB", "Conthrax", "Eurostile", "Bank Gothic", "Segoe UI Semibold", system-ui, sans-serif;
--font-body: "Proxima Nova", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
```

The brand book's own layout pairs large heavy headings with a lighter grey subtitle beneath, and small tracked-out labels.

## 4. Colour

The palette moves between **greys and yellow**: professionalism and energy, with strong contrast.

| Name | Hex | RGB | Use |
|---|---|---|---|
| **Main Yellow** | `#FFCD00` | 255 205 0 | Brand colour. The mark, primary actions, key highlights, focus |
| **Secondary Yellow** | `#FFE787` | 255 231 135 | Hover states, soft fills, secondary accents |
| **Off Black** | `#231F20` | 35 31 32 | Primary text, logotype, dark backgrounds |
| **Grey** | `#343232` | 52 50 50 | Secondary dark surface, cards on Off Black |

### Usage rules (derived)
- Neutral surfaces are white / light grey with Off Black text, or Off Black / Grey in dark contexts.
- **Yellow is an accent, not a text colour on light backgrounds**: `#FFCD00` on white is about 1.4:1 contrast. Use it for fills, borders, rules and marks.
- **Text on yellow is always Off Black.** Never white on yellow.
- Yellow text is fine on Off Black / Grey.
- Keep yellow rare enough to stay meaningful.
- Functional status colours (success green, warning orange, error red) are not part of the brand palette. Apps may use muted versions where meaning requires it, but they must never replace yellow as the brand accent.

## 5. Stationery

Stationery must stay consistent with the visual identity: logo, typography, palette and other brand resources.

- **Business card** (example): name in caps, role, phone, web (`www.sinergyflow.com`), address, email (`info@sinergyflow.com`).
- **Letterhead**: logo at the head, body in Proxima Nova, date and recipient block above the letter.
- Other items appear in the book; all use the palette above with generous white space.

## 6. Iconography

- Icons are used across the website, social media and most digital and physical products.
- A single graphic style is set first; every pictogram is then drawn in it. The book shows an icon pack of four icons, each with a title and short description.
- Style (derived): simple line/outline pictograms matching the thin strokes of the mark. Off Black on light, white on dark, Main Yellow for emphasis.

## 7. Pattern

- Built from **the mark**, repeated identically and continuously.
- Shown as thin outline arcs tiled in offset rows (each row shifted by half a tile) on white.
- Uses: merchandising, backgrounds. Keep it low-contrast so it never competes with content.

## 8. Imagery & tone

- Photography shown: wind turbines, solar panels and battery cabinets in clean, bright outdoor scenes; metallic 3D renders of the mark on dark slate. The look is clean, technical, renewable-energy.
- Tone (derived): clear, professional, optimistic. Plain statements over hype.

## 9. Applying the brand to SynergyScan

Implemented in `src/synergyscan/web/static/app.css` and `index.html`.

| Element | Treatment |
|---|---|
| Header | Off Black bar, Main Yellow 4px bottom rule, stacked SINERGY / FLOW wordmark with inline SVG mark, yellow "SCAN" product tag |
| Primary button | Main Yellow fill, Off Black text; hover Secondary Yellow |
| Scan field | 2px Main Yellow border |
| Cards | White (light) / Grey (dark) with a 3px Main Yellow top rule |
| Page background | Light warm grey (light) / Off Black (dark), following the OS setting |
| Headings, table headers, big quantity | Conthrax SB (fallback stack above), tracked uppercase for small labels |
| Body & controls | Proxima Nova (fallback stack above) |
| Focus ring | Off Black (light) / Main Yellow (dark) |
| Status (ok / low / reorder) | Muted green / orange / red, functional and distinct from brand yellow |
| Favicon | The yellow mark, inline SVG |

The inline mark is a simplified approximation of the official artwork. **Replace it with the official SVG** when available.

## 10. Open items

- Obtain the official logo files (SVG) and licensed Conthrax / Proxima Nova webfonts, bundle them, then drop the fallbacks.
- The book has no clear-space spec or dark-mode guidance; rules marked "derived" are our interpretation.
