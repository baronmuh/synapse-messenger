# Opérations — runbook de production

Ce runbook couvre l'installation réalisée par `install.sh` (SPEC_PRODUCTION) :
trois services supervisés par systemd, sauvegarde automatisée, supervision
passive et cycle de mise à jour.

## Transport local (multi-plateforme, 2026-08-08)

Le transport de l'API est une abstraction (`synapse/transport.py`) :
socket Unix par défaut sur POSIX (inchangé), **TCP loopback
(127.0.0.1 uniquement) + jeton par exécution** (`<run_dir>/transport.token`,
0600, vérifié en temps constant) sur Windows. Config : `transport`
("unix"/"tcp", défaut = auto selon l'OS), `transport_port` (défaut 7910),
`run_dir` (défaut : parent du socket en unix, chemin plateforme en tcp).
Les chemins par défaut sont plateforme-dépendants (`synapse/platform.py`) :
Linux `/var|/etc` (inchangé), macOS `~/.synapse`, Windows
`%LOCALAPPDATA%\Synapse`. Les primitives process sont portables
(`process_alive` par handle sur Windows, stop gracieux SIGBREAK,
spawn CREATE_NEW_PROCESS_GROUP). La suite complète (980 tests) tourne sur
Linux ; le chemin TCP est couvert par `tests/test_transport_tcp.py` et la
CI matrix ubuntu/macos/windows (`.github/workflows/ci-smoke.yml`) une fois
les workflows poussés.

## Cycle de vie des services (systemd)

En production, les trois services sont gérés par systemd — **on les arrête
via `systemctl`, jamais via le CLI** (un `synapse web stop` manuel tuerait
le daemon que systemd relancerait aussitôt par `Restart=on-failure`).

```bash
sudo systemctl status synapse synapse-web synapse-a2a@<agent>
sudo systemctl start synapse              # serveur socket Unix
sudo systemctl start synapse-web          # interface web (127.0.0.1:8080)
sudo systemctl restart synapse            # redémarrage propre (SIGTERM → arrêt propre)
```

- `synapse.service` : serveur principal (socket Unix). `Restart=on-failure`,
  `StartLimitIntervalSec=600` + `StartLimitBurst=5` (5 échecs en 10 min →
  unité en `failed`, pas de boucle infinie).
- `synapse-web.service` : interface web, `Requires=synapse.service`.
- `synapse-a2a@<agent>.service` : passerelle A2A, **une instance par agent
  exposé**. L'unité ne tourne que si les secrets de l'agent existent
  (`ConditionPathExists`) — provisionner puis activer :

```bash
sudo install -d -o root -g root -m 0700 /etc/synapse/secrets
printf '%s\n' 'MOT_DE_PASSE_AGENT' | sudo tee /etc/synapse/secrets/a2a-<agent>.password >/dev/null
printf '%s\n' "$(openssl rand -hex 32)" | sudo tee /etc/synapse/secrets/a2a-<agent>.token >/dev/null
sudo chmod 0600 /etc/synapse/secrets/a2a-<agent>.*
sudo systemctl enable --now synapse-a2a@<agent>.service
```

Les secrets vivent en `/etc/synapse/secrets/` (0700 root, fichiers 0600) et
ne sont **jamais** passés en argument ni en environnement : le wrapper
`/opt/synapse/bin/synapse-a2a-systemd` les lit et les transmet au CLI sur
stdin.

## Première organisation

```bash
sudo -u synapse /opt/synapse/venv/bin/synapse-init-org
```

- Refuse de s'exécuter si un compte existe déjà.
- Mot de passe demandé sur stdin (`getpass`), jamais en argument ou en
  variable d'environnement.

## Sauvegarde (automatisée)

- **Quotidien à 02:00** (`synapse-backup.timer`, `Persistent=true` : un run
  manqué — machine éteinte — est rattrapé au prochain démarrage).
- Commande : `synapse backup create --dir /var/backups/synapse`, suivie de
  `synapse backup prune --keep 14` (rétention : 14 archives maximum).
- Fichier `.synbk` : copie cohérente de la base + clé de signature des
  curseurs, chiffrée AES-256-GCM.
- **Preuve de restauration hebdomadaire** (dimanche 03:00,
  `synapse-backup-verify.timer`) : `synapse backup verify --latest`
  déchiffre et valide l'archive dans un stockage isolé — la production
  n'est jamais modifiée. Le résultat est visible dans journald et dans
  `monitor.json`.

Manuel :

```bash
sudo -u synapse /opt/synapse/venv/bin/synapse-backup
# ou via le CLI :
sudo -u synapse /opt/synapse/venv/bin/synapse backup create
sudo -u synapse /opt/synapse/venv/bin/synapse backup verify --latest
sudo -u synapse /opt/synapse/venv/bin/synapse backup list
```

### Clé de chiffrement (`backup.key`)

- La clé vit dans `<storage_dir>/backup.key` (0600), **hors** des
  sauvegardes.
- `install.sh` en place une copie de secours dans
  `/etc/synapse/backup.key.vault` (0640 root:synapse — root écrit, le
  compte `synapse` lit pour que le moniteur en vérifie l'empreinte).
  Si l'installation a précédé la première sauvegarde, créer la copie après
  le premier run :

```bash
sudo install -m 0640 -o root -g synapse /var/lib/synapse/backup.key /etc/synapse/backup.key.vault
```

- **Conservez une copie de cette clé dans un coffre séparé** : sans elle,
  aucune sauvegarde ne peut être restaurée. Le moniteur vérifie chaque
  5 minutes que la copie existe et que son empreinte sha256 correspond.

## Restauration

```bash
sudo systemctl stop synapse
sudo -u synapse /opt/synapse/venv/bin/synapse-restore /srv/backups/synapse-2026-08-04.synbk --force
sudo systemctl start synapse
```

- Refuse si le service tourne (le verrou est acquis pendant toute la
  restauration).
- Vérifie l'authentification du chiffré, l'intégrité SQLite, puis remplace
  la base atomiquement. Identifiants, dates et statuts ne sont **jamais**
  régénérés.
- `--force` est requis : l'opération écrase l'état courant.

## Journaux

- `synapse.log` : une ligne JSON par requête — `username`, `command`,
  `target_id`, `timestamp`, `result`, `process_id` (+ `exception_type` en
  cas d'erreur interne). Jamais de mot de passe ni de contenu.
- `web.log` / `a2a.log` : journaux propres à chaque service ;
  `synapse.error.log` / `web.error.log` / `a2a.error.log` : erreurs internes.
- Rotation quotidienne à minuit, rétention 90 jours puis suppression
  automatique.
- Sous systemd, les sorties `--foreground` sont aussi capturées par
  journald : `journalctl -u synapse -u synapse-web -u synapse-a2a@<agent>`.

Exemples d'exploitation :

```bash
# erreurs des dernières 24 h
grep '"result":"AUTH_FAILED"' /var/log/synapse/synapse.log | tail -50
# activité d'un agent
grep '"username":"agent_a"' /var/log/synapse/synapse.log | tail -20
```

## Supervision passive

- **Watchdogs systemd** : chaque daemon émet `WATCHDOG=1` toutes les 10 s
  (`WatchdogSec=30` dans les unités). Un **gel** (processus vivant mais
  bloqué) est détecté : systemd tue et redémarre le service. Hors systemd,
  le mécanisme est inerte (aucun effet).
- **Moniteur périodique** (`synapse-monitor.timer`, toutes les 5 min,
  utilisateur `synapse`) : état des trois services, âge de la dernière
  sauvegarde (< 26 h), fraîcheur de la base (dernier événement), espace
  disque (seuil 90 %), rafales d'erreurs (`AUTH_FAILED` et
  `exception_type` sur 15 min), empreinte de `backup.key.vault`.
- Sortie : `/var/lib/synapse/monitor.json` (0600) + code de sortie ≠ 0 en
  anomalie (→ journald). En cas d'anomalie, `alert_command` (clé
  `alert_command` de `/etc/synapse/config.json` ou variable
  `SYNAPSE_ALERT_COMMAND`) reçoit le rapport JSON sur stdin — extensible
  (mail, webhook, agent).
- Seuils configurables par environnement :
  `SYNAPSE_MONITOR_BACKUP_MAX_AGE_HOURS` (26),
  `SYNAPSE_MONITOR_DISK_WARN_PERCENT` (90),
  `SYNAPSE_MONITOR_ERROR_WINDOW_SECONDS` (900),
  `SYNAPSE_MONITOR_MAX_AUTH_FAILURES` (30),
  `SYNAPSE_MONITOR_MAX_EXCEPTIONS` (1),
  `SYNAPSE_MONITOR_KEY_VAULT` (`/etc/synapse/backup.key.vault`).

Exécution manuelle :

```bash
sudo -u synapse /opt/synapse/venv/bin/python /opt/synapse/scripts/synapse-monitor.py --config /etc/synapse/config.json
echo $?   # 0 = tout va bien ; 1 = anomalie(s) — voir monitor.json
```

## Interface web et passerelle A2A

Les deux interfaces écoutent sur `127.0.0.1` :

- **Web** (`synapse-web.service`, port 8080) : connexion par sélection
  d'organisation, sessions cookie, jeton de confiance local `web_token`
  (0600, run dir) écrit par le serveur — aucune saisie de secret à
  l'installation.
- **Passerelle A2A** (`synapse-a2a@<agent>.service`, port 8090) : agent-card.json
  + JSON-RPC `tasks/*`, jeton d'accès obligatoire (`X-Synapse-Token`), fourni
  par le fichier secret `a2a-<agent>.token`.

```bash
# état des services (une vue globale)
sudo -u synapse /opt/synapse/venv/bin/synapse status --json
```

## Mise à jour

```bash
sudo -u synapse /opt/synapse/venv/bin/synapse update check
sudo -u synapse /opt/synapse/venv/bin/synapse update apply --dry-run
sudo -u synapse /opt/synapse/venv/bin/synapse update apply
```

`update apply` exécute : sauvegarde automatique → arrêt du web → arrêt de
la passerelle A2A (si active) → arrêt du serveur → commande de mise à jour
(`update_command` de la configuration ou `SYNAPSE_UPDATE_COMMAND`) →
redémarrages. Sous systemd, les arrêts/redémarrages passent par
`systemctl` (un arrêt via le CLI serait contré par `Restart=on-failure`).
La variable `SYNAPSE_NO_SYSTEMD=1` force le mode CLI (sans `systemctl`) —
à n'utiliser que dans des environnements de test ou de développement.
En cas d'échec à mi-chemin, l'état précédent est restaurable via
`synapse backup restore <archive> --force`.

Le cycle de release (version, changelog, tag, lock de dépendances) est
décrit dans [`docs/production/RELEASE.md`](docs/production/RELEASE.md).

## CI locale

- **Hook pre-push** (installé par `scripts/install-git-hooks.sh`) : la suite
  complète (~16 min) bloque tout push non vert — c'est le gate de la branche
  main.
- **Timer nocturne** (`synapse-ci.timer`, 03:30, `Persistent=true`) : filet
  de détection des régressions même sans push ; sortie vers journald.

## Capacité et performances

Synapse authentifie **chaque commande** par une vérification Argon2id
complète (64 MiB, 3 itérations, parallélisme 1 — spec §3, pas de session).
Mesuré : ~255 ms par requête sur un i5-6300U, CPU-bound. La capacité réelle
est donc approximativement `nombre de cœurs CPU / 0,255 s` requêtes par
seconde :

- débit soutenu typique sur 4 threads : **7-9 req/s** (latence p50
  ~0,9-1,0 s avec 8 connexions parallèles) ;
- latence d'une requête isolée : **~260 ms** (plancher Argon2id) ;
- plafond de 64 connexions simultanées (anti-DoS, timeout d'acquisition
  2 s) ; mémoire 64 MiB par requête concurrente — l'unité systemd borne le
  serveur à 6 Go (`MemoryMax`, `OOMScoreAdjust=500`) : en tension mémoire,
  c'est le service qui est relancé, pas la machine qui OOM.

Pour mesurer sur votre matériel : `python benchmarks/bench.py --config
<config.json>` (voir `docs/perf/BENCHMARKS.md`).

## Dépannage

| Symptôme | Cause probable | Action |
|---|---|---|
| `systemctl status synapse` → unité en `failed` après 5 échecs | serveur impossible à démarrer (config, stockage) | `journalctl -u synapse -e` ; corriger puis `systemctl reset-failed synapse && systemctl start synapse` |
| « un autre service utilise déjà » | verrou actif (second serveur) | identifier et arrêter le processus concerné |
| « verrou » persistant après crash | verrou laissé par un SIGKILL | retiré automatiquement si le PID est mort ; sinon suppression manuelle après vérification |
| redémarrage silencieux du web | `Restart=on-failure` (crash ou OOM-kill) | `journalctl -u synapse-web -e` ; `MemoryMax=512M` atteint ? |
| restauration refusée | service en cours d'exécution | `sudo systemctl stop synapse` puis réessayer |
| « clé incorrecte » au restore | `backup.key` absent ou différent de celui de la sauvegarde | restaurer d'abord la copie de `backup.key.vault` conservée dans le coffre |
| moniteur : « copie de secours de la clé absente » | `backup.key.vault` non créé | créer la copie (voir « Clé de chiffrement ») |
| moniteur : « sauvegarde trop ancienne » | run manqué (machine éteinte à 02:00) | `Persistent=true` rattrape au boot ; sinon relancer `synapse backup create` |
| « Trop de tentatives échouées » | limitation 5 échecs / 15 min | attendre la fin de la fenêtre (une authentification réussie réinitialise) |
| service tué (OOM) en pleine charge | `MemoryMax` atteint | dimensionner (64 MiB × connexions) ; `OOMScoreAdjust=500` limite l'impact |
