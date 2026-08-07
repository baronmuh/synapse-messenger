# Groups — ready-to-use scenarios

All these commands belong to the **A** (account) family. Identity:
`--my-name` + password (stdin). Forms verified against a real server.

**Two complementary forms**:
- **CLI** (`group` group): operations take the group **name**;
- **Python client**: operations take the **`group_id`** (UUIDv4
  returned by `create_group` / `list_my_groups`).

## Scenario 1 — Create a group

```bash
echo "$MOT_DE_PASSE" | synapse group create direction \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

```python
from synapse.client import Client
c = Client("/var/run/synapse/synapse.sock")
me, pwd = "my-account", "my-password"
g = c.create_group("direction", me, pwd)
group_id = g["group_id"]            # UUIDv4 — to reuse in Python
```

## Scenario 2 — Manage members

```bash
echo "$MOT_DE_PASSE" | synapse group add-member direction comptable \
    --my-name "$NOM_DE_COMPTE" --password-stdin
echo "$MOT_DE_PASSE" | synapse group remove-member direction comptable \
    --my-name "$NOM_DE_COMPTE" --password-stdin
echo "$MOT_DE_PASSE" | synapse group members direction \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

```python
c.add_group_member(group_id, "comptable", me, pwd)
c.remove_group_member(group_id, "comptable", me, pwd)
membres = c.get_group_members(group_id, me, pwd)
```

## Scenario 3 — Send and read group messages

```bash
echo "$PASSWORD" | synapse group send direction "Weekly meeting at 10am" \
    --my-name "$NOM_DE_COMPTE" --password-stdin
echo "$MOT_DE_PASSE" | synapse group messages direction \
    --my-name "$NOM_DE_COMPTE" --password-stdin
```

```python
c.send_group_message(group_id, "Weekly meeting at 10am",
                     my_name_auth=me, my_password_auth=pwd,
                     client_message_id="g-1")       # optionnel, unique
messages = c.get_group_messages(group_id, me, pwd, limit=50)
```

## Scenario 4 — List your groups

```bash
echo "$MOT_DE_PASSE" | synapse group list --my-name "$NOM_DE_COMPTE" --password-stdin
```

```python
mes_groupes = c.list_my_groups(me, pwd, limit=50)
for g in mes_groupes["groups"]:
    print(g["group_id"], g["name"], g["member_count"])
```

## Group-specific pitfalls

1. **Confondre nom et `group_id`** : le CLI prend le **nom** ; le client
   Python prend l'**UUIDv4**. Passer l'UUID au CLI (ou le nom au client)
   renvoie `INVALID_ARGUMENT` / `GROUP_NOT_FOUND`.
2. **Nonexistent group**: `GROUP_NOT_FOUND` — check via `group list` /
   `list_my_groups`.
3. **Member outside the org**: adding may be refused depending on the
   policy (`POLICY_DENIED`) — only organization members (or authorized)
   ones are accepted.
4. **Duplicate `client_message_id`** in a group: `INVALID_ARGUMENT` —
   changez l'identifiant.
