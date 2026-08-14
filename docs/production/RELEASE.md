# Release — procédure (SPEC_PRODUCTION §5)

Ce document décrit le cycle de release d'une nouvelle version du projet.
Il est le pendant opérationnel du §5 de `SPEC_PRODUCTION.md`.

## Politique de version (SemVer)

- L'API serveur v2 (`SPEC.txt`) n'a pas changé → version **mineure**.
- Toute évolution de l'API serveur (nouvelle commande, changement de
  contrat) → version **majeure** (4.0.0, 5.0.0…).
- Les correctifs → version **corrective** (3.1.1…).

La version est déclarée **uniquement** dans `pyproject.toml` ; la source de
vérité à l'exécution est `importlib.metadata` (`synapse --version`,
`project_version()`, fichiers PID). `synapse/__init__.py` ne porte plus de
`__version__`.

## Étapes (dans l'ordre)

1. **Entrée dans `CHANGELOG.md`** (format Keep a Changelog) : section
   `[X.Y.Z] — <date>` avec Ajouté / Modifié / Supprimé.
2. **Bump de version** : `version = "X.Y.Z"` dans `pyproject.toml`.
3. **Régénération du lock de dépendances** (§7 de SPEC_PRODUCTION) :

   ```bash
   # Outil : pip-tools 7.6 avec pip < 26 (incompatibilité connue pip 26)
   python3 -m venv /tmp/synapse-lockenv
   /tmp/synapse-lockenv/bin/pip install "pip<26" pip-tools==7.6.0
   /tmp/synapse-lockenv/bin/pip-compile --generate-hashes \
       -o requirements.lock pyproject.toml
   ```

   Un bump de dépendance est une décision de release : le justifier dans le
   changelog. `scripts/check_lock.sh` vérifie que toute dépendance directe
   de `pyproject.toml` est présente dans le lock (exécuté par la CI).
4. **Certification** : venv **100 % frais** + suite complète :

   ```bash
   SYNAPSE_CI_FORCE_FRESH=1 scripts/ci.sh
   ```

   (La CI quotidienne utilise un venv cacheable ; la certification release
   impose le venv frais.)
5. **Installation de test** : re-run de `install.sh` sur une machine de
   test (idempotence, unités, chemins), démarrage des trois services,
   `synapse status` vert, `synapse backup create && verify`.
6. **Tag annoté** (signé si une clé GPG est disponible) :

   ```bash
   git tag -a vX.Y.Z -m "Version X.Y.Z — <résumé>"
   git push origin vX.Y.Z   # quand un remote existe
   ```

7. **Installation de production** depuis le tag :

   ```bash
   git checkout vX.Y.Z
   sudo ./install.sh .
   sudo systemctl daemon-reload && sudo systemctl start synapse synapse-web
   ```

## Activation d'un canal distant (optionnel)

`update check` compare la version installée à un canal distant quand
`update_url` est configuré (clé de configuration ou `SYNAPSE_UPDATE_URL`) :
un fichier/endpoint JSON `{"version": "X.Y.Z"}`. Sans canal, `update check`
signale simplement la version installée. La mise à jour en place se fait
via `synapse update apply` (voir OPERATIONS.md).

## Portage CI (quand un remote git existera)

Le dépôt n'a pas de remote aujourd'hui : la CI est locale
(`scripts/ci.sh`, hook pre-push, timer nocturne). Si un remote privé
apparaît, porter `scripts/ci.sh` tel quel dans un workflow (ex. GitHub
Actions) :

```yaml
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: ./scripts/ci.sh
```

La philosophie du projet reste « tout local » : le portage est une option,
pas une obligation.
