# SPEC-CLI — Interface en ligne de commande unifiée `synapse`

**Statut** : IMPLÉMENTÉ (2026-08-06, commit CLI unifié) — l'ensemble des
commandes, options et règles de fonctionnement décrites ci-dessous est
opérationnel. Les écarts volontaires (formes ergonomiques que l'API ne
peut pas servir littéralement) sont documentés dans
`docs/specs/SPEC_CLI_ECARTS.md`.
**Date** : 2026-08-06.
**Décisions** : les 5 points ouverts ont été tranchés (§7) — aucun choix en suspens.

Ce document définit l'architecture complète du point d'entrée unique `synapse` :
toutes les commandes, sous-commandes, options, comportements attendus, règles
de fonctionnement, exemples d'utilisation et décisions d'architecture.

---

## 1. Objectif

Aujourd'hui, le projet expose 7 points d'entrée séparés (`synapse-server`,
`synapse-web`, `synapse-init-org`, `synapse-backup`, `synapse-restore`,
`synapse-a2a-bridge`, et un `synapse` qui n'est qu'un client API brut).
L'objectif est de fusionner l'ensemble en **un seul point d'entrée
hiérarchique** `synapse`, couvrant :

- l'administration du serveur (démarrage, arrêt, statut, logs, config) ;
- l'interface web (démarrage, arrêt, statut, logs) ;
- la gestion des organisations, agents, messages, tâches, groupes,
  politiques, événements (habillage ergonomique des 65 commandes API) ;
- l'accès brut et évolutif à toute commande du service (`synapse api`) ;
- la sauvegarde/restauration, le pont A2A, les logs, le diagnostic,
  les mises à jour, l'état global.

---

## 2. Principes généraux

| Règle | Description |
|---|---|
| **Structure** | `synapse <groupe> <action> [options]` — maximum **2 niveaux** de sous-commandes (décision §7.5). `synapse` seul = `synapse server start` (décision §7.2). |
| **Mots de passe** | Jamais en argument de commande (historique du shell). Toujours via `getpass` (interactif) ou `--password-stdin`. Principe hérité du CLI actuel, conservé intégralement. |
| **Configuration** | `--config <chemin>` accepté par tout groupe touchant au serveur ; sinon recherche dans l'ordre : `$SYNAPSE_CONFIG`, chemin par défaut d'installation. |
| **Sortie JSON** | `--json` sur toute commande de lecture : sortie JSON brute (scripting, pas de mise en forme). |
| **Codes de sortie** | `0` = succès ; `1` = erreur (argument, refus) ; `3` = service indisponible (socket absent, serveur arrêté) ; `4` = déjà en cours d'exécution (démarrage d'un service déjà actif). |
| **Idempotence** | `start`/`enable` sont idempotents : si l'état demandé est déjà atteint, message clair et code 0 (pas d'erreur). |
| **Langue** | Messages d'aide et de sortie en français (langue du projet). |
| **Secrets** | Toute sortie de `config`/`diag` masque les secrets (hashs, jetons) sauf `--show-secrets` explicite. |

### 2.1 Authentification

Le CLI distingue trois modes, par ordre de priorité :

1. **Jeton local** (aucun mot de passe) : quand le CLI tourne sur le même
   poste que le service, il lit `run_dir/web_token` (0600) et s'authentifie
   comme l'identité web locale pour les opérations d'administration
   (org, agents, politiques, lecture). C'est le même mécanisme que l'interface
   web — cohérent et sans secret saisi (décision §7.3).
2. **Compte humain** : `--organization-name` + mot de passe (stdin) pour les
   opérations réservées aux humains (création/désactivation d'org, lecture de
   contenu, envoi en tant qu'humain).
3. **Compte agent** : `--my-name` + mot de passe (stdin) pour les opérations
   « en tant que compte » (messagerie d'un agent, file de travail, etc.).

Règle : si une commande peut être servie par le jeton local, elle ne demande
jamais de mot de passe. Les mots de passe ne sont requis que pour les actions
« en tant que compte » et les rotations.

### 2.2 État des processus (run dir)

Le répertoire d'exécution (`run_dir`, dérivé de `socket_path`) contient :

| Fichier | Contenu | Écrit par | Retiré à |
|---|---|---|---|
| `synapse.sock` | socket Unix du service | serveur | arrêt propre |
| `web_token` | jeton de confiance local (0600) | serveur | arrêt propre |
| `synapse.pid` | PID + horodatage + version du serveur | serveur | arrêt propre |
| `web.pid` | PID + horodatage + version du web | web | arrêt propre |

`status` fait une **double vérification** : le PID est vivant ET le socket
répond (`get_server_status`). Un PID vivant sans socket = état « dégradé ».

---

## 3. Arbre complet des commandes

```
synapse                              → démarre le serveur principal
├── server   start | stop | restart | status | logs | config
├── web      start | stop | restart | status | logs
├── org      init | list | status | enable | disable | password
│            | agents | structure | metrics | audit
├── agent    create | status | description | card | department | visibility
│            | budget | password | deactivate | reactivate | find
│            | create-observer | revoke-observer | observers
├── message  send | inbox | conversation | read | mark-no-reply | notifications
├── task     list | create | status | update | approve | reject
│            | request-approval | transfer | my-work
├── group    create | members | add-member | remove-member | messages | send | list
├── policy   show | set | escalation | delegate | revoke | delegations
├── event    stream | retention
├── api      <commande du service, ex. get_org_metrics>
├── backup   create | restore | list | prune | verify
├── a2a      start | stop | status
├── logs     [serveur|web] [--follow]
├── diag     status | doctor
├── update   check | apply
├── status   (état global)
└── version  (version installée, ou --version)
```

---

## 4. Détail des commandes

### 4.1 Groupe racine

#### `synapse`
- **Rôle** : démarre le serveur principal (raccourci de `server start`).
- **Déclenche** : `synapse server start` (voir ci-dessous).
- **Options** : aucune propre — passe à `server start` (qui accepte
  `--config`, `--foreground`, `--log-level`).
- **Exemple** : `synapse --config /etc/synapse/config.json`
- **Alternative** : `synapse server start` (forme longue explicite).
- **Remarque** : si le serveur tourne déjà, message « serveur déjà en cours
  d'exécution » et code 0 (idempotent, décision §7.2).

### 4.2 Groupe `server`

#### `synapse server start`
- **Rôle** : démarre le serveur principal (le service Synapse).
- **Déclenche** : lance le processus serveur (socket Unix, base SQLite,
  Argon2id, cache F1) ; écrit `synapse.pid` et `web_token` dans le run dir.
- **Options** : `--config <chemin>`, `--foreground` (rester au premier plan,
  logs sur stdout), `--log-level <debug|info|warning|error>`.
- **Exemple** : `synapse server start --foreground --log-level debug`
- **Complément** : idempotent — si déjà démarré, code 0 avec message.
  `synapse server status` pour l'état.

#### `synapse server stop`
- **Rôle** : arrêt propre du serveur.
- **Déclenche** : SIGTERM au PID de `synapse.pid`, attente de fermeture
  (max 15 s), retrait de `synapse.sock` et `web_token` ; SIGKILL si
  `--force`.
- **Options** : `--config <chemin>`, `--force`.
- **Exemple** : `synapse server stop --force`
- **Complément** : arrête le serveur, pas le web (arrêt séparé).

#### `synapse server restart`
- **Rôle** : arrêt puis démarrage du serveur.
- **Déclenche** : `stop` puis `start` séquentiellement.
- **Exemple** : `synapse server restart`

#### `synapse server status`
- **Rôle** : état du service.
- **Déclenche** : lit `synapse.pid` (PID vivant ?) + interroge le socket
  (`get_server_status`) ; affiche version, uptime, base de données, nombre
  de requêtes, état du socket, présence du jeton web.
- **Options** : `--json`.
- **Exemple** : `synapse server status --json`
- **Alternative** : `synapse status` (état global).

#### `synapse server logs`
- **Rôle** : logs du serveur.
- **Déclenche** : lit le `log_dir` du serveur.
- **Options** : `--follow`/`-f`, `--lines <N>` (défaut 100), `--level`.
- **Exemple** : `synapse server logs --follow`
- **Alternative** : `synapse logs` (fusion serveur + web).

#### `synapse server config`
- **Rôle** : configuration effective (fichier + défauts appliqués).
- **Déclenche** : charge la config et affiche les valeurs (secrets masqués).
- **Options** : `--json`, `--show-secrets` (dangereux, affiche les valeurs
  sensibles).
- **Exemple** : `synapse server config --json`
- **Alternative** : `synapse diag doctor` (validation de l'environnement).

### 4.3 Groupe `web`

#### `synapse web start`
- **Rôle** : démarre l'interface web (ancien `synapse-web`).
- **Déclenche** : serveur HTTP sur `127.0.0.1:<port>` ; lit le jeton local
  `web_token` pour s'authentifier auprès du service ; écrit `web.pid`.
- **Options** : `--config <chemin>`, `--port <n>` (défaut :
  `$SYNAPSE_WEB_PORT`, sinon 8080), `--foreground`, `--log-level`.
- **Exemple** : `synapse web start --port 8080`
- **Complément** : exige un serveur démarré (sinon code 3 avec message
  « service local non prêt ») ; `SYNAPSE_WEB_PORT` isole les tests sur une
  machine où la production écoute déjà sur 8080 (SPEC_PRODUCTION §10.5).

#### `synapse web stop`
- **Rôle** : arrêt du web.
- **Déclenche** : SIGTERM sur `web.pid`, attente max 15 s ; SIGKILL si
  `--force`.
- **Options** : `--config <chemin>`, `--force`.
- **Exemple** : `synapse web stop`

#### `synapse web restart`
- **Rôle** : arrêt puis démarrage du web.
- **Exemple** : `synapse web restart`

#### `synapse web status`
- **Rôle** : état du web.
- **Déclenche** : PID vivant ? + requête HTTP `GET /api/orgs` (200 ?) +
  nombre de sessions actives en mémoire + port.
- **Options** : `--json`.
- **Exemple** : `synapse web status --json`

#### `synapse web logs`
- **Rôle** : logs du web (mêmes options que `server logs`).
- **Exemple** : `synapse web logs --follow`

### 4.4 Groupe `org` — organisations

#### `synapse org init <nom>`
- **Rôle** : crée une organisation + son compte humain (ancien
  `synapse-init-org` ; même code que `create_org`).
- **Déclenche** : `create_organization` local (install.py) ; le mot de passe
  de l'organisation est demandé sur stdin (getpass) ou `--password-stdin`.
- **Options** : `--config <chemin>`, `--password-stdin`.
- **Exemple** : `echo "motdepasse-acme-1" | synapse org init acme --password-stdin`
- **Complément** : opération locale, disponible même sans humain existant.

#### `synapse org list`
- **Rôle** : liste les organisations ACTIVES.
- **Déclenche** : commande `list_orgs` via le jeton local (aucun mot de
  passe) ; avec `--all`, liste aussi les désactivées (compte humain requis).
- **Options** : `--json`, `--all`, `--config <chemin>`.
- **Exemple** : `synapse org list --json`

#### `synapse org status <nom>`
- **Rôle** : état d'une organisation (active/désactivée, compte humain,
  nombre d'agents, dernières métriques).
- **Déclenche** : `get_org_snapshot` + `get_org_metrics` (jeton local ou
  compte humain).
- **Options** : `--json`, `--config <chemin>`.
- **Exemple** : `synapse org status acme --json`

#### `synapse org enable <nom>`
- **Rôle** : réactive une organisation désactivée (gel levé).
- **Déclenche** : procédure locale `enable_org` (jamais d'API distante pour
  dégeler — règle de la spec) ; mot de passe d'org requis sur stdin.
- **Options** : `--config <chemin>`, `--password-stdin`.
- **Exemple** : `echo "mdp" | synapse org enable acme --password-stdin`
- **Complément** : idempotent — une org déjà active renvoie un message clair,
  code 0.

#### `synapse org disable <nom>`
- **Rôle** : désactive une organisation (gel absolu : sessions web
  invalidées, envois refusés, données intactes).
- **Déclenche** : `disable_org` (compte humain de l'org, ou jeton local).
- **Options** : `--config <chemin>`, `--password-stdin`, `--json`.
- **Exemple** : `synapse org disable acme --password-stdin`
- **Complément** : irréversible par API — le dégel est local (`enable`).

#### `synapse org password <nom>`
- **Rôle** : rotation du mot de passe de l'organisation.
- **Déclenche** : `change_organization_password` (ancien + nouveau sur stdin,
  jamais en arguments) ; la délégation humaine suit automatiquement.
- **Options** : `--password-stdin`.
- **Exemple** : `synapse org password acme --password-stdin`

#### `synapse org agents <nom>`
- **Rôle** : liste les agents de l'organisation.
- **Déclenche** : `list_org_agents` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse org agents acme --json`

#### `synapse org structure <nom>`
- **Rôle** : organigramme complet (départements, managers, groupes,
  affectations).
- **Déclenche** : `get_org_structure` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse org structure acme --json`

#### `synapse org metrics <nom>`
- **Rôle** : métriques de l'organisation (volume de messages, activité,
  latence, erreurs).
- **Déclenche** : `get_org_metrics` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse org metrics acme --json`

#### `synapse org audit <nom>`
- **Rôle** : journal d'audit de l'organisation (lectures de contenu, envois,
  gestion, échecs).
- **Déclenche** : `get_org_audit` (jeton local ou humain).
- **Options** : `--limit <n>` (défaut 20, max 100), `--cursor <seq>`,
  `--json`.
- **Exemple** : `synapse org audit acme --limit 50 --json`

### 4.5 Groupe `agent`

#### `synapse agent create <nom>`
- **Rôle** : crée un agent dans l'organisation.
- **Déclenche** : `create_agent` (auth org : jeton local).
- **Options** : `--password-stdin` (mot de passe de l'agent, requis),
  `--description <texte>`, `--department <dept>`, `--role <role>`,
  `--capability <x>` (répétable), `--domain <d>`, `--visible`/`--hidden`.
- **Exemple** : `synapse agent create support --department support --role employee --capability diagnostic`
- **Complément** : le suffixe `_humain` est réservé — refusé avec un message
  clair.

#### `synapse agent status <nom>`
- **Rôle** : état complet d'un agent (description, carte, réputation,
  département, visibilité, budget, état actif/inactif).
- **Déclenche** : `get_agent_description` + `get_agent_card` +
  `get_agent_reputation` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse agent status comptable --json`

#### `synapse agent description <nom> <texte>`
- **Rôle** : remplace la description d'un agent.
- **Déclenche** : `change_agent_description` (jeton local).
- **Exemple** : `synapse agent description support "Gère les demandes entrantes"`

#### `synapse agent card <nom>`
- **Rôle** : lecture de la carte agent (capacités, modèle, SLA, coût, limites).
- **Déclenche** : `get_agent_card` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse agent card support --json`
- **Alternative (écriture)** : `synapse agent card <nom> --set` avec
  `--capability <x>` (répétable), `--model <m>`, `--sla <s>`,
  `--estimated-cost <c>`, `--limits <l>` → `set_agent_card`.
  **Exemple** : `synapse agent card support --set --model synapse-agent-1 --sla "réponse < 1 h"`
- **Remarque** : `--set` transforme la lecture en écriture — pas de
  sous-commande supplémentaire (décision §7.5).

#### `synapse agent department <nom> <département> [--role <r>]`
- **Rôle** : affecte un agent à un département (et un rôle).
- **Déclenche** : `set_agent_department` (jeton local).
- **Exemple** : `synapse agent department support support manager`

#### `synapse agent visibility <nom> <visible|hidden>`
- **Rôle** : visibilité de l'agent dans l'annuaire.
- **Déclenche** : `set_agent_visibility` (jeton local).
- **Exemple** : `synapse agent visibility auditor hidden`

#### `synapse agent budget <nom> <montant>`
- **Rôle** : budget mensuel de l'agent.
- **Déclenche** : `set_agent_budget` (jeton local).
- **Exemple** : `synapse agent budget data 5000`

#### `synapse agent password <nom>`
- **Rôle** : rotation du mot de passe d'un agent.
- **Déclenche** : `change_agent_password` (nouveau mot de passe sur stdin).
- **Options** : `--password-stdin`.
- **Exemple** : `synapse agent password data --password-stdin`

#### `synapse agent deactivate <nom>` / `synapse agent reactivate <nom>`
- **Rôle** : désactive / réactive un agent.
- **Déclenche** : `deactivate_agent` / `reactivate_agent` (jeton local).
- **Exemple** : `synapse agent deactivate commercial`

#### `synapse agent find [motif]`
- **Rôle** : recherche d'agents (nom, capacités, domaine).
- **Déclenche** : `find_agents` (jeton local).
- **Options** : `--capability <x>`, `--domain <d>`, `--json`.
- **Exemple** : `synapse agent find --capability audit --json`

#### `synapse agent create-observer <nom>`
- **Rôle** : crée un compte observateur (lecture métadonnées uniquement).
- **Déclenche** : `create_observer_account` (jeton local ou humain).
- **Options** : `--password-stdin`, `--description <texte>`.
- **Exemple** : `synapse agent create-observer observateur --password-stdin`

#### `synapse agent revoke-observer <nom>`
- **Rôle** : révoque un compte observateur.
- **Déclenche** : `revoke_observer_account` (jeton local).
- **Exemple** : `synapse agent revoke-observer observateur`

#### `synapse agent observers`
- **Rôle** : liste les comptes observateur.
- **Déclenche** : `list_observers` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse agent observers --json`

### 4.6 Groupe `message`

#### `synapse message send <destinataire> <texte>`
- **Rôle** : envoie un message (agent ou humain).
- **Déclenche** : `send_message` ; l'identité d'envoi est
  `--my-name` (agent) ou l'humain de l'org (jeton local + destinataire
  interne).
- **Options** : `--my-name <compte>` (requis pour envoyer en tant
  qu'agent), `--client-message-id <id>` (sinon généré), `--password-stdin`.
- **Exemple** : `synapse message send comptable "Facture transmise" --my-name commercial --password-stdin`

#### `synapse message inbox`
- **Rôle** : messages reçus, paginés.
- **Déclenche** : `get_messages` (identité = `--my-name` ou humain).
- **Options** : `--my-name <compte>`, `--limit <n>`, `--cursor <seq>`,
  `--unread` (non lus uniquement), `--sender <nom>` (filtre), `--json`.
- **Exemple** : `synapse message inbox --my-name support --unread --json`

#### `synapse message conversation <autre>`
- **Rôle** : fil de conversation avec un interlocuteur.
- **Déclenche** : `get_conversation`.
- **Options** : `--my-name <compte>`, `--limit <n>`, `--cursor <seq>`,
  `--json`.
- **Exemple** : `synapse message conversation devops --my-name support`

#### `synapse message read <message-id>`
- **Rôle** : marque un message comme lu.
- **Déclenche** : `read_message` (réservé au destinataire).
- **Options** : `--my-name <compte>`, `--password-stdin`.
- **Exemple** : `synapse message read m-123 --my-name support --password-stdin`

#### `synapse message mark-no-reply <autre>`
- **Rôle** : marque la conversation « sans réponse ».
- **Déclenche** : `mark_conversation_no_reply`.
- **Options** : `--my-name <compte>`, `--password-stdin`.
- **Exemple** : `synapse message mark-no-reply commercial --my-name comptable --password-stdin`

#### `synapse message notifications`
- **Rôle** : notifications de l'agent courant (non lus groupés par
  expéditeur).
- **Déclenche** : `get_notifications`.
- **Options** : `--my-name <compte>`, `--limit <n>`, `--json`.
- **Exemple** : `synapse message notifications --my-name directeur --json`

### 4.7 Groupe `task`

#### `synapse task list`
- **Rôle** : liste les tâches.
- **Déclenche** : `list_tasks` / `list_department_tasks` (selon options).
- **Options** : `--state <état>`, `--assignee <agent>`, `--department <d>`,
  `--limit <n>`, `--cursor <seq>`, `--json`.
- **Exemple** : `synapse task list --state en_attente --json`

#### `synapse task create <titre> --assignee <agent>`
- **Rôle** : crée une tâche.
- **Déclenche** : `create_task` (créateur = `--creator` ou jeton local).
- **Options** : `--assignee <agent>` (requis), `--priority
  <normal|haute|basse>`, `--due <horodatage>`, `--creator <agent>`,
  `--department <d>`.
- **Exemple** : `synapse task create "Rapport mensuel" --assignee analyste --priority haute`

#### `synapse task status <id>`
- **Rôle** : détail d'une tâche.
- **Déclenche** : `get_task`.
- **Options** : `--json`.
- **Exemple** : `synapse task status t-42 --json`

#### `synapse task update <id> <état>`
- **Rôle** : change l'état d'une tâche (`en_cours`, `terminee`, ...).
- **Déclenche** : `update_task_state`.
- **Exemple** : `synapse task update t-42 terminee`

#### `synapse task approve <id>` / `synapse task reject <id>`
- **Rôle** : approuve / refuse une tâche en attente d'approbation.
- **Déclenche** : `approve_task` / `reject_task`.
- **Exemple** : `synapse task approve t-42`

#### `synapse task request-approval <id>`
- **Rôle** : demande d'approbation pour une tâche.
- **Déclenche** : `request_approval`.
- **Exemple** : `synapse task request-approval t-42`

#### `synapse task transfer <id> <nouvel-assigné>`
- **Rôle** : transfère une tâche.
- **Déclenche** : `transfer_task`.
- **Exemple** : `synapse task transfer t-42 support`

#### `synapse task my-work`
- **Rôle** : file de travail de l'agent courant.
- **Déclenche** : `get_my_work` (identité = `--my-name`).
- **Options** : `--my-name <compte>`, `--json`.
- **Exemple** : `synapse task my-work --my-name directeur --json`

### 4.8 Groupe `group`

#### `synapse group create <nom>`
- **Rôle** : crée un groupe de discussion.
- **Déclenche** : `create_group`.
- **Options** : `--description <texte>`.
- **Exemple** : `synapse group create direction --description "Pilotage"`

#### `synapse group members <nom>`
- **Rôle** : liste les membres d'un groupe.
- **Déclenche** : `get_group_members`.
- **Options** : `--json`.
- **Exemple** : `synapse group members direction --json`

#### `synapse group add-member <nom> <membre>` / `synapse group remove-member <nom> <membre>`
- **Rôle** : ajoute / retire un membre d'un groupe.
- **Déclenche** : `add_group_member` / `remove_group_member`.
- **Exemple** : `synapse group add-member direction comptable`

#### `synapse group messages <nom>`
- **Rôle** : messages du groupe.
- **Déclenche** : `get_group_messages`.
- **Options** : `--limit <n>`, `--cursor <seq>`, `--json`.
- **Exemple** : `synapse group messages direction --limit 50 --json`

#### `synapse group send <nom> <texte>`
- **Rôle** : envoie un message au groupe.
- **Déclenche** : `send_group_message`.
- **Options** : `--my-name <compte>` (expéditeur), `--password-stdin`.
- **Exemple** : `synapse group send direction "Point hebdo à 10 h" --my-name directeur --password-stdin`

#### `synapse group list`
- **Rôle** : groupes de l'agent courant.
- **Déclenche** : `list_my_groups`.
- **Options** : `--my-name <compte>`, `--json`.
- **Exemple** : `synapse group list --my-name directeur --json`

### 4.9 Groupe `policy`

#### `synapse policy show <org>`
- **Rôle** : politiques actuelles de l'organisation (entrant/sortant externe,
  budget, restrictions).
- **Déclenche** : `get_organization_policy` (jeton local).
- **Options** : `--json`.
- **Exemple** : `synapse policy show acme --json`

#### `synapse policy set <org>`
- **Rôle** : modifie les politiques de l'organisation.
- **Déclenche** : `set_organization_policy`.
- **Options** : `--allow-incoming-external` / `--deny-incoming-external`,
  `--allow-outgoing-external` / `--deny-outgoing-external`.
- **Exemple** : `synapse policy set acme --allow-outgoing-external`

#### `synapse policy escalation <org>`
- **Rôle** : politique d'escalade (lecture) ; avec `--set`, écriture.
- **Déclenche** : `get_escalation_policy` / `set_escalation_policy`.
- **Options** : `--set`, `--max-hours <n>`, `--targets <a,b>`, `--json`.
- **Exemple** : `synapse policy escalation acme --set --max-hours 24`
- **Contrainte** : les seuils (`due_after_seconds`/`failed_after_seconds`)
  sont des **entiers >= 1** — `null`/`0` est refusé (escalade immédiate
  sinon, SPEC.txt F9).

#### `synapse policy delegate <agent> <capacités>`
- **Rôle** : délègue des capacités à un agent.
- **Déclenche** : `create_delegation`.
- **Options** : `--expires <horodatage>` (délégation temporaire).
- **Exemple** : `synapse policy delegate data "audit,rapports" --expires 2026-09-01T00:00:00Z`

#### `synapse policy revoke <délégation-id>`
- **Rôle** : révoque une délégation.
- **Déclenche** : `revoke_delegation`.
- **Exemple** : `synapse policy revoke d-7`

#### `synapse policy delegations`
- **Rôle** : liste les délégations de l'agent courant.
- **Déclenche** : `get_my_delegations`.
- **Options** : `--my-name <compte>`, `--json`.
- **Exemple** : `synapse policy delegations --my-name directeur --json`

### 4.10 Groupe `event`

#### `synapse event stream`
- **Rôle** : flux d'événements du journal append-only (audit, activité).
- **Déclenche** : `get_events`.
- **Options** : `--limit <n>`, `--seq <n>` (pagination par séquence),
  `--json`.
- **Exemple** : `synapse event stream --limit 100 --json`

#### `synapse event retention <jours>`
- **Rôle** : durée de rétention des événements.
- **Déclenche** : `set_event_retention_days`.
- **Exemple** : `synapse event retention 90`

### 4.11 Groupe `api` — accès brut (évolutivité)

#### `synapse api <commande> [options...]`
- **Rôle** : accès brut à TOUTE commande du service — les 64 actuelles et
  toutes les futures. Le CLI structuré n'est qu'un habillage ergonomique ;
  `api` garantit qu'aucune commande n'est inatteignable.
- **Déclenche** : envoie la commande sur le socket avec les paramètres
  fournis, affiche la réponse JSON.
- **Options** : `--my-name <compte>` / `--organization-name <org>`,
  `--password-stdin`, `--config <chemin>`, `--json` (défaut : JSON brut).
- **Exemple** : `synapse api get_org_metrics --organization-name acme --password-stdin`
- **Complément** : les paramètres de chaque commande suivent leur validation
  existante (`validation.py`). C'est la porte d'évolutivité : une nouvelle
  commande API est utilisable immédiatement sans modification du CLI.

### 4.12 Groupe `backup`

#### `synapse backup create`
- **Rôle** : sauvegarde complète (base SQLite + configuration).
- **Déclenche** : ancien `synapse-backup` ; écrit une archive horodatée.
- **Options** : `--dir <chemin>` (défaut : `backup_dir` de la config),
  `--config <chemin>`.
- **Exemple** : `synapse backup create --dir /srv/backups`
- **Complément** : recommandé avant toute mise à jour.

#### `synapse backup restore <archive>`
- **Rôle** : restauration depuis une archive.
- **Déclenche** : ancien `synapse-restore` ; exige un serveur **arrêté**
  (sinon refus explicite).
- **Options** : `--force` (écrase l'existant), `--config <chemin>`.
- **Exemple** : `synapse server stop && synapse backup restore /srv/backups/2026-08-06.syn`

#### `synapse backup list`
- **Rôle** : liste les archives disponibles (date, taille, version).
- **Déclenche** : lecture du répertoire de sauvegardes.
- **Options** : `--json`.
- **Exemple** : `synapse backup list --json`

#### `synapse backup prune [--keep N]`
- **Rôle** : rétention — supprime les archives les plus anciennes au-delà
  des `N` plus récentes (défaut : 14). Ne touche que les `*.synbk` du
  répertoire de sauvegardes, jamais un autre fichier.
- **Déclenche** : appelé automatiquement après chaque sauvegarde par
  l'unité systemd `synapse-backup.service`.
- **Options** : `--keep <n>` (défaut 14), `--dir <chemin>` (défaut :
  `backup_dir` de la config), `--json`.
- **Exemple** : `synapse backup prune --keep 30`

#### `synapse backup verify <archive> | --latest`
- **Rôle** : preuve de restauration — déchiffre l'archive (authentification
  AES-GCM), restaure la base dans un stockage temporaire ISOLÉ, vérifie
  l'intégrité SQLite et compte les tables, puis détruit le scratch. La
  production n'est ni modifiée ni verrouillée : `verify` fonctionne même
  serveur en marche.
- **Déclenche** : vérification hebdomadaire automatique
  (`synapse-backup-verify.timer`, sur l'archive la plus récente).
- **Options** : `--latest` (archive la plus récente du `backup_dir`),
  `--dir <scratch>` (répertoire de travail, créé temporairement par
  défaut ; refusé s'il contient le stockage de production), `--json`.
- **Exemple** : `synapse backup verify /srv/backups/2026-08-06.synbk`

### 4.13 Groupe `a2a` — pont d'interopérabilité

#### `synapse a2a start`
- **Rôle** : démarre le pont A2A (ancien `synapse-a2a-bridge`).
- **Déclenche** : processus de pont ; exige un serveur démarré.
- **Options** : `--config <chemin>`, `--foreground`, `--agent-name <agent>`,
  `--port <n>` (défaut : `$SYNAPSE_A2A_PORT`, sinon 8090).
- **Exemple** : `synapse a2a start --agent-name support`
- **Complément** : `SYNAPSE_A2A_PORT` isole les tests sur une machine où
  un pont de production écoute déjà (SPEC_PRODUCTION §10.5).

#### `synapse a2a stop`
- **Rôle** : arrête le pont. **Options** : `--force`.
- **Exemple** : `synapse a2a stop`

#### `synapse a2a status`
- **Rôle** : état du pont. **Options** : `--json`.
- **Exemple** : `synapse a2a status --json`

### 4.14 Groupe `logs`

#### `synapse logs [serveur|web] [--follow]`
- **Rôle** : logs fusionnés serveur + web ; avec un argument, logs d'un seul
  service.
- **Déclenche** : lit les `log_dir` respectifs ; `--follow` = suivi continu.
- **Options** : `--follow`/`-f`, `--lines <N>` (défaut 100), `--config
  <chemin>`.
- **Exemple** : `synapse logs --follow` ; `synapse logs web --lines 50`
- **Alternative** : `synapse server logs` / `synapse web logs` (spécifiques).

### 4.15 Groupe `diag`

#### `synapse diag status`
- **Rôle** : état global détaillé (socket, base, versions, jeton web,
  organisations, sessions, sauvegardes récentes).
- **Déclenche** : agrégation des lectures locales + `get_server_status`.
- **Options** : `--json`.
- **Exemple** : `synapse diag status --json`
- **Alternative** : `synapse status` (vue condensée).

#### `synapse diag doctor`
- **Rôle** : diagnostic d'environnement — liste de contrôles avec verdict
  OK / ÉCHEC / ATTENTION :
  1. configuration lisible et valide ;
  2. répertoires (storage, run, logs, backups) présents et permissions
     correctes ;
  3. socket présent et répondant ;
  4. jeton web présent et lisible (0600) ;
  5. version Python/SQLite compatibles ;
  6. base cohérente (intégrité, WAL, schéma attendu) ;
  7. horloge synchronisée (écarts monotonic vs horloge).
- **Déclenche** : contrôles locaux + `get_server_status`.
- **Options** : `--config <chemin>`, `--json`.
- **Exemple** : `synapse diag doctor`
- **Complément** : c'est la commande à exécuter en premier en cas de
  problème ; sortie non nulle si au moins un contrôle ÉCHEC.

### 4.16 Groupe `update`

#### `synapse update check`
- **Rôle** : compare la version installée à la dernière version publiée.
- **Déclenche** : lecture de la version locale + vérification distante
  (le cas échéant).
- **Options** : `--json`.
- **Exemple** : `synapse update check`

#### `synapse update apply`
- **Rôle** : applique la mise à jour : sauvegarde automatique → arrêt du
  web → arrêt de la passerelle A2A (si active) → arrêt du serveur →
  commande de mise à jour → redémarrages.
- **Options** : `--dry-run` (affiche le plan sans rien faire),
  `--no-backup` (déconseillé).
- **Exemple** : `synapse update apply --dry-run`
- **Complément** : en cas d'échec à mi-chemin, l'état précédent est
  restaurable via `synapse backup restore`.
- **Supervision systemd** : quand les unités `synapse.service` /
  `synapse-web.service` existent (installation de production), `apply`
  pilote `systemctl stop/start` à la place du CLI (un arrêt via le CLI
  serait contré par `Restart=on-failure`) ; la passerelle A2A est arrêtée
  et redémarrée via ses instances `synapse-a2a@*.service` actives. Sans
  systemd (développement), le comportement CLI historique est conservé.

### 4.17 État global

#### `synapse status`
- **Rôle** : état global en une vue : serveur (PID, version, uptime,
  requêtes), web (port, sessions), passerelle A2A (agent, port),
  organisations (nombre d'actives), sauvegardes récentes.
- **Déclenche** : `server status` + `web status` + état A2A + `org list`
  (agrégés).
- **Options** : `--json`.
- **Exemple** : `synapse status --json`
- **Passerelle A2A** : état `stopped` = légitime (elle est optionnelle,
  provisionnée par la présence des secrets d'agent) ; seul `degraded`
  (PID vivant, HTTP muet) signale une anomalie.

### 4.18 Version installée

#### `synapse --version` / `synapse version`
- **Rôle** : affiche la version installée du paquet (source unique :
  `importlib.metadata` — SPEC_PRODUCTION §5). Ne nécessite ni serveur ni
  configuration.
- **Exemple** : `synapse --version` → `3.1.0`
- **Complément** : la même version est inscrite dans les fichiers PID et
  affichée par `server status` / `update check`.

---

## 5. Règles transverses de fonctionnement

1. **Ordre des contrôles au démarrage** (`server start` / `web start`) :
   config valide → répertoires créés (permissions 0700/0600) → verrou
   d'exclusion (pas de double démarrage) → écriture du PID → démarrage.
   Tout échec produit un message explicite et un code de sortie non nul.
2. **Arrêt propre** : SIGTERM → attente max 15 s → retrait du PID, du
   socket et du jeton ; SIGKILL seulement avec `--force`.
3. **Jamais de mot de passe en argument** : les mots de passe (org, agent,
   rotations) passent par `getpass` ou `--password-stdin`. Le CLI refuse
   explicitement tout argument ressemblant à un mot de passe en clair.
4. **Sortie humaine par défaut** : colonnes alignées, couleurs si tty ;
   `--json` force une sortie JSON machine (utile pour la supervision).
5. **Messages en français** ; l'aide (`-h`/`--help`) documente chaque
   commande avec un exemple.
6. **Le CLI n'écrit jamais directement dans la base** : tout passe par
   l'API socket (principe P1 de SPEC-WEB), sauf les opérations locales
   explicitement prévues (init d'org, enable, backup/restore, doctor).
7. **Comptabilité avec le jeton local** : une commande qui peut être servie
   par le jeton local (même poste) ne demande aucun mot de passe — y
   compris les commandes de gestion (org, agents, politiques) qui, via le
   web, sont déjà accessibles sans mot de passe.

---

## 6. Migration depuis les points d'entrée actuels

| Ancien point d'entrée | Nouvelle commande | Statut |
|---|---|---|
| `synapse-server` | `synapse server start` | alias déprécié |
| `synapse-web` | `synapse web start` | alias déprécié |
| `synapse-init-org` | `synapse org init` | alias déprécié |
| `synapse-backup` | `synapse backup create` | alias déprécié |
| `synapse-restore` | `synapse backup restore` | alias déprécié |
| `synapse-a2a-bridge` | `synapse a2a start` | alias déprécié |
| `synapse <commande API>` (client brut actuel) | `synapse api <commande>` | remplacé |

Les 6 binaires actuels deviennent des **alias dépréciés** : ils appellent le
CLI unifié et affichent un avertissement « déprécié, utilisez `synapse …` ».
Ils seront retirés à la prochaine version majeure (décision §7.1).

---

## 7. Décisions d'architecture (5 points tranchés)

### Décision 1 — Rétrocompatibilité des binaires actuels
**Choix** : conserver les 6 binaires comme **alias dépréciés** (délégation au
CLI unifié + avertissement), retrait à la prochaine version majeure.

**Analyse** : (a) suppression immédiate = rupture totale, scripts existants
cassés sans message clair ; (b) alias = transition douce, un seul code à
maintenir, avertissement visible pour migrer ; (c) coexistence indéfinie =
deux façons de faire, dérive documentaire.

**Justification** : l'alias préserve l'expérience utilisateur (rien ne
casse), garde un seul point de code (maintenabilité), et l'avertissement
pousse à la migration sans contrainte. Cohérent avec les pratiques
standards (CLIs systèmes gardent des alias de compatibilité).

### Décision 2 — `synapse` sans argument
**Choix** : `synapse` nu = `synapse server start`, et `server start` est
**idempotent** (déjà démarré → message clair, code 0).

**Analyse** : (a) afficher l'aide = sûr mais inutile en prod (deux frappes
pour démarrer) ; (b) `server start` directement = demande explicite de
l'utilisateur, pratique pour systemd/docker ; risque « démarrage accidentel »
neutralisé par l'idempotence ; (c) message d'erreur « serveur non démarré » =
pédagogique mais agaçant en script.

**Justification** : la demande utilisateur est explicite, l'idempotence
élimine le seul risque réel (double démarrage), et le comportement est
identique à `systemctl start` (familier). L'aide reste disponible via
`synapse --help` / `synapse help`.

### Décision 3 — Identification par défaut
**Choix** : le CLI utilise le **jeton de confiance local** (aucun mot de
passe) pour toute opération d'administration servie par le web local ; les
identifiants (`--my-name` / `--organization-name` + stdin) ne sont requis
que pour les actions « en tant que compte » (messagerie d'un agent précis,
file de travail, rotations de mots de passe).

**Analyse** : (a) toujours demander un mot de passe = sûr mais pénible et
contradictoire avec le web (qui n'en demande plus) ; (b) toujours le jeton =
impossible pour les actions « en tant que compte » (l'identité compte) ;
(c) hybride (choix retenu) = sans secret pour l'administration, identité
explicite quand le compte compte.

**Justification** : cohérence totale avec l'interface web (même mécanisme,
même périmètre de confiance), aucun mot de passe dans l'historique du
shell, et le périmètre du jeton reste strictement celui défini en
SPEC-WEB (jamais pour les comptes agents).

### Décision 4 — État des processus (PID files)
**Choix** : écrire `run_dir/synapse.pid` et `run_dir/web.pid` (PID +
horodatage + version) au démarrage, les retirer à l'arrêt propre ;
`status` fait une **double vérification** (PID vivant ET socket/HTTP
répond).

**Analyse** : (a) sans PID files, `stop`/`status` devraient scanner les
processus par nom (fragile, faux positifs) ; (b) PID files seuls = PID
réutilisé possible après crash ; (c) PID + vérification par socket
(choix retenu) = fiable et simple.

**Justification** : standard de l'industrie, robuste aux PID réutilisés,
permet un `stop` propre et un `status` honnête (détection de l'état
« dégradé » : PID vivant mais socket mort). Le run dir est déjà le lieu
d'état du service (socket, jeton) — cohérent.

### Décision 5 — Profondeur des sous-commandes
**Choix** : **maximum 2 niveaux** (`synapse <groupe> <action>`). Les cas qui
auraient demandé un 3e niveau sont résolus par des **verbes composés** ou
des **options** : `agent create-observer` / `revoke-observer` / `observers`,
`group add-member` / `remove-member` / `members`,
`agent card --set` (option d'écriture), `policy escalation --set`.

**Analyse** : (a) 3 niveaux (`group members add`) = arbre profond, tabulation
lourde, mémoire ; (b) 2 niveaux stricts avec verbes composés (choix retenu)
= arbre plat, auto-complétion simple, chaque commande reste une phrase
lisible ; (c) tout en options = perte de découvrabilité.

**Justification** : simplicité et évolutivité : ajouter une action ne fait
jamais grandir l'arbre au-delà de 2 niveaux, et les noms composés
(`create-observer`) restent explicites. Cohérent avec des CLIs éprouvés
(`docker`, `git`).

---

## 8. Évolutivité

1. **Nouvelles commandes API** : immédiatement utilisables via
   `synapse api <commande>` — aucun changement du CLI requis. L'habillage
   ergonomique (groupe dédié) s'ajoute ensuite au fil des besoins.
2. **Nouveaux groupes** : un groupe = un sous-parser ; ajouter un groupe ne
   touche pas les autres (architecture par modules).
3. **Plugins/externes** : le groupe `api` est la porte d'intégration
   standard ; un futur groupe `plugin` pourrait charger des sous-commandes
   externes sans modification du cœur.
4. **Sortie `--json`** sur toutes les lectures : la supervision et les
   scripts ne dépendent jamais du format texte.

---

## 9. Hors périmètre (volontairement non couvert)

- **Interface humaine de messagerie en CLI** : le CLI reste un outil
  d'administration et d'automatisation ; la messagerie interactive est
  l'interface web (le CLI envoie/lit des messages, pas de TUI conversationnel).
- **Gestion des clés/secret management externe** : les mots de passe passent
  par stdin ; l'intégration à un vault (Hashicorp, pass) peut être ajoutée
  plus tard via `--password-stdin` (le CLI lit déjà depuis un pipe).
- **Multi-instance orchestrée** : pas d'orchestration (docker-compose,
  systemd units) dans ce document — le CLI fournit les briques
  (`start`/`stop`/`status`) que l'orchestrateur appelle.

---

## 10. Implémentation (rappel des briques existantes)

- `synapse/server.py:main` → `server start` (déjà fonctionnel).
- `synapse/web.py:web_main` → `web start` (déjà fonctionnel).
- `synapse/install.py:org_init_main` → `org init` / `org enable`
  (déjà fonctionnel).
- `synapse/backup.py:backup_main/restore_main` → `backup create/restore`.
- `synapse/a2a_bridge.py:bridge_main` → `a2a start`.
- `synapse/cli/` (package) → CLI unifié complet (implanté, 2026-08-06) :
  `synapse api <commande>` remplace l'ancien `synapse/cli.py` (client plat,
  supprimé) ; les 6 binaires historiques sont des alias dépréciés
  (`synapse/cli/aliases.py`).

La structure d'implémentation : un package `synapse/cli/` (modules par
groupe) avec un `main()` racine qui assemble les sous-parsers, et un
module `synapse/cli/common.py` pour l'authentification (jeton local vs
identifiants), les PID files et les codes de sortie. Écarts volontaires
documentés dans `docs/specs/SPEC_CLI_ECARTS.md`.
