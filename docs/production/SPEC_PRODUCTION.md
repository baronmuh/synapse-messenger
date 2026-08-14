# Synapse — Spécification de mise en production (SPEC_PRODUCTION)

- **Statut** : **VALIDÉ et IMPLÉMENTÉ** (2026-08-07) — les 8 points sont
  implémentés et vérifiés (voir §10.4 Journal de mise en œuvre). Le
  présent document reste le contrat de référence ; toute évolution future
  doit le mettre à jour.
- **Version du document** : 1.0 (implémentation conforme).
- **Date** : 2026-08-07.
- **Périmètre** : les 8 résidus identifiés lors du verdict de readiness
  production (session du 2026-08-07), chacun tranché par une décision
  ferme. Ce document est le contrat d'implémentation : après validation,
  chaque point sera implémenté séparément, dans l'ordre du §10, et vérifié
  par les preuves exigées (tests ciblés, exécution réelle, captures).
- **Documents de référence** : `SPEC.txt`, `SPEC_WEB.txt`,
  `SPEC_CLI.md`, `SPEC_CLI_ECARTS.md`, `docs/production/OPERATIONS.md`,
  `docs/securite/SECURITY.md`, `docs/perf/PERFORMANCE.md`, `install.sh`, `pyproject.toml`.

---

## 0. Contexte et méthode

Huit résidus ont été identifiés entre l'état actuel du dépôt et un état
exploitable en production :

1. `synapse-web` et `synapse-a2a-bridge` ne sont pas supervisés par
   systemd (pas d'unité, pas de redémarrage auto, pas de démarrage au boot).
2. Aucune CI (pas de remote git, la suite de ~904 tests / ~16 min n'est
   jamais exécutée automatiquement).
3. Sauvegarde non automatisée, sans rétention, sans preuve de
   restauration, copie de `backup.key` non vérifiée.
4. Aucune supervision passive ni alerting (gel non détecté, pas de
   métriques, pas d'alerte).
5. Cycle de release caduc (tag v3.0.0 à 42 commits de HEAD, pas de
   changelog, pas de `--version`, `__version__` périmé).
6. Durcissement systemd incomplet (pas de bornes mémoire, pas de
   restrictions syscalls/capabilités, répertoires créés à la main).
7. Dépendances non épinglées (résolution à l'installation, builds non
   reproductibles).
8. Dettes de documentation (README et OPERATIONS décrivent un état
   obsolète du projet).

Règles de méthode :

- Chaque point est tranché par UNE décision, sans alternative laissée en
  suspens.
- La solution retenue privilégie la robustesse en production, pas la
  facilité d'implémentation.
- Aucune solution ne modifie l'API serveur v2 (`SPEC.txt`) : sur-ensemble
  strict, compatibilité totale.
- Toute nouvelle fonctionnalité (prune, verify, moniteur, sd_notify…) est
  accompagnée de tests ciblés et de mise à jour de la documentation
  correspondante.
- Les valeurs chiffrées (ports, seuils, durées, chemins) sont des
  décisions figées dans ce document, sauf mention contraire explicite.

---

## 1. Supervision systemd de l'interface web et de la passerelle A2A

### 1.1 Problème identifié

`install.sh` ne crée qu'une seule unité systemd, `synapse.service`
(serveur socket Unix). L'interface web (`synapse-web`) et la passerelle
A2A (`synapse-a2a-bridge`) doivent être lancées à la main, avec
`--token-stdin` (modèle interactif), sans redémarrage automatique, sans
démarrage au boot : après un redémarrage de la machine, le serveur
tourne mais la supervision web et le pont A2A sont morts silencieusement.

Vérification faite dans le code : le CLI unifié sait déjà gérer ces deux
services (`synapse web start|stop|status|logs`, `synapse a2a
start|stop|status`, fichiers PID, double vérification « PID vivant ET
HTTP répond »), mais en mode **détaché** : `synapse web start` lance un
enfant via `subprocess.Popen(start_new_session=True)` puis se termine.
C'est le piège classique du double-fork vis-à-vis de systemd : une unité
`Type=simple` qui appellerait ce mode serait déclarée « démarrée » puis
« morte » alors que le vrai daemon tourne en orphelin — `Restart=
on-failure` ne verrait jamais sa mort et `systemctl stop` ne tuerait
rien.

### 1.2 Solutions envisageables

- **A. Unités appelant les binaires historiques** (`synapse-web
  --token-stdin --observer-name …`, modèle décrit par OPERATIONS.md
  §82-108).
  - Avantages : rien à créer, conforme à la doc existante.
  - Inconvénients : modèle obsolète — le web utilise désormais le jeton
    local `web_token` (0600, run dir) écrit par le serveur, plus aucune
    saisie de jeton à la main ; `--token-stdin` est interactif donc
    ingérable en systemd proprement ; duplication de la logique du CLI
    (PID files, double vérification) au lieu de la réutiliser.
  - Verdict : rejeté.

- **B. Unités appelant le CLI en mode détaché** (sans `--foreground`).
  - Avantages : réutilise la commande existante telle quelle.
  - Inconvénients : systemd ne suit pas l'enfant détaché —
    `Restart=on-failure` inopérant, arrêt impossible proprement,
    démarrage au boot non garanti. Piège du double-fork documenté.
  - Verdict : rejeté.

- **C. Unités `Type=simple` appelant le CLI en mode premier plan**
  (`--foreground`).
  - Avantages : le mode `--foreground` existe déjà et est exactement le
    contrat systemd — le processus reste au premier plan de SA propre
    exécution, installe SIGTERM → arrêt propre (`stop_event`), écrit et
    retire son fichier PID (`finally`). systemd obtient : démarrage au
    boot, redémarrage sur échec, arrêt par signal, logs capturés par
    journald. Zéro changement de code serveur.
  - Inconvénients : nécessite de provisionner les secrets du pont A2A
    (mot de passe agent + jeton) par fichier → stdin.
  - Verdict : retenu.

### 1.3 Solution retenue

Les unités systemd vivent comme **templates dans `scripts/systemd/`**
(source unique, testée par `systemd-analyze verify`) et sont déployées par
`install.sh` vers `/etc/systemd/system/` (substitution des chemins) :

- **`synapse-web.service`** (`Type=simple`) :

      [Unit]
      Description=Synapse web supervision interface
      After=synapse.service
      Requires=synapse.service

      [Service]
      Type=simple
      User=synapse
      Group=synapse
      ExecStart=/opt/synapse/venv/bin/synapse web start --foreground --port 8080 --config /etc/synapse/config.json
      Restart=on-failure
      RestartSec=2
      StartLimitIntervalSec=600
      StartLimitBurst=5
      RuntimeDirectory=synapse
      RuntimeDirectoryMode=0700
      UMask=0077
      (+ bloc durci complet, voir §6)

      [Install]
      WantedBy=multi-user.target

- **`synapse-a2a@.service`** — unité **template** (l'agent exposé est
  `%i`), la plus évolutive pour le même coût : un pont par agent, par
  symétrie avec le CLI. Un wrapper est installé en
  `/opt/synapse/bin/synapse-a2a-systemd` :

      #!/usr/bin/env bash
      set -euo pipefail
      AGENT="$1"; PORT="$2"
      cat "/etc/synapse/secrets/a2a-${AGENT}.password" \
          "/etc/synapse/secrets/a2a-${AGENT}.token" \
      | exec /opt/synapse/venv/bin/synapse a2a start --foreground \
          --agent-name "$AGENT" --port "$PORT" \
          --config /etc/synapse/config.json \
          --password-stdin --token-stdin

  L'unité porte `ConditionPathExists=/etc/synapse/secrets/a2a-%i.password`
  : le pont ne tourne que si l'administrateur a provisionné l'agent.

- **Secrets** : `/etc/synapse/secrets/` (0700 root), fichiers 0600 root,
  créés par `install.sh` (génération `openssl rand -hex 32` pour le
  jeton, saisie du mot de passe d'agent sur stdin). Ils sont lus par le
  wrapper et passés **sur stdin uniquement** — jamais en argument de
  commande, jamais en environnement (règle du projet).

- **Activation par défaut** : `server` + `web` activées ; le bridge se
  déclenche par la seule présence du secret d'agent.

### 1.4 Justification du choix

La solution C réutilise 100 % de la logique existante et testée (PID
files, double vérification, arrêt propre) au lieu d'en recréer une ;
elle ne change aucun code serveur pour le web ; le CLI et systemd
restent cohérents (mêmes PID files : `synapse web status` fonctionne
toujours ; un arrêt via `systemctl` retire le PID file via le `finally`
du daemon — pas de désynchronisation d'état). Le template A2A rend
l'ajout d'un agent exposé trivial (une instance + 2 fichiers secrets)
sans toucher au code.

### 1.5 Impacts sur l'architecture existante

- OPERATIONS.md : le cycle de vie de production des trois services passe
  par systemd ; **en production, on arrête via `systemctl`, pas via le
  CLI** (un `synapse web stop` manuel tuerait le daemon que systemd
  relancerait aussitôt).
- `synapse update apply` (§5) : son plan actuel arrête le web via le CLI —
  sous systemd, systemd le relancerait immédiatement et le redémarrage
  du CLI échouerait (« déjà en cours d'exécution »). `update apply`
  doit donc détecter la supervision systemd et piloter `systemctl`
  quand l'unité existe ; comportement CLI actuel conservé sans systemd.
- Le bridge A2A, aujourd'hui ignoré par le plan de mise à jour, est
  ajouté au stop/start du plan (sinon il reste en vie contre un serveur
  arrêté).

### 1.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `install.sh` | déploie les templates `scripts/systemd/` (server, web, a2a@, backup, moniteur, CI), crée `/etc/synapse/secrets/` (0700 root), installe `/opt/synapse/bin/synapse-a2a-systemd` |
| `scripts/synapse-a2a-systemd` (nouveau) | wrapper secrets → stdin → CLI `--foreground` |
| `synapse/cli/update.py` | détection systemd (`systemctl -q is-active …`) + plan incluant le bridge |
| `docs/production/OPERATIONS.md` | cycle de vie systemd des 3 services, secrets A2A |
| `tests/test_update_apply.py` | banc étendu : mode systemd + mode CLI, bridge inclus |

### 1.7 Points d'attention et risques

- **Boucle de redémarrage** si le serveur est absent : bornée par
  `StartLimitIntervalSec=600` + `StartLimitBurst=5` (l'unité passe en
  `failed`, pas de boucle infinie).
- **Double instance** : protégée par le fichier PID (le CLI refuse de
  lancer si le PID est vivant).
- **Ordre de démarrage** : `After=` + `Requires=` ne garantissent pas la
  disponibilité du socket ; le CLI fait déjà le contrôle (`_require_server`,
  code 3) et `Restart=on-failure` couvre le cas « serveur pas encore prêt ».
- **Secrets** : le wrapper ne doit jamais journaliser les secrets (cat
  direct, pas d'echo) ; le répertoire reste 0700 root.

---

## 2. CI (intégration continue)

### 2.1 Problème identifié

Aucun pipeline : pas de `.github`, pas de remote git (`git remote -v`
vide), la suite complète (~904 tests, ~16 min) n'est exécutée que si
quelqu'un la lance à la main. Sur une branche main unique, toute
régression est silencieuse jusqu'au prochain run manuel.

### 2.2 Solutions envisageables

- **A. GitHub Actions (ou autre CI SaaS)**.
  - Avantages : standard du marché, exécution sur chaque commit.
  - Inconvénients : pas de remote aujourd'hui ; pousser une
    infrastructure de messagerie d'agents (contenus, audit) vers un SaaS
    contredit la philosophie du projet (tout local, socket Unix) ; coût
    de mise en place d'un remote + exposition.
  - Verdict : rejeté aujourd'hui ; le portage est documenté (§2.6) pour
    le jour où un remote privé existerait.

- **B. CI locale** : script canonique + hook git + timer systemd.
  - Avantages : aucune dépendance externe, fonctionne immédiatement,
    gate réel sur la main unique, filet de détection sans push.
  - Inconvénients : exécution locale (consomme du CPU de la machine).
  - Verdict : retenu.

- **C. Hermes cron** (écosystème de l'utilisateur).
  - Avantages : notification active possible (ex. Telegram).
  - Inconvénients : le produit doit être auto-suffisant — la CI ne peut
    pas dépendre de l'agent d'un utilisateur particulier.
  - Verdict : complément éventuel, jamais la CI du produit.

### 2.3 Solution retenue

- **`scripts/ci.sh`** — pipeline canonique :
  1. venv dédié réutilisable `~/.cache/synapse-ci/venv` (créé si absent,
     `pip install -e ".[dev]"`) ;
  2. exécution de la suite complète `pytest` depuis la racine du dépôt ;
  3. rapport de couverture (optionnel, `--coverage`) ;
  4. code de sortie honnête (0 = vert).
  La CI travaille **exclusivement** sur des configurations temporaires
  (modèle des benchmarks) : aucun contact avec le stockage, le socket ou
  les logs de production.
- **Hook git pre-push bloquant** installé par `scripts/install-git-hooks.sh`
  (ou par `install.sh`) : `ci.sh` est lancé avant chaque push ; un échec
  bloque le push. C'est le gate de la main unique.
- **Timer systemd nocturne** `synapse-ci.timer` + `synapse-ci.service`
  (`OnCalendar=*-*-* 03:30:00`, `Persistent=true`) : relance `ci.sh` et
  détecte les régressions même sans push ; sortie vers journald.
- **docs/production/RELEASE.md** : avant chaque tag, la certification impose un venv
  **100 % frais** + suite complète (le venv cacheable sert au quotidien,
  le venv frais à la certification).

### 2.4 Justification du choix

Sans remote, une CI SaaS n'est pas réalisable ; la CI locale est la
seule option honnête, et elle couvre les deux besoins : gate immédiat
(pre-push, main unique) et filet de régression sans push (timer
nocturne). Le script canonique rend le portage vers GitHub Actions
trivial le jour où un remote apparaît.

### 2.5 Impacts sur l'architecture existante

- Chaque push devient bloquant (~16 min) : assumé (sécurité/conformité
  > vitesse).
- Le timer nocturne consomme ~16 min de CPU par nuit : assumé,
  documenté.
- La CI ne modifie aucun fichier du dépôt (les daemons de test utilisent
  des stockages temporaires) ; `__pycache__`/`.coverage` restent
  gitignorés.

### 2.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `scripts/ci.sh` (nouveau) | pipeline canonique (venv dédié, suite complète, exit honnête) |
| `scripts/install-git-hooks.sh` (nouveau) | installe le hook pre-push |
| `.git/hooks/pre-push` (généré) | lance `ci.sh` |
| `synapse-ci.service` + `synapse-ci.timer` (générés par install.sh) | run nocturne |
| `docs/production/RELEASE.md` (nouveau, §5) | procédure de release + portage GitHub Actions documenté |

### 2.7 Points d'attention et risques

- **Piège connu du projet** : ne jamais éditer les sources pendant qu'une
  suite tourne (les daemons enfants ré-importent les modules) — `ci.sh`
  doit tourner sans modification concurrente du dépôt.
- **Isolation** : la CI ne doit jamais pointer vers la config de
  production (config temporaire dédiée, comme `benchmarks/`).
- **Machine endormie** : `Persistent=true` rattrape le run manqué au
  prochain réveil/boot.

---

## 3. Sauvegarde automatisée et restauration prouvée

### 3.1 Problème identifié

La sauvegarde existe (chiffrée AES-256-GCM, testée) mais : (a) n'est
jamais installée — OPERATIONS.md donne une cron à copier à la main ; (b)
aucune rétention — les `.synbk` s'accumulent sans purge ; (c) aucune
preuve de restauration — `synapse backup restore` exige serveur arrêté +
`--force` (destructif), donc jamais exécutable en automatique ; (d) la
copie de `backup.key` « dans un coffre » est une consigne, pas une
procédure vérifiée.

### 3.2 Solutions envisageables

- **Automatisation** :
  - cron root (`sudo -u synapse …`) : classique mais mélange root/sudo
    dans cron, pas de rattrapage des runs manqués, pas de capture
    journald ;
  - **timer systemd** (`Persistent=true`) : cohérent avec le reste du
    déploiement, rattrape les runs manqués au boot, journald capture.
    Verdict : retenu.
  - Sauvegarde internalisée au serveur : rejeté — découplage obligatoire
    (le service de sauvegarde doit survivre au service sauvegardé).
- **Rétention** :
  - logrotate : mal adapté (fichiers binaires chiffrés, nommage `.synbk`
    inconnu) ;
  - **option CLI `synapse backup prune --keep N`** : testable
    unitairement, ne touche que les `*.synbk` du `backup_dir`.
    Verdict : retenu.
- **Preuve de restauration** :
  - restore réel périodique : impossible en automatique (destructif,
    serveur arrêté requis) ;
  - **mode vérification isolée `synapse backup verify <archive> --dir
    <scratch>`** : restaure dans un stockage temporaire cloné depuis la
    config, vérifie, détruit le scratch. Verdict : retenu.
- **Clé de sauvegarde** :
  - consigne documentée seule : non vérifiable ;
  - **copie automatique + vérification d'empreinte** : retenu.

### 3.3 Solution retenue

- **`synapse-backup.service`** (`User=synapse`) :
  `ExecStart=/opt/synapse/venv/bin/synapse backup create --dir /var/backups/synapse`
  et `ExecStartPost=…/synapse backup prune --keep 14`.
- **`synapse-backup.timer`** : `OnCalendar=*-*-* 02:00:00`,
  `Persistent=true`.
- **Nouvelle sous-commande `synapse backup prune --keep <N>`** (défaut
  `14`) : supprime les archives les plus anciennes du `backup_dir`
  (chemin validé — jamais ailleurs).
- **Nouvelle sous-commande `synapse backup verify <archive> --dir
  <scratch>`** : déchiffrement (authentification AES-GCM), intégrité
  SQLite (`PRAGMA integrity_check`), comptage des tables, puis
  destruction du scratch. Le scratch est un `storage_dir` temporaire
  dérivé de la config — la production n'est ni lue en écriture ni
  verrouillée.
- **`synapse-backup-verify.timer`** : hebdomadaire (dimanche 03:00) sur
  la sauvegarde la plus récente.
- **Clé** : `install.sh` copie `backup.key` (0600 root) dans
  `/etc/synapse/backup.key.vault` — emplacement hors du `backup_dir`,
  identifié comme copie de secours. Le moniteur (§4) vérifie que la
  copie existe et que son empreinte `sha256` est identique à celle du
  stockage. La sortie « hors machine » (coffre physique) reste humaine ;
  la présence et l'intégrité de la copie deviennent vérifiées
  automatiquement.

### 3.4 Justification du choix

Le timer systemd est le seul mécanisme qui garantit un run quotidien
même après une machine éteinte à 02:00 (`Persistent=true`), sans wrapper
root. La rétention par option CLI est testable et bornée. Le `verify`
sur scratch transforme « on fait des sauvegardes » en « on sait que la
restauration fonctionne » — exigence de preuve réelle du projet — sans
jamais risquer la production.

### 3.5 Impacts sur l'architecture existante

- `synapse backup create` reste la seule écriture ; `prune` et `verify`
  sont des lectures/écritures sûres sur le `backup_dir` et un scratch.
- `synapse status` affiche déjà les 5 dernières sauvegardes — inchangé.
- Le moniteur (§4) intègre l'âge de la sauvegarde (< 26 h) et
  l'empreinte de `backup.key.vault`.

### 3.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `synapse/backup.py` | fonctions `prune` et `verify` (restore scratch) |
| `synapse/cli/backup.py` | sous-commandes `prune`, `verify` |
| `install.sh` | unités/timers backup + backup-verify, copie `backup.key.vault` |
| `synapse-backup.service` + `.timer`, `synapse-backup-verify.service` + `.timer` (générés) | planification |
| `docs/production/OPERATIONS.md` | procédure automatisée, rétention, vérification hebdo |
| `tests/test_backup.py` | tests ciblés prune/verify (scratch) |

### 3.7 Points d'attention et risques

- **`verify` ne doit JAMAIS pointer vers la production** : le `--dir`
  doit être validé (répertoire de scratch dédié, sous `/tmp` ou
  `~/.cache/synapse-verify`), le code refuse un chemin qui contiendrait
  le `storage_dir` de production.
- **Échec de sauvegarde** (ex. disque plein) : exit ≠ 0 → journald +
  alerte du moniteur.
- **Contention** : sauvegarde à 02:00, charge nulle — négligeable.
- **`prune`** : ne supprime que les `*.synbk` du `backup_dir` configuré,
  jamais de chemin arbitraire.

---

## 4. Supervision passive et alerting

### 4.1 Problème identifié

Rien ne surveille le service en continu. `synapse status --json`
agrège déjà serveur/web/organisations/sauvegardes et `synapse diag
doctor` effectue 7 contrôles d'environnement, mais tout est réactif :
aucun signal si le service **gèle** (processus vivant mais bloqué), si
la sauvegarde vieillit, si le disque se remplit ou si les `AUTH_FAILED`
s'emballent.

### 4.2 Solutions envisageables

- **A. Stack Prometheus + exporters**.
  - Avantages : standard du marché, dashboards.
  - Inconvénients : disproportionné pour un service socket local
    mono-machine (dépendance lourde, process supplémentaire, config) ;
    la capacité/perf est déjà couverte par le benchmark.
  - Verdict : rejeté ; porte de sortie documentée (le moniteur émet du
    JSON structuré → un exporter texte Prometheus serait trivial plus
    tard).
- **B. Watchdogs systemd (`sd_notify` + `WatchdogSec`) + moniteur
  périodique autonome**.
  - Avantages : détecte le GEL (que `Restart=on-failure` ne couvre pas —
    il ne couvre que la mort) ; zéro dépendance ; réutilise
    `synapse status --json` et le jeton local.
  - Inconvénients : petit module système dans le produit (inoffensif
    hors systemd) ; l'alerte active dépend d'une commande configurable.
  - Verdict : retenu.

### 4.3 Solution retenue

- **`synapse/systemd_notify.py`** (nouveau, ~30 lignes) : envoi de
  datagrammes vers `$NOTIFY_SOCKET` (`READY=1`, `WATCHDOG=1`,
  `STOPPING=1`), **aucune dépendance**, no-op si `NOTIFY_SOCKET` absent.
- **Thread heartbeat** dans les 3 daemons (`server`, `web`, `a2a`) :
  `sd_notify WATCHDOG=1` toutes les 10 s. `WatchdogSec=30` dans les 3
  unités (marge ×3). Un gel → systemd tue et relance le service.
- **`scripts/synapse-monitor.py`** (nouveau), déclenché par
  `synapse-monitor.timer` toutes les 5 min (`OnCalendar=*:0/5`,
  `User=synapse` — le jeton local 0600 suffit). Contrôles :
  1. état des 3 services via `synapse status --json` (un service
     `degraded` ou `stopped` attendu = anomalie) ;
  2. âge de la dernière sauvegarde : < 26 h ;
  3. fraîcheur de la base : dernier événement de la table `events` de
     `synapse.db` (métrique « une session tourne ») ;
  4. espace disque : `df` sur storage/logs/backups, seuil 90 % ;
  5. rafales d'erreurs dans les logs : `AUTH_FAILED` et
     `exception_type` sur les 15 dernières minutes (seuil configurable) ;
  6. empreinte `sha256` de `backup.key.vault` vs clé du stockage (§3).
  Sortie : `/var/lib/synapse/monitor.json` (état + horodatage) ; code de
  sortie ≠ 0 en anomalie ; si `alert_command` est configuré (config ou
  variable d'environnement), il est exécuté avec un résumé JSON sur
  stdin.
- **`alert_command`** : optionnel, extensible (mail, webhook, agent). Sans
  lui, le minimum livré est la détection + journald + code de sortie.

### 4.4 Justification du choix

Le seul trou réel de supervision est le **gel** — couvert par le
watchdog systemd, le seul mécanisme qui ne dépend pas d'une sortie du
processus. Le moniteur réutilise les briques existantes (status --json,
jeton local, logs JSON) sans nouvelle dépendance. Prometheus est un coût
sans besoin correspondant sur une infra locale mono-machine.

### 4.5 Impacts sur l'architecture existante

- Les 3 daemons embarquent un thread heartbeat (coût négligeable) ;
  `sd_notify` est inactif hors systemd (aucun changement de
  comportement en dev/CI).
- `config.example.json` documente `alert_command` (facultatif).
- OPERATIONS.md : section supervision réécrite (moniteur, watchdog,
  monitor.json).

### 4.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `synapse/systemd_notify.py` (nouveau) | client `$NOTIFY_SOCKET` sans dépendance |
| `synapse/cli/daemon.py` (+ chemins `--foreground` de `web.py`/`a2a.py`) | heartbeat + `READY=1`/`STOPPING=1` |
| `scripts/synapse-monitor.py` (nouveau) | les 6 contrôles, monitor.json, alert_command |
| `synapse-monitor.service` + `.timer` (générés par install.sh) | toutes les 5 min |
| `install.sh` | `WatchdogSec=30` dans les 3 unités |
| `config.example.json` | `alert_command` (facultatif) |
| `docs/production/OPERATIONS.md` | supervision passive, monitor.json, alerte |
| tests ciblés | sd_notify (avec et sans `NOTIFY_SOCKET`), logique du moniteur (checks unitaires) |

### 4.7 Points d'attention et risques

- **Watchdog vs charge légitime** : marge ×3 (heartbeat 10 s,
  WatchdogSec 30 s) ; une requête Argon2id dure ~255 ms, aucune boucle
  ne peut dépasser 30 s.
- **`sd_notify` hors systemd** : testé avec `NOTIFY_SOCKET` absent → no-op
  strict (pas d'exception, pas de log).
- **Alerte non garantie sans `alert_command`** : honnêteté assumée —
  détection + journald livrés par défaut ; l'alerte active est une
  configuration.
- **monitor.json** : écrit par l'utilisateur `synapse` dans `/var/lib/
  synapse` (déjà 0700) — permissions conservées.

---

## 5. Cycle de release

### 5.1 Problème identifié

Le tag `v3.0.0` est à 42 commits de HEAD (tout le CLI unifié, le webui
v3 et les bancs de validation sont sortis après) ; pas de changelog ;
pas de `synapse --version` ; `synapse/__init__.py` traîne un
`__version__ = "1.0.0"` périmé alors que `project_version()` (common.py)
lit `importlib.metadata` (version installée, correct) ; `update check/
apply` existent et sont testés, mais le canal distant est optionnel et
le plan ne couvre pas le bridge (tranché au §1).

### 5.2 Solutions envisageables

- **A. Release légère mais complète** : changelog, version CLI, procédure
  documentée, tag propre.
  - Avantages : cycle réel complet, coût faible, mécanisme `update`
    rendu actionnable.
  - Inconvénients : aucun significatif.
  - Verdict : retenu.
- **B. Release lourde** : wheels signés, canal distant obligatoire,
  canaux stable/beta.
  - Avantages : traçabilité maximale.
  - Inconvénients : sur-ingénierie pour un déploiement local
    mono-machine ; le mécanisme `update` s'activera quand un canal
    existera.
  - Verdict : documenté comme évolution possible, non retenu aujourd'hui.

### 5.3 Solution retenue

- **`CHANGELOG.md`** (format Keep a Changelog) à la racine : historique
  reconstitué depuis v3.0.0 (CLI unifié, webui v3, bancs de validation).
- **`synapse --version`** (et sous-commande `synapse version`) : affiche
  `project_version()` — cohérent avec les versions déjà inscrites dans
  les fichiers PID et affichées par `status`.
- **Suppression de `__version__` dans `synapse/__init__.py`** : source
  unique de vérité = `importlib.metadata`. Un grep préalable vérifie
  qu'aucun import n'en dépend.
- **`docs/production/RELEASE.md`** : procédure en 7 étapes — 1) entrée
  CHANGELOG.md ; 2) bump `version` dans `pyproject.toml` ; 3) venv 100 %
  frais + suite complète (gate) ; 4) `scripts/ci.sh` vert ; 5)
  régénération du lock de dépendances (§7) ; 6) tag annoté (signé si clé
  GPG disponible) ; 7) installation depuis le tag via `install.sh`.
- **Politique de version** : l'API serveur v2 n'a pas changé (seuls les
  outils ont évolué) → prochaine release **3.1.0** ; toute évolution
  d'API serveur → **4.0.0**. Le premier tag 3.1.0 est posé sur HEAD
  APRÈS l'implémentation des §1, §3, §4, §6.
- **`update apply`** : détection systemd (§1) + bridge inclus dans le
  plan ; `update_url` (canal distant) documenté comme activation
  optionnelle — pas obligatoire pour la prod locale.

### 5.4 Justification du choix

Le produit doit pouvoir répondre à « quelle version tourne sur cette
machine ? » et appliquer une mise à jour de façon fiable. La release
légère couvre ces deux besoins sans infrastructure supplémentaire ; le
mécanisme `update` existant (déjà banc-testé) devient actionnable en
prod une fois la supervision systemd en place.

### 5.5 Impacts sur l'architecture existante

- `importlib.metadata` devient la source unique de version (le paquet
  doit être installé — c'est le cas en venv -e comme en install.sh).
- `update apply` change de pilote selon l'environnement (systemd vs CLI)
  : nouveau chemin de test obligatoire.
- La doc du projet (README, OPERATIONS) référence la version courante
  sans la coder en dur.

### 5.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `CHANGELOG.md` (nouveau) | historique depuis v3.0.0 |
| `synapse/cli/main.py` | `--version` + sous-commande `version` |
| `synapse/__init__.py` | suppression de `__version__` (après grep) |
| `docs/production/RELEASE.md` (nouveau) | procédure 7 étapes, politique de version, portage CI |
| `synapse/cli/update.py` | système de détection systemd + plan incluant le bridge |
| `tests/test_update_apply.py` | banc étendu (mode systemd + mode CLI) |
| `docs/specs/SPEC_CLI.md` | commande `version` documentée |

### 5.7 Points d'attention et risques

- **Import externe de `synapse.__version__`** : grep préalable obligatoire ;
  toute dépendance cassée est migrée vers `project_version()`.
- **Tag posé trop tôt** : le tag 3.1.0 ne sera posé qu'une fois les
  §1/§3/§4/§6 implémentés et vérifiés (sinon on retage).
- **`update apply` sous systemd** : le banc doit couvrir les deux modes
  (avec et sans unités) — un oubli ici casserait la mise à jour de prod.

---

## 6. Durcissement systemd

### 6.1 Problème identifié

L'unité `synapse.service` a déjà un bon socle (`NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `UMask 0077`) mais :
pas de bornes mémoire alors que la mémoire est documentée (64 MiB par
requête concurrente, jusqu'à 64 connexions ≈ 4 GiB — un pic peut OOM la
machine AVANT le plafond applicatif) ; pas de restriction de syscalls ni
de capabilités ; répertoires créés à la main par `install.sh` au lieu
des directives systemd.

### 6.2 Solutions envisageables

- **A. Bloc durci de référence sur les 3 unités**.
  - Avantages : durcissement standard systemd, zéro dépendance, borne
    mémoire alignée sur la doc PERFORMANCE.
  - Inconvénients : un OOM-kill contrôlé remplace un plantage potentiel
    de la machine (comportement voulu).
  - Verdict : retenu.
- **B. Conteneurisation (systemd-nspawn / portable services)**.
  - Avantages : isolation maximale.
  - Inconvénients : sur-ingénierie pour un service socket local ; coût
    d'exploitation.
  - Verdict : rejeté.

### 6.3 Solution retenue

Bloc appliqué aux 3 unités (`synapse.service`, `synapse-web.service`,
`synapse-a2a@.service`) :

- `MemoryHigh=4G`, `MemoryMax=6G` sur le serveur ; `MemoryMax=512M` sur
  web et a2a ; `OOMScoreAdjust=500` (en tension mémoire, le service meurt
  et est relancé par `Restart` — la machine survit).
- `RestrictAddressFamilies=AF_UNIX` sur le serveur (vérifié : il n'ouvre
  aucun socket TCP) ; `AF_UNIX AF_INET` sur web/a2a (localhost HTTP).
- `CapabilityBoundingSet=` (vide), `SystemCallFilter=@system-service`,
  `PrivateDevices=yes`, `ProtectKernelTunables=yes`,
  `ProtectKernelModules=yes`, `ProtectControlGroups=yes`,
  `LockPersonality=yes`, `RestrictSUIDSGID=yes`, `RestrictRealtime=yes`.
- Remplacement de la création manuelle des répertoires par
  `StateDirectory=synapse` (`/var/lib/synapse`), `LogsDirectory=synapse`
  (`/var/log/synapse`), `RuntimeDirectory=synapse` (`/run/synapse`) —
  chemins IDENTIQUES aux actuels, ownership/permissions gérés par
  systemd, `RuntimeDirectory` nettoyée au boot (plus de socket/PID
  résiduels après crash+reboot). `/var/backups/synapse` n'a pas de
  directive équivalente → reste créé par `install.sh`.
- `StartLimitIntervalSec=600` + `StartLimitBurst=5` (déjà au §1) +
  `WatchdogSec=30` (déjà au §4).

Vérifications préalables : ni server, ni web, ni bridge ne fork ni ne
lancent de sous-processus en mode `--foreground` (seul le CLI parent le
fait) → `SystemCallFilter=@system-service` ne gêne pas. La suite de
tests n'est pas affectée (les unités ne s'appliquent qu'au système
installé, jamais au CI).

### 6.4 Justification du choix

Le durcissement de référence systemd applique directement la borne
mémoire documentée par PERFORMANCE.md (64 connexions × 64 MiB) : en cas
de pic, c'est le service qui meurt et redémarre, pas la machine. Les
directives `StateDirectory`/`LogsDirectory`/`RuntimeDirectory`
suppriment du code d'install et garantissent un état propre au boot.

### 6.5 Impacts sur l'architecture existante

- `install.sh` : la création des 3 répertoires principaux est déléguée à
  systemd (chemins inchangés) ; le bloc durci est appliqué aux 3 unités.
- OPERATIONS.md : runbook enrichi (unités en `failed`, OOM-kill,
  verrou après reboot).
- Aucun impact sur le code applicatif (hors heartbeat du §4).

### 6.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `install.sh` | réécriture des 3 unités (bloc durci complet, StateDirectory/LogsDirectory/RuntimeDirectory, StartLimitBurst, WatchdogSec) ; suppression de la création manuelle des répertoires gérés |
| `docs/production/OPERATIONS.md` | runbook : OOM-kill, unité failed, nettoyage au boot |
| vérification manuelle | re-run de `install.sh` sur une vraie installation existante (idempotence, chemins préservés) |

### 6.7 Points d'attention et risques

- **OOM-kill** = requêtes en vol perdues : acceptable (les clients ont
  des timeouts) ; documenté.
- **`SystemCallFilter`** : un appel système exotique futur serait bloqué
  avec une erreur immédiate et claire — à documenter dans le runbook.
- **Idempotence de `install.sh`** : le re-run doit être testé sur une
  installation réelle avant la release (point (b) des preuves §0).
- **`StateDirectory`** : confirmer à l'installation que les chemins
  résultants sont bien `/var/lib/synapse`, `/var/log/synapse`,
  `/run/synapse` (aucun déplacement).

---

## 7. Épinglage des dépendances

### 7.1 Problème identifié

`pyproject.toml` n'a que des bornes minimales (`argon2-cffi>=23.1.0`,
`cryptography>=42.0.4`, `orjson>=3.9`) et `install.sh` fait
`pip install "$REPO"` : la résolution se fait au moment de
l'installation. Deux installations à plusieurs mois d'écart peuvent
produire des environnements différents ; une régression amont
(cryptography, orjson) peut atteindre la production silencieusement.

### 7.2 Solutions envisageables

- **A. Lockfile pip-compile avec hashes, utilisé par install.sh**.
  - Avantages : reproductibilité totale, vérification d'intégrité
    (`--require-hashes`), pip-tools n'est nécessaire qu'au moment de la
    génération (jamais sur la prod), l'écosystème pip/venv existant est
    conservé.
  - Inconvénients : étape de régénération à ne pas oublier lors d'un
    bump (mitigé par RELEASE.md + vérification en CI).
  - Verdict : retenu.
- **B. uv** : lockfile natif et rapide, mais nouvel outil à installer
  sur la prod ; rupture avec l'écosystème actuel. Rejeté.
- **C. Vendoring des wheels** : lourd, fragile. Rejeté.

### 7.3 Solution retenue

- **`requirements.lock`** commité à la racine, généré par
  `pip-compile --generate-hashes` depuis `pyproject.toml` (pip-tools 7
  lit pyproject).
- **`install.sh`** :

      /opt/synapse/venv/bin/pip install --require-hashes -r requirements.lock
      /opt/synapse/venv/bin/pip install --no-deps "$REPO"

- **Dev** : continue d'utiliser `pyproject.toml` (souplesse) ; **prod** :
  utilise le lock (reproductibilité) — séparation classique.
- **Régénération** : étape obligatoire de `docs/production/RELEASE.md` (point 5 du
  process), avec justification du bump dans le CHANGELOG.
- **CI** : `ci.sh` vérifie qu'aucune contrainte de `pyproject.toml`
  n'est en dehors du lock (grep simple, échec explicite).

### 7.4 Justification du choix

La reproductibilité de l'environnement de production est un prérequis
de fiabilité ; `--require-hashes` ajoute la vérification d'intégrité
sans nouvelle dépendance sur la machine de prod. pip-tools est un outil
de développement, cohérent avec l'écosystème pip/venv existant.

### 7.5 Impacts sur l'architecture existante

- `install.sh` change d'ordre d'installation (lock d'abord, paquet local
  en `--no-deps`).
- La prod ne résout plus jamais de dépendances à l'installation.
- Un bump de dépendance devient une décision de release (CHANGELOG +
  régénération du lock), pas un effet de bord d'install.

### 7.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `requirements.lock` (nouveau) | résolution complète + hashes |
| `install.sh` | `--require-hashes` + `--no-deps` |
| `docs/production/RELEASE.md` | étape de régénération du lock |
| `scripts/ci.sh` | vérification lock ↔ pyproject |

### 7.7 Points d'attention et risques

- **Hash manquant** : `--require-hashes` échoue si une dépendance n'a pas
  de hash (rare sur PyPI) — échec explicite, à corriger à la
  régénération.
- **Oubli de régénération** : couvert par l'étape RELEASE.md obligatoire
  et le contrôle en CI.
- **pip lui-même non épinglé** : accepté (l'upgrade de pip dans
  install.sh reste un comportement standard).

---

## 8. Dettes de documentation

### 8.1 Problème identifié (vérifié ligne par ligne)

- `README.md` l.1-6 : « Aucune interface humaine, aucun port réseau
  ouvert » — faux depuis F18/F20 (web + bridge sur 127.0.0.1).
- `README.md` l.123 : « référence complète des 12 commandes » — l'API en
  compte 65.
- `README.md` l.58-59 : les 7 binaires listés sans signaler qu'ils sont
  des alias dépréciés du CLI unifié.
- `README.md` l.38-53 : arborescence citant `synapse/cli.py` (supprimé)
  et « 58 commandes » (65 aujourd'hui).
- `README.md` l.111-116 : exemples à l'ancienne forme plate
  (`synapse send_message …`) — supprimée, remplacée par
  `synapse message send …` (SPEC_CLI_ECARTS §10).
- `docs/production/OPERATIONS.md` §82-108 : décrit l'ancien modèle web
  (`--token-stdin`, `--observer-name`) remplacé par le jeton local
  `web_token` + `synapse web start` ; ne mentionne pas les unités
  web/a2a (nouveau §1).
- `synapse/__init__.py` : `__version__ = "1.0.0"` périmé (traité au §5).

### 8.2 Solutions envisageables

- **A. Réécriture ciblée** : README = portail à jour ; OPERATIONS =
  runbook de prod ; hiérarchie specs conservée.
  - Avantages : coût faible, risque nul, restaure la confiance dans la
    doc.
  - Verdict : retenu.
- **B. Réécriture totale** : risque de dérive hors périmètre. Rejeté.
- **C. Tout déplacer dans les specs** : README réduit à un portail
  minimal. Rejeté (perte d'information utile à l'installation).

### 8.3 Solution retenue

- **README.md** : intro corrigée (F18/F20, localhost avec jeton) ;
  compteur « 65 commandes » ; CLI unifié présenté comme outil de
  référence, les 7 binaires signalés comme alias dépréciés ;
  arborescence réelle (`synapse/cli/` package) ; exemples au nouveau
  format (`synapse message send`, `--password-stdin`).
- **OPERATIONS.md** : runbook de production réécrit pour : cycle de vie
  systemd des 3 services (arrêt via `systemctl`, pas le CLI) ; secrets
  A2A ; sauvegarde automatisée (timer, rétention 14, verify hebdo) ;
  supervision passive (watchdog, moniteur 5 min, monitor.json,
  alert_command) ; dépannage enrichi (unité `failed`, OOM-kill, verrou
  après reboot, run manqué rattrapé).
- **Hiérarchie conservée** : `SPEC.txt` / `SPEC_WEB.txt` / `SPEC_CLI.md`
  = source de vérité ; `SPEC_CLI_ECARTS.md` = référence des écarts
  (inchangée).

### 8.4 Justification du choix

La documentation obsolète est un risque de production réel (un
opérateur suit l'ancien modèle `--token-stdin`, un intégrateur croit
que l'API a 12 commandes). La réécriture ciblée rétablit la cohérence
sans toucher à la hiérarchie des specs qui fait la force du projet.

### 8.5 Impacts sur l'architecture existante

- Aucun impact fonctionnel : documentation uniquement.
- README et OPERATIONS deviennent synchrones avec l'état réel du dépôt
  et avec les nouveaux §1-§7.

### 8.6 Modifications nécessaires

| Fichier | Modification |
|---|---|
| `README.md` | portail à jour (intro, compteurs, CLI unifié, arborescence, exemples) |
| `docs/production/OPERATIONS.md` | runbook de prod (systemd, secrets, sauvegarde, supervision, dépannage) |
| vérification | grep des compteurs (65, plus de 12/58), aucun exemple à l'ancienne forme |

### 8.7 Points d'attention et risques

- **Compteurs périmés ailleurs** : grep systématique de « 12 commandes »,
  « 58 commandes » dans tout le dépôt (helpdoc, tests) avant clôture.
- **Dérive de synchro** : OPERATIONS doit rester le reflet des §1/§3/§4
  — toute évolution future de la supervision/sauvegarde l'actualise.

---

## 9. Contraintes finales

Liste numérotée des règles synthétisant l'ensemble des décisions.
Chaque règle est vérifiable (par inspection, exécution ou test).

1. Les trois services (serveur, web, passerelle A2A) sont supervisés
   par systemd ; les unités sont générées par `install.sh`.
2. En production, le web et le bridge ne sont JAMAIS lancés en mode
   détaché du CLI : uniquement les unités systemd en mode `--foreground`.
3. Les secrets du bridge vivent dans `/etc/synapse/secrets/` (0700 root,
   fichiers 0600) et ne sont jamais passés en argument ni en
   environnement ; ils transitent exclusivement par stdin.
4. `synapse update apply` pilote `systemctl` quand l'unité existe (sinon
   le CLI) et son plan inclut l'arrêt/redémarrage du bridge. La variable
   `SYNAPSE_NO_SYSTEMD=1` force le mode CLI (tests, développement).
5. Aucun push n'est accepté sans suite complète verte (hook pre-push
   bloquant).
6. `ci.sh` s'exécute sur des configurations temporaires ; il ne touche
   jamais au stockage, socket ou logs de production.
7. La sauvegarde est automatisée : timer quotidien 02:00,
   `Persistent=true`.
8. La rétention est bornée : 14 archives au maximum (`prune --keep 14`).
9. La restauration est prouvée chaque semaine : `backup verify` sur
   scratch (dimanche 03:00).
10. Une copie de `backup.key` existe en `/etc/synapse/backup.key.vault`
    et son empreinte sha256 est vérifiée par le moniteur.
11. Chaque daemon émet `WATCHDOG=1` toutes les 10 s ; `WatchdogSec=30` :
    un gel entraîne kill + redémarrage par systemd.
12. Le moniteur s'exécute toutes les 5 min, écrit `/var/lib/synapse/
    monitor.json` et sort un code ≠ 0 en anomalie ; `alert_command`
    (facultatif) est exécuté le cas échéant.
13. Chaque unité a des bornes mémoire (serveur : MemoryHigh=4G /
    MemoryMax=6G ; web/a2a : MemoryMax=512M) et `OOMScoreAdjust=500`.
14. Les unités utilisent `StateDirectory`/`LogsDirectory`/`RuntimeDirectory`
    (chemins `/var/lib`, `/var/log`, `/run/synapse`) ; `RuntimeDirectory`
    n'appartient QU'AU serveur (une unité qui échoue avec un
    RuntimeDirectory partagé ferait nettoyer le répertoire par systemd —
    socket du serveur supprimé).
15. Les dépendances de production sont épinglées dans `requirements.lock`
    (hashes) et installées avec `--require-hashes` + `--no-deps`.
16. La version est lue exclusivement via `project_version()`
    (importlib.metadata) ; `__version__` dans `synapse/__init__.py` est
    supprimé.
17. `synapse --version` affiche la version installée.
18. Tout tag est annoté, posé après suite complète en venv frais, et
    documenté dans `CHANGELOG.md` (prochaine release : 3.1.0).
19. `README.md` et `OPERATIONS.md` reflètent l'état réel (65 commandes,
    CLI unifié, 3 services systemd) ; aucun compteur périmé (12/58) ne
    subsiste dans le dépôt.
20. Aucune règle de ce document ne modifie l'API serveur v2 (docs/specs/SPEC.txt) :
    compatibilité stricte, sur-ensemble.

**Positionnement** : l'objectif est de rendre Synapse déployable et
exploitable en production sur une machine locale supervisée par systemd,
sans dépendance externe (ni SaaS, ni stack de supervision lourde), avec
une preuve d'exécution pour chaque garantie — et non de transformer
Synapse en plateforme distribuée ou en service multi-machines.

---

## 10. Validation et ordre d'implémentation

### 10.1 Validation utilisateur

Feu vert utilisateur donné le 2026-08-07 ; les 8 points sont **validés et
implémentés** (détails et preuves au §10.4) :

- [x] §1 Supervision systemd web + A2A — validé / implémenté
- [x] §2 CI — validé / implémenté
- [x] §3 Sauvegarde automatisée + restauration prouvée — validé / implémenté
- [x] §4 Supervision passive + alerting — validé / implémenté
- [x] §5 Cycle de release — validé / implémenté
- [x] §6 Durcissement systemd — validé / implémenté
- [x] §7 Épinglage des dépendances — validé / implémenté
- [x] §8 Dettes de documentation — validé / implémenté

Chaque point a été vérifié par les preuves exigées (tests ciblés verts,
exécution réelle, `systemd-analyze verify`, suite complète verte avant le
tag 3.1.0).

### 10.2 Ordre d'implémentation

1. **§8** (documentation — rapide, zéro risque) en parallèle de **§6**
   (durcissement — modifie install.sh et les unités) ;
2. **§1** (unités systemd web + A2A + update apply piloté systemd — le
   plus structurant) ;
3. **§3** (sauvegarde automatisée, prune, verify, backup.key.vault) ;
4. **§4** (sd_notify + heartbeat + moniteur) ;
5. **§2** (ci.sh + hooks + timer) ;
6. **§5** (CHANGELOG, --version, RELEASE.md, tag 3.1.0) ;
7. **§7** (requirements.lock + install.sh) — clôt la boucle release.

### 10.3 Preuves à fournir à la fin de chaque point

- Tests ciblés verts (lot habituel du projet + tests nouveaux du point).
- Exécution réelle sur une installation (re-run install.sh, unités
  actives, `systemctl status`, moniteur, verify).
- Mise à jour de la documentation correspondante dans le même commit.
- Un run complet de la suite (≈16 min) avant le tag 3.1.0 uniquement.

### 10.4 Journal de mise en œuvre (2026-08-07)

Feu vert utilisateur donné ; implémentation complète, commit par point :

| Point | Commit | Contenu |
|---|---|---|
| §4 (code) | `296afd6` | `synapse/systemd_notify.py`, battements de cœur des 6 chemins de daemon, état A2A dans `synapse status` |
| §3 | `97f44a9` | `backup prune` + `backup verify` (restore scratch isolé) |
| §1 + §5 (update) | `c4cfdbf` | `update apply` piloté systemd (détection d'unités, passerelle A2A au plan), `synapse --version`, suppression `__version__` |
| §1 + §4 + §6 (déploiement) | `c9a2ce5` | 11 templates `scripts/systemd/` (durcis, vérifiés par `systemd-analyze verify`), wrapper secrets A2A, `scripts/synapse-monitor.py`, `install.sh` réécrit, `config.example.json` |
| §2 + §7 | `056b786` | `scripts/ci.sh` + hook pre-push + timer nocturne, `requirements.lock` (hashes), pyproject 3.1.0 |
| §5 + §8 (docs) | `64763d1` | CHANGELOG, RELEASE.md, README, OPERATIONS, SPEC_CLI (prune/verify/version/A2A), SPEC_PRODUCTION (statut) |

Preuves de vérification :

- Tests ciblés verts pour chaque point (58 nouveaux tests : sd_notify,
  backup prune/verify, moniteur, unités systemd, ops production, update
  systemd) + tous les fichiers de test existants affectés (CLI unifié,
  update apply, backup, a2a).
- `systemd-analyze verify` réellement exécuté sur les 11 unités
  substituées (a détecté et permis de corriger une vraie erreur :
  `StartLimitIntervalSec`/`StartLimitBurst` placés en section `[Service]`
  au lieu de `[Unit]`).
- `requirements.lock` validé par une installation fraîche
  (`pip install --require-hashes -r requirements.lock` +
  `pip install --no-deps .` → imports + version OK).
- Suite complète verte via `scripts/ci.sh` (974 tests, exit 0) — la CI
  locale du §2 est ainsi validée de bout en bout.
- Tag annoté `v3.1.0` posé sur HEAD après la suite verte (procédure
  RELEASE.md).

Écarts d'implémentation assumés (documentés dans ce document) :
`scripts/synapse-monitor.py` (et non `.sh`), base `synapse.db` (et non
`state.db`), unités déployées depuis les templates `scripts/systemd/`.

### 10.5 Déploiement réel et correctifs (2026-08-07)

`install.sh` exécuté en root sur la machine de production (session
utilisateur) ; mise en service complète vérifiée. Trois correctifs
découverts par le déploiement réel (commits `295aa5b` et `6190b7c`) :

1. **`RuntimeDirectory=synapse` réservé au serveur** : les unités
   web/a2a/backup/moniteur le partageaient ; quand le web échouait au
   démarrage (serveur pas encore prêt), systemd nettoyait `/run/synapse`
   à l'arrêt de l'unité → socket + `web_token` + pid du serveur
   supprimés (socket orphelin, service injoignable malgré un processus
   vivant). Contrainte 14 précisée ; garde-fou de test
   (`test_runtime_directory_server_only`).
2. **`ExecStartPre` d'attente bornée (60 s) du socket serveur** sur
   `synapse-web.service` et `synapse-a2a@.service` : au lieu d'épuiser
   `StartLimitBurst` contre un serveur en démarrage, l'unité attend.
3. **`backup.key.vault` en 0640 root:synapse** : 0600 root rendait la
   copie illisible par le moniteur (anomalie réelle détectée : « lecture
   de la clé impossible ») — root écrit, `synapse` lit pour l'empreinte.

Vérifications du déploiement (état réel, pas simulé) :

- `synapse --version` → 3.1.0 (venv installé) ; 6 unités enabled ;
  `systemd-analyze verify` OK sur les fichiers déployés.
- `synapse.service` et `synapse-web.service` actives ; socket + jeton
  présents en `/run/synapse` (stabilité confirmée sur 75 s après
  correctif) ; `synapse status --json` : serveur running, web running
  (HTTP 200), organisation `acme_ia` listée.
- Organisation créée (`synapse org init acme_ia`) ; mot de passe initial
  conservé en `/etc/synapse/initial-org.password` (0600 root).
- `synapse backup create` réel → archive chiffrée ; `synapse backup
  verify --latest` → intégrité SQLite ok, 21 tables, scratch isolé ;
  copie `backup.key.vault` créée (sha256 identique à `backup.key`).
- Moniteur exécuté réellement : `ok: true`, aucune anomalie
  (services, sauvegarde < 26 h, disque, erreurs, empreinte de la clé).
- Suite complète verte (974 tests) avant le tag ; le tag `v3.1.0` pointe
  sur le HEAD vérifié après correctifs.
