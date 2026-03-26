"""
Optimal command sequences used by the heuristic baseline agent.

Kept in a separate module so baseline.py can import them
without pulling in FastAPI (which requires Python ≤ 3.12).
"""
from typing import Dict, List

BASELINE_SCRIPTS: Dict[str, List[str]] = {
    "task1_discovery": [
        "systemctl list-units --type=service",
        "systemctl status postgres",
        "systemctl status auth-service",
        "systemctl status api-gateway",
        "systemctl status user-service",
        "systemctl status notification-service",
        "curl http://localhost/health",
        "curl http://auth-service:8001/health",
        "curl http://user-service:8002/health",
        "curl http://notification-service:8003/health",
    ],
    "task2_rca": [
        "systemctl list-units --type=service",
        "systemctl status auth-service",
        "cat /var/log/auth-service/error.log",
        "cat /etc/crontab",
        "cat /opt/scripts/rotate_db_passwords.sh",
        "cat /var/log/postgres/postgresql.log",
        "grep -r 'password' /etc/services/auth-service/config.yml",
        "cat /etc/services/auth-service/config.yml",
        "cat /etc/services/user-service/config.yml",
        "ls /etc/secrets/",
        "cat /etc/secrets/db_credentials",
        "grep -r 'password' /var/log/postgres/postgresql.log",
    ],
    "task3_remediation": [
        # Discover state
        "systemctl list-units --type=service",
        "systemctl status auth-service",
        "cat /var/log/auth-service/error.log",
        "cat /var/log/postgres/postgresql.log",
        # Find new credentials
        "ls /etc/secrets/",
        "cat /etc/secrets/db_credentials",
        # Inspect stale configs
        "cat /etc/services/auth-service/config.yml",
        "cat /etc/services/user-service/config.yml",
        # Fix auth-service config
        "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' /etc/services/auth-service/config.yml",
        # Fix user-service config
        "sed -i 's/db_pass_v1_abc123/db_pass_v2_xyz789/g' /etc/services/user-service/config.yml",
        # Verify fixes
        "grep 'password' /etc/services/auth-service/config.yml",
        "grep 'password' /etc/services/user-service/config.yml",
        # Restart in dependency order
        "systemctl restart auth-service",
        "systemctl status auth-service",
        "systemctl restart user-service",
        "systemctl restart notification-service",
        "systemctl restart api-gateway",
        # Verify all healthy
        "systemctl status auth-service",
        "systemctl status user-service",
        "systemctl status notification-service",
        "systemctl status api-gateway",
        "curl http://localhost/health",
        "curl http://auth-service:8001/health",
        "curl http://user-service:8002/health",
        "curl http://notification-service:8003/health",
    ],
    "task4_disk_full": [
        # Discover the problem
        "df -h",
        "cat /var/log/api-gateway/error.log",
        "systemctl list-units --type=service",
        # Find large files
        "du -sh /var/log/*",
        "ls -lh /var/log/api-gateway/",
        "ls -lh /var/log/auth-service/",
        "ls -lh /var/log/notification-service/",
        # Free disk space
        "truncate -s 0 /var/log/api-gateway/access.log",
        "truncate -s 0 /var/log/api-gateway/error.log",
        "truncate -s 0 /var/log/auth-service/error.log",
        "truncate -s 0 /var/log/notification-service/error.log",
        # Verify space freed
        "df -h",
        # Restart in dependency order: auth first, then dependents
        "systemctl restart auth-service",
        "systemctl restart user-service",
        "systemctl restart notification-service",
        "systemctl restart api-gateway",
        # Verify all healthy
        "systemctl status auth-service",
        "systemctl status user-service",
        "systemctl status notification-service",
        "systemctl status api-gateway",
        "systemctl status postgres",
    ],
    "task5_ssl_expired": [
        # Discover the problem
        "systemctl list-units --type=service",
        "systemctl status api-gateway",
        "cat /var/log/api-gateway/error.log",
        # Inspect the expired cert
        "cat /etc/ssl/certs/api-gateway.crt",
        # Find renewed cert in staging
        "ls /etc/ssl/staging/",
        "cat /etc/ssl/staging/api-gateway.crt",
        # Check the certbot cron/script
        "cat /etc/cron.d/certbot",
        "cat /opt/scripts/deploy_cert.sh",
        # Deploy the new cert
        "cp /etc/ssl/staging/api-gateway.crt /etc/ssl/certs/api-gateway.crt",
        # Verify cert deployed
        "cat /etc/ssl/certs/api-gateway.crt",
        # Restart in dependency order
        "systemctl restart auth-service",
        "systemctl restart user-service",
        "systemctl restart notification-service",
        "systemctl restart api-gateway",
        # Verify all healthy
        "systemctl status api-gateway",
        "systemctl status auth-service",
        "systemctl status notification-service",
        "systemctl status user-service",
        "systemctl status postgres",
    ],
}
