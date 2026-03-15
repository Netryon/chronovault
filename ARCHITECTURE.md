# ChronoVault – Architecture Overview

## High-Level Design

```
[Your machines]  -->  [Backup jobs / scripts]
                              |
                              v
[Raspberry Pi 4]  -->  [Docker + services]
                              |
              +---------------+---------------+
              v               v               v
     [LUKS-encrypted]   [Firewall]    [Twingate/DuckDNS]
     disk 1 / disk 2    (restrict     (secure remote
     (cold storage)      access)       access only)
```

## Components

- **Raspberry Pi 4**: Single host for backup and access services.
- **LUKS + dual disks**: Two encrypted volumes; backup targets can be rotated for off-site or cold storage.
- **systemd**: Schedules and runs backup workflows and any recurring tasks.
- **Docker**: Runs services in containers with separate config files for easier updates and isolation.
- **Installer**: Script(s) that set up dependencies, firewall rules, and base configuration so the system is repeatable.
- **Twingate + DuckDNS**: Secure, controlled remote access without opening raw SSH to the internet.

## Security Notes

- Backups are encrypted at rest (LUKS).
- Access to the Pi is restricted by firewall and mediated by Twingate.
- Isolated configs and containers limit the impact of a single service compromise.
