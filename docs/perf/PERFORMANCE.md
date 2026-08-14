# Performance de Synapse — analyse, mesures et optimisations

Rapport d'optimisation (démarche : mesurer avant de modifier, optimiser par
étapes validées, ne jamais sacrifier la spécification ni la sécurité).

## 1. Méthodologie

1. **Analyse statique** des chemins critiques : transport (socket Unix),
   validation d'enveloppe, authentification, stockage SQLite (WAL).
2. **Mesure du coût par brique** : hacheur de production (Argon2id réel)
   vs hacheur rapide (isole le temps de service pur), micro-benchmarks
   ciblés, `EXPLAIN QUERY PLAN`.
3. **Baseline** : `benchmarks/bench.py` (hacheur de production, machine de
   référence i5-6300U, 4 threads).
4. **Recherche de pratiques** : durabilité SQLite WAL
   (`synchronous=NORMAL` vs `FULL`), threading Python et GIL avec
   extensions C, contention d'écriture SQLite, coût d'ouverture de
   connexions.
5. **Optimisation → tests → benchmark**, par commits séparés.

## 2. Analyse : où est le coût ?

### 2.1 Le goulot : Argon2id (contrat de la spécification)

La spécification (§3.3, contrainte 2, §15) impose : identifiants dans
chaque commande, vérification Argon2id (64 MiB, 3 itérations, parallélisme
1, non configurable) dans chaque commande, aucun mécanisme de session.

Mesures (machine de référence) :

| Mesure | Valeur |
|---|---|
| Vérification Argon2id isolée | 275 ms |
| Scaling 1 thread | 3,9 vérifications/s |
| Scaling 4 threads | 8,3 vérifications/s |
| Scaling 8 threads | 8,6 vérifications/s |

Le scaling est **fortement sous-linéaire** (2,1× pour 4 threads) :
Argon2id est *memory-hard* — 4 threads × 64 MiB travaillent sur un cache
L3 de ~4 MiB, la bande passante mémoire est saturée. C'est une propriété
**voulue** d'Argon2id (l'attaque parallèle est aussi coûteuse que le
calcul légitime). Conséquence : le serveur ne peut pas dépasser ~8,5
requêtes authentifiées/s sur cette machine, quel que soit le nombre de
threads.

### 2.2 Le budget de service (hors Argon2id)

Mesuré avec le hacheur rapide (machine libre) — décomposition :

| Brique | Coût |
|---|---|
| `db.connect()` complet (mkdir, chmod, schéma ×2, PRAGMA ×3, sqlite_master) | ~1,04 ms |
| SELECT simple sur connexion chaude | 2,5 µs |
| Hash rapide (8 KiB) | 0,05 ms |
| Total service d'une lecture (avant optimisation) | ~2,2-2,5 ms |
| Total service d'un envoi (avec fsync `synchronous=FULL`) | ~6,2 ms |

L'Argon2id (255-275 ms) représente **~99 %** du temps de requête. La part
optimisable (service) est d'environ 1 à 2,5 ms, soit 0,4 à 1 % de la
latence en production.

### 2.3 Plans d'exécution SQL

Les requêtes chaudes (`message_page`, `unread_by_sender`,
`conversation_page`) utilisent toutes les index
(`idx_messages_recipient`, `idx_messages_conversation`), la sous-requête
d'annuaire est indexée (clé primaire). Aucune requête coûteuse détectée.

## 3. Baseline (hacheur de production, `benchmarks/bench.py --quick`)

| Scénario | RPS | lat. moy | p50 | p95 | p99 | err | CPU | RSS pic |
|---|---|---|---|---|---|---|---|---|
| W=1 mixte | 2,68 | 263 ms | 262 | 274 | 274 | 0 % | 0,99/4 | 140 MiB |
| W=8 régime établi | 8,16 | 958 ms | 979 | 1354 | 1457 | 0 % | 3,48/4 | 531 MiB |
| W=16 | 8,54 | 1177 ms | 1230 | 1448 | 1499 | 0 % | 3,57/4 | 637 MiB |
| W=32 | 9,33 | 2545 ms | 2553 | 3592 | 3613 | 0 % | 3,58/4 | 1166 MiB |
| connexion/requête (E) | 6,07 | 866 ms | — | 1062 | 1130 | 0 % | 3,68/4 | 581 MiB |

Constats :

- Le RPS plafonne à ~8,5-9,3 : la **limite matérielle** Argon2id
  (8,6 vérifications/s mesurées) est atteinte à ~97 %.
- La latence explose avec la concurrence (file d'attente sur les 4 cœurs) :
  p99 passe de 274 ms (W=1) à 3613 ms (W=32).
- La mémoire suit le nombre de vérifications concurrentes
  (64 MiB chacune) : pic 1,2-1,3 GiB à W=32 — coût du contrat Argon2id.

## 4. Optimisations réalisées

### 4.1 Réparation du harnais de benchmark (commit `6542035`)

`benchmarks/bench.py` envoyait `api_version: "v1"` depuis la migration
v2 : la baseline échouait à 100 % (`INVALID_ARGUMENT`). Migration complète
au modèle Organisation (`create_organization`, organisation `bench_org`,
option `--org-password`). Le harnais est de nouveau une mesure fiable.

### 4.2 Schéma vérifié une seule fois par processus et par base (commit `5f64433`)

Chaque requête ouvrait une connexion SQLite qui répétait ~1 ms de
contrôles sans effet après la première ouverture (`mkdir`, `chmod`,
`PRAGMA table_info` ×3, `sqlite_master`, `_migrate`). Désormais
`ensure_storage` mémorise les chemins vérifiés ; la vérification complète
n'a lieu qu'à la première ouverture du processus sur une base.

Gains mesurés (hacheur rapide, machine libre) :

| Commande | Avant | Après | Gain |
|---|---|---|---|
| get_messages | 2,446 ms | 1,764 ms | -28 % |
| get_notifications | 2,755 ms | 2,084 ms | -24 % |
| get_org_agents | 2,412 ms | 1,697 ms | -30 % |
| help | 3,478 ms | 2,755 ms | -21 % |

### 4.3 Profil détaillé (cProfile) et optimisations du service (commits suivants)

Le profilage de 600 requêtes réelles a révélé que la part de service
(~2,3 ms) se décomposait ainsi : ~1,7 ms de **création/destruction d'un
thread par connexion** (le client ouvre une connexion par commande),
~0,76 ms de SQL dont des PRAGMA/chmod répétés à chaque connexion.

Trois optimisations en découlent (voir `git log`) :

1. **PRAGMA one-shot** : `journal_mode=WAL` (persistant dans le fichier) et
   `chmod` du fichier appliqués une seule fois par processus dans
   `ensure_storage`, au lieu de chaque connexion.
2. **Pool de threads serveur** (`_ConnectionPool`) : les travailleurs
   daemon sont réutilisés entre les connexions au lieu d'un thread par
   connexion ; la borne anti-DoS (64 connexions simultanées) est conservée
   par un sémaphore de même taille — aucune file d'attente supplémentaire.
3. **Bornage des calculs Argon2id concurrents** (sémaphore ~2 × cœurs) :
   chaque vérification alloue 64 MiB ; sans borne, 64 connexions
   concurrentes consommeraient jusqu'à 4 GiB. Le scaling d'Argon2id étant
   memory-hard, la borne ne réduit pas le débit mais borne la mémoire sous
   charge (pratique standard : SuperTokens `hashing_pool_size`, PingDS).

Gains mesurés (hacheur rapide, machine libre) :

| Commande | Origine | Après opt. 1 | Après opt. 1+2+3 | Gain total |
|---|---|---|---|---|
| get_messages | 2,446 ms | 1,764 ms | 1,545 ms | -37 % |
| get_notifications | 2,755 ms | 2,084 ms | 1,863 ms | -32 % |
| get_org_agents | 2,412 ms | 1,697 ms | 1,477 ms | -39 % |
| help | 3,478 ms | 2,755 ms | 2,651 ms | -24 % |
| send_message | 6,191 ms | 6,675 ms | 6,820 ms | inchangé (fsync FULL dominant) |

### 4.4 Résultats en production (bench complet, Argon2id réel)

| Scénario | Avant (baseline) | Après opt. 1 | Après opt. 1+2+3 | Gain total |
|---|---|---|---|---|
| W=1 lat. moyenne | 263 ms | 276 ms | 256 ms | -2,7 % |
| W=8 régime établi RPS | 8,16 | 8,56 | **9,54** | **+16,9 %** |
| W=8 lat. moyenne | 958 ms | 924 ms | 893 ms | -6,8 % |
| W=8 p95 | 1354 ms | 1265 ms | 1155 ms | **-14,7 %** |
| W=8 p99 | 1457 ms | 1286 ms | 1174 ms | **-19,4 %** |
| W=16 RPS | 8,54 | 8,75 | 8,96 | +4,9 % |
| W=32 RPS | 9,33 | 7,34 (bruit) | 9,35 | ~0 % |
| **W=32 RSS pic** | **1166 MiB** | 1025 MiB | **550 MiB** | **-53 %** |
| W=16 RSS pic | 637 MiB | 707 MiB | 573 MiB | -10 % |
| Erreurs W=16/W=32 | 9,8 % / 24,5 % | 6,3 % / 30,8 % | **0 % / 0 %** | éliminées |
| CPU W=8 (par requête) | 3,48/4 cœurs → 0,426 s·cœur/req | — | 3,79/4 cœurs → 0,397 s·cœur/req | **-6,8 % CPU/requête** |

Constats :

- **Mémoire bornée** : le RSS reste ~550-600 MiB quelle que soit la
  concurrence (le sémaphore limite à ~8 calculs × 64 MiB), au lieu de
  croître jusqu'à 1,2-1,3 GiB puis 4 GiB théoriques.
- **Débit réellement amélioré** : le pool de threads élimine le coût de
  création des threads ; le RPS W=8 passe de 8,16 à 9,54 (+16,9 %) et les
  erreurs de saturation (ConnectionResetError) disparaissent.
- La latence sous saturation (W=32, p99) reste élevée — c'est la file
  d'attente Argon2id, plafond matériel de la machine de référence.

**Sûreté** : le verrou du service garantit qu'un seul processus écrit la
base ; les outils hors-ligne (installation, sauvegarde, restauration)
ouvrent la base en premier et effectuent la vérification complète à ce
moment-là. Aucun changement de comportement fonctionnel (tests verts).

## 5. Optimisations évaluées et rejetées (avec justification)

| Optimisation | Gain potentiel | Raison du rejet |
|---|---|---|
| `synchronous=NORMAL` (SQLite WAL) | -1 à -4 ms sur les envois (fsync supprimé par commit) | Perte de la durabilité « coupure de courant » (les derniers commits peuvent disparaître, sans corruption). La spécification exige la persistance après redémarrage ; FULL est la garantie conservatrice. Gain global < 1 % (Argon2id domine). |
| Pool de connexions SQLite partagées | ~0,2 ms/requête | La contention d'écriture, pas l'ouverture de connexion, est le sujet SQLite (littérature confirmée). Complexité de cycle de vie non justifiée pour ~0,1 %. |
| Réutilisation de la connexion SQLite par connexion socket persistante | ~1 ms/requête pour les clients persistants | Le client réel ouvre une connexion par requête ; gain réservé au bench « persistant ». Complexité non justifiée. |
| asyncio / event-driven | — | Workload CPU-bound (Argon2id, extension C qui relâche le GIL) : le threading actuel est le bon modèle ; asyncio n'apporterait rien et casserait la concurrence des extensions C. |
| **Cache de vérification d'authentification** | **×3-×10 de débit effectif** | Voir section 6 — **décision utilisateur requise** (changement du contrat de sécurité). |

## 6. Cache de vérification d'authentification — proposition d'origine

**État : ACTÉ (SPEC.txt §V.6.1), formalisé (SPEC.txt §19.1) puis IMPLÉMENTÉ
(F1, commit `087dd21`) — la mesure figure au §11.** Ce paragraphe conserve
la justification de conception initiale.

### Principe

Après une vérification Argon2id **réussie** de `(principal, mot de
passe)`, mémoriser une entrée courte :
`clé = (type, nom, SHA-256(hash_stocké ‖ mot_de_passe_fourni))`, avec
expiration (TTL). Les commandes suivantes du même principal avec le même
mot de passe comparent la clé (µs) au lieu d'exécuter Argon2id (275 ms).

### Ce qui reste strictement inchangé

- Les **échecs** ne sont jamais cachés : chaque tentative échouée exécute
  Argon2id et compte dans la limitation (5 échecs / 15 min) → brute-force
  et énumération intactes.
- Le **leurre anti-chronométrage** (comptes inexistants) reste intégral.
- La **rotation du mot de passe** invalide le cache implicitement (le
  `hash_stocké` change → la clé ne matche plus).
- Les identifiants restent exigés dans chaque commande (aucune session
  persistante : le cache expire en TTL).

### Ce qui change

La promesse implicite « chaque commande paie 64 MiB de calcul » disparaît
pour les clients dont le mot de passe a été vérifié il y a moins du TTL.
Un client **légitime** (mot de passe valide) peut enchaîner les commandes
à ~2 ms au lieu de ~260 ms — c'est l'objectif. Un attaquant ne gagne
rien : il lui faut le mot de passe valide (qu'il a déjà, sinon il ne
s'authentifie pas).

### Impact attendu (estimé)

Workload réel d'un agent IA (enchaînement de lectures/écritures dans un
tour) : latence des commandes suivantes ~2 ms au lieu de ~260 ms, débit
effectif ×3-×10. Le harnais de benchmark mesurerait un RPS massivement
supérieur (les mêmes mots de passe se répètent).

### Décision requise

- **OUI** : implémenter (TTL 30-60 s, borné à 4096 entrées, purge LRU),
  documenter dans SPEC.txt (§3.3, contrainte 2) et docs/securite/SECURITY.md,
  re-benchmarker.
- **NON** : la limite matérielle (~8,5 req/s sur la machine de référence)
  est un plafond accepté du contrat Argon2id.

## 8. Comportement sur charge prolongée (fuites mémoire)

Mesures (hacheur rapide, 20 000+ requêtes) :

- RSS : 38 → 44 → 48 → 51 → 53 MiB par tranches de 5000 requêtes — une
  croissance **décroissante** (delta 6 → 4 → 3 → 2 MiB/tranche) : c'est le
  réchauffement normal des stacks des 64 travailleurs du pool et des
  arènes pymalloc (réutilisables), pas une fuite linéaire.
- **tracemalloc** (empreinte Python réelle, indépendante de l'allocateur) :
  0,13 Mo après 3000 requêtes, 0,18 Mo après 6000 — croissance de 47 Ko
  entre tranches (**~16 o/requête**), aucune fuite d'objets Python.

Débit soutenu mesuré : 574 req/s (hacheur rapide) puis 620 req/s,
constants entre les tranches — pas de dégradation sous charge prolongée.

## 9. État final

- Dépôt propre ; commits : réparation du harnais, schéma one-shot,
  PRAGMA one-shot, pool de threads, sémaphore Argon2id, documentation.
- 583 tests (suite complète) verts après optimisation.
- Couverture : 20/21 modules à 100 %, une ligne défensive restante.
- Aucune modification de la spécification, du modèle de données, des
  erreurs ou du comportement fonctionnel.

## 10. Points restants

- ~~Décision sur le cache de vérification~~ — actée, implémentée et mesurée
  (F1, voir §11) ; le seul levier de débit restant est documenté au §12.6
  (multi-processus, free-threaded — rejetés avec justification).
- La latence sous saturation (W=32) reste élevée : file d'attente
  Argon2id, plafond matériel de la machine de référence.
- Le bench « complet » (non `--quick`) sur une machine plus puissante
  donnerait une mesure de la capacité au-delà de la référence.

## 11. V3 — Cache de vérification d'authentification (F1), mesuré le 5 août 2026

Décision actée (SPEC.txt §V.6.1), amendement SPEC.txt §19.1, implémentation :
`synapse/service.py` (`_cached_password_ok`, cache borné 2048 entrées,
TTL 30 s par défaut, échecs jamais mémorisés, invalidation automatique à
toute rotation du hash). Harnais : `benchmarks/bench.py --quick`, serveur en
processus séparé, Argon2id de production, config `/tmp/synapse-v2/
config.json` (machine de référence i5-6300U).

| Scénario | RPS v2 | RPS v3 | moy v2 | moy v3 | p95 v2 | p95 v3 | erreurs v3 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W=1 | 2,4-3,0 | 188-311 | 262-317 ms | 2,2-3,7 ms | 320-414 ms | 5-8 ms | 0 % |
| W=8 établi | 7,2-9,2 | 128-162 | 866-1120 ms | 36-39 ms | 990-1890 ms | 105-122 ms | 0 % |
| W=16 établi | 7,9-8,7 | 128-162 | 1164-1268 ms | 62-83 ms | 1726-1903 ms | 202-265 ms | 0 % |
| W=32 établi | 7,6-8,1 | 109-136 | 1729-2081 ms | 154-170 ms | 2579-3473 ms | 576-685 ms | 0-0,2 %* |

\* 2 `ConnectionResetError` transitoires au balayage W=32 (churn, plafond
anti-DoS 64 connexions), identiques au jalon v2.

**Lecture du résultat (lecture colonne par colonne : « v2 » = avant le
cache, « v3 » = avec le cache).** Le débit passe de ~9,5 RPS (plafond
Argon2id) à ~130-200 RPS en régime établi (**×14-×20**), la latence moyenne
de ~900 ms à ~36 ms (**÷25**), p95 de ~1000-1900 ms à ~105-122 ms (**÷10**).
Le CPU par requête chute de 0,397 s·cœur à ~0,013-0,02 s·cœur :
Argon2id n'est plus payé par requête, mais une fois par principal par fenêtre
de 30 s (pics p99 et RSS 230-726 MiB = ces vérifications sporadiques). La
surcharge du cache lui-même est négligeable (une lecture de dict sous
verrou, ~µs) ; le nouveau goulot est SQLite + la sérialisation du pool.

**Limite de vérification.** Le gain s'exprime pour un même principal actif
(cas d'usage : agent qui poll, orchestrateur). Un flux de principaux tous
distincts conserve le plafond matériel ~9,5 RPS (chaque nouveau principal paie
sa vérification). Le contrat de sécurité (SPEC.txt §19.1) est inchangé dans
l'esprit : authentification vérifiée à chaque commande, fenêtre courte,
invalidation par rotation, échecs jamais mémorisés, cache non persistant.

**Modifications du harnais (bench v3).** `benchmarks/bench.py` :
`open_unix_connection(limit=256*1024)` (la réponse `help` complète, ~66 Ko en
JSON, dépassait le limit asyncio par défaut) ; `business_reference: None`
ajouté au payload `send_message` (les clés exactes sont exigées par la
validation v3).

## 12. V4 — Service pur : pool de connexions SQLite + cache help, mesuré le 5 août 2026

### 12.1 Analyse : après le cache F1, où est le coût ?

Avec le cache d'authentification (F1), Argon2id n'est plus payé par requête :
le goulot est devenu le **coût de service pur**. Décomposition par briques
(300 itérations, cache auth chaud, base réelle) :

| Brique | Coût |
|---|---|
| `db.connect` (ouverture réelle + PRAGMA par connexion) | **1,244 ms** |
| parse + validation d'enveloppe | 0,044 ms |
| authfail prune + count (DELETE 0 ligne : pas d'écriture WAL) | 0,042 ms |
| accounts.get | 0,021 ms |
| cache auth (hit) | 0,002 ms |
| authfail clear | 0,017 ms |
| BEGIN + message_page + COMMIT | 0,113 ms |
| row_to_message × 50 | 0,130 ms |
| json.dumps réponse | 0,153 ms |
| logging (JsonFormatter + fichier) | 0,080 ms |
| **Total service** | **~1,9 ms** |

`db.connect` représente **~60 % du temps de service** (1,24 ms sur 1,9 ms) :
chaque requête ouvrait une connexion SQLite (premier accès au fichier +
3 PRAGMA par connexion), mesuré 0,111 ms pour `sqlite3.connect` seul mais
~1,2-1,7 ms dès le premier `execute`/PRAGMA.

Mesures d'exclusion (ce qui n'est PAS le goulot, avec preuve) :

* **DELETE autocommit** (`authfail.prune`/`clear` par requête) : 0,046 ms —
  un DELETE sans ligne affectée n'écrit pas le WAL, donc pas de fsync ;
* **fsync** (`synchronous=FULL`) : 54 µs/fdatasync mesurés (SSD SanDisk
  X400) — le rejet de `synchronous=NORMAL` reste valable (contrat de
  durabilité) ET sans gain mesurable sur ce matériel ;
* **logging** : 0,080 ms par requête (append sur SSD) ;
* **strace -c** (serveur sous trace, 270 requêtes) : 81 % du temps cumulé
  dans `futex` (attentes/contention, threads) — le serveur est
  **GIL-bound** après F1 : le débit est limité par le temps Python par
  requête, pas par le CPU disponible (0,7-1,9 cœurs utilisés sur 4).

### 12.2 Optimisation 1 : pool de connexions SQLite par thread (commit `08388d4`)

Le serveur traite chaque requête dans un thread d'un pool fixe (64). Chaque
thread conserve désormais **sa** connexion SQLite (jamais partagée entre
threads, aucune synchronisation) : l'ouverture (1,24 ms) n'est payée qu'une
fois par thread. La connexion est remise à zéro avant réutilisation
(ROLLBACK si une transaction résiduelle) ; une connexion fermée
explicitement (`conn.close()`) est détectée et remplacée ; un thread qui
change de base (tests, outils hors-ligne) ferme la précédente — le nombre
de descripteurs reste borné (1 par thread).

Sûreté : sémantique inchangée (connexion transactionnelle via
`begin_immediate`/`begin_read`, fermable), 748 tests verts, aucun
changement de contrat. Le correctif « 1 connexion par thread » (au lieu
d'un cache multi-bases) est venu de la suite complète : un cache multi-bases
faisait fuir un descripteur par configuration de test dans le thread
principal de pytest (Errno 24 « Too many open files ») — désormais borné.

### 12.3 Optimisation 2 : documentation `help` mémorisée (commit `1b270d7`)

`help` ne touche pas la base : le pool ne l'aidait pas. La documentation est
statique par processus (dérivée de constantes) ; `build_documentation`
reconstruisait ~1,31 ms de texte (54 Ko) à chaque appel. Mémorisée
(`lru_cache`, 64 entrées) : **0,0006 ms** mesurés. Aucun changement de
comportement (tests help verts), coût mémoire : une copie du texte.

### 12.4 Bench avant/après (Argon2id production, machine de référence, `--quick`)

« Avant » = jalon v3 (cache F1, sans pool), « Après » = pool + cache help.
Run de confirmation sur machine calme :

| Scénario (W=8) | RPS avant | RPS après | Gain | lat. moy | p95 | p99 |
|---|---|---|---|---|---|---|
| help | 115,6 | 247,3 | +113,9 % | 45,2 → 21,0 ms | 79,7 → 33,6 | 144 → 42 |
| get_agent_description | 225,4 | 395,7 | +75,5 % | 23,6 → 14,4 ms | 44,3 → 23,2 | 91 → 28 |
| get_messages | 159,0 | 208,2 | +30,9 % | 33,3 → 25,5 ms | 49,8 → 37,9 | 123 → 42 |
| get_conversation | 142,8 | 279,9 | +96,0 % | 37,1 → 20,2 ms | 53,2 → 39,4 | 170 → 48 |
| get_notifications | 191,6 | 333,6 | +74,1 % | 29,7 → 15,9 ms | 49,5 → 22,8 | 114 → 27 |
| read_message | 80,7 | 153,9 | +90,6 % | 65,8 → 34,2 ms | 259,9 → 126,9 | 780 → 356 |
| send_message | 84,5 | 196,4 | +132,4 % | 61,6 → 26,9 ms | 241,4 → 117,4 | 628 → 237 |
| mark_conversation_no_reply | 90,3 | 105,5 | +16,8 % | 58,7 → 49,5 ms | 256,2 → 174,1 | 741 → 548 |
| **mixte W=8** | **138,5** | **279,3** | **+101,6 %** | 38,2 → 18,9 ms | 102,9 → 62,1 | 172 → 112 |
| régime établi W=8 | 166,9 | 416,8 | +149,7 % | 47,7 → 18,9 ms | 113,5 → 46,9 | 832 → 114 |
| régime établi W=16 | 206,8 | 427,2 | +106,6 % | 67,7 → 37,2 ms | 200,2 → 123,6 | 467 → 310 |
| régime établi W=32 | 116,3 | 385,4 | +231,5 % | 274,6 → 102,2 ms | 628,4 → 196,4 | 5249 → 2837 |
| connexion/requête (client réel) | 130,4 | 175,3 | +34,5 % | 40,6 → 27,8 ms | 97,7 → 74,8 | 250 → 137 |
| persistant | 162,9 | 227,5 | +39,7 % | 32,5 → 23,0 ms | 95,7 → 83,8 | 145 → 167 |

Balayage de concurrence (mixte, médiane de 2 passages) : W=1 +177 %,
W=2 +136 %, W=4 +90 %, W=8 +63 %, W=16 +93 %, W=32 +67 %. Le RSS pic du
mixte W=8 passe de 171 à 65 MiB (moins de connexions éphémères).

Lecture du tableau : les lectures et `help` doublent ; les écritures suivent
(+17 à +132 %, la variance est plus forte car elles sont sérialisées par
SQLite). Le run « pool seul » (sans cache help) mesurait déjà mixte W=8 à
246,7 RPS (+78 %) — le cache help ajoute le reste sur la part help du mixte.

### 12.5 Charge prolongée (fuites)

25 000 requêtes chaudes en 5 tranches de 5 000 (serveur réel) :

| Tranche | req/s | RSS MiB | fds |
|---|---|---|---|
| 1 | 1321 | 67 | 135 |
| 2 | 1147 | 67 | 135 |
| 3 | 1189 | 67 | 135 |
| 4 | 1306 | 67 | 135 |
| 5 | 1225 | 67 | 135 |

RSS **stable** (67 MiB, croissance nulle) et descripteurs **stables** (135) :
le pool par thread ne fuit ni mémoire ni fd. Débit soutenu ~1 150-1 300
req/s (lecture chaude, 1 connexion), sans dégradation.

### 12.6 Optimisations évaluées et rejetées (avec preuve)

| Optimisation | Gain potentiel | Raison du rejet |
|---|---|---|
| Regrouper l'audit (2e transaction) dans la transaction de la commande | ~0,05 ms/écriture | fsync négligeable sur SSD (54 µs) ; le coût de la 2e transaction est ~2-3 requêtes SQL + verrous ; restructuration risquée (early returns d'idempotence) pour un gain < 1 %. |
| Compteur de requêtes sans verrou (`itertools.count`) | ~1 µs/req | Le verrou actuel est correct ; le gain est dans le bruit de mesure. |
| `synchronous=NORMAL` | — | Déjà rejeté en v2 (durabilité, contrat) ; de plus, fdatasync mesuré à 54 µs sur ce SSD : pas de gain significatif à attendre. |
| Multi-processus (workers fork) | ×2-3 potentiel (dépasser le GIL) | Changement architectural majeur : verrou de service (écrivain unique), état en mémoire par processus (cache auth), tests à adapter. Le cas d'usage (messagerie locale entre agents) est déjà servi à ~280-400 RPS ; risque disproportionné. Piste documentée si le besoin de débit croît. |
| Python free-threaded (3.13t/3.14t) | ×2 potentiel sans changer le code | Expérimental : pénalité mono-thread 5-10 %, maturité des extensions C (sqlite3, argon2) à valider ; incompatible avec l'exigence de stabilité du projet. |

### 12.7 État final

* Optimisations : pool de connexions SQLite par thread (`08388d4`, mélangé
  involontairement au commit de conformité de l'agent partageant le dépôt),
  cache help (`1b270d7`) ; 748 tests verts (suite complète), couverture
  inchangée.
* Gains mesurés : mixte W=8 **+102 %** (138,5 → 279,3 RPS), latence moyenne
  **-50 %**, p95 **-40 %**, RSS **-62 %** ; régime établi jusqu'à **+232 %**
  (W=32).
- Le plafond restant est le **GIL** : le temps Python par requête (~0,6 ms
  de briques + overhead de threading) borne le débit à ~1 000 req/s
  théoriques pour un cœur ; mesuré ~280-430 RPS selon la concurrence.

## 13. V5 — Re-baseline, help pré-sérialisée, orjson, PRAGMA de lecture (mission prompt1, Phase 2)

### 13.1 Re-baseline avant/après cache F1 (mesures fraîches)

Harnais `--quick`, Argon2id production, machine de référence ; « TTL=0 »
désactive le cache via `config.auth_cache_ttl_seconds` (le code ne
mémorise alors rien : `now + 0` expire immédiatement).

| Scénario (W=8) | Sans cache | Avec cache | Gain |
|---|---|---|---|
| help | 3,7 RPS / 1340 ms | 250-523 RPS / ~20 ms | ×68-×140 |
| get_messages | 4,5 RPS / 1085 ms | 228 RPS / 23 ms | ×51 |
| mixte | 5,1 RPS / 941 ms | 234-336 RPS / ~22 ms | ×46-×66 |

Erreurs du scénario connexion-par-requête : 18,4 % → 0 %.

### 13.2 Optimisations V5 (mesurées le 5 août 2026)

V5 regroupe : **re-baseline fraîche** (§13.1), **`help` pré-sérialisée**
(octets JSON construits une fois, servis tels quels — plus de
sérialisation par requête), **`orjson`** pour l'encodage des réponses
(plus rapide que le json standard sur les gros payloads) et **PRAGMA de
lecture** (journal_mode, synchronous, mmap_size réglés à l'ouverture de
chaque connexion). Détail des mesures et rejets : §13.4-§13.5.

1. **Réponse `help` complète pré-sérialisée** : enveloppe + octets en cache
   (`helpdoc.full_help_envelope/payload`, `service.process` →
   `meta["pre_serialized"]`, `server._write_response`) — élimine ~1,5 ms
   d'encodage par appel help (55 Ko statiques). help : 523 → 650 RPS en
   médiane (échantillon haut : +159 %).
2. **orjson pour la sérialisation** (`synapse/jsonutil.py`, repli stdlib ;
   parsing conservé sur stdlib pour le hook anti-doublons) — dumps ~10×
   plus rapide sur les formes de réponse réelles. persistant : 265 → 321
   RPS (+21 %), régime établi W=32 : +28,5 %.
3. **PRAGMA de lecture** (`db.py::_open_connection`) : `mmap_size=64 MiB`,
   `temp_store=MEMORY` — neutres au bench, sans changement de sémantique.

Le mixte W=8 reste dans le bruit de mesure (±20 % machine) ; aucune
régression fonctionnelle (794 tests verts).

### 13.3 Risque découvert : bug WAL-reset SQLite (intégrité)

Vérifié sur sqlite.org/wal.html §11 : SQLite 3.7.0 → 3.51.2 porte un bug
rare de corruption déclenché par deux connexions (threads/processus
séparés) écrivant/checkpointant au même instant ; correctifs 3.51.3 +
backports **3.44.6 et 3.50.7 uniquement**. Notre environnement :
libsqlite3 **3.45.1** (Zorin 18.1/Ubuntu 24.04, pas de mise à jour apt) —
**non couvert**, architecture correspondant aux conditions déclenchantes.

**Décision de traitement (actée) :**

1. **Verrou d'écriture applicatif** (`db._WRITE_LOCK`, RLock, dans
   `begin_immediate`) : un seul écrivain à la fois → la course (≥2
   connexions écrivant/checkpointant au même instant) devient
   structurellement impossible. Les écritures étaient déjà sérialisées par
   SQLite lui-même (BEGIN IMMEDIATE) : le verrou ne déplace que l'attente
   côté applicatif ; les lecteurs (snapshots, jamais de checkpoint) ne
   sont pas concernés. Mesure avant/après : §13.5.
2. **Alerte à l'installation** (`install.sh`) : vérifie
   `sqlite3.sqlite_version` et prévient si la lib est dans la plage
   affectée (renvoi vers ce paragraphe).
3. **Surveillance** : quand le système livrera libsqlite3 ≥ 3.51.3 (ou un
   backport), la mise à jour referme définitivement la boucle. Les
   sauvegardes chiffrées restent le filet.
4. **pysqlite3-binary écarté aujourd'hui** : la dernière wheel publiée
   embarque SQLite **3.51.1, encore dans la plage affectée** (vérifié
   juillet 2025, issue Hermes #69784) — pas une solution clé en main.
   À réévaluer quand elle livrera ≥ 3.51.3.

### 13.4 Rejets (mesurés ou documentés)

orjson pour le parsing (hook anti-doublons non supporté — orjson accepte
les doublons, dernier gagnant), `sys.setswitchinterval` (latence/fairness),
QueueHandler (sémantique des logs), index partiel `read_at` (index existant
suffisant), multi-processus/free-threaded (déjà rejeté §12.6).

### 13.5 Mesure du verrou d'écriture (avant/après, §13.3 décision 1)

Bench `--quick` (mêmes config/scénarios ; avant = 3 passes ttl30, après =
2 passes, machine identique, cron en pause) :

| Scénario | Avant (méd) | Après (méd) | Δ |
|---|---|---|---|
| mixte:W8 | 335,7 RPS | 318,4 RPS | −5,2 % |
| send_message | 219,0 | 244,6 | +11,7 % |
| mark_conversation_no_reply | 132,5 | 169,1 | +27,7 % |
| persistant | 251,3 | 295,5 | +17,6 % |
| regime_etabli:W32 | 253,4 | 320,0 | +26,3 % |
| **get_agent_description (lecture pure)** | 419,9 | 372,7 | −11,2 % |
| **read_message (lecture pure)** | 297,4 | 244,7 | −17,7 % |

Lecture : les deux scénarios **purs de lecture** (jamais de
`begin_immediate`) varient de −11 % à −18 % entre les passes : la bande de
bruit machine est ≥ ±18 %. Dans cette bande, le mixte W=8 (−5,2 %) et les
scénarios d'écriture (tous ≥ +11 %) ne montrent **aucune régression
attribuable au verrou** — cohérent avec l'analyse : les écritures étaient
déjà sérialisées par SQLite (BEGIN IMMEDIATE), le verrou ne déplace que
l'attente. Test dédié : `test_concurrent_writes_keep_database_integrity`
(8 threads + `PRAGMA integrity_check` = ok).

