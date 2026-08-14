# Tests

## Exécution

```bash
cd /home/baron/Projects/A2A
.venv/bin/python -m pytest tests/ -q                 # suite complète (séquentiel)
.venv/bin/python -m pytest tests/ -q -n 3            # suite complète (3 workers)
.venv/bin/python -m pytest tests/test_auth.py -q     # un groupe
```

- **Suite complète : 977 tests, ~5-6 min en parallèle (`-n 3`)** sur la
  machine de référence (4 cœurs) ; ~16 min en séquentiel. Les durées et
  les optimisations sont documentées dans
  [`docs/perf/TEST_PERFORMANCE.md`](../perf/TEST_PERFORMANCE.md).
- Chaque test s'exécute sur un stockage/socket **temporaires isolés**
  (`tmp_path`) : rien n'est écrit hors du répertoire de test, aucun compte
  système requis.
- **Hacheur Argon2id rapide** installé par `tests/conftest.py` pour toute
  la session (un test dédié, `test_unit_security_config.py`, vérifie que
  les paramètres de production restent intacts). Les benchmarks de
  performance, eux, utilisent Argon2id de production.
- **Isolation des ports** : les tests CLI posent `SYNAPSE_WEB_PORT` /
  `SYNAPSE_A2A_PORT` sur des ports libres aléatoires (helpers
  `tests/cli_helpers.py`) ; les tests web en processus utilisent
  `port=0` (port alloué par l'OS). La suite passe donc **même avec la
  production en marche** (conflits 8080/8090 éliminés).
- **`SYNAPSE_NO_SYSTEMD=1`** est posé par les helpers de test : `update
  apply` reste en mode CLI pendant les tests, même sur une machine où les
  unités systemd réelles existent.
- Le harnais DOM (`tests/test_webui_dom_harness.py`) est **sauté**
  automatiquement si node/jsdom ne sont pas disponibles.

## Organisation (65 fichiers)

### Socle API v2 (messagerie)

| Fichier | Couverture |
|---|---|
| `test_validation.py`, `test_unit_validation.py`, `test_unit_validation_v3.py` | enveloppe, clés exactes, types, taille 1 MiB, noms, mots de passe, NFC/White_Space, `client_message_id`, UUID, `limit` |
| `test_errors.py` | formes success/error, codes d'erreur, immuabilité, commandes exactes (17 codes atteignables) |
| `test_auth.py` | authentification, comptes désactivés, limitation 5/15 min, fenêtre par nom, réinitialisation |
| `test_auth_cache.py` | cache de vérification (F1) : fenêtre, succès/échecs, purge |
| `test_org.py`, `test_organizations.py`, `test_security_org.py` | gestion des agents par une organisation, isolation, non-divulgation, désactivation |
| `test_send.py` | envoi, normalisation, destinataires, limites, une conversation par paire |
| `test_idempotency.py` (dans `test_send.py`) | rejeu, `MESSAGE_ALREADY_EXISTS`, casse, expéditeurs distincts |
| `test_messages.py` | `get_messages` (filtres, tri), `read_message` (première lecture, concurrence) |
| `test_conversations.py` | `get_conversation`, tri, non-divulgation, états de réponse |
| `test_notifications.py` | `unread_by_sender`, `needs_reply`, marquage `no_reply` |
| `test_pagination.py` | curseurs (opacité, signature, liaison, tampering), borne de snapshot, survie au redémarrage |
| `test_concurrency.py` | envois parallèles, course d'idempotence, première conversation, sérialisation |
| `test_persistence.py` | redémarrage : comptes, messages, statuts, idempotence, verrou |
| `test_backup.py` | sauvegarde/restauration, chiffrement, clé externe, corruption, mauvaise clé, service actif |
| `test_security.py` | paramètres Argon2id, absence de secrets en clair, permissions, socket Unix, journaux |
| `test_security_fixes.py` | régression des failles d'audit sécurité 2026 |
| `test_security_audit_2026.py` | régression de l'audit sécurité (web/A2A par sessions et jeton) |

### Coordination v3 (tâches, gouvernance, communautaire)

| Fichier | Couverture |
|---|---|
| `test_tasks.py` | cycle de vie des tâches, dépendances, priorités, transfert, approbations |
| `test_work_events.py` | files de travail et événements consultables (F10) |
| `test_escalation_budgets.py` | escalade automatique et budgets (F9), seuils >= 1 |
| `test_approvals.py` | approbations (F8) |
| `test_agent_cards.py`, `test_agent_description.py`, `test_find_agents.py` | cartes d'agent, description, recherche par compétence (F2/F3) |
| `test_groups.py` | groupes multi-agents, non-divulgation (F15) |
| `test_reputation_delegation.py` | réputation et délégation (F16/F17) |
| `test_principal_type.py` | principaux humains (F19) |
| `test_org_structure.py`, `test_audit_metrics.py`, `test_platform.py` | départements, audit, métriques, plateforme (F13/F14/F21) |
| `test_business_reference.py` | référence métier et idempotence étendue |
| `test_compliance.py`, `test_vision_compliance_v3.py`, `test_help.py` | conformité SPEC (65 commandes), fonctionnalités de vision F1-F21, `help` générée |
| `test_observers_web.py` | comptes observateurs, lecture seule stricte (F18) |

### CLI unifié et production

| Fichier | Couverture |
|---|---|
| `test_cli.py`, `test_cli_unified.py` | CLI unifié `synapse` : cycle de vie serveur/web/a2a, status global, codes 0/1/3/4, version, secrets sur stdin |
| `test_update_apply.py` | `update check` / `update apply` : cycle réel avec sauvegarde automatique, mode systemd (faux systemctl) et mode CLI |
| `test_unit_cli_install_server.py` | internals : install/org init, daemons, `agent status` (réputation réelle) |
| `test_production_ops.py` | `synapse --version`, état de la passerelle A2A dans `status` |
| `test_sd_notify.py` | client sd_notify : no-op hors systemd, socket réelle, battements |
| `test_systemd_units.py` | templates systemd : directives, `systemd-analyze verify` réel, wrapper secrets, RuntimeDirectory serveur seul |
| `test_monitor.py` | moniteur de supervision : 6 contrôles, monitor.json, alert_command |
| `test_backup_prune_verify.py` | rétention `prune` et preuve de restauration `verify` (scratch isolé) |
| `test_e2e_journey.py` | parcours complet : installation → comptes → messagerie → états → notifications → pagination → redémarrage → sauvegarde → restauration |
| `test_regression.py`, `test_independent_audit.py` | scénarios de bout en bout et tests indépendants |
| `test_campaign_extras.py`, `test_campaign_final.py` | branches d'erreur, courses, gestionnaires défensifs, timeouts, signaux, verrous |

### Interface web humaine (SPEC-WEB)

| Fichier | Couverture |
|---|---|
| `test_webui.py` | sessions web (login/logout, cookie HttpOnly, verrouillage 429, TTL, max sessions), snapshot, routes, ETag, 1 MiB |
| `test_spec_web_d1.py`, `test_spec_web_d3.py`, `test_spec_web_d4.py`, `test_spec_web_d6.py` | conversations avec contenu, gestion orgs/agents, compte humain, jeton de confiance local |
| `test_webui_dom_harness.py` | harnais DOM réel (jsdom + backend réel) : rendu des 8 vues, conversations, zéro erreur console |
| `test_http_edges.py` | cas limites HTTP (routes inconnues, corps géants, méthodes) |

### Unitaires

| Fichier | Couverture |
|---|---|
| `test_unit_cursor.py` | curseurs signés HMAC |
| `test_unit_db_store.py` | SQLite, schéma, migrations, transactions IMMEDIATE |
| `test_unit_security_config.py` | Argon2id (paramètres de production), clés, comparaisons constant-time |
| `test_unit_client_backup.py` | bibliothèque cliente et module backup |

## Couverture

Mesurée avec pytest-cov (`--cov=synapse`, suite complète `-n 3`) :

- **Cœur** (serveur, service, store, validation, sécurité, help) :
  **~99-100 %** de déclarations.
- **CLI** (`synapse/cli/*`) : 0-65 % sous pytest-cov — les tests CLI
  exécutent le code en **sous-processus** (`python -m synapse.cli`), que
  le traceur du processus pytest ne voit pas. Le code est réellement
  exercé (chaque sous-processus le charge et l'exécute) ; seule la
  mesure automatique ne le cumule pas (mesure totale du processus :
  ~72 %).
- Les seules lignes non couvertes du cœur sont les points d'entrée
  console (`if __name__ == "__main__"`).

Mesure :

```bash
.venv/bin/python -m pytest tests/ -q -n 3 --cov=synapse --cov-report=term
```
