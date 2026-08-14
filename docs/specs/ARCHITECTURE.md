# Architecture

## Vue d'ensemble

```
  Agent A ─┐                          ┌─ Agent B
  Agent C ─┼── socket Unix ──► synapse-server ──► SQLite (WAL)
  CLI ─────┘     (0600)       (threads)      (0600, répertoire 0700)
                              │
                              ├── journaux JSON-lines (rotation 90 j)
                              ├── clés : cursor.key (HMAC), backup.key (AES-GCM)
                              └── interfaces boucle locale optionnelles :
                                  web (127.0.0.1:8080) · passerelle A2A (127.0.0.1:8090)
```

Le service est un processus unique (threads par connexion) qui n'expose
aucun port réseau hors de la machine (les deux interfaces HTTP optionnelles
écoutent sur 127.0.0.1 avec authentification — voir
`docs/securite/SECURITY.md`). Chaque requête est une ligne JSON ≤ 1 MiB ;
chaque réponse est une ligne JSON.

## Couches

| Couche | Module | Rôle |
|---|---|---|
| Transport | `server.py` | socket Unix, framing, limite 1 MiB, verrou de service, arrêt propre |
| Interface | `validation.py` | enveloppe, paramètres, formats (validation **avant** authentification) |
| Application | `service.py` | dispatch, auth + rate-limit, rôles, **65 commandes**, pagination |
| Domaine | `store/` | requêtes SQL paramétrées, transactions, agrégats |
| Stockage | `db.py` | SQLite WAL, schéma v3, `BEGIN IMMEDIATE` / `BEGIN` |
| Support | `cursor.py`, `security.py`, `logging_setup.py`, `config.py` | curseurs signés, Argon2id/clés, journaux, configuration |
| Surfaces | `cli/` (CLI unifié), `web.py` (interface web), `a2a_bridge.py` (passerelle A2A), `backup.py` | opérateur, humains, interopérabilité, sauvegarde |

Le CLI unifié `synapse` (spécifié dans `docs/specs/SPEC_CLI.md`) est
l'outil de référence ; les 6 binaires historiques (`synapse-server`,
`synapse-web`, …) sont des alias dépréciés qui délèguent avec
avertissement.

## Flux d'une requête

1. **Framing** (`server.py`) : lecture ligne par ligne, rejet > 1 MiB
   (`INVALID_ARGUMENT`, avant auth).
2. **Parsing** (`validation.parse_json_request`) : UTF-8 strict, clés
   dupliquées refusées.
3. **Validation de l'enveloppe** (`validate_envelope`) : clés exactes,
   `api_version == "v1"`, commande connue (`UNKNOWN_COMMAND`), paramètres
   exacts et typés (`INVALID_ARGUMENT`), normalisation (noms, UUID,
   contenu NFC/trim, `limit` résolu).
4. **Authentification** (`service._authenticate`) : fenêtre glissante
   (5 échecs / 15 min), vérification Argon2id (vérification leurre pour un
   compte inconnu ou désactivé → chronométrage constant), réinitialisation
   du compteur en cas de succès. `AUTH_FAILED` sans toucher aux données.
5. **Principal** : commande d'organisation + agent → `ACCESS_DENIED` ; agent + commande d'organisation → `ACCESS_DENIED`.
6. **Opération métier** dans une transaction (`BEGIN IMMEDIATE` pour les
   écritures, `BEGIN` pour les lectures multipreuves).

## Concurrence

- SQLite en mode WAL : lecteurs concurrents, un écrivain à la fois.
- Toutes les écritures passent par `BEGIN IMMEDIATE` (sérialisation) avec
  `busy_timeout` ; en cas de collision d'unicité (conversation,
  idempotence), l'écrivain relit l'état existant :
  - `conversations.key UNIQUE` → relecture de la conversation ;
  - `messages(sender, client_message_id) UNIQUE` → relecture et décision
    idempotente (retour du message existant ou `MESSAGE_ALREADY_EXISTS`).
- `read_message` : `UPDATE ... WHERE read_at IS NULL` — la première
  transaction validée fixe la date ; les lectures concurrentes retournent
  toutes la même valeur.
- Envoi et marquage concurrents sont sérialisés par conversation ; la
  transaction validée en dernier détermine l'état final (aucun état
  intermédiaire observable).

## Transactions

La transaction d'envoi inclut : vérification du destinataire actif,
création/lecture de la conversation, insertion du message, clé
d'idempotence, annulation du marquage du destinataire et état
`no_reply_needed` de l'expéditeur — le tout atomiquement. `created_at` est
généré par le service dans la transaction (jamais par l'agent appelant).

## Pagination stable

- Borne de snapshot `boundary` : capturée par le service à la première
  page ; seuls les messages avec `created_at <= boundary` sont considérés.
- Statut de lecture figé à la borne : `read_at == NULL OU read_at > boundary`
  ⇒ « non lu à la borne ». Les lectures survenues après la borne ne
  modifient pas une pagination commencée.
- Pagination « keyset » : position `(created_at, message_id)` (ou
  `(last_received_at, conversation_id)` pour les notifications), tri
  identique à chaque page, `LIMIT n+1` pour détecter la page suivante.
- Curseur = payload JSON (commande, agent, filtres, tri, borne, position)
  signé HMAC-SHA256 ; clé persistante (`cursor.key`, incluse dans les
  sauvegardes) → les curseurs survivent aux redémarrages et restaurations.

## États de réponse (dérivés)

`reply_state` ne stocke que `(conversation_id, participant,
no_reply_for_message_id, no_reply_marked_at)`. L'état de réponse est
**calculé** à la lecture à partir des messages et du marquage — il n'est
jamais stocké directement, ce qui élimine toute désynchronisation.

## Sauvegarde / restauration

- `synapse backup create` : copie cohérente SQLite (API `backup`), en-tête
  JSON (clé de signature des curseurs) + octets SQLite, chiffrés
  AES-256-GCM avec la clé `backup.key` (hors des sauvegardes) ; rétention
  bornée (`synapse backup prune --keep`) et preuve de restauration
  (`synapse backup verify --latest`, scratch isolé).
- `synapse backup restore` : refuse si le service tourne (verrou), vérifie
  magic + authentification GCM + `PRAGMA integrity_check`, remplace la
  base atomiquement (nettoyage WAL), rétablit `cursor.key`. Aucun
  identifiant, date ou statut n'est régénéré.
