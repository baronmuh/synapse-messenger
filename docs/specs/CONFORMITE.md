# Matrice de conformité — SPEC.txt (F1-F21)

Document de traçabilité : chaque exigence significative de SPEC.txt est
reliée à son implémentation (fichier/composant), à ses tests et au résultat
du run final. Toute ligne est remontable : exigence → code → test → résultat.

**État : vérifié sur v3.1.1 (2026-08-07)** — suite complète 977 tests
verte ; la référence des compteurs est verrouillée par `test_compliance.py`
(65 commandes) et `test_vision_compliance_v3.py` (F1-F21). Les chemins de
fichiers de test cités ci-dessous existent tous dans le dépôt actuel.

## Fonctionnalités

| Exigence | Implémentation | Tests | Résultat |
| --- | --- | --- | --- |
| F1 — Cache de vérification d'authentification | `synapse/service.py` (`_cached_password_ok`, `_auth_cache`, `auth_cache_ttl_seconds` dans `config.py`) ; amendement SPEC.txt §19.1 | `tests/test_auth_cache.py` (6) ; bench production | PASS |
| F2 — Carte d'agent | `synapse/store/cards.py`, `synapse/service.py` (`_agent_set_agent_card`, `_agent_get_agent_card`, `_org_approve_agent_card`) | `tests/test_agent_cards.py` (15) | PASS |
| F3 — Recherche par compétence | `synapse/service.py` (`_agent_find_agents`), `synapse/store/cards.py` (`search`) | `tests/test_find_agents.py` (7) | PASS |
| F4 — Corrélation métier | `synapse/store/messages.py` (`business_reference`), `synapse/service.py` (idempotence étendue), migration db | `tests/test_business_reference.py` (11) | PASS |
| F5 — Tâches (cycle de vie) | `synapse/store/tasks.py`, `synapse/service.py` (handlers F5) ; machine à états + dépendances | `tests/test_tasks.py` (16) | PASS |
| F6 — Files de travail | `synapse/store/tasks.py` (`list_work`), `synapse/service.py` (`_agent_get_my_work`) | `tests/test_work_events.py` (2) | PASS |
| F7 — Transfert de tâche | `synapse/service.py` (`_agent_transfer_task`) | `tests/test_tasks.py::test_transfer_*` (2) | PASS |
| F8 — Approbations | `synapse/service.py` (`_agent_request_approval`, `_agent_approve_task`, `_agent_reject_task`) | `tests/test_approvals.py` (8) | PASS |
| F9 — Escalade et budgets | `synapse/service.py` (`_check_escalations`, `_check_task_budget`, `_check_message_budget`) | `tests/test_escalation_budgets.py` (9) | PASS |
| F10 — Événements consultables | `synapse/store/events.py`, `synapse/service.py` (`_agent_get_events`) | `tests/test_work_events.py` (5) | PASS |
| F11 — Audit organisationnel | `synapse/store/audit.py`, `synapse/service.py` (`_audit_action` au dispatch, `_org_get_audit`) | `tests/test_audit_metrics.py` (9) | PASS |
| F12 — Métriques | `synapse/service.py` (`_org_get_metrics`, `_org_get_server_status`) | `tests/test_audit_metrics.py` (4) | PASS |
| F13 — Structure organisationnelle | `synapse/service.py` (`_org_create_department`, `_org_set_agent_department`, `_org_get_org_structure`, `_structure_by_department`) | `tests/test_org_structure.py` (9) | PASS |
| F14 — Permissions (rôles fixes) | `synapse/service.py` (`_agent_list_department_tasks`) | `tests/test_org_structure.py` (3) | PASS |
| F15 — Groupes multi-agents | `synapse/service.py` (handlers groupes), tables `groups/group_members/group_messages` | `tests/test_groups.py` (8) | PASS |
| F16 — Réputation | `synapse/service.py` (`_agent_get_agent_reputation`) | `tests/test_reputation_delegation.py` (5) | PASS |
| F17 — Délégation contrôlée | `synapse/service.py` (`_task_visible_or_404` + `delegations`) | `tests/test_reputation_delegation.py` (6) | PASS |
| F18 — Comptes observateurs + interface web | `synapse/service.py` (dispatch lecture seule, `_agent_get_org_snapshot`), `synapse/web.py` (127.0.0.1, jeton d'accès obligatoire `X-Synapse-Token`) | `tests/test_observers_web.py` (8), `tests/test_http_edges.py` (jeton 401/200) | PASS |
| F19 — Principaux humains | `synapse/service.py` (`principal_type`), `synapse/store/accounts.py` | `tests/test_principal_type.py` (5) | PASS |
| F20 — Passerelle A2A | `synapse/a2a_bridge.py` (agent.json + JSON-RPC tasks/*) | `tests/test_a2a_bridge.py` (6) | PASS |
| F21 — Plateforme (intégration) | scénario de bout en bout multi-briques | `tests/test_platform.py` (2) | PASS |

## Principes et exigences transverses (SPEC.txt §V.4, §12)

| Exigence | Implémentation | Tests | Résultat |
| --- | --- | --- | --- |
| Principe « petit d'abord » (org plate minimale valide) | socle v2 conservé intact ; aucune fonctionnalité n'exige la hiérarchie | `tests/test_platform.py::test_two_agents_minimal_org` ; suite v2 historique | PASS |
| Non-régression : 26 contraintes SPEC.txt | aucune commande existante modifiée (19 + 39) ; contraintes 2 et 11 amendées seulement | `tests/test_compliance.py` (26 contraintes) ; suite complète | PASS |
| Isolation inter-organisations | orgs, tâches, groupes, audit, budgets tous scopés par organisation | `test_org_structure.py::test_foreign_agent_rejected`, `test_audit_metrics.py::test_audit_isolated_between_organizations`, `test_find_agents.py::test_find_only_own_organization` | PASS |
| Non-divulgation (tâches, groupes, approbations) | TASK_NOT_FOUND / GROUP_NOT_FOUND pour tout accès non autorisé | `test_tasks.py::test_task_invisible_to_others`, `test_groups.py::test_non_member_cannot_read_or_write`, `test_approvals.py::test_non_approver_cannot_approve` | PASS |
| Aucun contenu dans l'audit/métriques/snapshot | audit_log sans contenu ; snapshot sans contenu | `test_audit_metrics.py::test_audit_contains_no_content`, `test_observers_web.py::test_observer_reads_snapshot` | PASS |
| Sécurité des comptes observateurs (lecture seule stricte) | liste blanche de lectures au dispatch | `test_observers_web.py::test_observer_writes_denied` | PASS |
| Idempotence étendue (messages, tâches, messages de groupe) | client_message_id / client_task_id / UNIQUE | `test_business_reference.py::test_*_idempotence`, `test_tasks.py::test_client_task_id_idempotent`, `test_groups.py::test_group_message_idempotency` | PASS |
| Persistance / redémarrage | toutes les tables dans le stockage ; migrations idempotentes | `test_persistence.py`, `test_backup.py`, `test_tasks.py::test_task_persists_across_restart`, `test_groups.py::test_groups_persist_across_restart`, `test_org_structure.py::test_structure_persists_across_restart` | PASS |
| Documentation cohérente (doc ↔ code ↔ tests) | `help()` générée depuis COMMAND_SPECS (anti-dérive) ; amendement SPEC.txt §19 ; SPEC.txt §V.7 bis | `tests/test_help.py` (26) | PASS |
| Performance (cache F1) | bench production Argon2id réel | `docs/perf/PERFORMANCE.md` §11 (bench v3) | PASS |

## Limites de vérification (assumées et documentées)

* La passerelle A2A (F20) est un pont JSON-RPC local sans notifications push
  (SSE) : la conformité au protocole A2A est partielle par conception
  (documenté SPEC.txt §V.7 bis, `synapse/a2a_bridge.py`).
* L'interface web (F18) est un dashboard de supervision statique + API
  JSON locale (127.0.0.1) pilotée par le compte observateur Synapse ; elle
  exige un jeton d'accès (en-tête `X-Synapse-Token`, ajouté par l'audit
  sécurité 2026) — sans jeton valide, toutes les routes refusent (401).
* Les tests de concurrence de la suite existante (test_unit_db_store,
  test_campaign_final) couvrent la contention SQLite ; les nouveaux stores
  suivent le même pattern de transactions immédiates.
* Le bench v3 mesure le cache d'authentification en régime réel ; les autres
  fonctionnalités n'ajoutent pas de chemin chaud (lectures en début de
  transaction, index dédiés).
