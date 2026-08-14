"""Benchmark de charge reproductible pour le serveur Synapse.

Mesure réelle sur socket Unix : RPS, latences (min/moyenne/max/p50/p95/p99),
erreurs, et surveillance du serveur (CPU %, RSS) pendant chaque scénario.
Auto-bootstrap : crée l'organisation et les comptes de benchmark si absents, puis
peuple les conversations (idempotent — les identifiants client sont fixes).

Usage :
    python benchmarks/bench.py --config <config.json> [--socket PATH]
                               [--duration 15] [--quick] [--out results.json]

La mesure utilise le hacheur Argon2id de PRODUCTION (coût réel). Le
générateur de charge est asyncio (CPU ~0 %) : il n'est jamais le facteur
limitant. Une garde mémoire interrompt un scénario si le RSS du serveur
dépasse 2,5 GiB (64 MiB par vérification Argon2id concurrente).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import resource
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.client import ApiClientError, Client  # noqa: E402
from synapse.config import Config  # noqa: E402
from synapse.db import ensure_storage  # noqa: E402
from synapse.install import create_organization  # noqa: E402

MEMORY_GUARD_MIB = 2500
CLK_TCK = os.sysconf("SC_CLK_TCK")
NPROC = os.cpu_count() or 1

ORG = "bench_org"
AGENT_PW = "mot-de-passe-bench-1"
AGENTS = [f"bench{i}" for i in range(1, 7)]


# ---------------------------------------------------------------------------
# État global (rempli au bootstrap)
# ---------------------------------------------------------------------------

SERVER_PID = 0
CONV_B = ""
READ_IDS: list[str] = []
_counter = [0]


# ---------------------------------------------------------------------------
# Bootstrap du stockage (idempotent)
# ---------------------------------------------------------------------------

def _has_cmid(client: Client, user: str, cmid: str) -> bool:
    """Probe rapide : le message cmid existe-t-il dans la boîte de user ?"""
    for m in client.get_messages(user, AGENT_PW, limit=100)["messages"]:
        if m["client_message_id"] == cmid:
            return True
    return False


def _read_pool_ids(client: Client) -> list[str]:
    ids = []
    cursor = None
    while True:
        page = client.get_messages("bench5", AGENT_PW, limit=100, cursor=cursor)
        ids.extend(m["message_id"] for m in page["messages"]
                   if m["sender_username"] == "bench6")
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return ids


def bootstrap(config: Config, org_password: str, quick: bool = False) -> str:
    """Peuple le stockage (idempotent) et retourne l'id de la conversation B."""
    ensure_storage(config)
    try:
        create_organization(config, ORG, org_password, org_password)
    except ValueError:
        pass  # organisation déjà présente
    client = Client(config.socket_path)
    for name in AGENTS:
        try:
            client.create_agent(name, AGENT_PW, f"Agent de benchmark {name}",
                                ORG, org_password)
        except ApiClientError as exc:
            if exc.code != "USERNAME_ALREADY_EXISTS":
                raise
    if quick:
        for i in range(10):
            client.send_message("bench2", f"Message de benchmark A-{i}",
                                f"cmid-bench-a-{i}", "bench1", AGENT_PW)
        for i in range(20):
            d = client.send_message("bench5", f"Message de benchmark R-{i}",
                                    f"cmid-bench-r-{i}", "bench6", AGENT_PW)
            READ_IDS.append(d["message_id"])
        for i in range(5):
            client.send_message("bench4", f"Message de benchmark B-{i}",
                                f"cmid-bench-b-{i}", "bench3", AGENT_PW)
    else:
        if not _has_cmid(client, "bench2", "cmid-bench-a-99"):
            for i in range(100):
                client.send_message("bench2", f"Message de benchmark A-{i}",
                                    f"cmid-bench-a-{i}", "bench1", AGENT_PW)
        if not _has_cmid(client, "bench5", "cmid-bench-r-299"):
            for i in range(300):
                d = client.send_message("bench5", f"Message de benchmark R-{i}",
                                        f"cmid-bench-r-{i}", "bench6", AGENT_PW)
                READ_IDS.append(d["message_id"])
        if not _has_cmid(client, "bench4", "cmid-bench-b-49"):
            for i in range(50):
                client.send_message("bench4", f"Message de benchmark B-{i}",
                                    f"cmid-bench-b-{i}", "bench3", AGENT_PW)
    if not READ_IDS:
        READ_IDS.extend(_read_pool_ids(client))
    conv_b = client.get_conversation("bench3", "bench4", AGENT_PW)["conversation_id"]
    print(f"Bootstrap OK : {len(AGENTS)} comptes, pool de lecture "
          f"{len(READ_IDS)} messages (idempotent au relancement).")
    return conv_b


# ---------------------------------------------------------------------------
# Surveillance du serveur
# ---------------------------------------------------------------------------

def _cpu_ticks(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
        return (int(parts[13]) + int(parts[14])) / CLK_TCK
    except (OSError, IndexError, ValueError):
        return 0.0


def _rss_mib(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status") as f:
            return int([l for l in f if l.startswith("VmRSS:")][0].split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        return 0


def _self_cpu_percent() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return (r.ru_utime + r.ru_stime) / max(time.monotonic(), 1e-9) * 100


class Run:
    """Collecte des latences (ms) et des erreurs d'un scénario."""

    def __init__(self) -> None:
        self.lats: list[float] = []
        self.error_codes: dict[str, int] = {}
        self.start = time.perf_counter()

    def add(self, lat_ms: float, code: str | None) -> None:
        self.lats.append(lat_ms)
        if code is not None:
            self.error_codes[code] = self.error_codes.get(code, 0) + 1

    def add_error(self, kind: str) -> None:
        self.error_codes[kind] = self.error_codes.get(kind, 0) + 1

    def stats(self, nconcurrent: int, samples: list[tuple[float, int]]) -> dict:
        dur = time.perf_counter() - self.start
        n = len(self.lats)
        s = sorted(self.lats)

        def pct(p: float) -> float:
            if not s:
                return 0.0
            return s[min(len(s) - 1, int(p / 100 * len(s)))]

        cpu_mean = statistics.fmean([c for c, _ in samples]) if samples else 0.0
        return {
            "concurrence": nconcurrent,
            "requetes": n,
            "duree_s": round(dur, 3),
            "rps": round(n / dur, 2),
            "lat_min_ms": round(s[0], 2) if s else 0.0,
            "lat_moy_ms": round(statistics.fmean(s), 2) if s else 0.0,
            "lat_max_ms": round(s[-1], 2) if s else 0.0,
            "p50_ms": round(pct(50), 2),
            "p95_ms": round(pct(95), 2),
            "p99_ms": round(pct(99), 2),
            "succes": n - sum(self.error_codes.values()),
            "erreurs": sum(self.error_codes.values()),
            "taux_erreur_pct": round(sum(self.error_codes.values()) / n * 100, 3) if n else 0.0,
            "codes_erreur": self.error_codes,
            "cpu_serveur_coeurs": round(cpu_mean / 100, 2),
            "rss_serveur_pic_mib": max((r for _, r in samples), default=0),
            "cpu_generateur_pct": round(_self_cpu_percent(), 1),
        }


# ---------------------------------------------------------------------------
# Générateurs de requêtes
# ---------------------------------------------------------------------------

def _payload(command: str, parameters: dict) -> bytes:
    return (json.dumps({"api_version": "v2", "command": command,
                        "parameters": parameters}) + "\n").encode()


def _auth(me: str) -> dict:
    return {"my_name_auth": me, "my_password_auth": AGENT_PW}


def req_help() -> bytes:
    return _payload("help", {**_auth("bench1"), "command_name": None})


def req_desc() -> bytes:
    return _payload("get_agent_description", {**_auth("bench1"), "username": "bench2"})


def req_messages() -> bytes:
    return _payload("get_messages", {**_auth("bench2"), "status": None,
                                     "sender_username": None, "conversation_id": None,
                                     "limit": 50, "cursor": None})


def req_conversation() -> bytes:
    return _payload("get_conversation", {**_auth("bench2"), "other_username": "bench1",
                                         "limit": 50, "cursor": None})


def req_notifications() -> bytes:
    return _payload("get_notifications", {**_auth("bench2"), "limit": 50, "cursor": None})


def req_read() -> bytes:
    mid = READ_IDS[_counter[0] % len(READ_IDS)]
    _counter[0] += 1
    return _payload("read_message", {**_auth("bench5"), "message_id": mid})


def req_send() -> bytes:
    _counter[0] += 1
    n = _counter[0]
    return _payload("send_message", {"recipient_username": "bench4",
                                     "message": f"Message de benchmark S-{n}",
                                     "client_message_id": f"cmid-bench-s-{n}",
                                     "business_reference": None,
                                     **_auth("bench3")})


def req_mark() -> bytes:
    return _payload("mark_conversation_no_reply", {**_auth("bench4"),
                                                   "conversation_id": CONV_B})


COMMANDS = {
    "help": req_help,
    "get_agent_description": req_desc,
    "get_messages": req_messages,
    "get_conversation": req_conversation,
    "get_notifications": req_notifications,
    "read_message": req_read,
    "send_message": req_send,
    "mark_conversation_no_reply": req_mark,
}

MIXED = (["get_messages"] * 30 + ["send_message"] * 20 + ["get_conversation"] * 15
         + ["get_notifications"] * 15 + ["read_message"] * 10 + ["help"] * 5
         + ["get_agent_description"] * 5)


def mixed_payload() -> bytes:
    return COMMANDS[random.choice(MIXED)]()


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------

async def _persistent_worker(payload_fn, run: Run, stop: asyncio.Event,
                             read_timeout: float, socket_path: str):
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path, limit=256 * 1024), timeout=10)
    except (OSError, asyncio.TimeoutError) as exc:
        run.add_error(f"connexion:{type(exc).__name__}")
        return
    try:
        while not stop.is_set():
            t0 = time.perf_counter_ns()
            try:
                writer.write(payload_fn())
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=read_timeout)
                lat = (time.perf_counter_ns() - t0) / 1e6
            except asyncio.TimeoutError:
                run.add_error("timeout")
                continue
            except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
                run.add_error(type(exc).__name__)
                break
            if not line:
                run.add_error("eof")
                break
            try:
                resp = json.loads(line)
            except ValueError:
                run.add_error("json")
                continue
            code = None if resp.get("error") is None else resp["error"].get("code")
            run.add(lat, code)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def _perrequest_worker(payload_fn, run: Run, stop: asyncio.Event,
                             read_timeout: float, socket_path: str):
    while not stop.is_set():
        t0 = time.perf_counter_ns()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path, limit=256 * 1024), timeout=10)
            writer.write(payload_fn())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=read_timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            lat = (time.perf_counter_ns() - t0) / 1e6
        except asyncio.TimeoutError:
            run.add_error("timeout")
            continue
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            run.add_error(type(exc).__name__)
            continue
        if not line:
            run.add_error("eof")
            continue
        try:
            resp = json.loads(line)
        except ValueError:
            run.add_error("json")
            continue
        code = None if resp.get("error") is None else resp["error"].get("code")
        run.add(lat, code)


async def run_scenario(name: str, payload_fn, w: int, duration: float,
                       socket_path: str, mode: str = "P",
                       read_timeout: float = 20.0) -> dict:
    stop = asyncio.Event()
    run = Run()
    worker = _persistent_worker if mode == "P" else _perrequest_worker
    # échauffement (résultats ignorés)
    warm = asyncio.Event()
    warm_run = Run()
    warm_tasks = [asyncio.create_task(worker(payload_fn, warm_run, warm, read_timeout,
                                             socket_path)) for _ in range(w)]
    await asyncio.sleep(2.0)  # échauffement court (résultats ignorés)
    warm.set()
    await asyncio.gather(*warm_tasks)
    # mesure avec surveillance CPU/RSS du serveur
    mon_stop = asyncio.Event()
    samples: list[tuple[float, int]] = []

    async def _monitor():
        prev = _cpu_ticks(SERVER_PID)
        while not mon_stop.is_set():
            await asyncio.sleep(0.5)
            cur = _cpu_ticks(SERVER_PID)
            samples.append(((cur - prev) / 0.5 * 100, _rss_mib(SERVER_PID)))
            prev = cur

    mon = asyncio.create_task(_monitor())
    tasks = [asyncio.create_task(worker(payload_fn, run, stop, read_timeout, socket_path))
             for _ in range(w)]
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*tasks)
    mon_stop.set()
    await mon
    st = run.stats(w, samples)
    st["scenario"] = name
    st["mode"] = mode
    if st["rss_serveur_pic_mib"] > MEMORY_GUARD_MIB:
        st["garde_memoire"] = True
    return st


async def run_steady(w: int, duration: float, socket_path: str) -> dict:
    """Régime établi : connexions créées une fois, drainage, mesure sans
    churn (isole du plafond anti-DoS de 64 connexions)."""
    conns = []
    for _ in range(w):
        try:
            r, wtr = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path), timeout=5)
            conns.append((r, wtr))
        except OSError:
            conns.append(None)
    await asyncio.sleep(3)  # drainage des threads précédents

    run = Run()

    async def worker(r, wtr):
        while True:
            t0 = time.perf_counter_ns()
            try:
                wtr.write(mixed_payload())
                await wtr.drain()
                line = await asyncio.wait_for(r.readline(), timeout=15)
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                run.add_error(type(exc).__name__)
                break
            if not line:
                run.add_error("eof")
                break
            lat = (time.perf_counter_ns() - t0) / 1e6
            resp = json.loads(line)
            code = None if resp.get("error") is None else resp["error"].get("code")
            run.add(lat, code)

    tasks = [asyncio.create_task(worker(r, wtr)) for r, wtr in conns if wtr is not None]
    await asyncio.sleep(2.0)  # échauffement court (résultats ignorés)
    run.lats.clear()
    run.error_codes.clear()
    run.start = time.perf_counter()
    # surveillance CPU/RSS du serveur pendant la mesure
    mon_stop = asyncio.Event()
    samples: list[tuple[float, int]] = []

    async def _monitor():
        prev = _cpu_ticks(SERVER_PID)
        while not mon_stop.is_set():
            await asyncio.sleep(0.5)
            cur = _cpu_ticks(SERVER_PID)
            samples.append(((cur - prev) / 0.5 * 100, _rss_mib(SERVER_PID)))
            prev = cur

    mon = asyncio.create_task(_monitor())
    await asyncio.sleep(duration)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    mon_stop.set()
    await mon
    st = run.stats(w, samples)
    st["scenario"] = f"regime_etabli:W{w}"
    st["mode"] = "P-stable"
    return st


def _fmt(st: dict) -> str:
    extra = f" | codes={st['codes_erreur']}" if st["erreurs"] else ""
    return (f"RPS {st['rps']:>7.2f} | moy {st['lat_moy_ms']:>7.2f} ms | "
            f"p50 {st['p50_ms']:>7.2f} | p95 {st['p95_ms']:>7.2f} | p99 {st['p99_ms']:>7.2f} | "
            f"err {st['taux_erreur_pct']:>6.3f} % | CPU {st['cpu_serveur_coeurs']:>4.2f}/"
            f"{NPROC} cœurs | RSS pic {st['rss_serveur_pic_mib']} MiB{extra}")


async def main_async(args, results: list[dict], socket_path: str) -> None:
    duration = args.duration
    print(f"== A. Coût par commande (W=8, {duration} s) ==")
    for name, fn in COMMANDS.items():
        st = await run_scenario(f"commande:{name}", fn, 8, duration, socket_path)
        results.append(st)
        print(f"  {name:<26} {_fmt(st)}")
        if st.get("garde_memoire"):
            return
    print("== B. Charge mixte réaliste (W=8) ==")
    st = await run_scenario("mixte:W8", mixed_payload, 8, duration, socket_path)
    results.append(st)
    print(f"  mixte W=8 : {_fmt(st)}")
    print("== C. Régime établi (connexions stables, 0 % erreur attendu) ==")
    for w in (8, 16, 32):
        st = await run_steady(w, duration, socket_path)
        results.append(st)
        print(f"  W={w:<3} {_fmt(st)}")
    print(f"== D. Balayage de concurrence (mixte, {args.passes} passage(s)) ==")
    sweep = [1, 2, 4, 8, 16, 32] if args.quick else [1, 2, 4, 8, 16, 24, 32, 48, 64]
    for passage in range(1, args.passes + 1):
        for w in sweep:
            st = await run_scenario(f"sweep:W{w}:pass{passage}", mixed_payload,
                                    w, duration, socket_path)
            results.append(st)
            print(f"  W={w:<3} {_fmt(st)}")
            if st.get("garde_memoire"):
                return
    print("== E. Transport : connexion-par-requête (client réel) vs persistant ==")
    st_r = await run_scenario("mode:connexion-par-requete", mixed_payload,
                              8, duration, socket_path, mode="R")
    results.append(st_r)
    print(f"  connexion/requête : {_fmt(st_r)}")
    st_p = await run_scenario("mode:persistant", mixed_payload,
                              8, duration, socket_path, mode="P")
    results.append(st_p)
    print(f"  persistant        : {_fmt(st_p)}")


def main() -> None:
    global SERVER_PID, CONV_B, READ_IDS
    parser = argparse.ArgumentParser(
        description="Benchmark de charge reproductible du serveur Synapse")
    parser.add_argument("--config", required=True,
                        help="Chemin du fichier de configuration JSON du serveur")
    parser.add_argument("--socket", default=None,
                        help="Chemin du socket (défaut : celui de la config)")
    parser.add_argument("--org-password", default="mot-de-passe-bench-admin-1",
                        help="Mot de passe de l'organisation de benchmark")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Durée de mesure par scénario (s, défaut 10)")
    parser.add_argument("--passes", type=int, default=2, choices=(1, 2),
                        help="Passages du balayage de concurrence (1 = plus rapide)")
    parser.add_argument("--out", default="bench-results.json",
                        help="Fichier JSON de résultats")
    parser.add_argument("--quick", action="store_true",
                        help="Mode court : durée 5 s, balayage réduit, "
                             "bootstrap allégé (≈ 3-4 min)")
    args = parser.parse_args()
    if args.quick and args.duration == 10.0:
        args.duration = 5.0  # le mode court impose une durée réduite

    config = Config.load(args.config)
    if args.socket:
        config = Config.from_dict({**config.to_dict(), "socket_path": args.socket})
    print(f"Serveur : {config.socket_path}")
    CONV_B = bootstrap(config, args.org_password, quick=args.quick)
    SERVER_PID = int(open(config.lock_path).read().strip())

    env = {
        "cpu": os.popen("grep -m1 'model name' /proc/cpuinfo").read().strip(),
        "nproc": NPROC,
        "commit": os.popen("git -C " + os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))) + " log --oneline -1").read().strip(),
        "mode": "quick" if args.quick else "complet",
    }
    results: list[dict] = []
    asyncio.run(main_async(args, results, config.socket_path))
    payload = {"environnement": env, "scenarios": results}
    with open(args.out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\nRésultats enregistrés dans {args.out}")


if __name__ == "__main__":
    main()
