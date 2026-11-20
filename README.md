# Skills

Collection de skills pour agent IA (Google Antigravity / compatible `.agents/skills/`).

## Skills inclus

### `job-hunter` (`.agents/skills/job-hunter/`)
Workflow automatisé de recherche d'offres (`stage`, `alternance`, `cdd`, `cdi`) sur Welcome to the Jungle, HelloWork, Indeed France et LinkedIn :
- Filtrage déterministe des annonces d'écoles, CFA et organismes de formation (`references/school_blacklist.yaml`).
- Enrichissement des contacts recruteurs et déduplication multi-sources (`data/history.json`).
- Génération de brouillons de candidatures `.md` et `.eml` avec CV PDF en pièce jointe (`drafts/`).

### `profile-setup` (`.agents/skills/job-hunter/profile-setup/`)
Configuration et validation du profil candidat (`references/profile.yaml`) à partir d'un CV PDF ou texte.

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

## Tests

```bash
uv run --project .agents/skills/job-hunter pytest .agents/skills/job-hunter/tests
```
