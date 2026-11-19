"""Operational FSDR settings; separate from laboratory configuration and passwords."""

import json
import math
from pathlib import Path

from .core import Error


DEFAULTS = {
    "secret_id": None, "inventory_enabled": False, "state_dir": None, "log_dir": None,
    "ca_file": None, "request_timeout": 20, "wait_timeout": 1800, "poll_interval": 2,
    "use_private_ip": False,
    "leader_role": None, "follower_role": None, "inventory_interval_hours": 2,
    "max_inventory_age_hours": 4, "retention_days": 30, "service_timeout_seconds": 3600,
    "max_checkpoint_lag": 0, "enforce_rpo": False,
    "snapshot_repositories": None, "max_snapshot_age_hours": 24, "snapshot_required": False,
}


# Kept next to DEFAULTS so the API's internal CLI and accepted configuration
# cannot drift apart. The longer operational guidance remains in CONTAINER.rst.
FIELD_HELP = {
    "secret_id": "OCI Secret OCID containing clusters and OpenSearch credentials.",
    "inventory_enabled": "Allows the container scheduler to run inventory.",
    "state_dir": "Persistent directory for manifest, cache, locks, and history.",
    "log_dir": "Directory for JSONL logs.",
    "ca_file": "Path to an additional CA for OpenSearch endpoint TLS.",
    "use_private_ip": "Connects to OCI-discovered private IP while preserving FQDN for TLS.",
    "request_timeout": "Seconds allowed for each OCI or HTTP call.",
    "wait_timeout": "Seconds allowed while waiting for CCR convergence.",
    "poll_interval": "Seconds between checks while waiting.",
    "leader_role": "Override for CCR leader role; null uses default detection.",
    "follower_role": "Override for CCR follower role; null uses default detection.",
    "inventory_interval_hours": "Approximate container inventory interval in hours.",
    "max_inventory_age_hours": "Maximum inventory age in hours accepted during failover.",
    "retention_days": "Days to retain local logs and history.",
    "service_timeout_seconds": "Reserved timeout value for scheduled execution.",
    "max_checkpoint_lag": "Maximum CCR checkpoint lag when RPO is enforced.",
    "enforce_rpo": "Blocks failover if RPO cannot be met.",
    "snapshot_repositories": "Map of snapshot repositories for clusters a and b.",
    "max_snapshot_age_hours": "Maximum snapshot age in hours accepted by readiness.",
    "snapshot_required": "Requires a recent successful snapshot during readiness.",
}


def configuration_reference():
    """Return serializable configuration metadata for CLI help and tooling."""
    return [{"field": name, "default": default, "description": FIELD_HELP[name]}
            for name, default in DEFAULTS.items()]


def load_settings(path):
    cfg = dict(DEFAULTS)
    if path:
        path = Path(path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise Error(f"Arquivo de configuração não encontrado: {path}") from None
        except PermissionError:
            raise Error(f"Sem permissão para ler o arquivo de configuração: {path}") from None
        except UnicodeDecodeError:
            raise Error(f"Arquivo de configuração não está em UTF-8: {path}") from None
        except json.JSONDecodeError as exc:
            raise Error(
                f"JSON inválido no arquivo de configuração {path}, linha {exc.lineno}, "
                f"coluna {exc.colno}: {exc.msg}. Corrija a sintaxe (aspas, vírgulas e chaves); "
                "use the container API documentation to consult accepted fields."
            ) from None
        if not isinstance(data, dict) or set(data) - set(DEFAULTS):
            raise Error("Configuração FSDR inválida ou contém campos desconhecidos")
        cfg.update(data)
        for field in ("state_dir", "log_dir", "ca_file"):
            if cfg[field] is not None:
                if not isinstance(cfg[field], str) or not cfg[field]:
                    raise Error(f"{field} deve ser um caminho não vazio ou null")
                cfg[field] = str((path.parent / cfg[field]).resolve())
    for field in ("request_timeout", "wait_timeout", "poll_interval", "max_inventory_age_hours", "max_snapshot_age_hours"):
        if (isinstance(cfg[field], bool) or not isinstance(cfg[field], (int, float))
                or not math.isfinite(cfg[field]) or cfg[field] <= 0):
            raise Error(f"{field} deve ser positivo e finito")
    for field in ("inventory_interval_hours", "retention_days", "service_timeout_seconds"):
        if type(cfg[field]) is not int or cfg[field] < 1:
            raise Error(f"{field} deve ser um inteiro positivo")
    for field in ("inventory_enabled", "use_private_ip", "enforce_rpo", "snapshot_required"):
        if type(cfg[field]) is not bool:
            raise Error(f"{field} deve ser true ou false")
    if type(cfg["max_checkpoint_lag"]) is not int or cfg["max_checkpoint_lag"] < 0:
        raise Error("max_checkpoint_lag deve ser inteiro maior ou igual a zero")
    for field in ("secret_id", "leader_role", "follower_role"):
        if cfg[field] is not None and (not isinstance(cfg[field], str) or not cfg[field]):
            raise Error(f"{field} deve ser string não vazia ou null")
    repositories = cfg["snapshot_repositories"]
    if repositories is not None:
        if not isinstance(repositories, dict) or set(repositories) != {"a", "b"} or any(
                not isinstance(value, str) or not value for value in repositories.values()):
            raise Error("snapshot_repositories deve conter os nomes não vazios dos repositórios a e b")
    return cfg


def merge_args(args):
    cfg = load_settings(getattr(args, "config", None))
    for field in ("secret_id", "state_dir", "log_dir", "ca_file", "request_timeout", "wait_timeout",
                  "poll_interval", "use_private_ip", "leader_role", "follower_role",
                  "max_inventory_age_hours", "retention_days"):
        value = getattr(args, field, None)
        if value is not None:
            if field == "secret_id" and cfg[field] is not None and value != cfg[field]:
                raise Error("OCID informado difere do Secret no arquivo de configuração")
            cfg[field] = value
        setattr(args, field, cfg[field])
    # Values without CLI overrides are still exposed uniformly to runtime modules.
    for field, value in cfg.items():
        if not hasattr(args, field):
            setattr(args, field, value)
    return cfg
