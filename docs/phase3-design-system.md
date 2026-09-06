# Phase 3: shared design system

Scope: CSS tokens, existing UI primitives, layout foundation and presentation
lifecycle. No screen redesign, route, backend, database, extension or dependency
changes. Phase 4 has not started.

## Files and load order

| File | Responsibility |
| --- | --- |
| `static/css/tokens.css` | Dark/light palette, spacing, radii, typography, shadows, controls and motion durations. Existing variable aliases are retained. |
| `static/css/layout.css` | Existing reset, application shell, sidebar, topbar, grids and responsive utilities extracted from `base.html`. |
| `static/css/components.css` | Buttons, fields, panels, badges, notifications and opt-in dialog/loading skins. |
| `static/css/motion.css` | Entrance/notification/loading animations, reduced-motion overrides and hidden-tab pause. |
| `static/js/ui-motion.js` | One idempotent `visibilitychange` listener; no timers, network calls, storage or form hooks. |

`base.html` loads tokens, layout, components, motion, then the deferred UI script.
The login/register templates load tokens before their existing page styles and
components/motion after them. They keep their independent authentication layouts.
No other template or extension file was edited by Phase 3.

The shell's old grid constrained `.main` to the fixed sidebar's column. The shell
now uses normal block flow with the existing sidebar-width margin on `.main`.
Desktop sidebar width (248px), page maximum (1440px), routes, DOM order and mobile
drawer behavior remain the same. Per-screen layouts are deferred to Phase 4.

## Component contracts

- Existing `.btn`, `.panel`, `.card`, `.form-group`, `.badge`, `.toast` and auth
  control classes continue to work. Form attributes, validation, input names,
  handler IDs and all original scripts are unchanged.
- Use the `--space-*`, `--r-*`, `--shadow-*`, `--text-*` and `--duration-*` tokens
  when extending components. Existing aliases remain available.
- Native selects and `confirm()` dialogs stay intact. The new `dialog.ds-dialog`
  is an opt-in visual skin, not a replacement confirmation implementation.
- An opted-in dialog needs an accessible name (`aria-labelledby`), a close control
  and the native dialog lifecycle. Do not add it to existing business actions
  without separate interaction regression checks.
- `.ds-spinner` must have an accessible loading label (on it or its parent).
  `.skeleton` is decorative (`aria-hidden="true"`) and is only for genuinely
  pending content. Neither primitive automatically changes the application's state.
- `aria-busy="true"` changes a button's cursor; it does not disable it, submit it,
  or infer loading from existing disabled states.
- Existing toast functions, messages and 4500ms lifetime are unchanged. The exit
  animation remains shorter than the existing 200ms removal delay.
- Reduced motion works via CSS even if JavaScript does not load. The mobile
  sidebar transform is preserved because it controls drawer visibility.

## Validation

The repository now contains a Python/Playwright regression harness. Run:

```powershell
python -B tests/design_system_regression.py --contracts-only
python -B tests/design_system_regression.py --output "$env:TEMP\fbpp-phase3-check"
```

If the `py` launcher cannot find the installed interpreter on this workstation,
the checks were run with
`C:\Users\admin\AppData\Local\Programs\Python\Python314\python.exe`.
The project still declares Python 3.13; the test run used Python 3.14.

The harness sets `DATA_ROOT`, `USERS_FILE`, `DATABASE_URL` and secrets before app
import and serves a temporary local Flask instance. It uses synthetic accounts
and images in temporary storage, does not load the extension and never opens
Facebook. PostgreSQL and real Facebook posting are not covered by this run.
Font CDN responses are stubbed only inside the test browser for offline checks.

`tests/design_system_contracts.json` was captured from the working tree before
Phase 3 integration. It protects 21 files: markup/business scripts in all eight
templates, the backend, extension and configuration. Only CSS and the specifically
named new presentation-script tag are excluded from template fingerprints.
Do not regenerate this baseline just to make a regression pass.

Browser checks cover real Flask authentication/session, native forms, image
preview/upload, pair-code API and message shape, isolated agent job/stop APIs,
theme persistence, toast dismissal, mobile menu, dialog skin, reduced motion,
hidden-tab pause and duplicate listener prevention. Forty page/viewport cases
include mobile, tablet, laptop, Full HD, 2K, 4K and authentication pages.

Optional `--compare <baseline-directory>` compares rendered form ownership,
POST request sequence and horizontal overflow with a previous `result.json`.
The initial local baseline is under `%TEMP%\fbpp-phase3-baseline`.

## Known limitations retained for the next phase

- Dashboard at 390px still has 25px horizontal overflow (31px in the initial
  baseline). Other tested page/viewport combinations have no reported overflow.
  The test treats this as an existing page-level issue, not a responsive pass
  for that individual case.
- Saved-image delete forms are already nested inside the save form. Their actual
  browser form ownership was captured and compared, not silently changed here.
- Admin/legacy flows, PostgreSQL, actual Chrome Extension execution, Facebook
  posting, OS scaling and FPS profiling still need their own runtime validation.
- The test figures are test data only; production pages continue to render their
  existing backend values, including the old illustrative login panel.

No Phase 1 checklist item is removed by this scope. Unexecuted runtime checks
remain pending and must not be described as passing.
