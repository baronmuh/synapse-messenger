# Écarts documentés entre SPEC_CLI.md et l'API réelle (implémentation)

L'implémentation du CLI unifié suit SPEC_CLI.md. Quelques formes
ergonomiques du document ne peuvent pas être servies littéralement par
l'API existante (64+1 commandes, `validation.py` comme source de
vérité) ; chaque cas est résolu ici par le comportement RÉEL le plus
proche, jamais par un simulacre. Tout écart est volontaire, testé et
assumé.

## 1. `group create --description <texte>` (SPEC_CLI §4.8)

L'API `create_group` ne gère pas de description (table `groups` sans
colonne de description, SPEC.txt F15). La commande `synapse group create
<nom> --description` est **refusée explicitement** avec un message qui
renvoie à cette limite ; `synapse group create <nom>` fonctionne.

## 2. `policy delegate <agent> <capacités>` et `policy revoke <délégation-id>` (SPEC_CLI §4.9)

L'API `create_delegation` délègue une **tâche** à un agent avec une
échéance (paramètres `task_id`, `delegatee_username`, `expires_at` — pas
de « capacités », pas d'identifiant de délégation) ; `revoke_delegation`
révoque par `(task_id, delegatee_username)`.

Formes réelles implémentées :

    synapse policy delegate <agent> --task <id> --expires <horodatage>
    synapse policy revoke  <agent> --task <id>

`--expires` accepte le format de SPEC_CLI (`YYYY-MM-DDTHH:MM:SSZ`) — les
millisecondes manquantes sont ajoutées (l'API exige `.sssZ`).

## 3. `agent card <nom> --set` (SPEC_CLI §4.5)

`set_agent_card` ne définit que la carte du compte **authentifié**
(aucun paramètre `username`). La forme d'écriture exige donc l'identité
de compte : `--my-name <nom>` (qui doit désigner le même agent que le
positionnel). Le jeton local ne s'applique jamais aux comptes agents
(SPEC-WEB R6.7) : l'écriture de carte passe par le mot de passe de
l'agent.

## 4. `agent budget <nom> <montant>` (SPEC_CLI §4.5)

L'API `set_agent_budget` gère des budgets de **tâches actives** et de
**messages par heure** — pas de budget monétaire. Forme réelle :

    synapse agent budget <nom> --max-active-tasks <n> [--max-messages-per-hour <n>]

Un positionnel `<montant>` fourni est refusé avec un message explicatif.

## 5. `task create --creator` / `--department`, `task update` français (SPEC_CLI §4.7)

* `--creator` n'existe pas dans l'API : le créateur est le compte
  authentifié (identité via `--my-name` ou jeton local) — refus explicite.
* `--department` n'est pas supporté par `create_task` (les tâches d'un
  département se LISTENT via `task list --department`) — refus explicite.
* États et priorités : l'API utilise l'anglais (`submitted`,
  `in_progress`, `completed`, `failed`, `canceled`, `pending_approval` ;
  `low/normal/high`). Le CLI accepte les deux langues et traduit les
  équivalents français documentés par SPEC_CLI (`en_attente`, `en_cours`,
  `terminee`, `echec`, `annulee`, `en_approbation` ; `basse`, `haute`).

## 6. `event stream --seq <n>` (SPEC_CLI §4.10)

L'API pagine par **curseur opaque** (`cursor`), pas par séquence
(SPEC.txt F10). `--seq` est refusé avec un message renvoyant à
`--cursor` ; le flux affiche la séquence de chaque événement.

## 7. `logs --level <niveau>` (SPEC_CLI §4.2/§4.3)

Le format des journaux est contraint par SPEC.txt §4 (champs
`timestamp`, `process_id`, `username`, `command`, `target_id`, `result`
— pas de niveau). `--level` est refusé avec un message explicatif ;
filtrez le fichier directement.

## 8. `policy escalation <org>` — lecture (SPEC_CLI §4.9)

La commande de lecture `get_escalation_policy` n'existait pas dans
l'API : elle a été **ajoutée** (65e commande, lecture org-auth, défaut
« désactivée » si jamais configurée) — c'est la porte d'évolutivité
annoncée par SPEC_CLI §4.11. Compteurs « 64 commandes » mis à jour en
« 65 » (SPEC.txt, SPEC_WEB.txt, tests).

## 9. `org list --all` (SPEC_CLI §4.4)

`list_orgs` ne listait que les organisations ACTIVES. Le paramètre
optionnel `include_disabled` (comptes humains uniquement — l'identité
web locale reste limitée aux actives, SPEC-WEB R6.6) a été ajouté : les
désactivées apparaissent dans un champ `disabled` distinct. Compte
humain requis : une organisation désactivée ne peut pas lister depuis
son propre compte (gel absolu) — il faut le compte humain d'une
organisation active ou `--organization-name` explicite.

## 10. Divers

* `org status <nom>` : le gel absolu bloque toute lecture ; l'état
  « désactivée » est détecté via `list_orgs --all` quand un compte
  humain actif est disponible, sinon l'erreur est explicite (inconnue ou
  désactivée).
* `message mark-no-reply <autre>` : l'API marque par `conversation_id` ;
  le CLI résout d'abord la conversation avec l'interlocuteur
  (`get_conversation`), puis marque — deux appels réels.
* `group members/add-member/remove-member/messages/send <nom>` : l'API
  adresse les groupes par UUID ; le CLI résout le nom via
  `list_my_groups` (groupes personnels au compte authentifié).
* `policy set <org>` : l'API exige les deux booléens ; le CLI lit la
  politique courante et n'applique que les axes modifiés
  (--allow/--deny-*-external).
* `policy escalation --set` : valeurs par défaut = politique courante
  (lecture d'abord) ; `--targets` accepte un seul agent (l'API n'a
  qu'une cible).
* `agent create --department` : si le département n'existe pas, il est
  créé automatiquement (`create_department`) avant l'affectation.
* `agent create` sans `--description` : description par défaut
  « Agent <nom> (créé via CLI) » (l'API exige 1-500 caractères).
* `synapse` nu = `server start` ; `synapse help` = aide générale ;
  `synapse api help` = documentation intégrée du service.
