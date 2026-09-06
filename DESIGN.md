---
name: PeoplePay360
description: A civic-operations design system for precise, auditable HR and payroll work.
colors:
  command-rail: "#13243A"
  action-teal: "#0B6670"
  action-teal-hover: "#0F7C85"
  critical-vermilion: "#D14B32"
  success-green: "#197455"
  warning-ochre: "#9D650E"
  paper-ground: "#F4F6F8"
  work-surface: "#FFFFFF"
  primary-ink: "#172033"
  secondary-ink: "#526176"
  rule: "#CBD2DB"
typography:
  display:
    fontFamily: "Source Sans 3 Variable, Segoe UI, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Source Sans 3 Variable, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.4
  title:
    fontFamily: "Source Sans 3 Variable, Segoe UI, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Source Sans 3 Variable, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.43
    fontFeature: "rlig, calt, tabular-nums"
  label:
    fontFamily: "Source Sans 3 Variable, Segoe UI, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "0.06em"
rounded:
  compact: "2px"
  control: "4px"
  surface: "6px"
spacing:
  hairline: "1px"
  xs: "4px"
  sm: "8px"
  md: "16px"
  panel: "20px"
  canvas: "24px"
components:
  button-primary:
    backgroundColor: "{colors.action-teal}"
    textColor: "{colors.work-surface}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: "36px"
  button-outline:
    backgroundColor: "{colors.work-surface}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: "36px"
  input:
    backgroundColor: "{colors.work-surface}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "36px"
  status-badge:
    typography: "{typography.label}"
    rounded: "{rounded.compact}"
    padding: "2px 8px"
  ruled-surface:
    backgroundColor: "{colors.work-surface}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.control}"
    padding: "20px"
  command-item-active:
    backgroundColor: "{colors.action-teal}"
    textColor: "{colors.work-surface}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "36px"
---

# Design System: PeoplePay360

## Overview

**Creative North Star: "Civic Operations Standard"**

PeoplePay360 should feel like a governed public-service operations register: calm, exact, legible, and built for sustained daily use. The interface uses a compact command rail, ruled white work surfaces, and dense ledgers so business state leads and decoration recedes.

The system is refined and restrained. It rejects generic dashboard theater—floating metric-card mosaics, glass, gradients, glow, novelty typography, and decorative pseudo-logos—in favor of explicit hierarchy and trustworthy material simplicity.

**Key Characteristics:**

- A persistent navy command rail and white utility bar orient every authenticated screen.
- Cool paper ground separates the application canvas from white working surfaces.
- Teal identifies actions and current state; vermilion is reserved for critical state.
- Thin rules, compact controls, and aligned numerals support fast operational scanning.

## Colors

The palette is a civic combination of institutional navy, deep teal, warm critical color, and cool paper neutrals.

### Primary

- **Command Navy:** The persistent command rail and modal scrim establish place and authority.
- **Operations Teal:** Primary actions, active navigation, links, focus, and selected state share one controlled accent.

### Secondary

- **Critical Vermilion:** Destructive actions, blocking findings, and refused/error states only.
- **Success Green:** Approved, complete, and healthy operational state.
- **Warning Ochre:** Pending, advisory, and attention-required state.

### Neutral

- **Cool Paper:** The application ground behind all work surfaces.
- **Work Sheet:** The white surface for forms, tables, dialogs, and registers.
- **Ledger Ink:** Primary text and high-value record content.
- **Secondary Ink:** Descriptions, labels, and supporting metadata.
- **Cool Rule:** Borders and dividers; the main depth device of the system.

**The One Operational Accent Rule.** Teal is the only general-purpose accent; semantic colors appear only when the data carries that meaning.

## Typography

**Display Font:** Source Sans 3 Variable (with Segoe UI and sans-serif fallback)  
**Body Font:** Source Sans 3 Variable (with Segoe UI and sans-serif fallback)  
**Label/Mono Font:** Source Sans 3 Variable for labels; the platform monospace stack is limited to identifiers and calculations.

**Character:** A compact humanist grotesk gives the product the directness of a civic information system without becoming sterile. Tabular numerals keep payroll columns and summary values aligned.

### Hierarchy

- **Display** (semibold, 30px): Limited to the login statement or a similarly singular product-level message.
- **Headline** (semibold, 20px): Page titles and primary record titles.
- **Title** (semibold, 18px): Ruled panel and dialog titles.
- **Body** (regular, 14px): Forms, table cells, explanations, and operational copy.
- **Label** (semibold, 11px, modest tracking): Table headers, status labels, and compact metadata.

**The Working-Type Rule.** Use size and weight to express hierarchy; do not add decorative eyebrows above headings.

## Layout

Authenticated desktop screens use a 224px fixed command rail, a 56px utility bar, and a flexible scrollable work canvas with 24px desktop padding. Content remains full-width because records and tables need the space. Small screens replace the rail with an off-canvas drawer and reduce canvas padding to 16px.

The core rhythm is 4px-based, with 8px control gaps, 16px section gaps, 20px panel padding, and 24px canvas spacing. Related metrics form one divided register. Tables scroll horizontally rather than collapsing columns into ambiguous cards. Coarse-pointer controls and navigation targets maintain a 44px minimum hit height.

**The Register-before-Grid Rule.** When adjacent values describe one operational subject, group them inside one ruled register instead of making a floating card for each value.

## Elevation & Depth

The system is flat by default. Cool paper, white surfaces, one-pixel rules, and tonal fills create depth. Shadows are limited to transient layers that must sit above live work: dialogs, toasts, and search popovers.

- **Transient layer:** A soft navy-tinted ambient shadow identifies temporary overlays without making ordinary panels float.

**The Flat-at-Rest Rule.** Persistent cards, tables, and filters never use a shadow; elevation communicates temporary z-order only.

## Shapes

Corners are disciplined and quiet: 2px for badges, 4px for controls and most surfaces, and 6px as the upper bound for major containers. Borders stay one pixel. Circular geometry is reserved for inherently circular indicators or progress marks, not general-purpose labels.

## Components

### Buttons

- **Shape:** Compact rectangular control with a 4px radius.
- **Primary:** Solid operations teal with white semibold text; default height is 36px.
- **Hover / Focus:** A small teal tone shift on hover and a visible teal focus ring; no scaling or decorative shadow.
- **Outline / Ghost:** White or transparent surfaces with ink text and neutral hover fill.

### Chips

- **Style:** Rectangular 2px-radius status badges with a pale semantic fill, matching border, and dark semantic text.
- **State:** Chips report state; they do not substitute for primary actions.

### Cards / Containers

- **Corner Style:** Restrained 4–6px corners.
- **Background:** White work sheet over cool paper.
- **Shadow Strategy:** None at rest.
- **Border:** One-pixel cool rule; titled surfaces use a ruled header.
- **Internal Padding:** 20px standard, reduced only for ledger density.

### Inputs / Fields

- **Style:** White field, one-pixel rule, 4px radius, 36px desktop height.
- **Focus:** Border shifts to teal with a low-opacity teal ring.
- **Error / Disabled:** Errors use vermilion text near the field; disabled fields use cool paper and reduced opacity.

### Navigation

The command rail uses white and blue-gray text on navy. The current module is a solid teal rectangular item; inactive items use a quiet translucent hover. On mobile, the rail becomes an off-canvas drawer with a plain navy scrim and 44px targets.

### Ledger Table

Table headers are 40px high with compact uppercase labels. Body rows use 12px vertical padding, aligned tabular numerals, one-pixel dividers, and a pale-teal hover or selected fill. Wide tables remain scrollable.

## Do's and Don'ts

### Do:

- **Do** make current module, record state, filters, warnings, and the primary action scannable in that order.
- **Do** use rules and tonal surfaces to establish hierarchy.
- **Do** keep operational tables dense, aligned, and horizontally scrollable when necessary.
- **Do** preserve semantic colors for real business state and maintain keyboard-visible focus.

### Don't:

- **Don't** use gradients, glass, glow, backdrop blur, or decorative shadows on persistent surfaces.
- **Don't** create floating metric-card mosaics when one ruled register expresses the relationship.
- **Don't** introduce pseudo-logo emblems, decorative eyebrows, novelty type, side stripes, or pill-shaped general labels.
- **Don't** change behavior, authorization, validation, data contracts, or business-state meaning to solve a visual problem.
