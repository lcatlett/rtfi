# Changelog

All notable changes to this project will be documented in this file.

## [0.1.1] - 2026-02-16

### Fixed
- **Auto-install dependencies**: Hook handler and CLI now automatically install `pydantic` if missing
- **Startup errors**: Resolved `ModuleNotFoundError: No module named 'pydantic'` on plugin startup
- **Graceful degradation**: Plugin continues to work even if dependency installation fails

### Added
- **Setup script**: `scripts/setup.sh` for easy one-command installation
- **Troubleshooting guide**: Comprehensive guide at `docs/TROUBLESHOOTING.md`
- **Health check improvements**: Better error messages and dependency verification

### Changed
- **Installation process**: Updated README with clearer installation instructions
- **Dependency handling**: Dependencies are now installed automatically on first use

## [0.1.0] - 2026-01-31

### Added
- Initial release
- Real-time risk scoring based on session factors
- Hook system for tracking tool usage and session lifecycle
- CLI commands: sessions, risky, show, status, health
- Session analyzer agent for root cause analysis
- Risk scoring skill for threshold tuning guidance
- SQLite database for session storage
- Configurable threshold alerts (alert/block/confirm modes)

