---
name: job-hunter
description: >-
  Use this skill whenever the user wants to search or scrape job offers (stage,
  alternance, CDD, CDI), filter out schools/CFAs/training centers from real
  company job postings, enrich recruiter contact emails, and draft personalized
  application emails (.md and .eml with CV attachment).
---

# Job Hunter

Workflow automatisé pour rechercher des offres (`stage`, `alternance`, `cdd`, `cdi`) sur Welcome to the Jungle, HelloWork, Indeed France et LinkedIn, éliminer les fausses annonces d'écoles/CFA, trouver les contacts recruteurs et générer des brouillons complets (`.md` + `.eml` avec CV PDF joint).

## Fichiers de référence

- Profil candidat : [profile.yaml](./references/profile.yaml)
- Configuration depuis un CV : [profile-setup](./profile-setup/SKILL.md)
- Règles de filtrage Écoles/CFA : [school_blacklist.yaml](./references/school_blacklist.yaml)
- Guides de rédaction des mails : [email_templates.md](./references/email_templates.md)
- CLI Python : [cli.py](./scripts/cli.py)

## Procédure étape par étape

### 1. Vérifier le profil et les critères de recherche
1. Lire [profile.yaml](./references/profile.yaml).
2. Si `candidate.full_name` ou `candidate.email` contient `"À COMPLÉTER"` (ou si `skills` / `cv_path` manquent, ou si l'utilisateur fournit un nouveau CV), suivre [profile-setup](./profile-setup/SKILL.md) pour extraire le CV et mettre à jour/valider `profile.yaml`.
3. Déterminer les paramètres du run depuis la demande utilisateur (ou les `defaults` de `profile.yaml`) :
   - `--contract` : `stage`, `alternance`, `cdd` ou `cdi`
   - `--query` : mots-clés du poste (ex. `"développeur fullstack"`)
   - `--location` : ville/région (ex. `"Paris"`)
   - `--remote` : optionnel, pour privilégier le télétravail
   - `--sources` : par défaut `wttj,hellowork,indeed,linkedin`
   - `--limit` : nombre max d'offres par source (défaut `10`)

### 2. Installer les navigateurs Playwright (premier usage uniquement)
Si Chromium Playwright n'est pas encore installé :
```bash
uv run --project .agents/skills/job-hunter playwright install chromium
```

### 3. Scraper, filtrer les écoles et enrichir les contacts
Exécuter la commande `search` (nécessite l'accès réseau dans `run_command`) :
```bash
uv run --project .agents/skills/job-hunter job-hunter search \
  --contract "<contrat>" \
  --query "<mots-cles>" \
  --location "<lieu>" \
  --limit 10
```
La commande génère un dossier de run dans `.agents/skills/job-hunter/data/runs/<timestamp>/` contenant :
- `candidates.json` : offres validées (non-écoles, non-dupliquées) avec `contact_email`, `email_source`, `apply_url`, `language` (`fr` ou `en`).
- `report.json` : compteurs exacts (`scraped`, `rejected_schools`, `duplicates`, `kept`), motifs des rejets et statut de chaque source (`ok` ou `blocked_antibot`).

Présenter un résumé clair à l'utilisateur (y compris si une source a bloqué l'accès sans contourner de CAPTCHA).

### 4. Rédiger les mails personnalisés et générer les drafts `.md` + `.eml`
1. Lire `candidates.json` et [email_templates.md](./references/email_templates.md).
2. Pour chaque offre retenue (ou sélectionnée par l'utilisateur), rédiger :
   - `subject` : objet percutant adapté au contrat et à la langue (`offer.language`)
   - `body` : corps personnalisé reliant les projets/compétences du [profile.yaml](./references/profile.yaml) aux besoins de l'offre, incluant les modalités pratiques (rythme d'alternance, durée/dates de stage, disponibilité).
3. Écrire un fichier JSON `drafts_input.json` dans le dossier du run :
   ```json
   [
     {
       "offer_id": "...",
       "to_email": "recrutement@entreprise.fr",
       "subject": "...",
       "body": "..."
     }
   ]
   ```
4. Exécuter la génération des brouillons :
   ```bash
   uv run --project .agents/skills/job-hunter job-hunter build-drafts \
     --candidates ".agents/skills/job-hunter/data/runs/<timestamp>/candidates.json" \
     --drafts-json ".agents/skills/job-hunter/data/runs/<timestamp>/drafts_input.json"
   ```
5. Les brouillons (`application.md` + `draft.eml` avec CV PDF joint si `cv_path` est valide) sont créés dans `drafts/<YYYY-MM-DD>/<entreprise>_<poste>/`, et l'historique est mis à jour dans `data/history.json` et `data/applications.csv`.

### 5. Suivi des candidatures
Pour marquer une offre comme envoyée ou ignorée ultérieurement :
```bash
uv run --project .agents/skills/job-hunter job-hunter mark-status --offer-id "<id>" --status sent
uv run --project .agents/skills/job-hunter job-hunter list-history
```
