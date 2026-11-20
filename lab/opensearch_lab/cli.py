"""Independent local configuration/state for optional demonstration tools."""

import argparse
import json
import os
import sys

from .cloud import discover_runtime
from .client import clients
from .core import Error, LOG, Store, configure_logging, feedback, lab_settings
from .operations import IDLE, precheck
from .cleanup import cleanup
from .dashboard import mutate_batch
from .setup import setup
from .topology import observe_writer


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("informe um inteiro positivo")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("informe um inteiro não negativo")
    return number


def main(command, argv=None):
    parser = argparse.ArgumentParser(description=f"OpenSearch LAB / {command}; não executa DR")
    parser.add_argument("--secret-id", help="OCID do Secret OCI; alternatively set OPENSEARCH_LAB_SECRET_ID")
    parser.add_argument("--state-dir", default="var/lab/state", help="estado exclusivo do lab")
    parser.add_argument("--log-dir", default="var/lab/log", help="logs exclusivos do lab")
    parser.add_argument("--prefix", default="fsdr-demo-", help="prefixo dos índices descartáveis")
    private_ip = parser.add_mutually_exclusive_group()
    private_ip.add_argument("--use-private-ip", dest="use_private_ip", action="store_true", default=True)
    private_ip.add_argument("--no-use-private-ip", dest="use_private_ip", action="store_false")
    parser.add_argument("--ca-file", help="CA bundle used to validate OpenSearch TLS")
    parser.add_argument("--request-timeout", type=positive, default=20)
    parser.add_argument("--wait-timeout", type=positive, default=1800)
    parser.add_argument("--poll-interval", type=positive, default=2)
    parser.add_argument("--leader-role", help="OpenSearch role used as CCR leader")
    parser.add_argument("--follower-role", help="OpenSearch role used as CCR follower")
    parser.add_argument("--dashboard-interval", type=positive, default=2)
    parser.add_argument("--operations-per-second", type=positive, default=2)
    parser.add_argument("--create-probability", type=float, default=0.6)
    if command == "setup":
        parser.add_argument("--cleanup", action="store_true",
                            help="plan or remove only indices created by this lab setup")
        parser.add_argument("--execute", action="store_true",
                            help="required together with --cleanup to delete indices")
        parser.add_argument("--indices", type=positive, default=3)
        parser.add_argument("--docs-per-index", type=nonnegative, default=1000)
        parser.add_argument("--batch-size", type=positive, default=500)
        parser.add_argument("--shards", type=positive, default=1)
        parser.add_argument("--replicas", type=nonnegative, default=1)
    args = parser.parse_args(argv)
    os.umask(0o077)
    configured = False
    try:
        if not 0 <= args.create_probability <= 1:
            raise Error("create-probability deve estar entre 0 e 1")
        if command == "setup" and args.execute and not args.cleanup:
            raise Error("--execute só pode ser usado com --cleanup")
        cfg = lab_settings(args)
        # JSONL retains diagnostics; the terminal shows only the command result.
        configure_logging(cfg, console=False)
        configured = True
        if command != "dashboard":
            feedback("Iniciando {} do laboratório. Detalhes em {}.".format(command, cfg["log_dir"]))
        cfg, credentials = discover_runtime(cfg)
        store, cs = Store(cfg), clients(cfg, credentials)
        if command == "setup":
            with store.lock(blocking=False, name="controller"), store.lock(blocking=False):
                if args.cleanup:
                    feedback("Ambiente identificado. Validando o dataset do laboratório para limpeza.")
                    result = cleanup(cfg, cs, store, execute=args.execute, progress=feedback)
                else:
                    feedback("Ambiente identificado. Preparando índices e CCR do laboratório.")
                    setup(cfg, cs, store, args.indices, args.docs_per_index, args.batch_size,
                          args.shards, args.replicas, progress=feedback)
                    result = store.read()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif command == "precheck":
            print(json.dumps(precheck(cfg, cs), indent=2, ensure_ascii=False))
        elif command in ("create-docs", "delete-docs"):
            action = "create" if command == "create-docs" else "delete"
            with store.lock(blocking=False, name="controller"), store.lock(blocking=False):
                state = store.read()
                if state["phase"] not in (*IDLE, "SERVICE_RESTORED", "REPROTECTING") or not state["indices"]:
                    raise Error("Lote recusado: setup/DR em andamento ou inventário vazio")
                feedback("Ambiente identificado. Executando lote de {} em todos os índices do laboratório.".format(
                    "criação" if action == "create" else "exclusão"))
                state = observe_writer(cfg, cs, state)
                store.write(state)
                result = mutate_batch(cfg, cs, state, action)
            result.update(action=action, primary=state["active"], indices=len(state["indices"]))
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            store.read()
            from .dashboard import run_dashboard
            run_dashboard(cfg, cs, store)
        LOG.info("lab_execution_completed command=%s", command)
        return 0
    except KeyboardInterrupt:
        if configured:
            LOG.warning("lab_interrupted command=%s", command)
        return 130
    except Exception as exc:
        if configured:
            LOG.exception("lab_execution_failed command=%s", command)
            print("Falha no laboratório: {}. Consulte o log.".format(exc), file=sys.stderr)
        else:
            print("Falha de configuração/logging: {}".format(exc), file=sys.stderr)
        return 1
