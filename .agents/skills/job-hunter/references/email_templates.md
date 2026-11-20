# Guide de rédaction des emails de candidature

## Principes généraux
- **Langue** : rédiger en **français** si `offer.language == "fr"`, en **anglais** si `offer.language == "en"`.
- **Longueur** : court, direct, scannable (150 à 230 mots max).
- **Personnalisation réelle** : citer le nom de l'entreprise, un élément précis de sa mission/stack, et relier 2 expériences ou projets pertinents de `profile.yaml`.
- **Zéro blabla** : éviter les formules génériques ("Madame, Monsieur, suite à votre annonce parue ce jour...").

## Informations obligatoires selon le contrat

### Stage
- Mentionner : type de convention (stage conventionné), durée (`stage_duration`), date de début (`availability`), niveau d'études.
- Objet type FR : `Candidature Stage [Intitulé exact] – [Prénom Nom] ([Disponibilité])`
- Objet type EN : `Internship Application: [Job Title] – [Full Name] ([Availability])`

### Alternance
- Mentionner : rythme d'alternance (`alternance_rhythm`), durée du contrat (12 ou 24 mois), niveau préparé (`degree_level`), disponibilité.
- Objet type FR : `Candidature Alternance [Intitulé exact] – [Prénom Nom] ([Rythme])`
- Objet type EN : `Apprenticeship Application: [Job Title] – [Full Name]`

### CDD / CDI
- Mentionner : disponibilité (`availability`), expérience concrète sur la stack demandée, valeur ajoutée immédiate.
- Objet type FR : `Candidature [CDD/CDI] [Intitulé exact] – [Prénom Nom]`
- Objet type EN : `Application for [Job Title] – [Full Name]`

## Signature standard
Inclure à la fin du mail :
- Prénom Nom
- Téléphone (si renseigné)
- Email
- Liens cliquables pertinents (GitHub, LinkedIn, Portfolio — uniquement ceux renseignés dans `profile.yaml`)
- Mention du CV en pièce jointe (`Vous trouverez mon CV en pièce jointe.` / `Please find my resume attached.`)
