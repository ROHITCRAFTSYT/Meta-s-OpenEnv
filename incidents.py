"""
Additional incident scenarios for tasks 4 and 5.

task4_disk_full   — /var/log partition at 100%, services can't write logs → crash
task5_ssl_expired — API gateway SSL cert expired → HTTPS connections rejected
"""
from __future__ import annotations
from models import ServiceHealth, ServiceStatus

# ─────────────────────────── TASK 4: DISK FULL ───────────────────────────

DISK_FULL_FS = {
    "/etc/hostname": "incident-host-01\n",
    "/etc/os-release": 'NAME="Ubuntu"\nVERSION="22.04.3 LTS"\nID=ubuntu\n',

    "/etc/services/api-gateway/config.yml": (
        "server:\n  host: 0.0.0.0\n  port: 80\n  workers: 8\n\n"
        "upstreams:\n"
        "  auth_service: http://auth-service:8001\n"
        "  user_service: http://user-service:8002\n"
    ),
    "/etc/services/auth-service/config.yml": (
        "server:\n  host: 0.0.0.0\n  port: 8001\n\n"
        "database:\n  host: postgres\n  port: 5432\n"
        "  name: authdb\n  user: authuser\n  password: db_pass_current\n"
    ),

    # Giant log files filling up disk
    "/var/log/api-gateway/access.log": (
        "[2026-03-27 06:00:00] GET /api/products 200\n" * 50000 +
        "[2026-03-27 08:00:01] ERROR: Cannot write to log file: No space left on device\n"
        "[2026-03-27 08:00:01] FATAL: Disk full. Service shutting down.\n"
    ),
    "/var/log/api-gateway/error.log": (
        "ERROR repeated 200000 times\n" * 10000 +
        "[2026-03-27 08:00:01] FATAL: No space left on device\n"
    ),
    "/var/log/auth-service/error.log": (
        "[2026-03-27 07:58:00] INFO: Auth service starting\n"
        "[2026-03-27 08:00:05] ERROR: Failed to open log file: No space left on device\n"
        "[2026-03-27 08:00:05] FATAL: Cannot initialise logging subsystem. Exiting.\n"
    ),
    "/var/log/user-service/app.log": (
        "[2026-03-27 07:58:00] INFO: User service starting\n"
        "[2026-03-27 08:00:06] ERROR: Write failed: No space left on device\n"
        "[2026-03-27 08:00:06] WARN: Switching to degraded mode (no logging)\n"
    ),
    "/var/log/notification-service/error.log": (
        "[2026-03-27 08:00:08] ERROR: No space left on device\n"
        "[2026-03-27 08:00:08] FATAL: Exiting.\n"
    ),
    "/var/log/postgres/postgresql.log": (
        "[2026-03-27 08:00:00] LOG: Database running normally\n"
        "[2026-03-27 08:00:10] WARNING: Low disk space detected on /var/log partition\n"
    ),

    # Disk usage info (simulated)
    "/proc/mounts": (
        "sysfs /sys sysfs rw 0 0\n"
        "proc /proc proc rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n"
        "/dev/sda2 /var/log ext4 rw 0 0\n"
    ),
    "/etc/crontab": (
        "# No log rotation configured — this caused the disk full incident\n"
        "# TODO: Add logrotate cron job\n"
        "17 * * * * root run-parts /etc/cron.hourly\n"
    ),
    "/etc/logrotate.d/services": (
        "# Log rotation config (NOT ACTIVE — missing from cron)\n"
        "/var/log/api-gateway/*.log {\n"
        "  daily\n  rotate 7\n  compress\n  missingok\n  notifempty\n}\n"
    ),
}


def _disk_full_services():
    return {
        "postgres": ServiceHealth(
            name="postgres", status=ServiceStatus.RUNNING,
            cpu_percent=1.8, memory_mb=512.0, uptime_seconds=86400.0, pid=1001,
        ),
        "auth-service": ServiceHealth(
            name="auth-service", status=ServiceStatus.CRASHED,
            cpu_percent=0.0, memory_mb=0.0, uptime_seconds=None, pid=None,
            error_message="Cannot initialise logging: No space left on device",
        ),
        "api-gateway": ServiceHealth(
            name="api-gateway", status=ServiceStatus.CRASHED,
            cpu_percent=0.0, memory_mb=0.0, uptime_seconds=None, pid=None,
            error_message="No space left on device — log write failed",
        ),
        "user-service": ServiceHealth(
            name="user-service", status=ServiceStatus.DEGRADED,
            cpu_percent=1.2, memory_mb=64.0, uptime_seconds=600.0, pid=1089,
            error_message="Running in no-logging degraded mode",
        ),
        "notification-service": ServiceHealth(
            name="notification-service", status=ServiceStatus.CRASHED,
            cpu_percent=0.0, memory_mb=0.0, uptime_seconds=None, pid=None,
            error_message="No space left on device",
        ),
    }


# ─────────────────────────── TASK 5: SSL EXPIRED ───────────────────────────

SSL_EXPIRED_FS = {
    "/etc/hostname": "incident-host-01\n",
    "/etc/os-release": 'NAME="Ubuntu"\nVERSION="22.04.3 LTS"\nID=ubuntu\n',

    "/etc/services/api-gateway/config.yml": (
        "server:\n  host: 0.0.0.0\n  port: 443\n  workers: 8\n\n"
        "ssl:\n"
        "  cert: /etc/ssl/certs/api-gateway.crt\n"
        "  key:  /etc/ssl/private/api-gateway.key\n"
        "  verify_expiry: true\n\n"
        "upstreams:\n"
        "  auth_service: http://auth-service:8001\n"
        "  user_service: http://user-service:8002\n"
    ),
    "/etc/services/auth-service/config.yml": (
        "server:\n  host: 0.0.0.0\n  port: 8001\n\n"
        "database:\n  host: postgres\n  port: 5432\n"
        "  name: authdb\n  user: authuser\n  password: db_pass_current\n"
    ),

    # Expired cert (valid until yesterday)
    "/etc/ssl/certs/api-gateway.crt": (
        "-----BEGIN CERTIFICATE-----\n"
        "# Certificate: api-gateway.company.com\n"
        "# Issued:      2025-03-27\n"
        "# Expires:     2026-03-26  ← EXPIRED YESTERDAY\n"
        "# Issuer:      Let's Encrypt Authority X3\n"
        "MIIDXTCCAkWgAwIBAgIJAKoK0SZp3EXPIRED...\n"
        "[certificate data — EXPIRED]\n"
        "-----END CERTIFICATE-----\n"
    ),
    # Fresh cert ready to use
    "/etc/ssl/staging/api-gateway.crt": (
        "-----BEGIN CERTIFICATE-----\n"
        "# Certificate: api-gateway.company.com\n"
        "# Issued:      2026-03-27\n"
        "# Expires:     2027-03-27  ← VALID for 1 year\n"
        "# Issuer:      Let's Encrypt Authority X3\n"
        "# Auto-renewed by certbot at 06:00 today\n"
        "MIIDXTCCAkWgAwIBAgIJAKoK0SZpNEWCERT...\n"
        "[certificate data — VALID]\n"
        "-----END CERTIFICATE-----\n"
    ),
    "/etc/ssl/private/api-gateway.key": (
        "-----BEGIN PRIVATE KEY-----\n"
        "# Private key for api-gateway.company.com\n"
        "[private key data]\n"
        "-----END PRIVATE KEY-----\n"
    ),
    "/var/log/api-gateway/error.log": (
        "[2026-03-27 00:00:01] WARN:  SSL certificate expires in 0 days!\n"
        "[2026-03-27 06:00:00] INFO:  certbot renewal succeeded → /etc/ssl/staging/api-gateway.crt\n"
        "[2026-03-27 06:00:00] WARN:  New cert in staging — manual deploy required\n"
        "[2026-03-27 08:00:00] ERROR: SSL certificate EXPIRED: /etc/ssl/certs/api-gateway.crt\n"
        "[2026-03-27 08:00:00] ERROR: Expired: 2026-03-26, Now: 2026-03-27\n"
        "[2026-03-27 08:00:00] FATAL: Cannot start TLS listener. Service shutting down.\n"
    ),
    "/var/log/auth-service/error.log": (
        "[2026-03-27 08:00:01] ERROR: api-gateway TLS handshake failed: certificate verify failed\n"
        "[2026-03-27 08:00:01] ERROR: Internal service calls failing due to SSL error\n"
        "[2026-03-27 08:00:05] WARN:  Falling back to HTTP (insecure) mode\n"
    ),
    "/var/log/notification-service/error.log": (
        "[2026-03-27 08:00:02] ERROR: HTTPS connection to api-gateway failed: SSL certificate expired\n"
        "[2026-03-27 08:00:02] FATAL: Cannot deliver notifications. All HTTPS connections rejected.\n"
    ),
    "/var/log/postgres/postgresql.log": (
        "[2026-03-27 08:00:00] LOG: Database running normally\n"
        "[2026-03-27 08:00:00] LOG: No SSL connections attempted from api-gateway (service down)\n"
    ),
    "/etc/cron.d/certbot": (
        "# Certbot auto-renewal\n"
        "0 6 * * * root certbot renew --quiet --deploy-hook /opt/scripts/deploy_cert.sh\n"
        "# NOTE: deploy_cert.sh copies cert to /etc/ssl/staging/ but does NOT restart api-gateway!\n"
    ),
    "/opt/scripts/deploy_cert.sh": (
        "#!/bin/bash\n"
        "# Deploy renewed cert to staging directory\n"
        "# BUG: Does not copy to /etc/ssl/certs/ or restart api-gateway!\n"
        "cp /etc/letsencrypt/live/api-gateway/fullchain.pem /etc/ssl/staging/api-gateway.crt\n"
        "echo 'Cert deployed to staging. Manual promotion required.'\n"
        "# TODO: Add: cp /etc/ssl/staging/api-gateway.crt /etc/ssl/certs/api-gateway.crt\n"
        "# TODO: Add: systemctl restart api-gateway\n"
    ),
    "/etc/crontab": (
        "# System crontab\n"
        "0 6 * * * root /etc/cron.d/certbot\n"
    ),
}


def _ssl_expired_services():
    return {
        "postgres": ServiceHealth(
            name="postgres", status=ServiceStatus.RUNNING,
            cpu_percent=1.5, memory_mb=512.0, uptime_seconds=86400.0, pid=1001,
        ),
        "auth-service": ServiceHealth(
            name="auth-service", status=ServiceStatus.DEGRADED,
            cpu_percent=2.1, memory_mb=128.0, uptime_seconds=3600.0, pid=1045,
            error_message="Running in HTTP fallback mode (TLS unavailable)",
        ),
        "api-gateway": ServiceHealth(
            name="api-gateway", status=ServiceStatus.CRASHED,
            cpu_percent=0.0, memory_mb=0.0, uptime_seconds=None, pid=None,
            error_message="SSL certificate expired (2026-03-26). Cannot start TLS listener.",
        ),
        "user-service": ServiceHealth(
            name="user-service", status=ServiceStatus.DEGRADED,
            cpu_percent=1.8, memory_mb=192.0, uptime_seconds=3600.0, pid=1089,
            error_message="api-gateway down; serving internal requests only",
        ),
        "notification-service": ServiceHealth(
            name="notification-service", status=ServiceStatus.CRASHED,
            cpu_percent=0.0, memory_mb=0.0, uptime_seconds=None, pid=None,
            error_message="All HTTPS connections to api-gateway rejected",
        ),
    }
