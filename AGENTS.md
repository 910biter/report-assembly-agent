# AGENTS.md — UI / UX Design & Frontend Guidelines

## 1. Purpose

This document defines the visual language, interaction principles, component conventions, and implementation constraints for this project.

All AI coding agents and human contributors MUST follow these rules when creating or modifying frontend UI.

The target aesthetic is:

> **Quiet Luxury / Modern SaaS / AI-Native**

The product should feel:

- Premium
- Precise
- Modern
- Calm
- Technical
- Polished
- Cohesive

The UI should resemble the design quality of products such as:

- Linear
- Vercel
- Raycast
- Stripe Dashboard
- GitHub's newer interfaces
- Modern AI developer tools

Do **not** imitate any product literally.

The goal is to achieve the same level of restraint, hierarchy, detail, and polish.

---

# 2. Core Stack

Primary frontend stack:

- Vue 3
- TypeScript
- shadcn-vue
- Reka UI
- Tailwind CSS
- Lucide Icons
- Motion for Vue when advanced motion is necessary

Use existing project abstractions before introducing new dependencies.

## Component priority

When implementing UI, use components in this order:

1. Existing project components
2. Existing shadcn-vue components
3. Reka UI primitives
4. Small local custom components
5. New dependencies only when clearly justified

Do NOT introduce another general-purpose component library such as:

- Element Plus
- Ant Design Vue
- Vuetify
- PrimeVue
- Naive UI

unless explicitly requested.

The design system must remain visually coherent.

---

# 3. Design Philosophy

## 3.1 Fancy does not mean busy

The interface should feel expensive because of:

- typography
- spacing
- alignment
- hierarchy
- subtle borders
- careful contrast
- restrained animation
- thoughtful interaction feedback

It should NOT rely on:

- excessive gradients
- oversized shadows
- neon effects everywhere
- constant animations
- excessive glassmorphism
- too many cards
- decorative elements without purpose

Use the following rule:

> **95% restraint, 5% delight.**

The 5% may appear in:

- command palette
- important hero areas
- hover states
- active navigation
- dialogs
- onboarding moments
- empty states
- loading states
- charts
- important primary actions

---

# 4. Visual Direction

The preferred visual direction is:

> **Linear × Vercel × Raycast**

Characteristics:

- Dark-first visual language
- Neutral surfaces
- High-quality typography
- Thin borders
- Soft depth
- Minimal color
- Strong information hierarchy
- Compact but breathable layout
- Selective glow and glass effects
- Smooth micro-interactions

Avoid a traditional enterprise admin-dashboard appearance.

The interface should NOT resemble a generic:

- Bootstrap admin template
- Element Plus admin panel
- low-code dashboard
- CRUD management system

even when implementing CRUD functionality.

---

# 5. Color System

## 5.1 Use semantic tokens

Never scatter arbitrary hex colors throughout components.

Colors should primarily come from semantic CSS variables or Tailwind design tokens.

Preferred conceptual tokens:

```css
--background
--foreground

--surface
--surface-raised
--surface-hover

--muted
--muted-foreground

--border
--border-strong

--primary
--primary-foreground

--accent
--accent-foreground

--destructive
--warning
--success

--ring
```

Components should use semantic tokens instead of hard-coded visual values whenever possible.

---

# 6. Dark Theme

Dark mode is the primary visual reference.

Recommended visual relationships:

```text
Page Background
#09090B

Sidebar / Secondary Surface
#0C0C0F

Card / Raised Surface
#101014

Hover Surface
#16161B

Strong Hover
#1B1B21
```

These values are reference targets, not an excuse to hard-code them repeatedly.

Prefer design tokens.

## Text hierarchy

Approximate visual hierarchy:

```text
Primary:
rgba(255, 255, 255, 0.92)

Secondary:
rgba(255, 255, 255, 0.60)

Muted:
rgba(255, 255, 255, 0.38)

Disabled:
rgba(255, 255, 255, 0.25)
```

Never make every piece of text pure white.

Hierarchy is important.

---

# 7. Light Theme

Light mode should NOT simply invert dark mode.

Recommended visual relationships:

```text
Background
#FAFAFA / #F9F9FA

Primary Surface
#FFFFFF

Secondary Surface
#F6F6F7

Hover
#F1F1F3
```

Borders should be subtle.

Avoid strong gray outlines around every component.

---

# 8. Accent Color

Use ONE primary brand accent.

Preferred family:

> Deep indigo / restrained violet

Example visual direction:

```text
#5B40D6
#7C5CFF
#B5A7FF
```

Do NOT use multiple unrelated accent colors for decoration.

Additional colors such as red, orange, green, and blue should primarily communicate semantic state:

- Green → success
- Yellow / orange → warning
- Red → destructive/error
- Blue → information
- Primary accent → selected / active / primary action

Color should communicate meaning.

---

# 9. Typography

Typography is one of the highest-priority parts of the design.

Preferred sans-serif fonts:

1. Geist
2. Inter
3. system-ui

For technical content:

- Geist Mono
- JetBrains Mono

Use monospace selectively for:

- IDs
- tokens
- API keys
- hashes
- IP addresses
- model names
- metrics
- timestamps when appropriate
- code
- developer-facing technical values

Do not use monospace for normal body text.

---

# 10. Type Hierarchy

Recommended scale:

```text
Hero
32–40px
font-weight: 600
letter-spacing: -0.025em

Page Title
22–28px
font-weight: 600
letter-spacing: -0.02em

Section Title
16–18px
font-weight: 600

Body
14px
font-weight: 400

Compact UI
13px

Metadata / Label
12–13px

Tiny technical metadata
11–12px
```

Avoid unnecessarily bold typography.

Most headings should use:

```text
font-weight: 500–600
```

rather than 700–900.

---

# 11. Spacing System

Use a consistent spacing scale.

Prefer Tailwind spacing primitives.

Typical spacing:

```text
4px
8px
12px
16px
20px
24px
32px
40px
48px
64px
```

Avoid random values such as:

```text
margin-top: 17px
padding: 13px 21px
gap: 19px
```

unless necessary for pixel-level alignment.

## General principle

UI should be:

> compact, but never cramped.

Developer tools can have high information density, but hierarchy must remain obvious.

---

# 12. Layout

Prefer clear layout hierarchy.

Typical application structure:

```text
App
├── Sidebar
├── Topbar
└── Main
    ├── Page Header
    ├── Primary Content
    └── Secondary Content
```

Main content should usually have a reasonable maximum width.

Do not stretch every line of content to the full browser width.

For content-heavy pages, use structures such as:

```text
max-w-7xl
max-w-[1400px]
```

depending on the information density.

---

# 13. Sidebar

The sidebar should feel integrated into the product rather than like a detached menu.

Preferred characteristics:

- subtle background separation
- weak or no external shadow
- thin right border
- compact navigation
- clear active state
- grouped sections
- consistent icon size
- restrained typography

Active navigation should NOT look like a giant primary button.

Prefer:

- subtle elevated surface
- slightly brighter text
- small indicator
- muted accent background

Example:

```text
Inactive:
text-muted-foreground

Hover:
bg-white/[0.04]

Active:
bg-white/[0.07]
text-foreground
```

Optionally add a subtle indicator or glow.

---

# 14. Cards

Do NOT put every piece of content inside a card.

This is extremely important.

Avoid:

```text
Card
  Card
    Card
      Card
```

Use cards only when content logically belongs to an independent surface.

Prefer separating regions using:

- whitespace
- typography
- thin dividers
- background changes
- alignment

instead of excessive boxes.

## Good card style

Cards should generally use:

```text
subtle background
1px low-contrast border
12–16px radius
very soft shadow if necessary
```

Avoid:

```text
large dark shadow
thick border
20–30px radius everywhere
strong gradient background
```

---

# 15. Borders

Borders are more important than shadows in this design system.

Dark mode reference:

```text
Normal:
rgba(255, 255, 255, 0.07)

Strong:
rgba(255, 255, 255, 0.11)

Hover:
rgba(255, 255, 255, 0.14)
```

Borders should often be barely noticeable until the user looks for them.

For premium raised surfaces, an optional subtle top highlight may be used.

For example:

```css
box-shadow:
  inset 0 1px 0 rgba(255,255,255,0.04);
```

Do not overuse it.

---

# 16. Shadows

Shadows must be:

- large
- soft
- subtle

Avoid small, harsh shadows.

Bad:

```css
box-shadow: 0 2px 4px rgba(0,0,0,.4);
```

Better visual direction:

```css
box-shadow:
  0 12px 40px rgba(0,0,0,.18),
  0 2px 8px rgba(0,0,0,.12);
```

Use significant shadows mostly for:

- dialogs
- popovers
- dropdowns
- floating panels

Normal cards often need no shadow at all.

---

# 17. Border Radius

Use a controlled radius system.

Recommended base:

```text
Small controls: 6–8px
Buttons / Inputs: 8–10px
Cards: 12–16px
Dialogs: 16–20px
Large hero surfaces: max 20–24px
```

Do NOT make every component `rounded-2xl`.

Do NOT use pill shapes unless the component semantically benefits from it.

Pills are appropriate for:

- tags
- statuses
- compact filters
- segmented controls
- avatars

They are usually not appropriate for:

- every button
- inputs
- large cards
- table rows

---

# 18. Buttons

Buttons should feel precise and responsive.

Variants:

- Primary
- Secondary
- Ghost
- Destructive
- Icon

Avoid creating additional arbitrary button styles.

## Primary button

Primary buttons should be visually strong but not excessively bright.

Use them sparingly.

There should usually be only one obvious primary action per local context.

## Hover

Preferred behavior:

```text
background slightly brighter
border slightly stronger
optional translateY(-1px)
```

## Active

Optional:

```text
scale(0.98)
```

Duration should be short.

Do not make buttons bounce.

---

# 19. Inputs

Inputs should feel integrated into the surface.

Preferred:

- subtle border
- darker or lighter surface difference
- clear focus ring
- moderate height
- compact padding

Focus should be obvious without becoming neon.

Avoid extremely thick focus rings.

Use placeholder text with lower contrast.

---

# 20. Tables

Tables must NOT look like raw HTML tables or generic admin-dashboard tables.

Preferred characteristics:

- no heavy outer border
- subtle row separators
- generous horizontal alignment
- compact row height
- muted column headers
- hover feedback
- sticky headers when useful
- right-aligned numeric values
- monospace for technical numeric/ID data where appropriate

Actions should generally appear:

- on hover
- inside a kebab menu
- or in a restrained right-side action area

Avoid placing five permanent buttons in every row.

---

# 21. Metrics / Statistics

Do NOT automatically place every metric in a generic card.

Prefer typography-led metric presentation.

Example:

```text
Revenue                         +12.4%

$12,830.42

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
sparkline
```

instead of:

```text
┌─────────────────┐
│ Revenue         │
│ $12,830         │
│ +12%            │
└─────────────────┘
```

Numbers should have strong hierarchy.

Supporting labels should be subdued.

---

# 22. Charts

Charts should look integrated with the interface.

Use:

- minimal gridlines
- muted axes
- restrained colors
- one dominant series
- low-opacity secondary series
- high-quality tooltip
- subtle hover interaction

Avoid rainbow charts unless categories require distinct colors.

Do not show visual elements that do not communicate information.

---

# 23. Icons

Use Lucide icons by default.

Rules:

- Do not mix icon families casually.
- Use consistent stroke width.
- Most icons should be 14–18px.
- Navigation icons usually 16–18px.
- Avoid oversized 24px icons inside compact interfaces.
- Icons should usually support text rather than replace it when ambiguity exists.

Do not use emoji as primary interface icons.

---

# 24. Empty States

Empty states are a good place for controlled personality.

A good empty state may contain:

- subtle icon / illustration
- concise heading
- one-line explanation
- one clear action

Avoid giant empty-state illustrations.

Avoid excessive marketing copy inside application screens.

---

# 25. Loading States

Prefer skeletons over spinners for page content.

Use spinner primarily for:

- small inline actions
- button submissions
- indeterminate short actions

Skeletons should be subtle.

Shimmer effects, when used, must be slow and low contrast.

Avoid aggressive moving gradients.

---

# 26. Dialogs

Dialogs should feel like elevated premium surfaces.

Recommended characteristics:

- slightly stronger background than page
- strong but soft shadow
- thin border
- backdrop blur where appropriate
- restrained entrance motion
- generous internal spacing

Animation can combine:

```text
opacity
scale 0.97 → 1
small translateY
```

Do not animate dialogs from extreme positions unless the component is specifically a drawer.

---

# 27. Dropdowns / Popovers

These are appropriate places for subtle glass effects.

Possible visual treatment:

```text
semi-transparent surface
backdrop-blur-xl
thin bright border
soft deep shadow
```

Do not apply glassmorphism to every surface in the application.

---

# 28. Glassmorphism

Glass is a seasoning, not the design language.

Allowed primarily for:

- floating header
- command palette
- popover
- dropdown
- context menu
- overlay toolbar
- transient floating panel

Avoid glass for:

- every card
- entire dashboard
- large data tables
- forms
- permanent page surfaces

---

# 29. Gradients

Gradients must be subtle.

Allowed:

- faint radial background glow
- hero accent
- subtle primary-button highlight
- selected state
- onboarding / empty state illustration

Avoid:

```text
purple → pink → blue everywhere
```

The user should often notice the visual depth before noticing that a gradient exists.

---

# 30. Background Effects

For important screens, subtle depth may be added using:

- radial gradients
- faint grid
- extremely subtle noise
- low-opacity glow

Noise texture should generally remain under approximately 1–2% perceived opacity.

Decorative effects must never interfere with content readability.

---

# 31. Motion

Motion must communicate:

- cause and effect
- hierarchy
- state change
- spatial relationship

Motion should NOT exist merely because animation is possible.

## Recommended durations

```text
Micro interaction:
100–160ms

Hover:
120–180ms

Dropdown:
150–200ms

Dialog:
180–250ms

Large layout transition:
200–300ms
```

Avoid long animations above ~400ms for routine interface actions.

## Preferred easing

Use:

```text
ease-out
```

for most UI transitions.

Spring motion may be used for:

- dialog entrance
- drawer
- command palette
- selected indicator
- drag interactions

Keep spring behavior restrained.

No cartoon bouncing.

---

# 32. Micro-interactions

Micro-interactions are highly encouraged when subtle.

Good examples:

- button depresses slightly on click
- navigation indicator smoothly moves
- tooltip fades in
- dropdown scales from 98% to 100%
- icon opacity changes on hover
- row action fades into view
- number smoothly updates
- selected tab indicator slides
- copy icon briefly turns into checkmark

Bad examples:

- continuously floating cards
- pulsating buttons
- bouncing navigation
- spinning icons without reason
- exaggerated cursor-following effects

---

# 33. Command Palette

For a developer / AI / productivity product, a command palette is strongly encouraged.

Typical shortcut:

```text
⌘ K
Ctrl K
```

It may provide:

- navigation
- search
- actions
- project switching
- model switching
- quick settings

The command palette may be one of the more visually polished elements in the application.

---

# 34. Tooltips

Use tooltips for:

- icon-only controls
- unfamiliar actions
- keyboard shortcuts
- truncated information

Do not use a tooltip to explain obvious text buttons.

When possible, display shortcuts:

```text
Search      ⌘K
Settings    ⌘,
```

This improves the professional-tool feeling.

---

# 35. Toasts

Toasts should:

- be compact
- appear only when useful
- use semantic iconography
- disappear automatically for routine success states

Do not show a success toast after every trivial interaction.

Prefer inline state changes when possible.

---

# 36. Status Indicators

Status should usually combine:

```text
color + text
```

rather than color alone.

Examples:

```text
● Online
● Processing
● Failed
```

Keep the dot subtle.

Do not create giant colored badges unless emphasis is necessary.

---

# 37. Responsive Behavior

Desktop is important, but layouts must remain usable across reasonable viewport sizes.

Do not solve responsiveness by simply shrinking everything.

At narrower widths:

- collapse sidebar
- stack secondary panels
- move nonessential actions into menus
- reduce horizontal padding
- preserve readable typography

Critical actions must remain accessible.

---

# 38. Accessibility

Premium visual design must remain accessible.

Requirements:

- proper semantic HTML
- keyboard navigation
- visible focus state
- ARIA where required
- sufficient contrast
- dialogs must trap focus correctly
- dropdowns must support keyboard control
- buttons must be real buttons
- links must be real links

This is one reason Reka UI primitives should be preferred for complex interactions.

Do not implement custom div-based controls when accessible primitives already exist.

---

# 39. Component Architecture

Do not create giant page components.

Extract reusable structures.

Preferred examples:

```text
PageHeader
SectionHeader
EmptyState
Metric
StatusBadge
DataTable
ConfirmDialog
SettingsSection
SearchCommand
```

But avoid premature abstraction.

Do not create a component merely because markup appears twice.

Create abstractions when:

- the design behavior should remain consistent
- the component has meaningful API boundaries
- the component is likely to evolve independently
- repeated implementation risks visual inconsistency

---

# 40. Tailwind Guidelines

Prefer readable utility composition.

Avoid enormous single-line class strings when the component becomes difficult to understand.

Use utilities first.

Use CSS variables for design tokens.

Use component variants for repeated styles.

When multiple component states exist, prefer a variant utility such as `class-variance-authority` if already available.

Avoid arbitrary values when a design-system token exists.

Bad:

```text
mt-[17px]
rounded-[13px]
text-[#d3d3d8]
```

Preferred:

```text
mt-4
rounded-xl
text-muted-foreground
```

Arbitrary values are acceptable when implementing a deliberate design detail not represented by the normal token scale.

---

# 41. Visual Hierarchy

Every page should answer these questions immediately:

1. Where am I?
2. What is the most important information?
3. What can I do here?
4. What changed?
5. What requires my attention?

Use:

- typography
- spacing
- contrast
- grouping
- positioning

before relying on additional colors or borders.

---

# 42. Page Headers

Standard page header:

```text
Title                         Primary Action
Short description             Secondary Action
```

Do not create huge marketing-style headings inside ordinary application pages.

Most app page titles should stay around:

```text
22–28px
```

---

# 43. Information Density

This application should feel professional and efficient.

Avoid excessively large:

- cards
- buttons
- padding
- headings

A page should not require unnecessary scrolling simply because every element has huge spacing.

A good target is:

> Linear-like density with slightly more breathing room.

---

# 44. Content Tone

Interface copy should be:

- short
- clear
- calm
- confident
- technical when appropriate

Avoid:

- unnecessary exclamation marks
- overly friendly filler
- verbose explanations
- marketing language inside operational screens

Bad:

```text
Awesome! Your amazing new project has been created successfully!
```

Better:

```text
Project created
```

---

# 45. Error Messages

Error messages must explain:

1. what failed
2. why, if known
3. what the user can do

Bad:

```text
Something went wrong.
```

Better:

```text
Unable to connect to the model endpoint.
Check the endpoint URL and try again.
```

Show raw technical details only when useful, optionally behind:

```text
View details
```

---

# 46. Destructive Actions

Destructive actions must be visually distinct but should not dominate every screen.

Use destructive styling for:

- Delete
- Remove
- Revoke
- Reset
- Terminate

Confirm truly destructive actions.

Do not confirm harmless actions unnecessarily.

---

# 47. Progressive Disclosure

Do not show every advanced control by default.

Prefer:

```text
Basic options
Advanced settings ▸
```

This is especially important for technical configuration pages.

Power users should have access to advanced controls without making the default experience visually overwhelming.

---

# 48. Premium Detail Rules

Small details matter.

Agents should actively check:

- icon alignment
- 1px border consistency
- vertical centering
- button heights
- line heights
- truncation
- hover states
- disabled states
- focus states
- loading states
- empty states
- error states
- long text
- unusual numbers
- narrow screens

A UI is not finished when the happy-path screenshot looks good.

It is finished when interaction states also look intentional.

---

# 49. Things to Avoid

DO NOT:

- use gradients everywhere
- use glass everywhere
- use large shadows everywhere
- wrap everything in cards
- use excessive rounded corners
- use random colors
- mix icon libraries
- add animations to every component
- create huge page titles
- use pure black everywhere
- use pure white text everywhere
- put permanent action buttons in every table cell
- use borders around every visual region
- create generic CRUD layouts without visual hierarchy
- introduce another UI framework without explicit approval
- hard-code colors repeatedly
- use emoji as interface icons
- overuse badges
- use 20px+ radius on every component
- make every button pill-shaped

---

# 50. Preferred Patterns

Prefer:

```text
Whitespace > unnecessary card

Typography > decorative border

Border > shadow

Subtle contrast > strong color

One restrained indigo accent > many colors

Micro-motion > flashy animation

Semantic tokens > hard-coded colors

Existing component > new dependency

Progressive disclosure > control overload

Information hierarchy > visual decoration
```

---

# 51. AI Agent Workflow

Whenever an AI agent creates or significantly modifies a UI page, follow this sequence.

## Step 1 — Understand the page

Identify:

- primary user goal
- primary action
- secondary actions
- key information
- exceptional states

Do not immediately start placing cards.

## Step 2 — Establish hierarchy

Decide:

```text
Page
→ Sections
→ Primary information
→ Secondary information
→ Actions
```

## Step 3 — Reuse components

Search existing project components before building new ones.

Prefer extending existing design patterns.

## Step 4 — Implement base layout

First ensure:

- alignment
- spacing
- typography
- hierarchy

before adding visual effects.

## Step 5 — Add interaction states

Explicitly implement:

- hover
- focus
- active
- disabled
- loading
- empty
- error

where relevant.

## Step 6 — Add restrained polish

Only after the basic UI works, consider:

- subtle animation
- highlight
- shadow
- blur
- gradient
- glow

## Step 7 — Review visually

Before considering a UI task complete, ask:

- Does anything look like a generic admin template?
- Are there too many cards?
- Are there too many borders?
- Are too many colors competing for attention?
- Is the typography hierarchy strong enough?
- Is the primary action obvious?
- Are spacing values consistent?
- Are hover/focus states polished?
- Is anything unnecessarily oversized?
- Does the page look coherent with existing pages?

Fix issues before completion.

---

# 52. Agent Decision Rule

When unsure between two design approaches:

Choose the one that is:

1. simpler
2. calmer
3. more consistent
4. easier to scan
5. more reusable
6. less visually noisy

Do not add decoration merely to make a page appear "designed."

The design should emerge from proportion, hierarchy, spacing, and interaction quality.

---

# 53. Example Dashboard Direction

Preferred:

```text
┌─────────────────────────────────────────────────────────────┐
│ Product        Search ⌘K                      Bell   Avatar │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│ Overview     │ Overview                                     │
│ Models       │ Monitor usage and system health.             │
│ Playground   │                                              │
│ API Keys     │ $12,830        1.2M            99.98%        │
│ Usage        │ Spend          Tokens          Uptime         │
│              │ +12.4%         +8.1%           Operational   │
│ ───────────  │                                              │
│ Settings     │ ───────────────────────────────────────────  │
│              │                                              │
│              │ Usage                                        │
│              │                                              │
│              │              chart                           │
│              │                                              │
│              │ ───────────────────────────────────────────  │
│              │                                              │
│              │ Recent activity                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

Notice:

- Metrics are not automatically wrapped in four separate cards.
- Sections rely on whitespace.
- Borders are used selectively.
- Information hierarchy is created by typography.
- The page remains dense but readable.

---

# 54. Example Component Styling

A premium surface may follow this general visual pattern:

```vue
<div
  class="
    rounded-xl
    border
    border-border/60
    bg-card/80
    shadow-sm
    backdrop-blur-sm
  "
>
  ...
</div>
```

This is only an example.

Do not copy these classes blindly into every component.

---

# 55. Example Interactive Row

Preferred:

```vue
<div
  class="
    group
    flex items-center
    rounded-lg
    px-3 py-2
    transition-colors
    hover:bg-muted/50
  "
>
  ...
</div>
```

Actions may fade in:

```text
opacity-0
group-hover:opacity-100
```

provided accessibility is preserved.

---

# 56. Signature Moments

Most of the application should remain calm.

A few areas are allowed to carry stronger visual identity.

Preferred signature moments:

### Command Palette

Highly polished search experience with:

- blur
- smooth opening motion
- keyboard hints
- grouped results

### Primary Dashboard

Subtle background glow behind important data.

### AI / Processing State

Sophisticated but restrained animated indicator.

### Empty State

Small custom visual or tasteful abstract graphic.

### Selected Navigation

Smooth indicator movement.

Do not attempt to make every component a signature moment.

---

# 57. Dark Mode Quality Bar

Dark mode must not simply mean:

```text
background: black;
card: #111;
text: white;
```

High-quality dark mode depends on subtle luminance differences.

Think in layers:

```text
Background
↓
Surface
↓
Raised surface
↓
Hover surface
↓
Active surface
```

Each layer should differ slightly.

This creates depth without obvious shadows.

---

# 58. Light Mode Quality Bar

Light mode should retain the same premium feeling.

Do not overuse pure white cards against pure white backgrounds.

Use slight surface differentiation and thin neutral borders.

Dark mode and light mode should share:

- spacing
- typography
- radius
- hierarchy
- interaction behavior

while adapting contrast appropriately.

---

# 59. Definition of Done for UI Work

A UI-related task is not complete until:

- layout is coherent
- typography hierarchy is correct
- spacing is consistent
- colors use semantic tokens
- existing components have been reused where appropriate
- dark mode works
- light mode works if supported
- hover states exist
- focus states exist
- disabled states work
- loading state is considered
- empty state is considered
- error state is considered
- narrow viewport behavior is reasonable
- no obvious generic-admin styling remains
- animation is restrained
- accessibility is preserved

---

# 60. Final Design Principle

The product should feel like it was designed by one disciplined design team.

Not like individual pages were independently generated by different AI agents.

Whenever adding something new, ask:

> Does this feel native to the existing product?

If not, adapt the new feature to the design system rather than inventing a new visual language.

The desired result is:

> **Quiet, technical, premium, and unmistakably intentional.**

---

# 61. Workspace Composition

Do not force every page into the same card grid. Use one of these page archetypes:

```text
Workbench
  One primary surface with a clear action bar.
  Use for report configuration and task execution.

Browser
  Page header → command/filter row → dense list or table → optional detail pane.
  Use for tasks, materials, and templates.

Editor
  Navigation/context → document canvas → evidence or quality context.
  Use for report writing and review.
```

For a workbench with several setup steps:

- Keep a single outer workspace surface.
- Let the highest-interaction area occupy the primary column.
- Place secondary configuration in a narrower supporting column.
- Use dividers, alignment, and spacing between steps instead of a card for every step.
- Keep the configuration summary and primary action together in one bottom action bar.
- Preserve this reading order at narrow widths: intent → primary input → secondary configuration → action.
