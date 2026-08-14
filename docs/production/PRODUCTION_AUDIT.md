# Audit de production — Synapse (A2A)

- **Date** : 2026-08-07
- **Périmètre** : audit complet du dépôt `/home/baron/Projects/A2A` — documentation,
  code, tests, configuration, déploiement — pour vérifier l'état « prêt pour la
  production ».
- **Méthode** : lecture intégrale de la documentation (SPEC.txt, SPEC_WEB.txt,
  docs/*.md), comparaison document ↔ code, exécution de la suite complète,
  audit technique (sécurité, fiabilité, performance, tests), audit de cohérence
  des compteurs et contrats.
- **Document de suivi** : [`docs/production/PRODUCTION_PROGRESS.md`](PRODUCTION_PROGRESS.md)
  (checklist par problème, preuves, état).

Ce document est le **plan directeur de l'intervention** : chaque problème est
tracé jusqu'à sa résolution et sa validation.

---

## Contexte — état du dépôt au début de l'audit

- HEAD : `4b6e7a3` (main), tag `v3.1.0` posé (SPEC_PRODUCTION implémenté et
  déployé le 2026-08-07).
- Suite complète exécutée (2026-08-07) : **5 tests en échec** (4
  `test_update_apply` + 1 `test_webui_dom_harness`).
- 8 processus résiduels de démonstration tournaient depuis le 2026-08-06
  (4 serveurs + 4 web sur `/tmp/synapse-demo-*`), en contradiction avec
  l'exigence « zéro processus résiduel » (rapport final de la mission
  prompt1, archivé dans l'historique git).

---

## Synthèse des problèmes identifiés

| ID | Gravité | Problème | État |
|---|---|---|---|
| AUDIT-001 | HIGH | 5 tests en échec sur la machine de production (détection systemd réelle + course au démarrage du web) | ✅ Corrigé |
| AUDIT-002 | HIGH | CLI `agent status` : affichage de la réputation cassé (contrat `score`/`total_reviews`/`reputation` inexistant côté serveur) | ✅ Corrigé |
| AUDIT-003 | MEDIUM | `set_escalation_policy` : `null` converti en `0` par la validation → escalade immédiate de toutes les tâches (au lieu d'« aucune limite » ou d'un rejet) | ✅ Corrigé |
| AUDIT-004 | LOW | Écarts `helpdoc` ↔ code : `get_org_agents` renvoie `reputation` non documenté ; `get_org_snapshot` documenté « réservé observateurs » mais ouvert aux humains | ✅ Corrigé |
| AUDIT-005 | LOW | `test_errors.py` : le test d'atteignabilité ne couvre que les 12 codes v1 (les 5 codes v3 ne sont pas dans le test global) | ✅ Corrigé |
| AUDIT-006 | MEDIUM | Whitelist observateur (`_OBSERVER_READ_COMMANDS`) maintenue à la main, sans test la reliant aux vraies commandes de lecture | ✅ Corrigé |
| AUDIT-007 | LOW | Compteurs documentaires obsolètes : « 64 commandes » (SPEC_CLI.md, CHECKLIST_SPEC_WEB.md, SKILLS_AGENT) au lieu de 65 | ✅ Corrigé |
| AUDIT-008 | LOW | `docs/production/TESTING.md` référence `/home/baron/Projects/Synapse` (mauvais chemin) | ✅ Corrigé |
| AUDIT-009 | LOW | Processus résiduels de démo (8) en contradiction avec « zéro processus résiduel » | ✅ Nettoyé |
| AUDIT-010 | INFO | Audit post-action non atomique (V7 documenté) : `_audit_action` dans une transaction séparée après le handler | Documenté (risque résiduel assumé, voir §10) |
| AUDIT-011 | INFO | Course IntegrityError idempotence groupe/tâche identifiée par l'audit — **faux positif** : tout est dans `begin_immediate` sérialisé par le verrou applicatif | Vérifié, non déclenchable |
| AUDIT-012 | INFO | Rate-limit web par nom d'organisation (pas par IP) — limité au modèle de menace localhost | Documenté (assumé) |
| AUDIT-013 | INFO | `get_org_structure` cap 10 000 agents ; `list_org_conversations` agrégation complète par page | Documenté (limite assumée) |

---

## Détail des problèmes

### AUDIT-001 — Tests en échec sur la machine de production (HIGH)

**Description.** La suite complète échouait sur 5 tests :

1. `test_update_apply.py` (4 tests) : `_systemd_unit_exists()` détecte les
   **vraies unités systemd** de la machine de production (cette machine a
   `install.sh` exécuté) et bascule `update apply` sur `systemctl`, qui ne
   connaît pas les services temporaires des tests.
2. `test_webui_dom_harness.py` : le web de test est lancé **immédiatement**
   après le serveur ; `_require_server` (SPEC_CLI §4.3) exige le socket prêt,
   or le serveur n'a pas encore créé le socket → le web meurt avec le code 3
   (« service local non prêt ») et le port n'est jamais ouvert.

**Fichiers concernés.** `synapse/cli/update.py`, `tests/cli_helpers.py`,
`tests/test_update_apply.py`, `tests/test_webui_dom_harness.py`.

**Solutions possibles.**

- A. Masquer les unités systemd dans les tests (variable d'échappement).
- B. Ne pas détecter systemd du tout (régression du comportement production).
- C. Rendre les tests indifférents à l'environnement (attente du socket).

**Solution retenue.** A + C :

- Nouvelle variable `SYNAPSE_NO_SYSTEMD=1` honorée par les helpers systemd de
  `update.py` (force le mode CLI — tests et développement) ;
- posée dans `tests/cli_helpers.py` (tous les tests CLI simulent un
  environnement sans systemd) ;
- `test_update_apply_systemd_mode` la retire (le faux `systemctl` sur le PATH
  EST la simulation) ;
- `test_webui_dom_harness.py` : ajout de `_wait_socket()` qui attend le socket
  Unix du serveur avant de lancer le web (élimine la course).

**Pourquoi c'est la meilleure.** La variable est un échappatoire standard,
documentée dans OPERATIONS.md et SPEC_PRODUCTION.md ; l'attente du socket
corrige le vrai bug de robustesse du test (course au démarrage).

**Modifications.** `synapse/cli/update.py` (4 helpers), `tests/cli_helpers.py`,
`tests/test_update_apply.py`, `tests/test_webui_dom_harness.py`,
`docs/production/OPERATIONS.md`, `docs/production/SPEC_PRODUCTION.md`.

**Testée par.** `tests/test_update_apply.py` (9 tests), `tests/test_webui_dom_harness.py`,
`tests/test_unit_cli_install_server.py`, `tests/test_production_ops.py`,
`tests/test_systemd_units.py`, `tests/test_sd_notify.py` — tous verts.

**Critère de résolution.** Suite complète verte sur la machine de production.

---

### AUDIT-002 — CLI `agent status` : affichage de la réputation cassé (HIGH)

**Description.** `synapse agent status <agent>` lit `reputation["reputation"]["score"]`
et `["total_reviews"]` — un contrat **inexistant** côté serveur. Le serveur
renvoie pour soi `{username, completed, failed, canceled, active, completion_rate}`
et pour les autres `{username, qualitative}` (plat, sans clé `reputation`).
Résultat : l'affichage « réputation score — (0 avis) » est toujours vide, et la
mention qualitative/completion_rate n'est jamais affichée.

**Fichiers concernés.** `synapse/cli/agent.py` (`_cmd_status`),
`synapse/service.py` (`_reputation_summary`, `_agent_get_agent_reputation`).

**Solutions possibles.**

- A. Corriger le CLI pour lire le contrat réel (plat).
- B. Faire renvoyer par le serveur le contrat attendu par le CLI (changement
  d'API, incompatible avec la spec F16).

**Solution retenue.** A — le CLI affiche le contrat réel :

- soi-même : `completion_rate`, compteurs (completed/failed/canceled/active) ;
- les autres : mention `qualitative` ;
- sortie `--json` inchangée (elle renvoie déjà la réponse brute du serveur).

**Pourquoi.** La spec F16 documente le contrat serveur (détail pour soi,
qualitatif pour les autres) ; c'est le CLI qui s'est écarté. Aucun changement
d'API.

**Testée par.** Nouveau test CLI `test_unit_cli_install_server.py` (ou dédié) :
`agent status --json` reflète la réponse serveur.

**Critère de résolution.** `synapse agent status X` affiche la réputation réelle.

---

### AUDIT-003 — `set_escalation_policy` : `null` → escalade immédiate (MEDIUM)

**Description.** Dans `validation.py`, les seuils `due_after_seconds` et
`failed_after_seconds` utilisent `lambda v: validate_budget(v) or 0`. Le
`or 0` transforme `None` (valeur valide « aucune limite » pour les budgets)
en `0`. Conséquence : un client API envoyant `null` déclenche
`now_utc_offset(0)` = maintenant → **toutes** les tâches non terminées en
retard/échec sont escaladées immédiatement à chaque écriture. Le helpdoc
documente « >= 1 » : `null` n'est pas une valeur documentée.

**Fichiers concernés.** `synapse/validation.py` (ligne ~1038).

**Solutions possibles.**

- A. Rejeter `null` (INVALID_ARGUMENT) — cohérent avec le helpdoc « >= 1 ».
- B. Accepter `null` comme « seuil désactivé » avec une sémantique propre.

**Solution retenue.** A — les deux seuils sont des entiers requis >= 1 ;
`null` → `INVALID_ARGUMENT`. Le CLI et les tests existants passent des entiers
>= 1 (aucun impact). La sémantique « désactivé » reste exprimable via
`enabled=false` (le champ existe).

**Pourquoi.** Le helpdoc documente « >= 1 » ; `enabled=false` est le mécanisme
officiel de désactivation. Rejeter `null` ferme le comportement dangereux sans
nouvelle sémantique.

**Testée par.** Test de validation dédié : `set_escalation_policy` avec
`due_after_seconds=null` → `INVALID_ARGUMENT`.

**Critère de résolution.** Aucun chemin API ne peut stocker `0` dans les seuils.

---

### AUDIT-004 — Écarts helpdoc ↔ code (LOW)

**Description.**

1. `helpdoc.py` documente `get_org_agents` → `{agents: [{username, description,
   status, can_see_org_agents}]}` sans le champ `reputation` que le serveur
   renvoie réellement (détail par agent, F16).
2. `helpdoc.py` documente `get_org_snapshot` « Réservée aux comptes observateurs »
   alors que le handler accepte aussi les comptes humains (SPEC-WEB).

**Fichiers concernés.** `synapse/helpdoc.py`.

**Solutions possibles.**

- A. Mettre le helpdoc en cohérence avec le code.
- B. Retirer `reputation` de la réponse serveur (régression fonctionnelle).

**Solution retenue.** A — le helpdoc reflète la réponse réelle (reputation
dans get_org_agents ; snapshot accessible aux humains et observateurs).

**Critère de résolution.** `help()` cohérent avec les réponses réelles
(vérifié par `test_help.py`).

---

### AUDIT-005 — Test d'atteignabilité des codes d'erreur incomplet (LOW)

**Description.** `tests/test_errors.py::test_all_error_codes_reachable` ne
couvre que les 12 codes v1. Les 5 codes v3 (`TASK_NOT_FOUND`,
`TASK_STATE_INVALID`, `TASK_DEPENDENCY_NOT_MET`, `QUOTA_EXCEEDED`,
`GROUP_NOT_FOUND`) sont testés individuellement ailleurs, mais pas dans le
test global d'atteignabilité. `ALL_ERROR_CODES` (17) existe déjà.

**Fichiers concernés.** `tests/test_errors.py`.

**Solution retenue.** Étendre le test pour atteindre les 5 codes v3 via des
scénarios réels (tâche invisible, transition invalide, dépendance non
terminée, budget dépassé, groupe hors membership).

**Critère de résolution.** `seen == ALL_ERROR_CODES` (17 codes) sans
`INTERNAL_ERROR` (filet défensif, testé séparément).

---

### AUDIT-006 — Whitelist observateur sans garde-fou de test (MEDIUM)

**Description.** `_OBSERVER_READ_COMMANDS` (19 commandes) est la liste des
lectures autorisées à un compte observateur. Elle est maintenue à la main ;
aucun test ne vérifie qu'elle couvre exactement les commandes de lecture
réelles. Une future commande de lecture ajoutée à `_AGENT_HANDLERS` sans
mise à jour de la whitelist deviendrait silencieusement exécutable par un
observateur.

**Fichiers concernés.** `synapse/service.py`, tests.

**Solution retenue.** Ajouter un test qui verrouille : (a) chaque commande de
la whitelist existe dans `COMMAND_SPECS` ; (b) la whitelist est un sous-ensemble
des commandes non-organisation, non-humaines ; (c) un observateur ne peut
exécuter aucune commande hors whitelist (déjà testé par
`test_observers_web::test_observer_writes_denied` — renforcé pour couvrir
chaque commande de `_AGENT_HANDLERS` hors whitelist).

**Critère de résolution.** Le test échoue si une commande de lecture est
ajoutée sans être whitelistée.

---

### AUDIT-007 — Compteurs documentaires « 64 commandes » (LOW)

**Description.** L'API compte **65 commandes** (vérifié : `len(COMMAND_SPECS)
== 65`, testé par `test_compliance.py`). Trois documents disent encore
« 64 » :

- `docs/specs/SPEC_CLI.md` l.28 (« habillage ergonomique des 64 commandes API ») ;
- `docs/webui/CHECKLIST_SPEC_WEB.md` l.5 et l.44 ;
- `SKILLS_AGENT/synapse/references/api-reference.md` l.1.

**Fichiers concernés.** `docs/specs/SPEC_CLI.md`, `docs/webui/CHECKLIST_SPEC_WEB.md`,
`SKILLS_AGENT/synapse/references/api-reference.md`.

**Solution retenue.** Corriger les compteurs en « 65 ». Les rapports
historiques (RAPPORT_V3, RAPPORT_SECURITE_PHASE3, VERIFICATION_PHASE1,
PROVENANCE, RAPPORT_FINAL_P5) sont des documents datés qui décrivent l'état à
leur époque : non modifiés (ils documentent l'évolution).

**Critère de résolution.** Aucun document « vivant » (specs, README, guides)
ne contient de compteur périmé.

---

### AUDIT-008 — TESTING.md chemin obsolète (LOW)

**Description.** `docs/production/TESTING.md` l.6 référence `/home/baron/Projects/Synapse`
au lieu de `/home/baron/Projects/A2A`.

**Solution retenue.** Corriger le chemin.

---

### AUDIT-009 — Processus résiduels de démo (LOW)

**Description.** 8 processus orphelins (4 `synapse-server` + 4 `synapse-web`)
tournaient depuis le 2026-08-06 sur des configs `/tmp/synapse-demo-*`
(redesign, p7, v3, fresh). Contradiction avec « zéro processus résiduel »
(RAPPORT_FINAL_P5) ; consommation de ressources et risques d'interférence.

**Solution retenue.** Arrêt propre des 8 processus (déjà fait). Les
répertoires `/tmp/synapse-demo-*` sont conservés (données de démo
régénérables).

**Critère de résolution.** `ps aux | grep synapse` ne montre aucun processus
de démo résiduel.

---

### AUDIT-010 — Audit post-action non atomique (V7) — documenté, non corrigé (INFO)

**Description.** `_audit_action` (service.py) écrit l'audit des commandes
simples (send_message, read_message, create_agent…) dans une **transaction
séparée** après la transaction du handler. Un crash entre les deux commits
laisse une action sans trace d'audit. Les commandes de coordination (tâches,
groupes, délégations) s'auditent déjà dans leur propre transaction.

**Analyse approfondie.** Rendre l'audit atomique exigerait de restructurer
tous les handlers de commandes simples pour recevoir l'audit dans leur
transaction — un refactor à risque (RAPPORT_AUDIT_SECURITE_2026 §9 V7 l'a
déjà écarté). L'impact est limité : l'audit est append-only et sans contenu ;
une action sans audit n'est pas une fuite de confidentialité ni une atteinte
à l'intégrité.

**Décision.** Documenté comme risque résiduel assumé (cohérent avec
RAPPORT_AUDIT_SECURITE_2026 §9.2). Non corrigé dans cette passe — le coût de
refactor dépasse le bénéfice pour un système local mono-processus. Revoir si
une exigence de conformité stricte émerge.

---

### AUDIT-011 — Course IntegrityError idempotence groupe/tâche — faux positif (INFO)

**Description.** L'audit technique a signalé des courses d'idempotence non
protégées dans `_agent_send_group_message` et `_agent_create_task`
(IntegrityError → INTERNAL_ERROR). **Vérification approfondie : non
déclenchable.** Les deux handlers font le check d'idempotence ET l'INSERT dans
la même transaction `begin_immediate`, sérialisée par le verrou applicatif
global `_WRITE_LOCK` (db.py) : un seul écrivain à la fois. Le `try/except
IntegrityError` de `_send_message` est un filet défensif supplémentaire,
cohérent avec le pattern historique, sans cas de déclenchement.

**Décision.** Aucune correction. Noté pour éviter une future réintroduction.

---

### AUDIT-012 — Rate-limit web par nom d'organisation (INFO)

**Description.** Le verrouillage de connexion web (5 échecs → 429) est
compté **par nom d'organisation** en mémoire (web.py), pas par IP. Un
attaquant local peut tenter des noms d'org différents sans verrouillage.
Cohérent avec le modèle de menace : le web n'écoute que sur 127.0.0.1 et le
contrôle anti-agent réel est la possession du mot de passe d'org / du jeton
local (SPEC-WEB §9-I9).

**Décision.** Assumé et documenté (SPEC-WEB R6.3/I8). Non corrigé.

---

### AUDIT-013 — Bornes de lecture sur gros volumes (INFO)

**Description.**

1. `get_org_structure` : `limit=10_000` agents (cap dur, documenté
   RAPPORT_AUDIT_SECURITE_2026 §9.4).
2. `list_org_conversations` : agrégation `GROUP BY` sur tous les messages de
   l'organisation à chaque page (sans LIMIT interne).

**Décision.** Accepté pour le modèle « organisation de taille raisonnable » ;
à réévaluer si une organisation atteint des volumes très importants
(100k+ messages). Documenté.

---

## Ordre d'implémentation

1. AUDIT-001 (tests en échec) — déjà fait, vérifié.
2. AUDIT-009 (processus résiduels) — déjà fait.
3. AUDIT-002 (CLI agent status) — correction de code + test.
4. AUDIT-003 (validation escalation) — correction de code + test.
5. AUDIT-004 (helpdoc) — correction de doc.
6. AUDIT-005 (test_errors 17 codes) — test.
7. AUDIT-006 (whitelist observateur) — test.
8. AUDIT-007/008 (compteurs et chemins) — doc.
9. Suite complète + validation finale.

## Critères de fin (rappel de la mission)

- Toutes les fonctionnalités prévues sont implémentées et cohérentes avec la
  documentation ;
- les problèmes critiques (HIGH) sont corrigés ;
- les problèmes de sécurité identifiés sont traités ou documentés comme
  risques résiduels assumés ;
- les tests couvrent les fonctionnalités importantes ;
- aucune régression connue ne subsiste (suite complète verte) ;
- chaque affirmation est accompagnée d'une preuve (test, commande, capture).
