# Real-DOM verification of the web interface (Phase 4, adapted from SPEC-WEB)

Functional test harness for the Synapse web interface: loads the full
application in jsdom wired to a **real backend**, logs in as a human
(SPEC-WEB D5: organization login + password, session cookie), then checks
the rendering of the views, the live org chart, the representation toggle,
keyboard focus, the Conversations view and the absence of console errors.

This is the verification level between the backend HTTP tests
(tests/test_webui.py) and a visual review in a browser: the JS really runs,
the DOM is real, and the data comes from the actual server.

## Prerequisites

- A demo server running (e.g. `scripts/seed_demo.py --dir /tmp/demo`
  then `synapse-server` and `synapse-web` on the desired port).
- `npm install` in this directory (dependency: jsdom).

## Usage

    SYNAPSE_WEB_ORIGIN=http://127.0.0.1:8092 \
    SYNAPSE_WEB_ORG=org-name \
    SYNAPSE_WEB_PASSWORD=org-password \
    node verify.mjs

Exit code 0 = all checks pass and zero console errors;
2 = console errors; 1 = harness failure (login refused, missing module...).

## Checks

- Real login (POST /api/login) and human identity in the sidebar
  (account badge).
- App mounting (sidebar, content).
- Organization view: organization header, Org chart/Cards toggle,
  1 column per department, real nodes and links, round-trip toggle,
  keyboard focus on an agent link.
- Conversations view (SPEC-WEB D1): conversation list, opening the
  detail (thread) and presence of the reply composer.
- Navigation across the 8 views (dashboard, agents, communications, tasks,
  activity, organization, server, conversations) without console errors.

## Selection-based login (amended SPEC-WEB D5)

Since the amendment, the login screen no longer asks for a password:
it shows the dropdown list of active organizations (served by
`GET /api/orgs` — `list_orgs` command, local trust token of the run
dir). The harness is split into two verifications:

    SYNAPSE_WEB_ORIGIN=http://127.0.0.1:8092 \
    node verify_login.mjs          # login screen: select + options + button

    SYNAPSE_WEB_ORIGIN=http://127.0.0.1:8092 \
    SYNAPSE_WEB_ORG=org-name \
    node verify.mjs                # selection-based login + 8 views

`SYNAPSE_WEB_PASSWORD` no longer exists. Exit code 0 = checks OK and
zero console errors; 2 = console errors; 1 = harness failure.
