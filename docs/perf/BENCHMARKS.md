# Benchmark de charge Synapse

Harnais de mesure **réelle** de la capacité du serveur : RPS, latences
(min/moyenne/max/p50/p95/p99), taux d'erreur, consommation CPU/RSS du
serveur pendant la charge, point de saturation et goulot.

Le harnais est auto-suffisant : il crée l'organisation et les comptes de
benchmark s'ils n'existent pas, peuple les conversations (idempotent — les
`client_message_id` sont fixes, un relancement ne duplique rien), puis
exécute les scénarios. Il mesure avec le hacheur Argon2id de **production**
(coût réel : ~255 ms par requête sur la machine de référence).

## Prérequis

- Un serveur Synapse **arrêté** sur la configuration cible (le harnais
  crée l'organisation via `create_organization` ; le serveur peut être
  démarré ensuite, le harnais lit le PID dans `service.lock`).
- `python benchmarks/bench.py --config <config.json>` depuis la racine du
  dépôt.

## Utilisation

```bash
# Créer une configuration de benchmark isolée (stockage/socket/logs temporaires)
mkdir -p /tmp/synapse-bench/var /tmp/synapse-bench/logs
cat > /tmp/synapse-bench/config.json <<EOF
{
  "storage_dir": "/tmp/synapse-bench/var",
  "socket_path": "/tmp/synapse-bench/synapse.sock",
  "log_dir": "/tmp/synapse-bench/logs",
  "backup_dir": "/tmp/synapse-bench/backups"
}
EOF

# Démarrer le serveur (autre terminal) :
#   .venv/bin/synapse-server --config /tmp/synapse-bench/config.json

# Lancer le benchmark complet (≈ 7-8 min avec les réglages par défaut ;
#   ~12 min avec durée 15 s et 2 passages de balayage) :
python benchmarks/bench.py --config /tmp/synapse-bench/config.json --out bench-results.json

# Variantes rapides :
python benchmarks/bench.py --config /tmp/synapse-bench/config.json --passes 1   # ≈ 4-5 min
python benchmarks/bench.py --config /tmp/synapse-bench/config.json --quick       # ≈ 3-4 min
python benchmarks/bench.py --config /tmp/synapse-bench/config.json --duration 5 --passes 1
```

Le serveur doit tourner pendant la mesure (le harnais n'a pas besoin d'y
être connecté pour le bootstrap : il passe par le socket). Si le socket est
absent au bootstrap, démarrez le serveur avant le harnais.

## Scénarios mesurés

- **A. Coût par commande** (W=8) : les 8 commandes représentatives
  (`help`, `get_agent_description`, `get_messages`, `get_conversation`,
  `get_notifications`, `read_message`, `send_message`,
  `mark_conversation_no_reply`).
- **B. Charge mixte réaliste** (W=8) : pondération 30 % lectures de
  messages, 20 % envois, 15 % conversations, 15 % notifications,
  10 % lectures individuelles, 10 % documentation/annuaire.
- **C. Régime établi** (W=8/16/32, 30 s) : connexions créées une fois,
  drainage, mesure sans churn — isole du plafond anti-DoS de 64 connexions.
- **D. Balayage de concurrence** (W=1→64, 2 passages) : recherche du point
  de saturation.
- **E. Transport** : connexion-par-requête (comportement du client réel)
  vs connexions persistantes.

Chaque scénario : échauffement (résultats ignorés) puis mesure ; latences
en nanosecondes (percentiles exacts) ; surveillance CPU (proc/pid/stat) et
RSS du serveur toutes les 0,5 s pendant la charge ; garde mémoire
(2,5 GiB) interrompant un scénario si le RSS menace la machine hôte.

## Résultats de référence (mesurés le 4 août 2026)

Environnement : HP ProBook 640 G2, Intel Core i5-6300U @ 2,40 GHz
(2 cœurs / 4 threads), 7,6 GiB RAM, Zorin OS 18.1, ext4, commit 31b8547.

| Scénario | RPS | moy ms | p50 | p95 | p99 | erreurs | CPU (cœurs) |
|---|---|---|---|---|---|---|---|
| Requête isolée (W=1) | 2,4-3,0 | 262-317 | 254-313 | 320-414 | 334-442 | 0 % | 0,96 |
| Mixte W=8 (régime établi) | 7,2-9,2 | 866-1120 | 870-1020 | 990-1890 | 1020-2180 | 0 % | 3,2-3,8 |
| Mixte W=16 (régime établi) | 7,9-8,7 | 1164-1268 | 1143-1236 | 1726-1903 | 2067-2380 | 0 % | ~3,5 |
| Mixte W=32 (régime établi) | 7,6-8,1 | 1729-2081 | 1684-1860 | 2579-3473 | 3172-3821 | 0 % | ~3,6 |
| Balayage W=64 (churn) | 5,6-6,6 | ~3500 | ~3600 | ~4600-5200 | ~4500-5700 | 20-30 %* | ~3,4 |

\* En régime transitoire uniquement : les refus (ConnectionResetError) sont
le plafond anti-DoS de 64 connexions simultanées (timeout d'acquisition
2 s), déclenchés par le churn du harnais entre scénarios. En régime établi
avec ≤ 32 connexions : **0 % d'erreur**.

Constat principal : le débit plafonne à ~7-9 req/s dès W≈8 (CPU saturé
3,2-3,8 cœurs sur 4). La cause est la vérification Argon2id
(64 MiB, 3 itérations) exécutée sur **chaque** requête (spec §3 :
authentification par commande, sans session) — mesurée à ~255 ms/requête.
Capacité ≈ `cœurs / 0,255 s` req/s. Secondaires : 64 MiB de mémoire par
requête concurrente (RSS ≈ 2 GiB à W=48) et sérialisation des écritures
SQLite (`send_message` ≈ 20-30 % plus lent que les lectures).

Variance 5,6-9,2 RPS à W=8 selon les passages : throttling thermique du
CPU portable sous charge soutenue — les chiffres sont une fourchette, pas
un pic ponctuel.
