# Checklist SPEC-WEB — avancement

Résultat final de la suite complète : suite complète exécutée (2026-08-06)
→ **9 échecs identifiés, tous corrigés** (sémantique pré-SPEC-WEB : comptage
des comptes humains, principal_type 'human' créable, liste des 65 commandes,
département non assigné) ; re-vérification : les 9 fichiers en échec verts +
**lot ciblé des 34 fichiers impactés : 573 tests verts** + 20 fichiers
restants verts (couverture totale hors bench) + lot envois/messages/politiques
122 verts. Aucun échec résiduel. La vérification finale §8 a découvert et
corrigé un bug R4.3 (envoi vers org désactivée) — testé (C2.1) ; cas
particuliers C6.3/C4.1 testés (désactivation pendant session web).
Tests SPEC-WEB dédiés : D1 14, D3 19, D4 7, D5 33 (dont D2) = **73 tests**.

## Point D1 — Conversations avec contenu
- [x] Contrat get_org_conversations implémenté + validé — *commits e9a06cc ; renommé `list_org_conversations` (collision de préfixe help) ; validé par smoke réel : liste paginée avec participants/volume/non-lus (orga1/orga2, échanges externes visibles des deux côtés)*
- [x] Contrat get_org_conversation (contenu) implémenté + validé — *commit e9a06cc ; smoke réel : contenu « cross org » lu par l'humain, ACCESS_DENIED pour un agent, pagination, CONVERSATION_NOT_FOUND hors portée org ; lecture auditée (F11)*
- [x] Requêtes store/ (portée org, contenu) implémentées — *requêtes SQL portées-org dans `service.py` (`_human_list_org_conversations`, `_human_get_org_conversation`) : portée par appartenance de participant, contenu chargé à la demande, jamais dans le snapshot*
- [x] Tests D1 écrits et verts — *tests/test_spec_web_d1.py : 12 tests verts (2026-08-06) — portée org interne+externe, refus agent, CONVERSATION_NOT_FOUND hors portée, contenu, pagination liste+contenu, réponse humain, audit de lecture, snapshot sans contenu*

## Point D2 — Changement d'organisation
- [x] Re-connexion avec identifiants d'une autre organisation — *commit e69da40 ; session par utilisateur (org + mot de passe), bouton « Se déconnecter » → retour à l'écran de connexion → connexion à une autre org ; smoke réel : orga1/orga2/orga3 connectées successivement, verrouillage 429 par org indépendant*
- [x] Tests D2 écrits et verts — *test_webui.py::test_webui_switch_organization (2026-08-06) : login root_org → logout → login second_org → la session porte la nouvelle identité et les données suivent*

## Point D3 — Gestion orgs/agents (désactivation)
- [x] create_org implémenté + validé — *commit e9a06cc ; auth humaine requise (ACCESS_DENIED pour un agent — smoke réel), org + compte humain dans la même transaction, refus doublon*
- [x] disable_org implémenté + validé — *commit e9a06cc ; isolation (propre org uniquement), gel complet de l'auth (humain, agents, observateurs — smoke réel 7b), données intactes, idempotence*
- [x] enable_org implémenté + validé — *procédure locale `synapse-init-org --enable <org>` + mot de passe d'org (décision I15 actée dans SPEC_WEB) ; smoke réel : réactivation puis auth humaine+agent restaurées*
- [x] Tests D3 écrits et verts — *tests/test_spec_web_d3.py : 19 tests verts (2026-08-06) — create_org (humain requis, atomique, doublon, audit, mdp court), disable (gel absolu de l'auth, données intactes, isolation, refus agent), enable local (UNKNOWN_COMMAND côté API, preuve par mdp, inconnue, déjà active), garde-fous humains (suffixe réservé, non-désactivable, pas de mdp propre, description), change_agent_description*

## Point D4 — Compte humain
- [x] Compte humain auto-créé (mot de passe = org, principal_type human) — *commit e9a06cc ; auth déléguée au hash de l'org (jamais copié — hash sentinelle argon2 aléatoire), suffixe `_humain` réservé (create_agent refuse), non désactivable individuellement (deactivate/change_password/description refusés)*
- [x] install.py / init : auto-création — *`create_organization` crée l'org + son humain dans la même transaction ; backfill idempotent à la migration pour les orgs existantes ; smoke réel : « Compte humain créé : orga1_humain »*
- [x] Tests D4 écrits et verts — *tests/test_spec_web_d4.py : 7 tests verts (2026-08-06) — auto-création install, backfill multi-processus (base historique sans humain → redémarrage → humain créé, idempotent), sentinelle argon2 non-copiée, rotation du mot de passe d'org suivie par l'humain, visibilité snapshot, exclusion de list_org_agents, réception de messages*

## Point D5 — Sessions web (remplacement de la clé)
- [x] web.py : login/logout/session, clé statique supprimée — *commit e69da40 ; cookie HttpOnly SameSite=Strict TTL 15 min, max 3 sessions/org, rate-limit 5 échecs → 429 (smoke réel), logout, aucune route sans session (401), `synapse-web` sans --observer/--token*
- [x] config.py : session_ttl, login_max_attempts, max_sessions — *commit e69da40 : web_session_ttl_seconds (900), web_login_max_attempts (5), web_login_lockout_seconds (900), web_max_sessions (3)*
- [x] webui : écran de connexion + badge humain + gestion session — *commits UI ; sidebar « compte humain · orga2_humain · orga2 » + Se déconnecter vérifiés par captures ; panneaux de gestion agents/orgs rendus*
- [x] **UI Conversations réorganisée (demande utilisateur, 2026-08-06) : switch [Agent ↔ Agent | Humain ↔ Agent]** — *Agent ↔ Agent : lecture seule (aucun composeur) ; Humain ↔ Agent : lecture + envoi (composeur) ; style messagerie (mine/theirs, timestamps) ; sélection désactivée si la conversation n'appartient pas au nouveau mode ; RAFRAÎCHISSEMENT FLUIDE : comparaison par empreinte (ids/compteurs/horodatages) — le DOM n'est pas reconstruit quand rien ne change (ni « Chargement du contenu… », ni clignotement, ni perte de scroll) ; preuve harnais : threadStableAfterRefresh + noReloadSpinner ; seed_demo : échange humain↔agent ajouté (vue HA testable) ; raccourcis sidebar « g d / g a / g c » retirés (demande utilisateur) ; garde noNavShortcuts*
- [x] **AMENDEMENT D5 (2026-08-06, décision utilisateur) : connexion par sélection** — *plus de saisie d'identifiants : liste déroulante des organisations actives (`GET /api/orgs` → commande `list_orgs`, 64e commande), bouton « Se connecter » ; le web s'authentifie par jeton de confiance local (fichier `web_token` 0600 du run dir, écrit par le serveur au démarrage, retiré à l'arrêt) ; le jeton remplace le mot de passe d'org pour les humains et les commandes d'org, jamais pour les agents ; `_web_local` limité strictement à `list_orgs` ; échecs de connexion = org inconnue/désactivée, verrouillage 429 par org conservé ; COMPROMIS DE SÉCURITÉ documenté (SPEC.txt §20.7) : tout processus du même utilisateur lisant le jeton peut agir comme le web ; correction d'une boucle infinie réelle (bootstrap récursif via checkSession) qui bloquait la connexion dans le navigateur ; **CORRECTION RÉELLE 2e passe (constat utilisateur « onglets vides + fenêtre toujours visible ») : le CSS `.login-root { display:flex }` et `.app { display:flex }` écrasaient l'attribut HTML `hidden` (règle CSS > feuille UA) → règles `.login-root[hidden]`/`.app[hidden] { display:none }` ajoutées ; PROUVE par navigateur réel (Chrome headless + CDP, getComputedStyle) : loginDisplay flex → none après le clic, appDisplay flex, dashboard 5 cartes, 4 conversations, 0 erreur console ; gardes anti-régression dans les harnais (cssRulesPresent, loginHiddenAfterAuth)** ; tests : test_spec_web_d6.py (14 verts), test_webui.py adapté, harnais DOM double (verify_login + verify) verts*
- [x] Tests D5 écrits et verts — *tests/test_webui.py réécrit (33 tests, 2026-08-06) : C6.3/C4.1 désactivation pendant une session → 401 + reconnexion après réactivation locale ; avec 32 tests existants : login+cookie HttpOnly/SameSite, session info, 401 sans session, mauvais login, verrouillage 429 par org, logout, TTL, max sessions/org, mort de session à la rotation du mdp d'org, snapshot sans contenu, routes agents/recherche/org/conversations/envoi, gestion agents/orgs, ETag, cache par org, corps 1 MiB (413) ; test_observers_web + test_security_audit_2026 + test_http_edges adaptés au modèle sessions (plus aucun jeton) — 110 tests verts sur le lot web*

## Surface API & docs
- [x] 65 commandes au total (help + compteurs à jour) — *test_compliance mis à jour (58 → 65, `list_orgs` et `get_escalation_policy` inclus), help généré depuis COMMAND_SPECS, compteur vérifié par exécution réelle*
- [x] helpdoc.py : 5 nouvelles entrées — *change_agent_description, create_org, disable_org, list_org_conversations, get_org_conversation + _PARAM_FORMATS (organization_name, organization_password)*
- [x] Docs à jour (SPEC.txt, rapports) — *SPEC.txt amendé (2026-08-06) : §20 « Amendement SPEC-WEB » (comptes humains F19, lectures de contenu option B, gestion orgs/agents, interface web F18 amendé, 63 commandes, organisations.enabled) ; observateurs principal_type corrigé en 'agent' ; SPEC_WEB.txt amendé (rename list_org_conversations, enable local, change_agent_description) ; CHECKLIST_SPEC_WEB.md source de vérité*

## Règles de fonctionnement — à respecter STRICTEMENT
1. Une seule case à la fois : termine-la, vérifie-la, coche-la, committe, PUIS passe à la suivante. Jamais de demi-travail.
2. Coche une case UNIQUEMENT quand c'est fait ET vérifié (tests verts qui couvrent le comportement, preuve réelle : fichier, test, commande). Pas de case cochée sans preuve.
3. Ne décoche JAMAIS une case sans justification écrite (régression documentée en rouge dans le fichier). Si tu découvres une régression, corrige-la immédiatement plutôt que de décocher.
4. Après chaque case cochée : commit immédiat avec un message clair mentionnant la case (ex. « spec-web: D3 enable_org + test ») et la checklist à jour.
5. Mets la checklist à jour (cochée, datée) AVANT de commencer la tâche suivante. Le fichier doit toujours refléter l'état exact du dépôt.
6. À la fin : vérifie que toutes les cases sont cochées, relance la suite complète, et note le résultat final en tête du fichier.
