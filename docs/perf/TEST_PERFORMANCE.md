# Performance de la suite de tests — mesures et optimisations

> Mesures réelles, machine de référence : 4 cœurs, 7 Go RAM, production
> Synapse active (services systemd + moniteur). Suite : **977 tests**.
> Date : 2026-08-07 (v3.1.1).

## Avant / Après

| Métrique | Avant (séquentiel) | Après (`-n 3`, pytest-xdist) | Gain |
|---|---|---|---|
| Durée totale | **16 min 16,51 s** | **5 min 59,74 s** | **×3,4 (−63 %)** |
| Tests collectés | 977 | 977 | identique |
| Échecs | 0 | 0 | identique |
| Couverture `synapse/` (processus pytest, `--cov`) | — | cœur ~99-100 % ; CLI en sous-processus non tracé (total ~72 %) | méthodologie identique |
| Utilisation CPU | 35 % | 81 % | le matériel est exploité |
| Fautes de page mineures | 10 565 755 | (I/O dominante des deux côtés) | — |

Commandes de mesure :

```bash
# Avant
/usr/bin/time -v .venv/bin/python -m pytest tests/ -q -p no:cacheprovider --durations=40
# Après
/usr/bin/time -v .venv/bin/python -m pytest tests/ -q -n 3 -p no:cacheprovider --durations=40
# Certification complète (venv frais + lock + suite + couverture)
rm -rf ~/.cache/synapse-ci/venv && SYNAPSE_CI_PYTEST_ARGS="tests/ -q -n 3 --cov=synapse" ./scripts/ci.sh
```

## Analyse : où partait le temps ?

Profil `--durations=40` (séquentiel) :

1. **Sous-processus CLI** (`tests/cli_helpers.py::run_cli`) : chaque test
   lance `python -m synapse.cli` (démarrage Python ~0,3-0,5 s) puis des
   daemons serveur/web/a2a réels avec cycles complets. Les tests de cycle
   de vie (`test_cli_unified.py`, `test_update_apply.py`,
   `test_a2a_interop.py`) pèsent 2 à 11 s chacun.
2. **Harnais DOM** (`test_webui_dom_harness.py::test_webui_dom_harness_sessions`,
   30,9 s) : backend réel (seed_demo + serveur + web) + 2 exécutions node
   jsdom de l'application complète. C'est la preuve de rendu la plus
   forte ; ses attentes (0,3 s) sont des pollings légitimes, pas des
   sleeps gratuits.
3. **I/O** : 10,5 M de fautes de page mineures et 512 K changements de
   contexte volontaires → la suite est dominée par l'attente
   (sous-processus, sockets, SQLite WAL), pas par le CPU (35 %).

Le hacheur Argon2id est **déjà** remplacé par une instance rapide pour
toute la session de test (`tests/conftest.py`) — un test dédié vérifie que
les paramètres de production restent intacts. Le coût Argon2 n'était donc
pas le goulot.

## Optimisation retenue : parallélisation pytest-xdist

La suite est **par construction parallélisable** (vérifié avant
d'appliquer) :

- **Ports isolés** : `SYNAPSE_WEB_PORT` / `SYNAPSE_A2A_PORT` (ports libres
  aléatoires, `tests/cli_helpers.py`) ; interface web en processus sur
  `port=0` (port alloué par l'OS). Aucun port en dur → zéro collision
  entre workers, y compris avec la production en marche.
- **Stockages isolés** : chaque test utilise `tmp_path` (unique par test
  et par worker) ; aucun chemin fixe.
- **État global** : le hacheur rapide est une fixture **session** — chaque
  worker installe la sienne ; `SYNAPSE_NO_SYSTEMD=1` posé par worker.
- **Aucune ressource partagée** entre tests (pas de base commune, pas de
  fichier global) : `--dist loadscope` n'est pas nécessaire, la
  distribution par défaut (round-robin) suffit.

Intégration : `pytest-xdist` ajouté aux extras `[dev]` + `requirements.lock`
régénéré (`pip-compile --generate-hashes --all-extras`, procédure
RELEASE.md) ; `scripts/ci.sh` exécute par défaut `tests/ -q -n 3`
(`SYNAPSE_CI_WORKERS` pour ajuster).

### Pourquoi cela ne dégrade pas la qualité

- **Aucun test supprimé, désactivé ni affaibli** : les 977 tests restent
  les mêmes, avec les mêmes assertions, les mêmes données réelles
  (serveurs et daemons réels en sous-processus, harnais DOM réel).
- **Aucun mock ajouté** : le parallélisme ne remplace aucun comportement.
- **Couverture inchangée** : mesurée avec pytest-cov — cœur ~99-100 %,
  CLI en sous-processus non tracé par le traceur du processus pytest
  (détail : `docs/production/TESTING.md` §Couverture).
- **L'isolation est renforcée, pas affaiblie** : les ports aléatoires et
  les stockages `tmp_path` rendent la suite déterministe même exécutée
  plusieurs fois de front — le seul prérequis était déjà en place pour
  l'isolation séquentielle.
- **Contrainte matérielle honnête** : `-n 3` (et non `auto` = 4) laisse un
  cœur pour l'OS et la production ; sur une machine plus petite, réduire
  `SYNAPSE_CI_WORKERS`.

## Optimisations évaluées et rejetées

| Candidate | Verdict | Justification |
|---|---|---|
| Réduire les attentes du harnais DOM | Rejeté | Les pollings 0,3 s avec timeout 20 s sont déjà serrés ; le coût est le chargement jsdom + backend réel (preuve de rendu) |
| Fusionner les 2 exécutions node du harnais | Rejeté | Structure du harnais (login puis vérification complète) — gain ~5 s pour un risque de régression du garde-fou |
| `-n auto` (4 workers) | Rejeté | 4 cœurs / 7 Go RAM avec production active : risque de famine mémoire (OOM des daemons de test) — `-n 3` mesuré stable |
| Marquage `slow` + exclusions | Rejeté | Équivaudrait à désactiver des tests par défaut — interdit par la politique du projet |
| Réutilisation session d'un serveur commun | Rejeté | Briserait l'isolation par test (état partagé, courses) — le cœur de la fiabilité de la suite |

## Résultat final

Suite complète **verte** en ~6 min au lieu de ~16 min, à couverture et
fiabilité identiques — vérifié par le run de certification (venv 100 %
frais, lock vérifié, `-n 3`, `--cov`).
