# Documentation technique — Synapse

Hub unique de la documentation technique du projet. Chaque document est
tenu à jour pour refléter **l'état actuel** du dépôt (v3.1.1, 65 commandes
API, CLI unifié `synapse`, suite de tests 977). Les rapports de phases et
audits historiques dont les conclusions sont dépassées ont été supprimés ;
ce qui reste est vivant.

## Organisation

| Dossier | Contenu |
|---|---|
| [`specs/`](specs/) | Les spécifications source de vérité : `SPEC.txt` (vision + API), `SPEC_WEB.txt`, `SPEC_CLI.md` (CLI unifié), `SPEC_CLI_ECARTS.md` (écarts volontaires), `ARCHITECTURE.md`, `CONFORMITE.md` (matrice exigence → code → test) |
| [`production/`](production/) | Mise en production et exploitation : `SPEC_PRODUCTION.md` (8 points, validé et implémenté), `OPERATIONS.md` (runbook), `RELEASE.md` (procédure de release), `TESTING.md` (suite de tests), `PRODUCTION_AUDIT.md` + `PRODUCTION_PROGRESS.md` (audit de production), `TEST_PERFORMANCE.md` (optimisation de la suite) |
| [`securite/`](securite/) | `SECURITY.md` — modèle de menace et garanties de sécurité (audits passés fusionnés) |
| [`webui/`](webui/) | Interface web humaine : `DESIGN.md` (système de design « Registre », v3), `REDESIGN_V3.md` (journal de la refonte), `CHECKLIST_SPEC_WEB.md` (avancement SPEC-WEB) |
| [`perf/`](perf/) | Performance : `PERFORMANCE.md` (analyses, mesures, optimisations du service), `BENCHMARKS.md` (harnais de benchmark) |

## Documents racine

- [`README.md`](../README.md) — portail du projet (fonctionnalités,
  installation, points d'entrée).
- [`CHANGELOG.md`](../CHANGELOG.md) — historique des versions
  (Keep a Changelog + SemVer).

## Règles de maintenance

1. La **source de vérité** fonctionnelle est `specs/SPEC.txt` (API) et
   `specs/SPEC_CLI.md` (CLI) — tout écart de code doit y être reflété.
2. Les **compteurs** (65 commandes, 977 tests, versions) sont vérifiés par
   des tests dédiés (`test_compliance.py`, assertions de version).
3. Les documents de `production/`, `securite/` et `perf/` doivent
   correspondre au déploiement réel : vérifier les résultats avant de les
   modifier, jamais de chiffre inventé.
4. Ne pas accumuler : un rapport de phase terminé dont les conclusions
   sont dans un document vivant est **supprimé** (l'historique git le
   conserve).
