# Changelog

## [2.0.1] — 2026-11-01

### Fixed

- Fixed moves between filesystems placing model files inside an extra directory. Files and the manifest now arrive at the expected library path, and the library record points to that copy.
- Preserve actionable HTTP download errors and Xet guidance. For an unexplained HTTP 500 on a known gated model, retain the original error and add access guidance as a possible cause.
- Restore SIGKILL reporting even when the worker printed output before it was killed, with a possible-cause explanation and a documentation reference.
- Keep failed download rows compact, with expandable details, selectable error text, and a Copy error action.
- Place restarted downloads at the back of the queue and their latest completed or failed attempt at the top of History. Keep running transfers ahead of waiting jobs.
- Enable State sorting in Explore and Library, and column sorting throughout Watched. Align Watched headers with their data.

## [2.0.0]

### Changed

* Weighthouse — the project has a new name
* Library database moved to a neutral filename with automatic migration from modelharbor.sqlite3
* Comment and documentation cleanup, dependency upgrades, and a security sweep

## [1.3.0] — 2026-10-29

### Added

- Pause and resume individual downloads or the whole queue, and move a queued download to the front.
- Restart failed downloads in place, filter download history to failed jobs, and clear selected history records without deleting model files.
- Added a Downloaded column to the Library and expanded sorting controls for Library and Explore.

### Changed

- Check access before starting a gated-model download and show model-terms links and Hub-token guidance when access is refused.
- Show queue and download state on the model page, and give model notes their own column.
- Make full-repository downloads and update actions available directly from model cards.
- Add All/None selection and a copyable repository name to the file-removal panel.
- Hide the model page's runtime action when no runtime destinations are configured.

## [1.2.0] — 2026-10-01

First public release as ModelHarbor, forked from [HuggingHack](https://github.com/tyedalwaves/HuggingHack).

### Added

- Download plans that pin the requested revision to a commit and record selected files, their upstream identifiers, and selected, cached, and transfer sizes.
- Watch repositories independently of saved models and downloaded copies. Review upstream changes and choose which files to update; upstream removals do not delete local files.
- Verify tracked copies against their manifests, reporting missing files, size differences, and files outside the recorded selection.
- Named filesystem storage locations, indexing across locations, and moves between locations.
- Private and shared model-note threads, pinned notes, and library searches that include notes visible to the current account.
- Scoped agent keys and an MCP endpoint for discovery, library inspection, download planning, updates, moves, and deletion, with per-key activity records.
- Model cards pinned to the revision of the local copy.

### Changed

- Reworked the interface around a dedicated model page, Library cards and rows, and saved display and column preferences.
- Added versioned manifests recording download intent and provenance, while retaining read compatibility with legacy HuggingHack manifests.
- Extended the library index to recover copy metadata from model files and manifests after the database is recreated. Accounts, notes, and other application state still require their own database backup.
- Use the HTTP download transport by default, with Xet available as an opt-in setting.
