# Skills

Collection de skills pour agent IA (Google Antigravity / compatible `.agents/skills/`).

## Skills inclus

### `job-hunter` (`.agents/skills/job-hunter/`)
Workflow automatisé et sécurisé de recherche d'offres (`stage`, `alternance`, `cdd`, `cdi`) sur Welcome to the Jungle, HelloWork, Indeed France et LinkedIn :
- **Filtrage déterministe** des écoles/CFA/organismes de formation (`references/school_blacklist.yaml`), incompatibilités de contrat et exclusions personnelles (`preferences.exclude_*`).
- **Scoring de pertinence (`0–100`)** basé sur `profile.yaml` (`matched_skills`, `missing_skills`, alignement titre/lieu/remote/canal) + mode `--from-profile`.
- **Enrichissement sécurisé des contacts recruteurs** : protection SSRF (validation IP/DNS à chaque redirection, limite de taille 1,5 Mo), dé-obfuscation d'emails, détection ATS et cache par domaine.
- **Défenses de sécurité intégrées** : détection de prompt-injection dans les annonces scrappées, validation stricte des brouillons (`--dry-run`, blocage placeholders/CRLF/domaines écoles/doublons déjà envoyés), vérification `%PDF-` du CV, protection path traversal, écritures atomiques et protection contre l'injection de formules CSV.
- **Suivi complet du pipeline** : statuts (`drafted`, `sent`, `interview`, `offer`, `rejected`, `ignored`), notes, tableau de bord `stats` et détection automatique des relances J+7.

### `profile-setup` (`.agents/skills/job-hunter/profile-setup/`)
Configuration et validation sécurisée du profil candidat (`references/profile.yaml`) à partir d'un CV PDF ou texte.

## Installation rapide

```bash
uv sync --project .agents/skills/job-hunter --extra dev
uv run --project .agents/skills/job-hunter playwright install chromium
cp .agents/skills/job-hunter/references/profile.example.yaml .agents/skills/job-hunter/references/profile.yaml
```

Renseignez ensuite `.agents/skills/job-hunter/references/profile.yaml` (ou utilisez `profile-setup` avec votre CV), puis validez :

```bash
uv run --project .agents/skills/job-hunter job-hunter validate-profile
```

## Commandes principales

```bash
# 1. Pré-vérification complète (profil, CV PDF, règles, Chromium)
uv run --project .agents/skills/job-hunter job-hunter doctor

# 2. Recherche pilotée par profile.yaml (renvoie top_candidates triés par score)
uv run --project .agents/skills/job-hunter job-hunter search --from-profile --limit 10

# 3. Inspecter uniquement les offres retenues + le profil candidat
uv run --project .agents/skills/job-hunter job-hunter show --offer-id <id1> <id2> --with-profile

# 4. Générer un brouillon en 1 commande (ou en lot via build-drafts)
uv run --project .agents/skills/job-hunter job-hunter draft --offer-id <id1> --subject "..." --body "..."
uv run --project .agents/skills/job-hunter job-hunter build-drafts --drafts-json <drafts_input.json>

# 5. Suivi & relances
uv run --project .agents/skills/job-hunter job-hunter mark-status --offer-id <id> --status sent --notes "Envoyé"
uv run --project .agents/skills/job-hunter job-hunter stats --follow-up-days 7
```

## Tests

```bash
uv run --project .agents/skills/job-hunter pytest .agents/skills/job-hunter/tests
```
