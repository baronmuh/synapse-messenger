# Sécurité

## Modèle de menace

Le système protège contre :

- la **lecture non autorisée** de messages par un autre agent ou par une
  organisation (aucune commande d'organisation n'expose le contenu) ;
- la **falsification** (identité, messages, curseurs de pagination) ;
- la **fuite de secrets** (mots de passe, contenus) dans le stockage, les
  journaux, les erreurs, les sauvegardes ou les arguments de processus ;
- l'**énumération** de comptes, messages ou conversations ;
- les **attaques par chronométrage** (enumeration des comptes via Argon2) ;
- le **déni de service** (requêtes géantes, force brute) ;
- l'**accès au stockage** par d'autres comptes système.

## Garanties

### Authentification

- Vérifiée dans chaque commande, avant toute opération métier — pour les
  agents (`my_name_auth` / `my_password_auth`) et pour les organisations
  (`organization_name_auth` / `organization_password_auth`).
- Compte inconnu, mot de passe erroné ou compte désactivé →
  `AUTH_FAILED` identique (aucune distinction exploitable), avec
  **vérification leurre Argon2id** pour égaliser le chronométrage. Un nom
  d'organisation inexistant provoque également `AUTH_FAILED` ; un agent
  qui tente de s'authentifier comme organisation provoque `ACCESS_DENIED`.
- Limitation : 5 échecs par principal (nom d'utilisateur **ou** nom
  d'organisation, comptés séparément — préfixe `org:`) dans une fenêtre
  glissante de 15 minutes (persistée en base ; un redémarrage ne
  réinitialise pas le compteur). Les tentatives refusées ne sont pas
  re-journalisées (pas de blocage permanent).

### Mots de passe

- **Argon2id** — mémoire 64 MiB, 3 itérations, parallélisme 1, sel
  aléatoire unique (paramètres fixés par la spécification, non
  configurables).
- Jamais en clair dans le stockage, les réponses, les erreurs, les logs ou
  les sauvegardes (les sauvegardes ne contiennent que des hash, et sont
  chiffrées).
- Jamais en argument de commande, dans l'historique du shell ou en
  variable d'environnement : lecture sur stdin uniquement (`getpass` ou
  `--password-stdin`).

### Contrôle d'accès

- `agent` : agit uniquement en son propre nom et dans son organisation ;
  ses lectures sont limitées à ses messages reçus et ses conversations ;
  une commande d'organisation → `ACCESS_DENIED` ; la découverte est
  limitée à sa propre organisation (`list_org_agents`, permission
  `can_see_org_agents`, défaut `false`).
- `organisation` : gestion de ses propres agents uniquement — **aucun**
  accès au contenu des messages, conversations ou notifications (aucune
  commande d'organisation ne le retourne). Un compte d'une autre
  organisation est indistinguable d'un compte inexistant
  (`USER_NOT_FOUND`).
- Communication externe : soumise aux politiques des deux organisations
  (`allow_outgoing_external` de l'expéditeur, `allow_incoming_external`
  du destinataire), évaluées à l'envoi ; refus → `POLICY_DENIED`, retourné
  **avant** révélation d'information sur le destinataire (une organisation
  fermée ne permet pas d'énumérer les usernames d'une autre). Les messages
  existants restent accessibles après tout changement de politique.
- Désactivation : le compte ne peut plus s'authentifier, envoyer ni lire ;
  ses données sont conservées (statuts inchangés) et redeviennent
  accessibles après réactivation. Les organisations sont permanentes
  (jamais désactivées ni supprimées).
- Non-divulgation : message ou conversation inaccessible →
  `MESSAGE_NOT_FOUND` / `CONVERSATION_NOT_FOUND` (indiscernable d'un
  objet inexistant).
- Départements (F13/F14) : `list_department_tasks` est scopée à
  l'organisation du manager — un département homonyme d'une autre
  organisation ne révèle ni ses tâches ni ses membres.
- Groupes (F15) : seul le créateur du groupe retire les autres membres ;
  l'auto-retrait reste possible. Un groupe dont l'appelant n'est pas
  membre est indiscernable d'un groupe inexistant (`GROUP_NOT_FOUND`).
- Budgets (F9) : le quota de messages horaire s'applique aux messages
  directs **et** de groupe (`QUOTA_EXCEEDED`).

### Transport et stockage

- Socket Unix uniquement (aucun port réseau) ; socket `0600` dans un
  répertoire `0700` ; stockage, journaux et clés `0600`/`0700`, propriété
  du compte système du service.
- Limite stricte de 1 MiB par requête, rejet avant authentification.
- Requêtes SQL toutes paramétrées (aucune injection possible) ; aucun
  chemin n'est dérivé d'une entrée client.

### Interfaces locales (F18 web / F20 A2A)

- L'interface web (127.0.0.1:8080) et la passerelle A2A (127.0.0.1:8090)
  sont des **interfaces de boucle locale** (jamais exposées hors de la
  machine) et exigent une authentification :
  - **Web** : jeton de confiance local (`web_token`, fichier 0600 du run
    dir, écrit par le serveur au démarrage, retiré à l'arrêt) ; l'identité
    `_web_local` est limitée strictement à `list_orgs` ; les humains se
    connectent par sélection d'organisation + session HttpOnly
    SameSite=Strict (TTL 15 min, 3 sessions max par org, verrouillage 429
    par org). Compromis documenté (SPEC.txt §20.7) : tout processus du
    même utilisateur lisant le jeton peut agir comme le web.
  - **A2A** : jeton d'accès fourni au démarrage via stdin (jamais en
    argument ni en environnement — le wrapper systemd le lit depuis
    `/etc/synapse/secrets/`, fichiers 0600), exigé sur toutes les routes
    (en-tête `X-Synapse-Token`, comparaison en temps constant) : sans
    jeton valide, la requête est refusée (401).
- Les jetons sont distincts des mots de passe Synapse : ils protègent
  l'accès aux métadonnées de l'organisation (web) et aux opérations de
  tâches de l'agent exposé (A2A). Leur compromission est révoquée en
  redémarrant l'interface (nouveau jeton).

### Intégrité et cryptographie

- Messages immuables : aucune commande ne modifie ni ne supprime un
  message (seul `read_at` évolue, par le destinataire).
- Curseurs de pagination signés HMAC-SHA256 (clé persistante, hors des
  données) : toute falsification → `INVALID_ARGUMENT`.
- Sauvegardes AES-256-GCM, nonce aléatoire, clé conservée **hors** des
  sauvegardes ; restauration vérifiée (authentification + intégrité SQLite)
  et refusée si le service tourne.

### Journalisation

- Champs autorisés uniquement : `username`, `command`, `target_id`,
  `timestamp`, `result`, `process_id`. Jamais de mot de passe, de contenu
  ou de paramètre sensible.
- JSON-lines, rotation quotidienne, rétention 90 jours puis suppression
  automatique.

## Opérations sensibles

| Opération | Précaution |
|---|---|
| Première organisation | `synapse org init <nom>` sous le compte du service ; refuse un nom déjà pris ; mot de passe sur stdin ; les organisations suivantes sont créées par la même procédure locale (jamais par l'API) |
| Sauvegarde | `synapse backup create` (ou timer systemd quotidien) ; **conserver `backup.key` hors des sauvegardes** (copie 0640 root:synapse dans `/etc/synapse/backup.key.vault` + coffre séparé) |
| Restauration | service arrêté ; `synapse backup restore` avec `--force` explicite ; vérifie intégrité et clé |
| Supervision | moniteur périodique (5 min) écrivant `monitor.json` + `alert_command` ; journaux dans `/var/log/synapse` (`synapse.log` / `synapse.error.log`) |

## Audits de sécurité réalisés

- **Audit sécurité 2026 (v3.0.0)** : 4 vulnérabilités réelles + 1 variante
  (fuite inter-organisations `list_department_tasks`, contournement des
  politiques via groupes, budget F9 via `send_group_message`, web/A2A
  accessibles sans authentification, retrait de membre abusif) — toutes
  **corrigées avec tests de régression** (`test_security_fixes.py`,
  `test_security_audit_2026.py`). Deux faiblesses assumées sans
  correction : canal temporel du cache d'authentification (F1) et audit
  post-action non atomique avec l'action.
- **Audit de production (2026-08-07)** : voir
  `docs/production/PRODUCTION_AUDIT.md` (13 problèmes tracés, 3 bugs
  corrigés, compteurs alignés).
- **Verdict** : le système est fortement sécurisé pour son modèle de
  menace (système local mono-compte, données accessibles au seul compte
  service) ; il n'existe pas de garantie mathématique de sécurité — les
  contrôles vérifiés et les risques résiduels sont listés dans les
  documents ci-dessus.
