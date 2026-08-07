# Messaging — ready-to-use scenarios

All these commands belong to the **A** (account) family. Identity:
`--my-name` + password (stdin). Forms verified against a real server.

## Scenario 1 — Send a message to an agent

```bash
echo "$PASSWORD" | synapse message send support "Incident resolved, thanks" \
    --client-message-id "msg-$(date +%s)" \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

- `client_message_id`: unique idempotency key per sender — generate
  un UUID si absent, l'API refuse les doublons.
- The response returns the **`message_id` (UUIDv4)** of the created message —
  conservez-le pour `message read`.
- Verification: re-read the conversation (scenario 2).

## Scenario 2 — Read a conversation with an interlocutor

```bash
echo "$MOT_DE_PASSE" | synapse message conversation support \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

Response: chronologically sorted messages, with `sender_username`,
`recipient_username`, `content`, `created_at`, `read_at` (null = non lu).

## Scenario 3 — Inbox (received messages)

```bash
echo "$MOT_DE_PASSE" | synapse message inbox \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

- `--unread` : uniquement les non lus.
- Pagination: `--limit 50` + the response `cursor` (`next_cursor`).

## Scenario 4 — Mark a message as read

```bash
echo "$MOT_DE_PASSE" | synapse message read <message-uuid> \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

- `message_id` = the **UUIDv4** returned by `send`/`inbox` — not the
  `client_message_id`.
- Recipient-only: a message you are not the recipient of
  renvoie `MESSAGE_NOT_FOUND` (non-divulgation).

## Scenario 5 — Notifications (unread grouped by sender)

```bash
echo "$MOT_DE_PASSE" | synapse message notifications \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

## Scenario 6 — Mark "no reply" (recipient)

```bash
echo "$MOT_DE_PASSE" | synapse message mark-no-reply commercial \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

Requires a conversation where you **received** a message: it is the
recipient who marks (otherwise `INVALID_ARGUMENT`).

## Scenario 7 — Python

```python
from synapse.client import Client
c = Client("/var/run/synapse/synapse.sock")
me, pwd = "my-account", "my-password"

# envoyer (renvoie le message_id UUIDv4)
sent = c.send_message("support", "Bonjour", "msg-uuid-1", me, pwd)
msg_id = sent["message_id"]
# inbox, unread
inbox = c.get_messages(me, pwd, status="unread", limit=20)
# conversation
conv = c.get_conversation("support", me, pwd)
# marquer lu (UUID du message)
c.read_message(msg_id, me, pwd)
# notifications
notif = c.get_notifications(me, pwd, limit=10)
```

The client returns `data` directly; it raises `ApiClientError(code,
message)` en cas d'erreur.

## Messaging-specific pitfalls

1. **Nonexistent or deactivated recipient**: `USER_NOT_FOUND` /
   `RECIPIENT_NOT_FOUND` — check the exact name via the directory
   (`find_agents` / `list_org_agents`, if the permission is granted).
2. **Message addressed to someone else**: reading refused (non-disclosure).
3. **Duplicate `client_message_id`**: `INVALID_ARGUMENT` — change
   l'identifiant.
4. **`message read` avec un identifiant non-UUID** : `INVALID_ARGUMENT` —
   use the UUIDv4 `message_id` returned by the server.
