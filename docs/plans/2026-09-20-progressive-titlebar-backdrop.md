# Progressive Titlebar Backdrop Implementation Plan

> **For agentic workers:** This plan is executed inline in the current session because the final architecture was explicitly selected by the user.

**Goal:** Attach a transparent AppKit progressive blur between the macOS titlebar chrome and the existing SwiftUI Dashboard content so scrolling text receives a strong blur within the 52pt titlebar height, with the bottom 24pt acting as the variable-radius fade.

**Architecture:** A SwiftUI `NSViewRepresentable` creates an attachment view only as a lifecycle hook. That view installs a separate `ProgressiveTitlebarBlurView` into `window.contentView?.superview ?? window.contentView`, above the content view and below the AppKit toolbar/titlebar chrome. The blur view uses `NSView.backgroundFilters` with a newly constructed `CIMaskedVariableBlur` whose mask is rebuilt after layout and resize.

**Tech Stack:** SwiftUI, AppKit, Core Image, `NSView.backgroundFilters`, `CIFilter.maskedVariableBlur()`.

**Spec:** `docs/dashboard-窗口材质分层规格.md` and the user-provided final decision in the current task.

## Global Constraints

- Preserve the transparent `NSWindow` and `DashboardWindowBackdrop` using the already approved `.sidebar` system material.
- Keep `toolbarBackgroundVisibility(.hidden, for: .windowToolbar)` and `window.titlebarSeparatorStyle = .none`.
- Do not put the blur view into the SwiftUI ZStack as a visual layer or add it as a normal `window.contentView` child.
- Use a black-to-white `CIMaskedVariableBlur` mask in the blur view's local coordinate space: black means no blur, white means maximum blur.
- Disable the custom filter when Reduce Transparency is enabled; do not paint a fallback color in the blur view.
- Do not modify sidebar transparency in this task.

### Task 1: Replace the titlebar effect integration

**Files:**
- Create: `native/CapsWriter/Sources/CapsWriterDashboard/ProgressiveTitlebarBackdrop.swift`
- Modify: `native/CapsWriter/Sources/CapsWriterDashboard/Appearance.swift`
- Modify: `native/CapsWriter/Sources/CapsWriterDashboard/CapsWriterDashboard.swift`

- [x] Add the window-frame attachment and progressive blur view with Chinese comments explaining the non-obvious layer ordering and Core Image mask coordinates.
- [x] Remove the old `DashboardTitlebarScrollEdge` and its `scrollEdgeEffectStyle` usage.
- [x] Add named `DashboardSurfaceStyle` geometry constants and attach the representable from the root without changing the existing backdrop.
- [x] Correct the first visual failure by keeping the overlay total height equal to the titlebar height; the 24pt fade is internal to that height, not an additional strip.

### Task 2: Verify build and static consistency

- [x] Run `bash tools/build_dashboard.sh` and inspect the complete exit status.
- [x] Run `swift run --package-path native/CapsWriter DashboardCoreChecks` and inspect all checks.
- [x] Run `git --no-pager diff --check`.
- [x] Update `CLAUDE.md` with implementation and verification status, explicitly separating code/build verification from visual acceptance.
