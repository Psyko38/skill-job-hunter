# Guide de rédaction des emails de candidature & relances

## Principes généraux
- **Langue** : rédiger en **français** si `offer.language == "fr"`, en **anglais** si `offer.language == "en"`.
- **Longueur** : court, direct, scannable (**150 à 230 mots max**).
- **Personnalisation réelle** : citer le nom de l'entreprise, un élément précis de sa mission/stack (s'appuyer sur `matched_skills`), et relier 2 expériences ou projets vérifiés de `profile.yaml`.
- **Zéro blabla** : éviter les formules génériques ("Madame, Monsieur, suite à votre annonce parue ce jour...", "dynamique et motivé").
- **Zéro placeholder** : ne laisser AUCUN crochet (`[...]`), accolade (`{{...}}`) ou mention `À COMPLÉTER` — `build-drafts --dry-run` les bloquera automatiquement.
- **Zéro hallucination** : ne mentionner que les compétences, projets, diplômes et dates réellement présents dans `profile.yaml`.

## Informations obligatoires selon le contrat

### Stage
- Mentionner : type de convention (stage conventionné), durée (`stage_duration`), date de début (`availability`), niveau d'études.
- Objet type FR : `Candidature Stage <Intitulé exact> – <Prénom Nom> (<Disponibilité>)`
- Objet type EN : `Internship Application: <Job Title> – <Full Name> (<Availability>)`

### Alternance
- Mentionner : rythme d'alternance (`alternance_rhythm`), durée du contrat (12 ou 24 mois), niveau préparé (`degree_level`), disponibilité.
- Objet type FR : `Candidature Alternance <Intitulé exact> – <Prénom Nom> (<Rythme>)`
- Objet type EN : `Apprenticeship Application: <Job Title> – <Full Name>`

### CDD / CDI
- Mentionner : disponibilité (`availability`), expérience concrète sur la stack demandée, valeur ajoutée immédiate.
- Objet type FR : `Candidature <CDD/CDI> <Intitulé exact> – <Prénom Nom>`
- Objet type EN : `Application for <Job Title> – <Full Name>`

## Adaptation selon le canal (`apply_channel`)
- **`email`** : mail complet avec objet percutant, corps personnalisé et signature (le CV PDF est joint automatiquement dans `draft.eml`).
- **`ats` ou `jobboard`** (aucun email direct trouvé) : rédiger un message de motivation concis (100–160 mots) prêt à coller dans le champ "Message au recruteur" / "Cover note" du formulaire (`apply_url`).

## Modèle de relance (J+7 via `job-hunter stats`)
- **FR** — Objet : `Relance – Candidature <Contrat> <Intitulé> – <Prénom Nom>`
  - 4 à 6 lignes max : rappeler la date d'envoi, réitérer l'intérêt concret pour l'équipe/stack, proposer un court échange de 15 min, re-joindre le CV.
- **EN** — Subject: `Follow-up – <Job Title> Application – <Full Name>`
  - Keep to 4–6 concise lines, reiterate concrete interest, offer a brief 15-minute intro call.

## Signature standard
Inclure à la fin du mail :
- Prénom Nom
- Téléphone (si renseigné)
- Email
- Liens cliquables pertinents (GitHub, LinkedIn, Portfolio — uniquement ceux renseignés dans `profile.yaml`)
- Mention du CV en pièce jointe (`Vous trouverez mon CV en pièce jointe.` / `Please find my resume attached.`)
