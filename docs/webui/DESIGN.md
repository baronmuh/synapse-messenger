# Synapse — Système de design & direction artistique de l'interface web

> Document de conception de l'interface web de supervision de Synapse (SPEC.txt F18).
> Il définit la direction artistique « Registre » (redesign TOTAL v3, Hallmark,
> 2026-08-06) et établit le design system (tokens, composants, patterns) qui
> régit toute l'interface. Source de vérité visuelle : ce document.
> Source de vérité fonctionnelle : SPEC.txt / SPEC_WEB.txt (aucune règle métier
> n'est modifiée par l'interface).
>
> **REDESIGN v3 « Registre » (Hallmark, 2026-08-06)** : refonte TOTALE décidée
> par l'utilisateur (main libre, zéro restriction) — la v2 « Signal » (sombre,
> vert/cyan) était jugée trop proche de la palette de marque. La v3 repart de
> zéro : thème CLAIR ivoire, accent vermillon, typographie Bricolage Grotesque
> + Switzer + JetBrains Mono, coquille « barre de marque + tab strip » (la
> sidebar disparaît), macrostructure Stat-Led. Voir `docs/webui/REDESIGN_V3.md`.

---

## 1. Positionnement

### 1.1 Direction artistique — « Registre »

Synapse est une **infrastructure locale de coordination pour organisations
d'agents IA** : un poste de pilotage, pas un site vitrine. L'interface est
servie sur 127.0.0.1 uniquement, zéro dépendance réseau.

**Métaphore directrice : le registre.** Un registre de bord — le cahier
tenu à jour sur le poste de commande : pages ivoire, annotations sobres,
chiffres clairs, un seul encrage fort pour ce qui réclame l'attention. La
surface est claire et calme (le contenu est la vedette), l'accent vermillon
est un **surligneur** (≤ 3-5 % du viewport), jamais un remplissage.

Ce parti pris répond au verdict utilisateur sur la v2 : la couleur (vert/cyan)
était le maillon faible, trop proche de la palette de marque. La v3 inverse :
**lumière au lieu d'obscurité, chaleur ambrée au lieu de froideur bleutée,
encrage vermillon unique au lieu de double teinte.**

### 1.2 Principes directeurs

| Principe | Traduction concrète |
|---|---|
| **Clarté d'abord** | Hiérarchie typographique stricte ; un titre par section ; zéro jargon UI. |
| **La lumière porte** | Fond ivoire chaud ; surfaces élevées PLUS claires (paper-4) + ombre unique discrète. |
| **La couleur signifie** | Chaque couleur a un rôle sémantique unique ; jamais décorative. L'accent est un surligneur : ≤ 3-5 % du viewport, jamais de remplissage massif. |
| **États = produit** | Loading, vide (3 types), erreur, permission, offline : tous dessinés et tokenisés. |
| **Système par tokens** | Toute décision visuelle est un token nommé (`--color-*`, `--font-*`, `--space-*`) ; aucune valeur ad hoc (gate 48). |
| **Contraste systémique** | Contraste WCAG AA vérifié par calcul pour chaque paire (texte ≥ 4.5:1, icônes/rings ≥ 3:1) — 34 paires calculées (§3.8). |
| **Métadonnées, pas contenu** (F18) | L'interface expose ce que l'humain a le droit de voir ; la non-divulgation reste affichée et respectée. |
| **Évolutif par construction** | Tokens partout ; 10 agents comme 1 000. |
| **Performance = respect** | Zéro dépendance réseau : polices self-hostées, zéro CDN, polling léger, `prefers-reduced-motion`. |

---

## 2. Genre & macrostructure (Hallmark)

- **Genre : modern-minimal** — outil dev/infra/dashboard, monochrome chaud
  avec un accent unique, typographie sans-serif confiante, motion minimale.
- **Macrostructure : Stat-Led** — le dashboard est mené par les chiffres
  (KPI en display Bricolage, la première cellule plus large — asymétrie
  voulue, pas une grille de cartes identiques) ; tout ce qui suit soutient
  les chiffres.
- **Nav : N9 edge-aligned** (barre de marque : marque à gauche, actions à
  droite, vide entre) **+ tab strip applicatif** horizontal sous la barre
  (les 8 vues en onglets, actif souligné accent — jamais de side-tab border).
- **Footer : none (app)** — une application n'a pas de pied de page marketing.
- **Enrichissement : none** — les pages applicatives ne portent PAS
  d'enrichissement (règle Hallmark) ; la typographie porte la page.
- **Motion : minimale** — un seul signal au survol, focus instantané,
  succès silencieux, `prefers-reduced-motion` respecté.

---

## 3. Design tokens

Convention de nommage : `--<fondation>-<propriété>-<modificateur>`.
Jeu de noms UNIQUE `--color-*` / `--font-*` / `--space-*` … — les aliases
`--syn-*` de la v2 ont été **supprimés** (js/ réécrit pour référencer les
tokens Hallmark directement).
Une seule source : `synapse/webui/css/tokens.css`. Les composants n'utilisent
que des tokens — aucune valeur hexadécimale ni pixel hors tokens (seule
exception tolérée et documentée : les data-URI des chevrons de `<select>`,
qui ne peuvent pas référencer `var()`).

### 3.1 Couleurs (thème « Registre », CLAIR) — OKLCH

Anchor chaud 70-75°, accent vermillon 35° :

| Token | Valeur | Rôle |
|---|---|---|
| `--color-paper` | `oklch(0.975 0.008 75)` | fond d'application — ivoire clair |
| `--color-paper-2` | `oklch(0.955 0.010 75)` | surface principale (panneaux, strip) |
| `--color-paper-3` | `oklch(0.930 0.012 75)` | surface de contenu (cartes) |
| `--color-paper-4` | `oklch(0.990 0.006 75)` | surface élevée (popovers, toasts) |
| `--color-rule` | `oklch(0.860 0.014 75)` | séparateurs, hairlines |
| `--color-rule-2` | `oklch(0.780 0.016 75)` | bordures d'interaction |
| `--color-ink` | `oklch(0.180 0.012 70)` | texte principal — noir chaud |
| `--color-ink-2` | `oklch(0.380 0.018 70)` | texte secondaire |
| `--color-ink-3` | `oklch(0.495 0.020 70)` | texte atténué (métadonnées) |
| `--color-ink-4` | `oklch(0.600 0.016 70)` | texte désactivé (≥ 3:1) |
| `--color-accent` | `oklch(0.540 0.185 35)` | vermillon : actif, action, succès |
| `--color-accent-strong` | `oklch(0.470 0.175 35)` | hover des actions primaires |
| `--color-accent-ink` | `oklch(0.980 0.010 60)` | texte sur fond accent (5.2:1) |
| `--color-accent-soft` | `oklch(0.940 0.028 40)` | fonds d'état actif — texte ink dessus |
| `--color-link` | `oklch(0.440 0.110 250)` | bleu acier : liens, info |
| `--color-link-ink` | `oklch(0.980 0.010 250)` | texte sur fond link (7.3:1) |
| `--color-success` | `oklch(0.480 0.130 150)` | vert forêt |
| `--color-warning` | `oklch(0.650 0.140 75)` | ambre |
| `--color-danger` | `oklch(0.520 0.190 25)` | rouge profond |
| `--color-danger-bright` | `oklch(0.520 0.185 25)` | **texte** danger sur fonds clairs (4.9:1) |
| `--color-focus` | `oklch(0.500 0.160 250)` | anneau de focus (≥ 3:1) |
| `--color-overlay` | `oklch(0.160 0.010 70 / 0.40)` | blanket modale/palette |

Règles de contraste vérifiées par calcul (WCAG) : texte ≥ 4.5:1 sur toutes les
surfaces ; icônes, points de teinte et rings ≥ 3:1. **Jamais de texte de la même
teinte sur un fond soft** (échoue AA) : les états actifs portent du texte ink,
les badges pleins portent l'encre de la teinte, les bandeaux sont neutres avec
bordure + icône colorée.

### 3.2 Typographie — 2 + 1 familles (gate 37)

| Token | Famille | Usage |
|---|---|---|
| `--font-display` | Bricolage Grotesque (variable 400-700) | titres de page, titres de cartes, chiffres KPI |
| `--font-body` | Switzer (400/500/600/700) | tout le texte |
| `--font-mono` | JetBrains Mono (400/500/600) | données techniques (ids, timestamps, commandes) |

Échelle (taille / rôle) : caption 12 px · small 13 px · body 14 px · body-lg
15 px · h3 16 px · h2 18 px · h1 28 px · display
`clamp(1.9rem, 1.6vw + 1.2rem, 2.75rem)` (30-44 px — chiffres KPI).
Règles : `font-variant-numeric: tabular-nums` sur toutes les données ; tracking
`-0.02em` sur display ; titres TOUJOURS romains (jamais d'italique — gate 38a) ;
body 14 px assumé (app d'infra dense, compromis documenté).

### 3.3 Spacing

Base 4 px, échelle nommée par rôle : `--space-3xs` 2 · `--space-2xs` 4 ·
`--space-xs` 8 · `--space-sm` 12 · `--space-md` 16 · `--space-lg` 24 ·
`--space-xl` 40 · `--space-2xl` 64 · `--space-3xl` 96 (px). Rythme de page :
24 px entre cartes, 40 px de gouttière de contenu (desktop).

### 3.4 Radius — serrés (registre instrument)

`--radius-sm` 4 px (boutons, inputs, badges) · `--radius-md` 6 px (navigation,
rangées) · `--radius-lg` 8 px (cartes, panneaux) · `--radius-xl` 12 px
(modales, login) · `--radius-full` 9999 px (avatars, pills).

### 3.5 Élévation

Thème clair : l'élévation est portée par l'ombre unique + paper-4 plus clair.
`--shadow-whisper` (cartes) · `--shadow-popover` (menus, toasts) ·
`--shadow-modal` (modales, login). Z-index échelle nommée en six niveaux :
base 1 · raised 10 · dropdown 100 · sticky 200 · modal 400 · toast 500 ·
tooltip 600.

### 3.6 Motion

`--ease-out: cubic-bezier(0.16, 1, 0.3, 1)` · `--ease-in: cubic-bezier(0.7, 0, 0.84, 0)` ·
`--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1)` · durées micro 120 ms /
short 200 ms / long 300 ms. Principes : transform/opacity uniquement (sauf
barres de progression, fonctionnelles) ; focus rings instantanés ; tooltips
survol 800 ms / focus 0 ms ; succès silencieux ; `prefers-reduced-motion` →
durée ~0.

### 3.7 Breakpoints & grille

`--bp-sm` 40 rem (640 px) · `--bp-md` 56.25 rem (900 px — conversations
empilées) · `--bp-lg` 75 rem (1200 px — grille desktop complète) ·
`--bp-xl` 90 rem (1440 px — contenu centré, gouttière 40 px).

### 3.8 Preuve de contraste (34 paires, calculées)

Script : `python3 /tmp/contrast_registre.py` (OKLCH → sRGB → WCAG 2.1).
Résultat : **34/34 paires passent**. Les plus critiques :

| Paire | Ratio | Seuil | |
|---|---|---|---|
| ink / paper | 17.50:1 | 4.5 | PASS |
| ink-2 / paper-3 | 8.16:1 | 4.5 | PASS |
| ink-3 / paper-3 | 5.00:1 | 4.5 | PASS |
| ink-4 / paper-3 | 3.22:1 | 3.0 | PASS |
| accent-ink / accent | 5.21:1 | 4.5 | PASS |
| accent / paper-3 | 4.50:1 | 3.0 | PASS |
| link / paper | 7.21:1 | 4.5 | PASS |
| warning-ink / warning | 4.87:1 | 4.5 | PASS |
| danger-ink / danger | 5.72:1 | 4.5 | PASS |
| danger-bright / paper-3 | 4.93:1 | 4.5 | PASS |
| focus / paper-3 | 4.83:1 | 3.0 | PASS |
| ink / accent-soft | 15.66:1 | 4.5 | PASS |

Re-vérification navigateur (getComputedStyle sur les tokens réellement servis,
Chrome headless, 2026-08-06) : ratios identiques à ±0.01.

---

## 4. Architecture de l'interface

### 4.1 Coquille applicative — « Registre »

```
┌──────────────────────────────────────────────────────────────────────┐
│ brandbar (marque · espace · identité · recherche ⌘K · état · cloche · quitter) │
├──────────────────────────────────────────────────────────────────────┤
│ tabstrip : Tableau de bord · Agents · Communications · Conversations │
│           · Tâches · Activité · Organisation · Serveur               │
├──────────────────────────────────────────────────────────────────────┤
│ contenu (max 1440 px, gouttière 40 px)                               │
└──────────────────────────────────────────────────────────────────────┘
```

- **Brandbar (56 px, sticky, N9 edge-aligned)** : marque + « Synapse ·
  Supervision » + org courante à gauche ; identité de session, recherche
  (⌘K / — le raccourci reste fonctionnel, son libellé n'est plus affiché
  dans la barre), indicateur de connexion, cloche d'attention, « Quitter ».
- **Tab strip (44 px, sticky sous la barre)** : les 8 vues en onglets.
  L'item actif est marqué par le **soulignement accent + la graisse +
  l'icône teintée** — jamais de barre latérale (side-tab border interdit,
  gate 42). Compteurs (non lus, tâches actives) en pastilles.
- **Plus de sidebar** : la v2 avait une colonne gauche 264 px ; la v3 rend
  toute la hauteur au contenu. Sur mobile le tab strip défile horizontalement.

### 4.2 Vues

Identiques à la v2 sur les ROUTES et les FONCTIONNALITÉS (SPEC_WEB.txt :
connexion par sélection, switch AA/HA, non lus, gestion agents/orgs,
rafraîchissement fluide par empreinte). Le redesign a restructuré le
**rythme visuel** : dashboard Stat-Led (premier KPI élargi), cartes ivoire
avec ombre whisper, kanban sans carte imbriquée, rangées de gestion sans
bordures internes, organigramme vivant conservé.

### 4.3 États d'interface (tous dessinés)

- **Loading** : skeletons par vue, jamais de spinner seul sur une vue entière.
- **Empty — zéro donnée** : message + première action suggérée.
- **Empty — zéro résultat** : « aucun résultat » + action pour lever les filtres.
- **Erreur** : bandeau neutre à bordure danger + icône ; pas de message générique.
- **Permission/offline** : indicateur dans la brandbar ; données conservées.
- **Données longues** : ellipsis + title, troncature, dates relatives.

### 4.4 Accessibilité

HTML sémantique, labels explicites, `aria-current`, `aria-live`, focus visible
2 px instantané (outline), cibles ≥ 40 px (44 px sur tactile via
`@media (pointer: coarse)`), contraste AA vérifié par calcul, information jamais
par la seule couleur, `prefers-reduced-motion`.

### 4.5 Responsive

- **≥ 1200 px** : grille complète, brandbar + tab strip pleins.
- **900-1200 px** : grilles 2 colonnes ; identité de session compacte.
- **< 640 px** : tab strip défilant, grilles 1 colonne, tableaux en cartes,
  recherche réduite à l'icône, `overflow-x: clip` global (aucun scroll horizontal).
- Vérifié à 320 / 375 / 414 / 768 / 1280 px par captures Chrome headless
  (zéro scroll horizontal, zéro erreur console, polices chargées — voir
  `docs/webui/REDESIGN_V3.md`).

### 4.6 Performance

Zéro dépendance : pas de framework, pas de build, **polices self-hostées**
(`assets/fonts/`, ~350 Ko, cache HTTP long), tout est servi par le serveur
stdlib. Polling 5 s sur le snapshot avec ETag/304 ; pause quand l'onglet est
caché ; rendu paginé ; FOUC maîtrisé (tokens statiques).

---

## 5. Patterns de composants (v3)

1. **Boutons** : primaire (accent plein + accent-ink, hover accent-strong, pas
   de glow), secondaire (paper-4 + bordure), fantôme (texte), danger (outline
   rouge). États : hover, actif (translateY 1 px), focus (outline instantané),
   disabled (opacité + curseur + attribut), loading (spinner inline).
2. **Badges de statut** : **pleins** — le fond porte la teinte, le texte porte
   l'encre de la teinte (accent-ink, warning-ink, danger-ink, link-ink).
   Pastille 6 px + libellé (jamais la couleur seule).
3. **Cartes** : paper-4 (plus claire que le fond), bordure hairline, radius-lg,
   élévation whisper. Titre display + actions à droite ; hover = bordure
   seulement (pas de lift).
4. **KPI** : chiffre display Bricolage tabulaire + label caption avec **point
   de teinte** (`--kpi-tone`) devant ; jamais de bande colorée. Stat-Led : la
   première cellule du dashboard est élargie (asymétrie).
5. **Tableaux** : en-têtes sticky dans le conteneur, texte 15 px, lignes 44 px,
   hover surface, tri visuel (aria-sort), pagination en pied, colonnes de
   données en mono tabulaire.
6. **Formulaires** : labels au-dessus, input 40 px (44 px tactiles), bordure
   1 px constante (zéro décalage), focus = outline 2 px instantané, erreur =
   bordure danger + bandeau (texte ink sur fond danger-soft).
7. **Modales / confirmations** : blanket + panneau elev-modal, fermeture Échap,
   focus piégé, retour au déclencheur.
8. **Toasts** : 4 types, auto-dismiss 5 s sauf erreur, `aria-live=polite`.
   **Succès silencieux** : pas de toast quand l'effet est visible.
9. **Palette de commandes** : champ, résultats groupés, navigation clavier
   complète.
10. **Messagerie** : bulles à radius asymétrique (queue côté expéditeur),
    humain à droite (accent-soft), agents à gauche ; composeur aligné (44 px) ;
    non lus et rafraîchissement fluide inchangés (SPEC_WEB §2).
11. **Skeletons** : blocs animés, formes des contenus réels.

---

## 6. Contrat de sécurité de l'interface

L'interface est un **compte humain** (SPEC_WEB) — la connexion se fait par
**sélection d'organisation** (aucune saisie d'identifiant), les sessions sont
portées par cookie HttpOnly SameSite=Strict, aucune donnée sensible en
localStorage/URL/logs.

- le web est exclusivement humain ; les agents n'ont ni mot de passe d'org ni
  jeton web ;
- **aucun contenu de message** n'est jamais exposé à un agent non participant ;
  la lecture avec contenu est réservée au compte humain de l'organisation
  (SPEC_WEB §2) ;
- l'interface le revendique : bloc « contenu protégé — non-divulgation » dans
  les vues Communications et Tâches ;
- zéro dépendance réseau : polices et assets servis localement, aucune donnée
  ne quitte la machine (P4).

---

## 7. Journal du redesign v3 (2026-08-06)

Voir `docs/webui/REDESIGN_V3.md` (récapitulatif fichier par fichier, gates
passées, compromis assumés). Décisions notables :

- **Thème inversé** : sombre « Signal » (v2) → CLAIR « Registre » (ivoire
  chaud, accent vermillon) — réponse au verdict utilisateur (palette de marque).
- **Coquille réécrite** : sidebar + topbar + nav mobile → brandbar + tab strip.
- **Typographie remplacée** : Space Grotesk + IBM Plex Sans → Bricolage
  Grotesque + Switzer (JetBrains Mono conservé en outlier) — self-hostées,
  fichiers v2 obsolètes supprimés (plex-*, sg-*).
- **Macrostructure** : Workbench → Stat-Led (dashboard mené par les chiffres).
- **Aliases `--syn-*` supprimés** : js/ réécrit pour référencer les tokens
  Hallmark directement.
- **Avatars** : palette hex de marque (v2) → palette OKLCH chaud alignée sur
  le système.
- Contraste re-vérifié par calcul sur 34 paires + re-vérification navigateur.
- Preuves : harnais DOM (jsdom + backend réel) adapté à la coquille
  (`verify.mjs` : `.sidebar-footer` → `.session-identity`,
  `#app .sidebar` → `#app .brandbar`) ; captures Chrome headless 5 largeurs ×
  9 écrans ; flux complet conversations (non lus) prouvé en navigateur réel.
