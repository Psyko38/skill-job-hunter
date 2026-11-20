---
name: profile-setup
description: >-
  Use this skill whenever the user shares a CV (PDF, Markdown, or text) or asks
  to initialize, parse, update, or validate job-hunter's
  references/profile.yaml (contact info, CV path, education, alternance/stage
  details, skills, highlights, search defaults, and exclusion preferences).
---

# Profile Setup (CV → `profile.yaml`)

Procédure intégrée à [job-hunter](../SKILL.md) pour lire un CV (PDF ou texte), copier/référencer le CV dans `references/`, configurer les préférences de ciblage/exclusion et valider [profile.yaml](../references/profile.yaml).

## Règles de sécurité
- **Confidentialité** : `references/profile.yaml` et les CV `.pdf` sont ignorés par Git (`.gitignore`). Ne jamais les copier dans des fichiers versionnés ni les partager vers des services externes.
- **Vérification PDF** : Vérifier que le CV copié est un vrai fichier PDF non vide (en-tête `%PDF-`, taille <= 10 Mo) — contrôlé automatiquement par `validate-profile`.
- **Zéro invention** : Extraire uniquement les faits présents dans le CV ou explicitement confirmés par l'utilisateur.

## Fichiers cibles

- Profil à configurer : [profile.yaml](../references/profile.yaml)
- Exemple de référence : [profile.example.yaml](../references/profile.example.yaml)
- Dossier des CV de référence : `.agents/skills/job-hunter/references/`
- Validateur de profil : [validate_profile.py](../scripts/validate_profile.py)

## Procédure étape par étape

### 1. Récupérer et stocker le CV
1. Identifier le chemin du CV fourni par l'utilisateur (ex. `~/Downloads/CV.pdf` ou un fichier dans le workspace).
2. Copier le CV PDF dans `.agents/skills/job-hunter/references/` (sans écraser un autre CV existant sans prévenir) afin que `candidate.cv_path` reste stable.
3. Vérifier que le fichier PDF existe, est non vide et commence par `%PDF-`.

### 2. Extraire les informations du CV
1. Lire le CV PDF avec `read_file` (ou en fallback texte avec `pdftotext "<chemin_cv.pdf>" -`).
2. Extraire uniquement les faits présents dans le CV :
   - `candidate`: `full_name`, `email` (syntaxe valide), `phone`, `location`, `cv_path`, `links` (`github`, `linkedin`, `portfolio` — URLs `https://...` uniquement).
   - `education`: `school`, `current_level`, `degree_level`, `alternance_rhythm` (ou `stage_duration`), `contract_duration`, `availability`.
   - `skills`: `spoken_languages`, `languages`, `frameworks`, `tools`, `specialties`.
   - `highlights`: 3 à 6 réalisations concrètes et projets clés issus du CV (avec technos et dates si indiquées).

### 3. Compléter les critères de recherche (`defaults`) et préférences (`preferences`)
Lire l'état actuel de [profile.yaml](../references/profile.yaml) pour conserver les réglages existants.
Si les éléments suivants manquent, les demander brièvement à l'utilisateur :
- `defaults.contracts` : parmi `stage`, `alternance`, `cdd`, `cdi`
- `defaults.queries` : 1 à 3 intitulés de poste ciblés (ex. `"Développeur Fullstack"`, `"Développeur Web"`)
- `defaults.location` : ville/région cible (ex. `"Lyon"`)
- `defaults.remote_ok` : `true` ou `false`
- `defaults.sources` : par défaut `["wttj", "hellowork", "indeed", "linkedin"]`
- Pour une alternance/stage : rythme (`alternance_rhythm`), durée (`contract_duration` / `stage_duration`), date de disponibilité (`availability`).
- *(Optionnel)* `preferences`:
  - `exclude_companies`: entreprises/ESN à écarter automatiquement lors des recherches
  - `exclude_keywords`: technos ou postes non souhaités (ex. `"COBOL"`, `"WordPress"`)
  - `preferred_keywords`: mots-clés bonus pour le scoring (ex. `"SaaS"`, `"Open Source"`, `"Produit"`)

### 4. Mettre à jour `profile.yaml`
Écrire ou éditer `.agents/skills/job-hunter/references/profile.yaml` en respectant le schéma de [profile.example.yaml](../references/profile.example.yaml).

### 5. Valider le fichier généré
Exécuter la commande de validation avant de conclure :
```bash
uv run --project .agents/skills/job-hunter job-hunter validate-profile
```
Vérifier que `"valid": true` et qu'aucune erreur bloquante n'est signalée (signaler aussi les `warnings` éventuels), puis présenter un résumé concis des champs mis à jour à l'utilisateur.
