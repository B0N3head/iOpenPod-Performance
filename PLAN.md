# iOpenPod Health and Architecture Plan

## Summary
- Current scan: 160 first-party modules, 4 first-party import cycles, and no normal PR health workflow.
- Biggest hotspots are `GUI/app.py` (3242 lines, 64 local imports), `GUI/widgets/syncReview.py` (3598 lines), `SyncEngine/sync_executor.py` (2241 lines), `ipod_device/scanner.py` (2496 lines), `ipod_device/info.py` (2408 lines), and `settings.py` owning global mutable runtime state.
- Strictness gap is architectural more than syntactic: 697 local imports, 360 `except Exception` sites, 201 `except ...: pass` sites, direct GUI imports of `SyncEngine` / `ipod_device` / `PodcastManager` / `settings`, and stateful singletons living inside UI code.
- Type coverage is already decent enough to build on, so the first job is not “add types everywhere”; it is “make boundaries, ownership, and failure behavior strict.”

## Target Architecture
- `GUI`: PyQt views and widgets only. No direct imports from `SyncEngine`, `ipod_device`, `PodcastManager`, or raw settings helpers.
- `app_core`: composition root, runtime/session services, commands/queries, view-model adapters, and background job coordination.
- `domain_engine`: sync planning/execution, device identity/capabilities, podcast logic, and parser/writer orchestration.
- `infrastructure`: filesystem, JSON persistence, USB/VPD backends, ffmpeg/fpcalc integration, network/download adapters.
- New public interfaces: `AppContext`, `SettingsService`, immutable `SettingsSnapshot`, `DeviceSessionService`, `DeviceSession`, `LibraryService`, `LibrarySnapshot`, `SyncService`, `SyncRequest`, `SyncProgress`, `SyncOutcome`, and a typed `AppError` hierarchy.
- Dependency rule: only the composition root wires concrete implementations together. Everywhere else depends on protocols and DTOs, not module globals.

## Phase 0 — Immediate Safety Net
- Add a normal CI workflow for PRs and pushes: `ruff`, `mypy`, `pytest`, packaging smoke, and a repo-specific architecture check.
- Replace `flake8` as the main gate with `ruff` configured for imports, bug-prone patterns, and unused code; keep formatting churn out of scope at first.
- Add hard rules immediately: no new `except Exception: pass`, no new cross-layer imports, no new module-level singleton state outside `app_core`, and no new untyped raw `dict`/`list` payloads across subsystem boundaries.
- Add a small architecture checker that fails on new import cycles, non-whitelisted local imports, and new violations of layer boundaries.
- Seed a real `tests/` tree with fixture folders for SysInfo, device metadata, iTunesDB, ArtworkDB, and sync-plan samples.
- Exit criteria: every PR gets health feedback, and new debt is blocked even before old debt is fully removed.

### Phase 0 Gap Scan
- Current CI exists and runs the intended health gates, but it is an Ubuntu-only baseline. Keep that as the fast gate, then add a tiny Windows/macOS import and path-resolution smoke once the platform backends are split enough to test without hardware.
- The architecture checker now guards import cycles, GUI cross-layer imports, runtime singleton access, settings runtime reach-through, sync-review workers, private `SyncExecutor` usage, and growth in `except Exception: pass`. It still needs a general layer dependency matrix so all first-party imports are either allowed by design or explicitly grandfathered.
- The allowlists in `scripts/architecture_rules.json` should become a debt ledger: each exception needs an owner/phase target or at least a short reason. Right now the counts block growth, but they do not explain which migration will remove each allowance.
- The plan says "no new module-level singleton state" and "no new raw `dict`/`list` boundary payloads"; neither rule is fully enforceable yet. Phase 0 should add a lightweight scanner for obvious module state and a typed-boundary inventory for GUI/app_core/sync/device DTOs before Phase 1 leans on those contracts.
- The fixture directories exist, but the useful fixture content is still missing. Minimum Phase 0 fixtures should include representative SysInfo files, device metadata snapshots, one tiny iTunesDB/ArtworkDB pair, and serialized sync-plan cases for add/remove/update/integrity repair.
- Ruff and mypy are intentionally scoped to the new typed island. Treat that as a ratchet: touched `app_core`, `infrastructure`, contract, and test files stay in the typed/linted set, and each migration phase expands the scope rather than trying to clean the whole repo in one pass.
- Add one documented local health command that mirrors CI. The workflow is real, but contributors should not have to read YAML to know the Phase 0 gate.

## Phase 1 — Establish the Composition Root
- Create `app_core` and move app wiring out of `GUI/app.py` and into `main.py` plus `AppContext`.
- Wrap existing globals instead of rewriting them first: put `settings.py`, `DeviceManager`, and `iTunesDBCache` behind services so callers can migrate without a flag day.
- Make `MainWindow` depend on services and page controllers rather than constructing and owning every operational subsystem directly.
- Break the 12-module GUI cycle by moving shared commands, state, and events into `app_core`, leaving widgets as consumers.
- Standardize one background job abstraction for progress, cancellation, result delivery, and error propagation; stop inventing new worker patterns inside UI modules.
- Exit criteria: `GUI/app.py` is a shell/composition module, not the owner of settings, device session, cache, sync, updater, and thread policy.

### Phase 1 Gap Scan
- The composition root exists (`main.py` -> `app_core.bootstrap` -> `AppContext`), but its ownership boundary is still fuzzy. Declare `app_core.bootstrap` as the only app-core module allowed to import concrete GUI classes, and keep all other `app_core` modules GUI-free.
- `AppContext` wraps settings, device sessions, and library cache, but the services still expose `manager()` and `cache()`, which hands mutable singleton-style objects back to the UI. Treat those as compatibility seams and replace them with command/query methods plus immutable snapshots.
- `GUI/app.py` is smaller than the original hotspot but still owns the main sync, back-sync, podcast-plan, execute, eject, rename, tool-download, and drop-scan worker lifecycles. Phase 1 needs explicit shell controllers for sync orchestration, device commands, drop/import, startup restore, and update checks.
- Some worker classes moved to `app_core.jobs`, but widget-local threading still exists (`_DriveWatcher`, `_PCLibScanWorker`, `_PhotoWriteWorker`) and widgets still use `ThreadPoolSingleton`/`Worker` directly. The missing concept is a `JobRunner`/`BackgroundTaskService` facade with a single cancellation/progress/result contract.
- `app_core.jobs` is becoming the next large coordination module. Split it by workflow before it hardens: sync jobs, device jobs, backup jobs, playlist jobs, podcast jobs, and tool/update jobs.
- Main-window dependency injection is started, but page creation is still centralized in `MainWindow`. Add a page factory or navigation controller so `MainWindow` wires pages at a high level instead of knowing every widget constructor and service combination.
- Shared events are still mostly raw Qt signals carrying `object`, `str`, `dict`, or lists. Add typed command/event DTOs for device-selected, library-loaded, sync-requested, sync-plan-ready, sync-finished, settings-changed, and page-navigation events.
- The architecture checker should gain Phase 1 ratchets: no new direct `app_core.runtime` imports from widgets, no new GUI-local `QThread` worker classes, no new `MainWindow` worker ownership, and no `app_core` imports from `GUI` except the bootstrap/composition root allowance.
- Define measurable exit criteria for this phase: `GUI/app.py` line/import/worker-count caps, zero widget-owned operational workers outside temporary allowlists, and every page receiving services or controllers rather than reaching through globals.

## Phase 2 — Make Runtime State Strict
- Split `settings.py` into schema, persistence, secret codec, and active-session runtime pieces.
- Replace mutable module globals such as `_global_instance`, `_effective_instance`, and active-device overlay fields with one synchronized runtime service returning immutable snapshots.
- Remove duplicated sources of truth; version, path resolution, active device, and effective settings each get one authoritative provider.
- Move device/cache/settings coordination into explicit state transitions and typed events rather than ad hoc calls spread through UI methods.
- Add unit and concurrency tests for settings load/save, redirect handling, device overlay activation, reload behavior, secret encryption/decryption, and failure recovery.
- Exit criteria: no code outside `app_core` reads or mutates raw settings globals, and device/session state cannot drift across multiple owners.

## Phase 3 — Isolate Device and Sync Domain Logic
- Split `ipod_device` into discovery, identity enrichment, capability resolution, and backend transport modules with a curated public facade instead of the current broad re-export surface.
- Refactor `ipod_device/info.py` so enrichment stages become small pure functions or narrow services; platform-specific probing stays behind backend interfaces.
- Refactor `SyncEngine/sync_executor.py` into stage services: preflight, file transfer, metadata update, podcast prep, photo sync, playcount/rating merge, and final database write.
- Replace raw dict-heavy contracts between GUI, sync, and device layers with typed DTOs for tracks, playlists, device capabilities, sync requests, and sync results.
- Replace generic exception swallowing with typed recoverable errors plus structured logging and clear user-facing failure categories.
- Exit criteria: the device SCC is gone, GUI no longer calls engine internals directly, and `sync_executor` is an orchestrator instead of a god object.

## Phase 4 — Rebuild the UI Around Commands and View Models
- Decompose the largest widgets (`syncReview`, `MBListView`, `settingsPage`, `playlistBrowser`, `podcastBrowser`) into passive views plus controllers/presenters.
- Standardize view models for track lists, playlist trees, device summary, settings forms, and sync review so widgets stop pulling live data from domain modules on demand.
- Push all non-rendering decisions into `app_core` commands and queries; UI only dispatches intents and renders returned state.
- Consolidate page-to-page communication through typed events or service callbacks instead of widget-to-widget imports.
- Add `pytest-qt` smoke tests for startup, device selection, settings apply/cancel, sync review open/close, backup browser navigation, and safe-eject flows.
- Exit criteria: no widget imports `SyncEngine`, `ipod_device`, `PodcastManager`, or raw settings helpers.

## Phase 5 — Lock In Data Contracts and Regression Coverage
- Add fixture-driven parser and writer round-trip tests for iTunesDB, ArtworkDB, and SQLite device paths.
- Add sync integration tests against temp directories and fake adapters covering add/remove/update, cancellation with partial save, device-specific settings overlays, podcast failure paths, and platform backend selection.
- Add hardware-smoke scripts for real-device validation and keep them outside the unit suite but documented and repeatable.
- Add regression gates for architecture metrics: zero import cycles, no new local imports outside whitelisted backend/plugin seams, no unapproved catch-all exception swallowing, and capped module sizes for touched files.
- Remove the temporary compatibility facades introduced in earlier phases once callers are migrated.
- Exit criteria: the app has both a unit/integration safety net and enforceable architecture contracts, not just conventions.

## Test Plan
- Unit tests: settings schema and persistence, device key handling, identity resolution, capability selection, sync-stage input/output contracts, cache transitions, and typed error mapping.
- Integration tests: parse existing device libraries, build sync plans from fixtures, execute syncs against temp directories, write/read DB round-trips, backup/restore, and updater/settings interactions.
- UI smoke tests: app boot, theme load, device attach/detach, settings overlay activation, sync review, cancellation, and safe eject.
- Architecture checks: layer-import contracts, cycle detection, local-import allowlist, and catch-all exception policy.

## Assumptions and Defaults
- Pace: incremental, not a big-bang rewrite.
- Enforcement: strict immediately, with temporary allowlists tracked centrally and removed phase by phase.
- Keep PyQt6, current packaging, and current on-disk device/database formats.
- Prefer stdlib `dataclass`, `Enum`, `TypedDict`, and `Protocol` over heavier framework abstractions.
- Start with `GUI/app.py`, `settings.py`, and the `ipod_device` / `SyncEngine` orchestration seam first, because that is where the current bug-friendly looseness is most concentrated.
