# REDESIGN V3 — « Registre » (refonte TOTALE, Hallmark)

> Décision utilisateur 2026-08-06 : **main libre, zéro restriction, refonte
> TOTALE** — la v2 « Signal » (sombre, vert/cyan) était jugée trop proche de la
> palette de marque. La v3 repart de zéro : rien n'est hérité de la v2 sauf les
> contrats API (intouchables, SPEC_WEB §3) et les comportements fonctionnels
> (switch AA/HA, non lus, rafraîchissement fluide).

## Choix de design (accountability line, mode autonome)

- **Genre** : modern-minimal · **Macrostructure** : Stat-Led
- **Thème** : custom « Registre » — papier ivoire chaud CLAIR, accent vermillon,
  display Bricolage Grotesque + body Switzer + outlier JetBrains Mono.
  Axes : light / sans-expressif / warm (diffère de Signal v2 : dark / grotesk /
  chromatic-green sur les TROIS axes).
- **Nav** : N9 edge-aligned + tab strip app · **Footer** : none (app)
- **Enrichissement** : none (app) · **Motion** : minimale, succès silencieux.

## Récapitulatif fichier par fichier

### `synapse/webui/index.html` — RÉÉCRIT
Coquille v3 : `#brandbar` (barre de marque N9) + `#tabstrip` (onglets des 8
vues) + `#content`. La `#sidebar` / `#topbar` / `#mobile-nav` de la v2 ont
disparu. Meta `color-scheme: light`, favicon aux couleurs du registre.

### `synapse/webui/css/tokens.css` — RÉÉCRIT
Palette « Registre » complète en OKLCH (34 paires de contraste vérifiées par
calcul, §3.8 de docs/webui/DESIGN.md). Radius serrés (4-8-12). Échelle de type avec
display `clamp(1.9rem, 1.6vw + 1.2rem, 2.75rem)`. **Aliases `--syn-*`
supprimés** (plus de double jeu de noms).

### `synapse/webui/css/fonts.css` — RÉÉCRIT
Bricolage Grotesque (variable 400-700, latin + latin-ext) + Switzer
(400/500/600/700) + JetBrains Mono (400/500/600, conservé). Fichiers v2
obsolètes supprimés (`plex-*.woff2`, `sg-*.woff2` — 14 fichiers).

### `synapse/webui/css/base.css` — RÉÉCRIT
Coquille « Registre » : brandbar sticky + tab strip sticky (actif souligné
accent, jamais de side-tab border), contenu max 1440 px. Responsive 320 →
desktop, `overflow-x: clip` global, cibles tactiles 44 px, reduced-motion.

### `synapse/webui/css/components.css` — RÉÉCRIT
Tous les composants au registre clair : boutons (primaire vermillon plein +
accent-ink), badges pleins (encre de la teinte), cartes paper-4 + ombre
whisper, KPI Stat-Led (point de teinte), tableaux, formulaires (bordure 1 px
constante, focus outline instantané), modales, toasts, tooltips (800 ms / 0 ms),
palette ⌘K, skeletons.

### `synapse/webui/css/views.css` — RÉÉCRIT
Vues restylées : dashboard Stat-Led (première cellule KPI élargie),
conversations (switch AA/HA, messagerie, composeur), login « Registre » (pas
de carte flottante centrée — marque en display), agents, tâches (kanban sans
carte imbriquée), organisation (organigramme vivant conservé), serveur,
activité. Classes fonctionnelles CONSERVÉES (le harnais DOM les exige) :
`.conv-*`, `.org-chart*`, `.seg-btn`, `.msg-*`, `.badge`, etc.

### `synapse/webui/js/app.js` — RÉÉCRIT
Coquille v3 : `renderBrandbar()` (marque, identité de session `.session-identity`,
recherche ⌘K, état, cloche, « Quitter ») + `renderTabstrip()` (onglets avec
compteurs). La sidebar/rail/nav mobile ont disparu. Routeur, palette, cloche,
raccourcis : comportements identiques.

### `synapse/webui/js/icons.js` — ADAPTÉ
`brandMark()` : couleurs v2 (`--syn-*`) → tokens v3 (`--color-accent` vermillon,
`--color-link` bleu acier, `--color-paper-2`). Jeu d'icônes conservé (dessiné
main, un seul style).

### `synapse/webui/js/format.js` — ADAPTÉ
Palette d'avatars : hex de marque (v2, vert/cyan) → 8 dégradés OKLCH chauds
alignés sur le système « Registre ». Encre des initiales : ivoire.

### `synapse/webui/js/ui.js` — ADAPTÉ
`avatarWithStatus()` : `--syn-*` → tokens v3.

### `synapse/webui/js/views/dashboard.js`, `tasks.js`, `agent.js`, `server.js` — ADAPTÉS
Références `--syn-*` → tokens v3 (`--color-link`, `--color-success`,
`--color-rule-2`, `--color-paper-2`, `--radius-full`, `--text-small`…).

### `synapse/webui/js/views/*.js` (login, conversations, agents, org, comms, activity) — INCHANGÉS
La logique fonctionnelle (connexion par sélection, switch AA/HA, non lus,
rafraîchissement fluide, gestion) est identique à la v2 — seuls les styles ont
changé (CSS). C'est un choix : la refonte porte sur la couche design, pas sur
les comportements (SPEC_WEB est le contrat fonctionnel).

### `scripts/webui-dom-check/verify.mjs` — ADAPTÉ (justifié)
La coquille change : `#app .sidebar` → `#app .brandbar`,
`.sidebar-footer .sidebar-footer-text` → `.session-identity`.
Toutes les autres assertions (switch AA/HA, non lus, stabilité du DOM,
organigramme) sont inchangées.

### `docs/webui/DESIGN.md` — RÉÉCRIT
Source de vérité du système v3 « Registre » : direction artistique, tokens,
composants, preuve de contraste (34 paires).

### `synapse/webui/fonts/` — NETTOYÉ
Suppression des 14 fichiers plex-*/sg-* (v2). Restent : bg-* (Bricolage),
swz-* (Switzer), jbm-* (JetBrains Mono) — 12 fichiers.

## Gates Hallmark — résultats

### Passées (vérifiées)
- **Slop test 58 gates** : aucun Inter/Roboto/system par défaut (Bricolage +
  Switzer + JBM) ; aucun gradient violet→bleu ; aucune carte imbriquée ; aucun
  side-tab border (actif = soulignement) ; aucune métrique inventée ; aucun
  emoji-icône ; aucun faux chrome navigateur/IDE ; aucun `transition-all`
  (transitions par propriété) ; aucun hover uniforme multi-signaux ; aucun hero
  centré 100vh.
- **Tokens nommés partout** (gate 48) : aucune valeur hex/pixel hors tokens
  (les data-URI des chevrons de `<select>` sont l'exception documentée).
- **OKLCH partout** (gates 22, 7) : neutres teintés vers l'anchor chaud
  (chroma ≥ 0.006), pas de #000/#fff purs.
- **Responsive** (gates 34, 49, 50-57) : vérifié à 320/375/414/768/1280 px —
  zéro scroll horizontal, aucun texte cliquable sur 2 lignes, grilles
  `minmax(0, 1fr)`, `overflow-x: clip` sur html ET body.
- **Contraste AA calculé** (gates 40-41) : 34/34 paires ≥ seuils (texte 4.5:1,
  icônes/rings 3:1) — script + re-vérification navigateur (getComputedStyle).
- **États interactifs** (gate 26) : focus-visible + active + disabled sur tout
  élément ; focus rings instantanés en outline (gates 15, 39).
- **prefers-reduced-motion** (gate 27) : collapse global.
- **Tooltips** (gate 17) : survol 800 ms / focus 0 ms.
- **Succès silencieux** (gate 16) : pas de toast quand l'effet est visible.
- **Typographie pure** (gate 38a) : titres romains, zéro italique.
- **2+1 polices** (gate 37) : Bricolage + Switzer + JetBrains Mono.
- **Diversification** (gates 8, 20-21, 32) : macrostructure Stat-Led ≠
  Workbench (v2), thème différent sur 3 axes, nav N9+tabstrip ≠ N3.

### Compromis assumés (honnêteté)
1. **Body 14 px** : densité d'app d'infra assumée (déjà le cas en v2) — 16 px
   serait plus confortable mais alourdirait les vues denses (tableaux, kanban).
   Compromis documenté, pas une erreur.
2. **Les classes fonctionnelles des vues sont conservées** (`.conv-*`,
   `.org-chart*`, `.seg-btn`…) : la refonte porte sur la couche design
   (structure de coquille, thème, typo, composants) ; les comportements et
   leurs sélecteurs de test restent stables. Le harnais DOM n'a été adapté que
   sur la coquille (2 sélecteurs).
3. **Le jeu d'icônes est conservé** (tracé 24 px, trait 1.75) : il est dessiné
   main, cohérent, et ne porte pas la palette de marque (couleurs via
   `currentColor` + tokens). Le remplacer n'aurait apporté aucun gain gate.
4. **Bricolage Grotesque est une variable font** : un fichier par subset
   (latin/latin-ext) couvre 400-700 — gain de poids vs 6 fichiers statiques.
5. **Le thème clair n'a pas de variante sombre** : décision de design (registre
   de bord = lumière), structurellement possible via la couche de tokens.

## Preuves (2026-08-06)

- **Harnais DOM réel** (jsdom + backend seed_demo) : `verify_login.mjs` + `verify.mjs`
  — **EXIT 0, zéro erreur console** ; 8 vues rendues, switch AA/HA, non lus
  (badge → lu), thread stable après refresh, organigramme vivant.
- **Tests pytest** : `tests/test_webui_dom_harness.py tests/test_webui.py`
  — **35 passed**.
- **Captures Chrome headless + CDP** : 45 captures (login + 8 vues ×
  320/375/414/768/1280 px) dans `/tmp/synapse-proof-v3/` — **zéro scroll
  horizontal, zéro erreur console, polices chargées** (`document.fonts.check`
  pour Bricolage/Switzer/JetBrains Mono).
- **Flux conversations en navigateur réel** : mode AA par défaut (lecture
  seule, composeur absent, alternance des 2 côtés), bascule HA (liste 1 conv,
  badge « 1 non lu » visible), ouverture → **badge disparu immédiatement**,
  composeur présent.
- **Contrastes navigateur** (tokens réellement servis) : ink/paper 17.5:1,
  ink-3/paper-3 5.0:1, accent-ink/accent 5.21:1, focus/paper 5.52:1, …
