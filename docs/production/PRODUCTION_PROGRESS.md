# Progression — Audit de production Synapse (A2A)

> Fichier de suivi de l'audit production (2026-08-07). Chaque élément n'est
> coché qu'avec une preuve de validation (tests verts, commande, vérification).
> Plan directeur : [`docs/production/PRODUCTION_AUDIT.md`](PRODUCTION_AUDIT.md).

## Checklist

```text
[x] AUDIT-001 — Tests en échec sur la machine de production (5) — corrigés
[x] AUDIT-002 — CLI agent status : affichage réputation cassé — corrigé
[x] AUDIT-003 — set_escalation_policy : null → escalade immédiate — corrigé
[x] AUDIT-004 — Écarts helpdoc ↔ code (get_org_agents, get_org_snapshot) — corrigés
[x] AUDIT-005 — Test d'atteignabilité des codes d'erreur (17 codes) — étendu
[x] AUDIT-006 — Whitelist observateur verrouillée par test — ajouté
[x] AUDIT-007 — Compteurs « 64 commandes » → 65 — corrigés
[x] AUDIT-008 — TESTING.md chemin obsolète — corrigé
[x] AUDIT-009 — Processus résiduels de démo — nettoyés
[x] AUDIT-010 — Audit post-action non atomique (V7) — documenté (résiduel assumé)
[x] AUDIT-011 — Course IntegrityError groupe/tâche — vérifié : faux positif
[x] AUDIT-012 — Rate-limit web par org-name — documenté (assumé)
[x] AUDIT-013 — Bornes de lecture gros volumes — documenté (assumé)
[x] VALIDATION-FINALE — Suite complète verte (977 tests, 0 échec) + smoke réel
```

## Journal détaillé

### 2026-08-07 — Phase 1 : découverte et diagnostic

- Lecture intégrale de la documentation (SPEC.txt, SPEC_WEB.txt, README,
  docs/*.md, CHANGELOG).
- Exécution de la suite complète → **5 échecs** :
  `test_update_apply.py` (4) + `test_webui_dom_harness.py` (1).
- Diagnostic :
  - `test_update_apply` : `_systemd_unit_exists()` détecte les vraies unités
    systemd de la machine de production → `update apply` bascule sur
    `systemctl` (qui ne connaît pas les services de test temporaires).
  - `test_webui_dom_harness` : le web de test est lancé avant que le socket
    serveur soit prêt → `_require_server` refuse (code 3) → port jamais ouvert.
- Découverte de 8 processus résiduels de démo (depuis le 2026-08-06).

### 2026-08-07 — Phase 2 : corrections AUDIT-001 + AUDIT-009

**AUDIT-001 — Tests en échec.**

Fichiers modifiés :

| Fichier | Modification |
|---|---|
| `synapse/cli/update.py` | `SYNAPSE_NO_SYSTEMD=1` honoré par les 5 helpers systemd (`_systemd_unit_exists`, `_systemd_active`, `_a2a_instances`, `_systemctl_stop`, `_systemctl_start`) |
| `tests/cli_helpers.py` | `SYNAPSE_NO_SYSTEMD=1` posé dans l'environnement de tous les tests CLI |
| `tests/test_update_apply.py` | `test_update_apply_systemd_mode` retire la variable (le faux systemctl EST la simulation) ; `test_update_systemd_helpers_cli_mode` la pose via monkeypatch |
| `tests/test_webui_dom_harness.py` | `_wait_socket()` : attend le socket Unix du serveur avant de lancer le web (élimine la course) |
| `docs/production/OPERATIONS.md` | `SYNAPSE_NO_SYSTEMD` documenté (section Mise à jour) |
| `docs/production/SPEC_PRODUCTION.md` | Règle 4 : `SYNAPSE_NO_SYSTEMD` documenté |

Preuves :

```text
$ .venv/bin/python -m pytest tests/test_update_apply.py -v
9 passed
$ .venv/bin/python -m pytest tests/test_webui_dom_harness.py
1 passed
$ .venv/bin/python -m pytest tests/test_unit_cli_install_server.py tests/test_production_ops.py tests/test_systemd_units.py tests/test_sd_notify.py
47 passed
```

**AUDIT-009 — Processus résiduels.**

Arrêt propre des 8 processus (4 serveurs + 4 web de démo sur
`/tmp/synapse-demo-*`). Vérification :

```text
$ ps aux | grep synapse-  →  aucun processus résiduel
```

### 2026-08-07 — Phase 3 : rédaction du plan directeur

- `docs/production/PRODUCTION_AUDIT.md` créé (13 problèmes tracés : 9 corrigés/traités,
  4 documentés comme résidus assumés).
- `docs/production/PRODUCTION_PROGRESS.md` créé (ce fichier).

### 2026-08-07 — Phase 4 : implémentations (AUDIT-002 à AUDIT-008)

**AUDIT-002 — CLI agent status (réputation).**

| Fichier | Modification |
|---|---|
| `synapse/cli/agent.py` | `_cmd_status` : affiche le contrat serveur réel — mention `qualitative` pour les autres, `completion_rate` + compteurs (t/e/a/c) pour soi ; suppression du contrat fantôme `score`/`total_reviews`/`reputation` |
| `tests/test_unit_cli_install_server.py` | Nouveau test `test_cli_agent_status_reputation` ; suppression du test de debug résiduel `test_dbg_cli` |

Preuve : `pytest tests/test_unit_cli_install_server.py` → 29 passed.

**AUDIT-003 — Validation set_escalation_policy.**

| Fichier | Modification |
|---|---|
| `synapse/validation.py` | Nouveau validateur `validate_positive_seconds` (entier >= 1, `None` refusé) appliqué aux seuils `due_after_seconds`/`failed_after_seconds` — supprime le `lambda ... or 0` qui transformait `null` en 0 (escalade immédiate) |
| `tests/test_escalation_budgets.py` | Nouveau test `test_escalation_thresholds_reject_null_and_zero` (null/0 → INVALID_ARGUMENT) |

Preuve : `pytest tests/test_escalation_budgets.py tests/test_unit_validation.py` → 33 passed.

**AUDIT-004 — helpdoc ↔ code.**

| Fichier | Modification |
|---|---|
| `synapse/helpdoc.py` | `get_org_agents` : documente `principal_type` et `reputation` réels ; `get_org_snapshot` : « observateur ou humain » (au lieu de « réservé observateurs ») |

Preuve : `pytest tests/test_help.py tests/test_compliance.py` → 51 passed ; help = 61483 octets (marge 4053).

**AUDIT-005 — Test d'atteignabilité des 17 codes.**

| Fichier | Modification |
|---|---|
| `tests/test_errors.py` | `ALL_ERROR_CODES` étendu aux 5 codes v3 ; scénarios réels ajoutés (TASK_NOT_FOUND, TASK_STATE_INVALID, TASK_DEPENDENCY_NOT_MET, QUOTA_EXCEEDED, GROUP_NOT_FOUND) |

Preuve : `pytest tests/test_errors.py` → 7 passed.

**AUDIT-006 — Whitelist observateur verrouillée.**

| Fichier | Modification |
|---|---|
| `tests/test_observers_web.py` | Nouveau test `test_observer_whitelist_matches_real_read_commands` : (1) whitelist ⊆ spec, (2) whitelist ⊆ commandes agent, (3) chaque commande agent hors whitelist refusée à un observateur (ACCESS_DENIED) |

Preuve : `pytest tests/test_observers_web.py` → 13 passed.

**AUDIT-007 — Compteurs 64 → 65.**

`docs/specs/SPEC_CLI.md`, `docs/webui/CHECKLIST_SPEC_WEB.md`, `SKILLS_AGENT/synapse/references/api-reference.md` : compteurs corrigés à 65 (la référence est `len(COMMAND_SPECS) == 65`, verrouillée par `test_compliance.py`).

**AUDIT-008 — TESTING.md.**

`docs/production/TESTING.md` : chemin `/home/baron/Projects/Synapse` → `/home/baron/Projects/A2A`.

### 2026-08-07 — Phase 5 : validation finale

**Suite complète** : `pytest tests/ -q` → **977 tests collectés, tous verts, 0 échec**
(le run initial de l'audit avait 5 échecs ; la suite corrigée est 100 % verte).

**Smoke réel `synapse agent status`** (serveur de démo réel, seed_demo) :

```text
$ synapse agent status comptable --my-name comptable --password-stdin
Agent 'comptable'
  description   Tient la comptabilité : factures, budgets, reporting.
  carte         validation pending / capacités [...] / modèle synapse-agent-1 / SLA réponse < 1 h
  réputation    complétion None (t0 / e0 / a1 / c0)     ← soi : compteurs réels

$ synapse agent status directeur --my-name comptable --password-stdin
  réputation    inconnu                                 ← autre : mention qualitative
```

L'ancien affichage fantôme « score — (0 avis) » a disparu.

**Nettoyage** : aucun processus résiduel de démo ; serveur de smoke arrêté.

## Points restant à vérifier

- [x] Suite complète verte sur la machine de production (977 tests, 0 échec).
- [x] `synapse agent status` sur un agent réel (smoke ci-dessus).
- [x] Relecture finale des compteurs (65 commandes) dans tout le dépôt.
