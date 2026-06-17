# Changelog

All notable changes to ChronoVault are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - 2026-06-17

### Added

- **Safe Container Updater** (`chronovault-container-update.sh`) — daily compose-stack `docker compose pull` + `up` at 05:00 UTC
- **Stack Guard** (`chronovault-stack-guard.sh`) — heals missing or unhealthy Nextcloud/Immich stacks every 15 minutes
- Email alerts **H1** (Nextcloud DB missing), **H2** (Nextcloud unhealthy), **H3** (update/guard failure)
- Postgres healthchecks and `depends_on: service_healthy` in Immich and Nextcloud compose templates
- Watchtower opt-out labels on application containers
- Backup: DB-missing alert logging, state file, and compose stack reconcile after backup completes

### Changed

- **Removed Watchtower** from default install — replaced by Safe Container Updater
- Installer step 18 now deploys container maintenance timers (updater + stack guard) instead of Watchtower
- Immich install health check uses `/api/server/ping` (Immich v2)
- README updated for v1.1 maintenance model and Pi 4 as primary target

### Fixed

- Prevents Nextcloud outage when a database container is removed during unsafe per-container image updates (the root cause addressed in this release)

### Tested

- Fresh Raspberry Pi 4 install (Debian/Raspberry Pi OS 64-bit)
- Stack guard heal after `docker stop` and `docker rm -f nextcloud-postgres`
- Safe updater dry-run and live single-stack update
- Full manual backup with Immich and Nextcloud DB dump verification
- Email notify dry-run for H1, H2, H3

---

## [1.0.0] - 2026-03-15

### Added

- 18-step interactive installer for Raspberry Pi
- LUKS encryption for primary and backup disks
- Immich (photos) and Nextcloud (documents) via Docker Compose
- DuckDNS dynamic DNS and Twingate zero-trust remote access
- Control API and web UI (FastAPI, port 8787)
- Automated daily backups with rsync mirror and hard-link snapshots (14 daily, 12 weekly)
- Database dumps before each backup
- Ransomware / abnormal change detection with backup freeze
- Email notifications (backup, mirror, disk space, service health)
- Weekly host OS updates via systemd timer
- Watchtower for daily container image updates
