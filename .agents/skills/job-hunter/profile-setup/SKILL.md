---
name: profile-setup
description: >-
  Use this skill whenever the user shares a CV (PDF, Markdown, or text) or asks
  to initialize, parse, update, or validate job-hunter's
  references/profile.yaml (contact info, CV path, education, alternance/stage
  details, skills, highlights, and search defaults).
---

# Profile Setup (CV → `profile.yaml`)

Procédure intégrée à [job-hunter](../SKILL.md) pour lire un CV (PDF ou texte), copier/référencer le CV dans `references/`, et configurer ou valider [profile.yaml](../references/profile.yaml).

## Fichiers cibles

- Profil à configurer : [profile.yaml](../references/profile.yaml)
- Dossier des CV de référence : `.agents/skills/job-hunter/references/`
- Validateur de profil : [validate_profile.py](../scripts/validate_profile.py)

## Procédure étape par étape

### 1. Récupérer et stocker le CV
1. Identifier le chemin du CV fourni par l'utilisateur (ex. `~/Downloads/CV.pdf` ou un fichier dans le workspace).
2. Copier le CV PDF dans `.agents/skills/job-hunter/references/` (sans écraser un autre CV sans prévenir) afin que `candidate.cv_path` reste stable.
3. Vérifier que le fichier PDF existe et est non vide.

### 2. Extraire les informations du CV
1. Lire le CV PDF avec `read_file` (ou en fallback texte avec `pdftotext "<chemin_cv.pdf>" -`).
2. Extraire uniquement les faits présents dans le CV (ne jamais inventer de compétences, dates, liens ou diplômes) :
   - `candidate`: `full_name`, `email`, `phone`, `location`, `cv_path`, `links` (`github`, `linkedin`, `portfolio` — uniquement ceux trouvés).
   - `education`: `school`, `current_level`, `degree_level`, `alternance_rhythm` (ou `stage_duration`), `contract_duration`, `availability`.
   - `skills`: `spoken_languages`, `languages`, `frameworks`, `tools`, `specialties`.
   - `highlights`: 3 à 6 réalisations concrètes et projets clés issus du CV (avec technos et dates si indiquées).

### 3. Compléter les critères de recherche (`defaults`)
Lire l'état actuel de [profile.yaml](../references/profile.yaml) pour conserver la structure attendue.
Si les éléments suivants manquent dans le CV et dans `profile.yaml`, les demander brièvement à l'utilisateur :
- `defaults.contracts` : parmi `stage`, `alternance`, `cdd`, `cdi`
- `defaults.queries` : 1 à 3 intitulés de poste ciblés (ex. `"Développeur Fullstack"`, `"Développeur Web"`)
- `defaults.location` : ville/région cible (ex. `"Lyon"`)
- `defaults.remote_ok` : `true` ou `false`
- `defaults.sources` : par défaut `["wttj", "hellowork", "indeed", "linkedin"]`
- Pour une alternance/stage : rythme (`alternance_rhythm`), durée (`contract_duration` / `stage_duration`), date de disponibilité (`availability`).

### 4. Mettre à jour `profile.yaml`
Écrire ou éditer `.agents/skills/job-hunter/references/profile.yaml` en respectant le schéma YAML exact :
```yaml
candidate:
  full_name: "..."
  email: "..."
  phone: "..."
  location: "..."
  cv_path: "/chemin/absolu/vers/.agents/skills/job-hunter/references/CV.pdf"
  links:
    github: "..."
    linkedin: "..."
    portfolio: "..."

education:
  school: "..."
  current_level: "..."
  degree_level: "..."
  alternance_rhythm: "..."
  contract_duration: "..."
  availability: "..."

defaults:
  contracts:
    - "alternance"
  queries:
    - "..."
  location: "..."
  remote_ok: true
  sources:
    - "wttj"
    - "hellowork"
    - "indeed"
    - "linkedin"

skills:
  spoken_languages: []
  languages: []
  frameworks: []
  tools: []
  specialties: []

highlights: []
```

### 5. Valider le fichier généré
Exécuter la commande de validation avant de conclure :
```bash
uv run --project .agents/skills/job-hunter job-hunter validate-profile
```
Vérifier que `"valid": true` et qu'aucune erreur bloquante n'est signalée, puis présenter un résumé concis des champs mis à jour à l'utilisateur.
