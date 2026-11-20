"""Linux curses UI. Network collection never blocks keyboard handling."""

from concurrent.futures import ThreadPoolExecutor
import curses
from datetime import datetime
import queue
import random
import threading
import time
from urllib.parse import quote
import uuid

from .core import Error, LOG
from .operations import IDLE, STOPPED, assert_uuid, check_shards
from .setup import scoped
from .topology import observe_writer


def collect(client, entries, stop):
    """Collect only indices owned by this lab execution.

    Listing every index through ``_cat/indices/*`` requires broader permissions
    than a CCR demonstration user normally has.  The lab already has the exact
    index names in its own state, so querying those directly is both safer and
    produces totals meaningful to the demonstration.
    """
    result = {"indices": {}, "index_total": len(entries), "docs_total": 0,
              "error": None, "collected_at": time.time()}
    for entry in entries:
        if stop.is_set():
            return result
        index = entry[client.name]
        try:
            count = client.count(index)
            result["docs_total"] += count
            try:
                status = client.status(index)
                details = status.get("syncing_details", {})
                leader = details.get("leader_checkpoint")
                follower = details.get("follower_checkpoint")
                ccr_status = status.get("status", "—")
                lag = leader - follower if isinstance(leader, int) and isinstance(follower, int) else None
            except Error:
                # The count remains useful when the credential cannot read the
                # replication status endpoint for this side of the pair.
                LOG.exception("dashboard_replication_status_error cluster=%s index=%s", client.name, index)
                ccr_status, lag = "indisponível", None
            result["indices"][index] = {"count": count, "status": ccr_status, "lag": lag}
        except Error:
            result["error"] = "Amostra parcial; consulte o log"
            LOG.exception("dashboard_index_error cluster=%s index=%s", client.name, index)
            result["indices"][index] = {"count": "ERRO", "status": "ERRO", "lag": None}
    return result


def mutate_once(cfg, cs, state, rng=random):
    active = state["active"]
    entry = rng.choice(state["indices"])
    index = scoped(cfg, entry[active])
    client = cs[active]
    assert_uuid(client, index, entry["uuids"][active])
    if rng.random() < cfg["create_probability"]:
        doc_id = "live-" + uuid.uuid4().hex
        client.request("PUT", f"/{index}/_create/{doc_id}", {
            "kind": "live", "created_at": int(time.time() * 1000), "value": rng.randrange(1_000_000)})
        action = "create"
    else:
        result = check_shards(client.request("POST", f"/{index}/_search", {
            "size": 1, "_source": False, "query": {"function_score": {
                "query": {"match_all": {}}, "random_score": {
                    "seed": rng.randrange(2_000_000_000), "field": "_seq_no"}}}}))
        if result.get("timed_out"):
            raise Error("Busca para exclusão excedeu timeout")
        hits = result["hits"]["hits"]
        if not hits:
            LOG.info("generator_delete_skipped cluster=%s index=%s reason=empty", active, index)
            return "skip"
        doc_id = hits[0]["_id"]
        if doc_id.startswith(("fsdr-probe-", "fsdr-replication-probe-")):
            return "skip"
        result = client.request("DELETE", f"/{index}/_doc/{quote(doc_id, safe='')}", allow=(404,))
        if result.get("_http_status") == 404:
            return "skip"
        action = "delete"
    LOG.info("generator_operation cluster=%s index=%s action=%s id=%s", active, index, action, doc_id)
    return action


def mutate_batch(cfg, cs, state, action, rng=random):
    """Apply one explicit F4/F8 batch to every owned lab index."""
    active, client = state["active"], cs[state["active"]]
    result = {"create": 0, "delete": 0, "skip": 0, "planned": {}}
    for entry in state["indices"]:
        index = scoped(cfg, entry[active])
        assert_uuid(client, index, entry["uuids"][active])
        amount = rng.randint(100, 300)
        result["planned"][index] = amount
        if action == "create":
            documents = [(index, "live-" + uuid.uuid4().hex, {
                "kind": "live", "created_at": int(time.time() * 1000),
                "value": rng.randrange(1_000_000)}) for _ in range(amount)]
            client.bulk(documents)
            result["create"] += len(documents)
            continue
        search = check_shards(client.request("POST", f"/{index}/_search", {
            "size": amount, "_source": False, "query": {"function_score": {
                "query": {"match_all": {}}, "random_score": {
                    "seed": rng.randrange(2_000_000_000), "field": "_seq_no"}}}}))
        if search.get("timed_out"):
            raise Error("Busca para exclusão excedeu timeout")
        ids = [hit["_id"] for hit in search["hits"]["hits"]
               if not hit["_id"].startswith(("fsdr-probe-", "fsdr-replication-probe-"))]
        if not ids:
            result["skip"] += amount
            continue
        result["delete"] += client.bulk_delete([(index, doc_id) for doc_id in ids])
        result["skip"] += amount - len(ids)
    LOG.info("dashboard_batch_completed action=%s active=%s result=%s", action, active, result)
    return result


def display_status(values, cluster, active):
    """Return a compact, operator-facing CCR state for the dashboard."""
    if values["count"] == "ERRO":
        return "ERRO"
    status = str(values.get("status") or "—").upper()
    if cluster == active and status in ("STOPPED", "REPLICATION NOT IN PROGRESS"):
        return "PRIMARY"
    if status == "SYNCING":
        lag = values.get("lag")
        return "SYNC" if lag is None else "SYNC lag={}".format(lag)
    if status == "INDISPONÍVEL":
        return "CCR indisponível"
    return status


def run_dashboard(cfg, cs, store):
    stop = threading.Event()
    data_lock = threading.Lock()
    actions = queue.Queue(maxsize=1)
    snapshots = {}
    topology = {"active": "?", "error": "coletando"}
    counters = {"create": 0, "delete": 0, "skip": 0, "error": 0}
    batch = {"status": "aguardando comando", "last": None}

    def sample():
        with ThreadPoolExecutor(max_workers=2) as pool:
            while not stop.is_set():
                try:
                    entries = store.read()["indices"]
                except Exception:
                    LOG.exception("dashboard_state_for_sample_error")
                    entries = []
                futures = {name: pool.submit(collect, client, entries, stop) for name, client in cs.items()}
                for name, future in futures.items():
                    value = future.result()
                    with data_lock:
                        snapshots[name] = value
                try:
                    observed = observe_writer(cfg, cs, store.read())
                    with data_lock:
                        topology.update(active=observed["active"], error=None)
                except Exception:
                    LOG.exception("lab_topology_unavailable")
                    with data_lock:
                        topology.update(active="?", error="topologia em transição/indisponível")
                stop.wait(cfg["dashboard_interval"])

    def worker():
        while not stop.is_set():
            try:
                action = actions.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with store.lock(blocking=False):
                    state = store.read()
                    if state["phase"] not in (*IDLE, "SERVICE_RESTORED", "REPROTECTING") or not state["indices"]:
                        raise Error("Lote recusado: setup/DR em andamento ou inventário vazio")
                    state = observe_writer(cfg, cs, state)
                    store.write(state)
                    result = mutate_batch(cfg, cs, state, action)
                    with data_lock:
                        for key in ("create", "delete", "skip"):
                            counters[key] += result[key]
                        batch.update(status="último lote: {}".format(action), last=result)
            except Exception:
                LOG.exception("dashboard_batch_failed")
                with data_lock:
                    counters["error"] += 1
                    batch.update(status="falha no lote; consulte o log")
            finally:
                actions.task_done()

    sampler = threading.Thread(target=sample, daemon=True, name="dashboard-sampler")
    generator = threading.Thread(target=worker, daemon=True, name="dashboard-worker")
    sampler.start()
    generator.start()

    def ui(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        theme = {"border": 0, "title": curses.A_BOLD, "cluster_a": curses.A_BOLD,
                 "cluster_b": curses.A_BOLD, "primary": curses.A_BOLD,
                 "follower": curses.A_BOLD, "good": curses.A_BOLD,
                 "warning": curses.A_BOLD, "error": curses.A_BOLD,
                 "muted": curses.A_DIM, "clock": 0, "shortcut": curses.A_BOLD,
                 "plain": 0, "create": curses.A_BOLD,
                 "pending_create": curses.A_BOLD | curses.A_BLINK}
        if curses.has_colors():
            try:
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_CYAN, -1)
                curses.init_pair(2, curses.COLOR_GREEN, -1)
                curses.init_pair(3, curses.COLOR_BLUE, -1)
                curses.init_pair(4, curses.COLOR_YELLOW, -1)
                curses.init_pair(5, curses.COLOR_RED, -1)
                curses.init_pair(6, curses.COLOR_MAGENTA, -1)
                curses.init_pair(7, curses.COLOR_WHITE, -1)
                theme.update(border=curses.color_pair(1), title=curses.color_pair(1) | curses.A_BOLD,
                             cluster_a=curses.color_pair(4) | curses.A_BOLD,
                             cluster_b=curses.color_pair(6) | curses.A_BOLD,
                             primary=curses.color_pair(2) | curses.A_BOLD,
                             follower=curses.color_pair(3) | curses.A_BOLD,
                             good=curses.color_pair(2), warning=curses.color_pair(4) | curses.A_BOLD,
                             error=curses.color_pair(5) | curses.A_BOLD, muted=curses.A_DIM,
                             clock=curses.color_pair(1), shortcut=curses.color_pair(3) | curses.A_BOLD,
                             plain=curses.color_pair(7), create=curses.color_pair(2) | curses.A_BOLD,
                             pending_create=curses.color_pair(5) | curses.A_BOLD | curses.A_BLINK)
            except curses.error:
                # Monochrome terminals retain the same layout and information.
                pass
        screen.keypad(True)
        screen.timeout(200)
        offset = 0
        while True:
            try:
                state = store.read()
                active, phase = state["active"], state["phase"]
            except Exception:
                LOG.exception("dashboard_state_error")
                active, phase = "?", "ERRO DE ESTADO"
            with data_lock:
                views = dict(snapshots)
                counts = dict(counters)
                active = topology["active"]
                topology_error = topology["error"]
                batch_status = batch["status"]
            height, width = screen.getmaxyx()
            screen.erase()

            def text(row, col, value, limit=None, attr=0):
                if 0 <= row < height and 0 <= col < width:
                    try:
                        available = width - col if limit is None else min(limit, width - col)
                        screen.addnstr(row, col, value, max(0, available), attr)
                    except curses.error:
                        pass

            def box(top, left, box_height, box_width, title, title_attr=None):
                """Draw a btop-like panel, safely clipped to the terminal."""
                if box_height < 3 or box_width < 4 or top >= height or left >= width:
                    return
                box_height = min(box_height, height - top)
                box_width = min(box_width, width - left)
                inner = box_width - 2
                label = " {} ".format(title)
                border = "─" * inner
                text(top, left, "┌" + border + "┐", box_width, theme["border"])
                text(top, left + 2, label, max(0, box_width - 3), title_attr or theme["title"])
                for current in range(top + 1, top + box_height - 1):
                    text(current, left, "│", attr=theme["border"])
                    text(current, left + box_width - 1, "│", attr=theme["border"])
                text(top + box_height - 1, left, "└" + border + "┘", box_width, theme["border"])

            def panel_line(row, left, box_width, label, value, value_attr=0):
                # Render the two fields separately.  Formatting the whole line
                # first can clip the right-aligned value by one column on wide
                # terminals, which makes small values appear absent.
                content_left = left + 2
                content_right = left + box_width - 2
                rendered = str(value)
                text(row, content_left, label, max(0, content_right - content_left), theme["muted"])
                text(row, max(content_left, content_right - len(rendered)), rendered,
                     max(0, content_right - content_left), value_attr)

            def segments(row, col, values, limit):
                """Draw adjacent styled fragments, clipped to the terminal."""
                current = col
                for value, attr in values:
                    if current >= col + limit:
                        break
                    text(row, current, value, col + limit - current, attr)
                    current += len(value)

            def cluster_name(name):
                return cfg["clusters"].get(name, {}).get("display_name") or name.upper()

            def cluster_attr(name):
                return theme["cluster_a"] if name == "a" else theme["cluster_b"]

            box(0, 0, 4, width, " OCI OpenSearch CCR / LAB ")
            observed_name = cluster_name(active) if active in ("a", "b") else "indisponível"
            first_label = "Primary observado: "
            last_label = "   |   {}".format(topology_error or "topologia identificada")
            text(1, 2, first_label, width - 4, theme["muted"])
            first_name_col = 2 + len(first_label)
            text(1, first_name_col, observed_name, width - first_name_col - 2,
                 cluster_attr(active) if active in ("a", "b") else theme["warning"])
            text(1, first_name_col + len(observed_name), last_label,
                 width - first_name_col - len(observed_name) - 2,
                 theme["warning"] if topology_error else theme["good"])
            segments(2, 2, [
                ("F4", theme["shortcut"]), (" criar 100-300 docs por índice   ", theme["plain"]),
                ("F8", theme["shortcut"]), (" excluir até 100-300 docs por índice   ", theme["plain"]),
                ("Q", theme["shortcut"]), (" sair   ", theme["plain"]),
                ("↑↓", theme["shortcut"]), (" rolar", theme["plain"]),
            ], width - 4)
            operations_prefix = "Operações: +{}  -{}  sem ação={}  erros={}   | ".format(
                counts["create"], counts["delete"], counts["skip"], counts["error"])
            text(3, 2, operations_prefix, width - 4,
                 theme["error"] if counts["error"] else theme["muted"])
            batch_text = batch_status.lower()
            if ("lote de criação pendente" in batch_text or
                    "lote de exclusão pendente" in batch_text):
                batch_attr = theme["pending_create"]
            elif "create" in batch_text:
                batch_attr = theme["create"]
            elif "aguarde: já existe um lote pendente/em execução" in batch_text:
                batch_attr = theme["error"]
            else:
                batch_attr = (theme["warning"] if "pausadas:" in batch_text else
                              theme["error"] if "falha" in batch_text else theme["plain"])
            text(3, 2 + len(operations_prefix), batch_status,
                 width - 4 - len(operations_prefix), batch_attr)

            summary_top = 5
            table_top = 11
            view_a, view_b = views.get("a"), views.get("b")
            left_width = max(4, width // 2 - 1)
            right_left = left_width + 1
            right_width = max(4, width - right_left)
            box(summary_top, 0, 5, left_width,
                " {} {} ".format(cluster_name("a"), "(PRIMARY)" if active == "a" else "(FOLLOWER)"),
                cluster_attr("a"))
            box(summary_top, right_left, 5, right_width,
                " {} {} ".format(cluster_name("b"), "(PRIMARY)" if active == "b" else "(FOLLOWER)"),
                cluster_attr("b"))
            if view_a is None or view_b is None:
                text(summary_top + 2, 2, "Coletando índices do laboratório...", left_width - 4)
                text(summary_top + 2, right_left + 2, "Coletando índices do laboratório...", right_width - 4)
                table = []
            else:
                age_a = int(time.time() - view_a["collected_at"])
                age_b = int(time.time() - view_b["collected_at"])
                panel_line(summary_top + 1, 0, left_width, "Índices:", view_a["index_total"], theme["title"])
                panel_line(summary_top + 2, 0, left_width, "Documentos:", "{:,}".format(view_a["docs_total"]).replace(",", "."), theme["good"])
                panel_line(summary_top + 3, 0, left_width, "Amostra:", "{}s".format(age_a), theme["clock"])
                panel_line(summary_top + 1, right_left, right_width, "Índices:", view_b["index_total"], theme["title"])
                panel_line(summary_top + 2, right_left, right_width, "Documentos:", "{:,}".format(view_b["docs_total"]).replace(",", "."), theme["good"])
                panel_line(summary_top + 3, right_left, right_width, "Amostra:", "{}s".format(age_b), theme["clock"])
                notices = "; ".join(filter(None, (view_a["error"], view_b["error"])))
                if notices:
                    text(summary_top + 3, 2, notices, left_width - 4, theme["warning"])
                table = []
                try:
                    entries = store.read()["indices"]
                except Exception:
                    entries = []
                half = max(20, (width - 5) // 2)
                index_width = max(8, half - 29)
                for entry in entries:
                    index_a, index_b = entry["a"], entry["b"]
                    values_a = view_a["indices"].get(index_a, {"count": "—", "status": "—", "lag": None})
                    values_b = view_b["indices"].get(index_b, {"count": "—", "status": "—", "lag": None})
                    table.append("{:<{}} {:>10} {:<16} │ {:<{}} {:>10} {:<16}".format(
                        index_a[:index_width], index_width, str(values_a["count"]), display_status(values_a, "a", active)[:16],
                        index_b[:index_width], index_width, str(values_b["count"]), display_status(values_b, "b", active)[:16]))

            table_height = max(3, height - table_top - 2)
            box(table_top, 0, table_height, width, " COMPARATIVO DE ÍNDICES REPLICADOS ")
            if width >= 70:
                half = max(20, (width - 5) // 2)
                index_width = max(8, half - 29)
                label_a = ("ÍNDICE " + cluster_name("a"))[:index_width]
                label_b = ("ÍNDICE " + cluster_name("b"))[:index_width]
                header = "{:<{}} {:>10} {:<16} │ {:<{}} {:>10} {:<16}".format(
                    label_a, index_width, "DOCS", "ESTADO", label_b, index_width, "DOCS", "ESTADO")
                text(table_top + 1, 2, header, width - 4, theme["muted"])
                text(table_top + 1, 2, label_a, index_width, cluster_attr("a"))
                right_header_col = 2 + index_width + 31
                text(table_top + 1, right_header_col, label_b, index_width, cluster_attr("b"))
            content_bottom = table_top + table_height - 1
            for number, item in enumerate(table[offset:], table_top + 2):
                if number >= content_bottom:
                    break
                row_attr = theme["error"] if "ERRO" in item else theme["good"] if "SYNC" in item else 0
                text(number, 2, item, width - 4, row_attr)
            footer = "Contagens seguem refresh; totais iguais não comprovam sincronismo."
            clock = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
            text(height - 1, 0, footer, max(0, width - len(clock) - 2), theme["muted"])
            text(height - 1, max(0, width - len(clock)), clock, len(clock), theme["clock"])
            screen.refresh()
            key = screen.getch()
            if key in (ord("q"), ord("Q")):
                break
            if key in (curses.KEY_F4, curses.KEY_F8):
                action = "create" if key == curses.KEY_F4 else "delete"
                try:
                    actions.put_nowait(action)
                    with data_lock:
                        batch.update(status="lote {} pendente".format("de criação" if action == "create" else "de exclusão"))
                    LOG.info("dashboard_batch_queued action=%s", action)
                except queue.Full:
                    with data_lock:
                        batch.update(status="aguarde: já existe um lote pendente/em execução")
            elif key == curses.KEY_DOWN:
                offset = min(offset + 1, max(0, len(table) - 1))
            elif key == curses.KEY_UP:
                offset = max(0, offset - 1)
    try:
        curses.wrapper(ui)
    finally:
        stop.set()
        generator.join()
        LOG.info("dashboard_closed counters=%s", counters)
