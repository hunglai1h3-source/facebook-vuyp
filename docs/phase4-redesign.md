# Phase 4 — Screen redesign and regression record

Scope: the seven active Flask customer pages and the Chrome Connector popup.
Screens were changed and rendered individually, with regression checks before
moving to the next screen. Phase 5 has not started.

## Changes by screen

| Screen | Files changed in Phase 4 | Presentation changes | Verification |
| --- | --- | --- | --- |
| Dashboard | `templates/dashboard.html`, `static/css/pages/dashboard.css` | Cinematic gradient hero, stat cards, campaign/connector panels, responsive activity rows; actual 390px overflow fixed without clipping the document | Dark/light renders, 390px long-text stress, existing links, baseline forms/API sequence |
| Groups | `templates/groups.html`, `static/css/pages/groups.css` | Roomier URL entry, group cards, wrapping URLs, visible delete controls | Add group, cancel/accept indexed deletion, mobile input icon spacing, dark/light renders |
| History | `templates/history.html`, `static/css/pages/history.css` | Timeline cards, separate timestamps, wrapping details and semantic status icons | Real activity records, cancel/accept clear history, dark/light renders |
| Login | `templates/login.html`, `static/css/pages/login.css` | Wider split composition, readable form, existing illustrative panel, removal of large backdrop blur | Username/email sign-in, wrong password, password toggle, remember unchecked, `next` redirect, session and logout |
| Register | `templates/register.html`, `static/css/pages/register.css` | Matching split composition, feature cards, responsive five-field form | Successful registration, mismatched-password error, mobile/tablet/desktop layouts |
| Compose | `templates/compose.html`, `static/css/pages/compose.css` | Stable composer/summary columns, wrapping upload area, preview gallery, always-visible image controls, delay/save positioning compatible with existing browser-parsed forms | Upload/FileReader preview, saved thumbnails, valid individual deletion, cancel/accept delete-all, empty-gallery save/delay, isolated run/stop APIs, saved/empty layout renders and original form-ownership comparison |
| Settings | `templates/settings.html`, `static/css/pages/settings.css` | Connector hero, clear configuration and connection panels, pairing code presentation | Save name/delay/theme, existing delay normalization, pair-code API/message, clipboard, bridge error notification, cancel/accept disconnect |
| Extension popup | `extension/popup.css` | Matching dark palette, improved contrast and focus states, 44px controls, responsive width, short entrance animation | Native MV3 load/storage/messaging, real isolated Flask pairing and heartbeat, normalization, reload persistence, check, local disconnect, open-tab button, online/offline renders |

Shared new files:

- `static/css/pages/workspace.css`: presentation shared by the five workspace
  pages; capped 2000px content, fluid spacing, panel lighting and entrance motion.
- `static/css/pages/auth.css`: shared login/register composition, typography,
  fields and entrance motion; capped 1440px content.
- `tests/popup_regression.py`: native Chromium extension regression harness.
- `docs/phase4-redesign.md`: this record.

`tests/design_system_regression.py` was extended with individual screen checks,
screenshots, strict overflow assertions, legacy Compose layout assertions,
clipboard/bridge checks and more image-deletion coverage. Its pairing-code wait
now waits for the actual handler message; the eight-character placeholder was
previously an intermittent test race. Reduced-motion checks allow a focus
transition started before the preference change to finish.

No new frontend framework, animation dependency, remote asset, WebGL, video or
production JavaScript was introduced. Motion uses short opacity/transform
animations and existing button interactions. The Phase 3 reduced-motion and
hidden-tab behavior remains active. Existing lazy-loaded saved images remain
lazy-loaded. There is no new polling or artificial loading state.

## Baseline and protected scope

The Git working tree already contained UI changes before these phases. Therefore
the total diff against HEAD is not the Phase 4 diff. The pre-Phase-3 contract file
is the reference for preserving the actual starting behavior, not a regenerated
baseline. The backup directory has not been modified.

The original `tests/design_system_contracts.json` is unchanged. Twenty source
contracts still protect all eight template structures/scripts, backend,
extension HTML/JavaScript/manifest and configuration. Phase 4 deliberately
excludes only `extension/popup.css` from its old full-file hash because that
stylesheet is now in the authorized redesign scope. All other original hashes
are still enforced. Template stylesheet links and CSS are presentation exceptions.

No Phase 4 change was made to `app.py`, routes, API contracts, database/schema,
authentication/session, `extension/popup.js`, `extension/popup.html`, manifest,
service worker, Facebook runner or web bridge. Phase 3 foundation files and
`templates/base.html` were retained.

## Validation and artifacts

The web suite ran after every screen. Each full run checks 40 page/viewport
combinations across 390, 768, 1366, 1920, 2560 and 3840 CSS pixels, with additional
screen-specific theme renders and interactions. Every screen was also inspected
from screenshots. The final checked cases have no horizontal overflow and no
console/page errors. Dashboard also passed a 390px long-message/URL stress case.

The web suite compares actual rendered form ownership and the POST sequence to
the initial local baseline. It exercises native forms, authentication/session,
upload, pairing and isolated job/stop HTTP endpoints. Shared native-dialog skin,
loading primitives, reduced motion, hidden-tab pause, toast, mobile drawer,
theme persistence and listener idempotence are covered. No business modal or
tab implementation was replaced.

Popup checks use the real unchanged MV3 manifest, service worker and popup JS in
a disposable Chromium profile, with real Flask pairing/heartbeat against
temporary storage. Twelve online/offline viewport combinations include 320,
360, 390, 768, 1920 and 3840px. The Facebook button creates a native tab, but its
document is fulfilled with blank local test HTML: no real Facebook content or
posting is exercised. No campaign job is created by the popup test. Local
disconnect is verified to preserve the server-side device record.

Reproduce on this workstation:

```powershell
$pythonExe = 'C:\Users\admin\AppData\Local\Programs\Python\Python314\python.exe'
& $pythonExe -B tests/design_system_regression.py --contracts-only
& $pythonExe -B tests/design_system_regression.py --screen compose --output "$env:TEMP\fbpp-phase4-compose" --compare "$env:TEMP\fbpp-phase3-baseline"
& $pythonExe -B tests/design_system_regression.py --screen settings --output "$env:TEMP\fbpp-phase4-settings" --compare "$env:TEMP\fbpp-phase3-baseline"
& $pythonExe -B tests/popup_regression.py --output "$env:TEMP\fbpp-phase4-popup"
git diff --check
```

Other `--screen` values already exercised: `dashboard`, `groups`, `history`,
`login`, `register`. Their screenshots and `result.json` are in corresponding
`%TEMP%\fbpp-phase4-<screen>` directories. The baseline directory is local and
temporary; omit `--compare` if it is no longer available, without regenerating
the source contract file. Fonts are stubbed only in the offline test browser.
Production still uses its original font loading. Python used for tests is 3.14;
the project deployment declaration remains 3.13.

## Existing defects and unverified runtime areas

1. **Resolved during Phase 5: Compose nested forms.** The malformed saved-image
   branch found in Phase 4 was repaired by moving each image-delete form outside
   the main save form and linking its existing button with the HTML `form`
   attribute. The `/save-post` and `/delete-post-image/<filename>` routes,
   methods, field names, IDs, validation and handlers remain unchanged. The
   final audit verifies save/delay while saved images exist, individual delete
   and delete-all behavior.
2. PostgreSQL, deployed Render environment, admin/legacy flows, real Facebook
   authentication/posting, browser restart recovery and production accounts were
   not exercised. The admin/legacy screens were outside this Phase 4 screen plan
   and remain unchanged. The active extension popup has native runtime coverage,
   but that is not end-to-end Facebook automation coverage.
3. Viewports are CSS pixels. OS scaling, physical 4K hardware, mobile Safari and
   sustained FPS profiling remain unverified. The animation implementation avoids
   continuous heavy effects, but this is not a measured 60 FPS guarantee.

All planned screen UI work and the stated regression checks are complete.
These checks demonstrate no detected regression in their covered scope; they do
not establish that every pre-existing production feature is defect-free.
Phase 5 requires the user's next instruction and has not begun.
