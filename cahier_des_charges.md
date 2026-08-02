
# CONTX

## Cahier des charges fondateur

**Version :** 0.3
**Date :** 2 août 2026
**Statut :** base de référence active
**Propriétaire produit :** Emi
**Nature du document :** source de vérité fonctionnelle et technique du projet

---

# 0. Rôle du document

Ce document définit la vision, le périmètre, les invariants, les exigences, l’architecture cible et la stratégie de validation de CONTX.

Il doit remplir quatre fonctions :

1. servir de référence commune pendant la conception et le développement ;
2. permettre à Codex ou à un autre agent de reprendre le projet sans dépendre de l’historique des conversations ;
3. empêcher les dérives de périmètre et les choix techniques contradictoires ;
4. rester suffisamment souple pour intégrer les résultats des prototypes et des tests.

Ce document n’est pas immuable. Il doit évoluer lorsque de nouvelles observations invalident une hypothèse ou lorsqu’une décision d’architecture importante est prise. Les changements structurants devront être consignés dans un journal de décisions, puis intégrés dans une nouvelle version du cahier des charges.

## 0.1 Hiérarchie des décisions

Les éléments du document sont classés selon quatre statuts :

* **[INVARIANT]** : principe considéré comme stable. Toute remise en cause nécessite une décision explicite.
* **[DÉCISION v0]** : choix retenu pour la version fonctionnelle initiale. Il peut évoluer sans modifier la philosophie du produit.
* **[DÉCISION v1]** : choix retenu pour le durcissement et la préparation à une diffusion plus large.
* **[HYPOTHÈSE]** : proposition à tester par un prototype ou une expérimentation.
* **[OUVERT]** : point volontairement non figé.

En cas de conflit entre plusieurs sources :

1. la dernière décision d’architecture acceptée prévaut ;
2. le présent document doit ensuite être mis à jour ;
3. les tests automatisés définissent le comportement réel attendu du logiciel ;
4. les commentaires de code ne doivent jamais contredire le cahier des charges ou les décisions acceptées.

## 0.2 Vocabulaire normatif

* **DOIT** : exigence obligatoire.
* **NE DOIT PAS** : comportement interdit.
* **DEVRAIT** : exigence fortement recommandée, dérogeable avec justification.
* **PEUT** : capacité optionnelle.

---

# 1. Résumé exécutif

CONTX est une infrastructure personnelle, locale et open source qui observe l’activité d’un utilisateur sur son Mac afin de construire automatiquement la mémoire de travail de son agent personnel.

L’objectif n’est pas d’enregistrer chaque action de l’utilisateur ni de produire un journal exhaustif de sa journée. CONTX doit transformer une grande quantité de traces numériques en une mémoire compacte, utile, progressive et compréhensible.

La première version se concentre sur ce qui se passe sur l’ordinateur :

* application active ;
* fenêtre active ;
* durée passée dans un contexte ;
* changements de contexte ;
* captures d’écran déclenchées de manière sélective ;
* propositions de souvenirs ou de corrections envoyées par l’agent principal.

CONTX transforme ces traces selon la chaîne suivante :

```text
Activité du Mac
    ↓
Observations brutes
    ↓
Filtrage et sécurisation locale
    ↓
Événements structurés
    ↓
Patterns et inférences
    ↓
Souvenirs candidats
    ↓
Worker mémoire
    ↓
OptMem ou moteur mémoire dérivé
    ↓
Contexte mémoire directement fourni à l’agent
```

OptMem, ou une version améliorée d’OptMem, constitue la couche finale du système. Il ne s’agit pas d’une base secondaire consultée par un Context Builder. La sortie mémoire elle-même est le contexte transmis à l’agent.

La v0 est conçue pour :

* un utilisateur ;
* un Mac ;
* un agent principal ;
* une instance locale de CONTX.

Le système doit toutefois rester compatible avec différents agents généralistes comme Hermes, OpenClaw, Claude Code ou Codex.

---

# 2. Vision produit

## 2.1 Problème

Les agents personnels ne connaissent généralement que ce que l’utilisateur leur raconte explicitement, ce qu’ils voient dans une conversation ou ce qu’ils peuvent lire dans des fichiers accessibles.

Cette vision est incomplète.

L’utilisateur ne décrit pas spontanément :

* tout ce sur quoi il travaille ;
* les projets qu’il reprend ou abandonne ;
* les sujets auxquels il revient régulièrement ;
* la manière dont son attention évolue ;
* les changements récents de ses habitudes ;
* les problèmes rencontrés en dehors des sessions avec l’agent ;
* les périodes où un projet devient central ou secondaire ;
* la continuité entre plusieurs journées ou semaines de travail.

Un agent peut donc posséder beaucoup d’intelligence, tout en restant presque aveugle à la vie numérique réelle de l’utilisateur.

## 2.2 Proposition de valeur

**[INVARIANT]** CONTX doit donner à un agent une compréhension plus continue, plus située et plus actuelle de l’utilisateur en observant son activité numérique entre les interactions explicites avec cet agent.

La valeur principale de CONTX ne réside pas dans l’accès aux conversations ou aux dossiers déjà facilement accessibles. Elle réside dans sa capacité à capter le contexte diffus qui entoure ces éléments.

CONTX doit permettre à l’agent de comprendre :

* ce qui occupe actuellement l’utilisateur ;
* quels projets sont réellement actifs ;
* quels sujets prennent ou perdent de l’importance ;
* quels changements se produisent dans son travail ;
* quelles habitudes apparaissent dans le temps ;
* quelles décisions ou difficultés ont marqué une période récente ;
* comment les activités de l’utilisateur s’inscrivent dans une continuité.

## 2.3 Positionnement

**[INVARIANT]** CONTX est une mémoire de travail continue de l’activité numérique de l’utilisateur.

Il n’est pas, dans sa première forme, une mémoire générale de toute la vie de l’utilisateur.

L’intégration future du téléphone, d’une montre connectée, des photos, de la localisation ou de la domotique constituerait une extension majeure ou un produit distinct inspiré de CONTX. Cette possibilité ne doit pas élargir prématurément le périmètre de la v0.

---

# 3. Modèle d’utilisation

## 3.1 Utilisateur cible initial

**[DÉCISION v0]** CONTX est d’abord conçu pour Emi, dans un usage réel et quotidien.

Le projet est cependant structuré pour pouvoir devenir open source et être installé par d’autres utilisateurs techniques ou semi-techniques.

La priorité est donc :

1. résoudre correctement le problème pour un utilisateur réel ;
2. éviter les raccourcis qui rendraient le projet impossible à distribuer ;
3. ne pas construire prématurément une plateforme multi-utilisateur.

## 3.2 Relation avec l’agent

**[DÉCISION v0]** Une instance de CONTX alimente un agent principal.

```text
Utilisateur
    ↓
Instance CONTX locale
    ↓
Agent principal
```

Le moteur reste indépendant de l’agent choisi.

Changer d’agent ne doit pas entraîner la perte de la mémoire ni exiger une migration complète des données.

## 3.3 Évolution possible

À terme, une instance CONTX pourrait exposer plusieurs profils mémoire adaptés à différents agents :

```text
Instance CONTX
├── mémoire personnelle commune
├── profil Hermes
├── profil Codex
└── profil autre agent
```

**[HORS PÉRIMÈTRE v0]** Cette architecture multi-agent ne doit pas être implémentée avant que la mémoire mono-agent soit validée.

## 3.4 Modèle de versions

**[DÉCISION v0]** La v0 couvre les jalons 0 à 7. Elle se termine par un pilote réel de 7 à 14 jours et doit satisfaire les critères fonctionnels de la section 33.

**[DÉCISION v1]** La v1 correspond au jalon 8. Elle traite les résultats du pilote et ajoute le durcissement nécessaire à une version prête à diffuser : installation, mise à jour, désinstallation, sauvegarde, restauration, récupération, distribution macOS, documentation de sécurité et chaîne de licences vérifiée.

Les versions intermédiaires de la v0 ne constituent pas un contrat de compatibilité publique. Les données persistantes restent néanmoins protégées par des migrations explicites dès le premier schéma.

---

# 4. Invariants du projet

Les éléments suivants constituent la base stable de CONTX.

1. **[INVARIANT]** CONTX est initialement mono-utilisateur.
2. **[INVARIANT]** CONTX est local-first.
3. **[INVARIANT]** Les données brutes restent sur le Mac.
4. **[INVARIANT]** Les données brutes sont supprimées au plus tard 48 heures après leur capture.
5. **[INVARIANT]** L’extraction et l’interprétation sémantiques de la v0 utilisent obligatoirement un LLM local.
6. **[INVARIANT]** Aucun fournisseur de modèle distant ni transport sortant de contenu utilisateur n’existe dans la v0.
7. **[INVARIANT]** Les entrées, sorties et transformations du modèle local restent sur le Mac.
8. **[INVARIANT]** L’utilisateur doit pouvoir inspecter les transformations produites par le modèle local.
9. **[INVARIANT]** Une observation brute ne devient jamais directement un souvenir.
10. **[INVARIANT]** Le système distingue observation, événement, pattern ou inférence, souvenir candidat et souvenir enregistré.
11. **[INVARIANT]** Les souvenirs proposés par un agent passent par CONTX avant d’entrer dans la mémoire.
12. **[INVARIANT]** L’agent ne possède pas directement la mémoire.
13. **[INVARIANT]** OptMem ou une évolution d’OptMem constitue la couche finale de contexte mémoire.
14. **[INVARIANT]** Il n’existe pas de Context Builder séparé dans la v0.
15. **[INVARIANT]** Le système doit fonctionner entièrement hors ligne.
16. **[INVARIANT]** L’utilisateur peut suspendre la collecte à tout moment.
17. **[INVARIANT]** L’utilisateur peut exclure des applications, fenêtres ou situations.
18. **[INVARIANT]** CONTX ne doit pas capturer l’écran aveuglément à intervalle fixe sans tenir compte du contexte.
19. **[INVARIANT]** Le système doit rester inspectable, exportable et remplaçable.
20. **[INVARIANT]** Le code a vocation à être open source.
21. **[INVARIANT]** CONTX ne doit pas devenir un orchestrateur généraliste d’agents.
22. **[INVARIANT]** CONTX ne doit pas exécuter les actions personnelles à la place de l’agent.
23. **[INVARIANT]** La qualité de la mémoire importe davantage que la quantité de données collectées.
24. **[INVARIANT]** Le système doit privilégier une architecture monolithique modulaire avant toute séparation en microservices.
25. **[INVARIANT]** Les choix techniques doivent rester remplaçables derrière des interfaces stables.

---

# 5. Hors périmètre de la v0

La v0 ne doit pas inclure :

* collecte depuis un téléphone ;
* collecte depuis une montre connectée ;
* suivi de localisation ;
* domotique ;
* analyse de photos personnelles externes au Mac ;
* intégration du calendrier ;
* collecte directe des messages et des mails ;
* fonctionnement multi-utilisateur ;
* mémoire partagée entre plusieurs agents ;
* synchronisation cloud obligatoire ;
* application mobile ;
* prise en charge de Windows ou Linux ;
* automatisation générale ;
* exécution d’actions dans les applications ;
* assistant conversationnel intégré ;
* analyse psychologique profonde ;
* diagnostic d’émotions ou d’états mentaux ;
* archivage permanent des captures d’écran ;
* stockage d’une vidéo continue de l’écran ;
* création d’un profil commercial de l’utilisateur ;
* télémétrie distante activée par défaut.

---

# 6. Concepts fondamentaux

## 6.1 Observation

Une observation représente ce qu’un collecteur a directement détecté.

Exemples :

* l’application active est Visual Studio Code ;
* le titre de la fenêtre contient `CONTX` ;
* une capture d’écran a été prise à 14 h 32 ;
* l’utilisateur est resté 18 minutes dans la même fenêtre ;
* l’ordinateur est devenu inactif ;
* l’agent a proposé une correction.

Une observation ne contient pas nécessairement une interprétation.

## 6.2 Événement

Un événement représente l’interprétation structurée d’une ou plusieurs observations.

Exemple :

> Emi a travaillé pendant environ 45 minutes sur l’architecture de CONTX dans Visual Studio Code.

Un événement peut être créé à partir :

* d’une seule observation forte ;
* d’une séquence d’observations cohérentes ;
* d’une période continue d’activité ;
* d’un changement significatif de contexte.

## 6.3 Pattern

Un pattern représente une régularité observée sur plusieurs événements.

Exemples :

* CONTX est ouvert plusieurs jours consécutifs ;
* l’utilisateur travaille fréquemment tard le soir ;
* un projet précédemment inactif redevient central ;
* le temps passé sur une catégorie d’activité augmente fortement.

Un pattern ne doit pas être produit à partir d’un seul événement isolé.

## 6.4 Inférence

Une inférence est une interprétation qui va au-delà de l’observation directe.

Exemples :

* CONTX semble redevenir une priorité ;
* l’utilisateur paraît rencontrer un blocage persistant ;
* un sujet semble perdre en importance.

Les inférences doivent être explicitement différenciées des faits.

## 6.5 Souvenir candidat

Un souvenir candidat est une formulation concise susceptible d’être utile à l’agent dans le futur.

Il peut être :

* accepté ;
* rejeté ;
* différé ;
* fusionné ;
* reformulé ;
* associé à un souvenir existant ;
* transformé en correction.

## 6.6 Souvenir

Un souvenir est une information sélectionnée et enregistrée dans la couche mémoire finale.

Un souvenir doit être :

* utile ;
* concis ;
* compréhensible hors du contexte immédiat ;
* suffisamment fiable ;
* non redondant ;
* compatible avec les politiques de confidentialité ;
* relié à une provenance dans CONTX.

## 6.7 Contexte mémoire

Le contexte mémoire est la sortie d’OptMem ou du moteur mémoire qui lui succède.

**[INVARIANT]** Ce contexte est directement fourni à l’agent.

---

# 7. Niveaux épistémiques

CONTX doit distinguer au minimum trois niveaux.

## 7.1 Observé

Information directement soutenue par une source.

Exemple :

> Visual Studio Code a été actif pendant 42 minutes sur une fenêtre liée à CONTX.

## 7.2 Déduit

Interprétation raisonnable soutenue par plusieurs observations.

Exemple :

> Emi a travaillé activement sur CONTX cet après-midi.

## 7.3 Hypothétique

Interprétation possible mais encore fragile.

Exemple :

> CONTX semble être redevenu une priorité importante.

## 7.4 Métadonnées minimales

Les événements, patterns, inférences et souvenirs candidats DEVRAIENT inclure :

```text
epistemic_status: observed | inferred | hypothetical
confidence: nombre compris entre 0 et 1
source_ids: liste des observations ou événements associés
valid_from: date de début éventuelle
valid_until: date de fin éventuelle
```

Le score de confiance ne doit pas être présenté à l’utilisateur comme une probabilité scientifique exacte. Il sert à hiérarchiser, différer ou empêcher certaines décisions automatiques.

---

# 8. Expérience produit attendue

## 8.1 Parcours principal

1. CONTX fonctionne en arrière-plan sur le Mac.
2. Il détecte l’application active et la fenêtre active.
3. Il capture sélectivement certains changements visuels.
4. Il regroupe les observations en événements.
5. Il détecte les répétitions et évolutions importantes.
6. Il propose des souvenirs candidats.
7. Le worker mémoire accepte, fusionne ou rejette ces candidats.
8. La mémoire finale est mise à jour.
9. L’agent exécute une commande de réveil en début de session.
10. L’agent reçoit une vision compacte de l’utilisateur, de ses projets et de leurs évolutions.

## 8.2 Comportements à démontrer

La v0 doit démontrer trois capacités principales.

### Reprise de projet

L’agent doit pouvoir reprendre correctement un projet récent sans demander à l’utilisateur de réexpliquer tout son contexte.

### Compréhension de la période récente

L’agent doit pouvoir expliquer ce qui a principalement occupé l’utilisateur au cours des derniers jours.

### Détection de changement

L’agent doit pouvoir remarquer un changement significatif, par exemple :

* reprise d’un projet ;
* abandon apparent ;
* augmentation de l’activité ;
* changement de sujet dominant ;
* apparition d’une nouvelle habitude de travail.

---

# 9. Architecture fonctionnelle

```text
┌──────────────────────────────────────────────────────┐
│                      macOS                           │
│                                                      │
│  Application active                                 │
│  Fenêtre active                                     │
│  Activité utilisateur                               │
│  Capture d’écran sélective                          │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Collecteurs macOS                                    │
│ Détection, horodatage, déduplication simple          │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Stockage brut local                                  │
│ Métadonnées + fichiers temporaires                   │
│ Rétention maximale : 48 h                            │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Interprétation multimodale locale                    │
│ Modèle local, schémas stricts, sensibilité, audit    │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Base enrichie                                        │
│ Événements, entités, projets, confiance, provenance  │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Moteur de patterns et d’inférences                   │
│ Répétitions, tendances, changements                  │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Worker mémoire                                       │
│ Sélection, déduplication, fusion, correction         │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ MemoryStore                                          │
│ OptMem ou version améliorée                          │
│ Contexte final de l’agent                            │
└─────────────────────────┬────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Agent principal                                      │
│ wake, recall, zoom, proposition, correction          │
└──────────────────────────────────────────────────────┘
```

## 9.1 Architecture générale

**[DÉCISION v0]** CONTX doit être construit comme un monolithe modulaire.

Les responsabilités doivent être séparées dans le code, mais elles peuvent fonctionner dans un même processus ou dans un petit nombre de processus locaux.

Les modules principaux sont :

* `collectors`
* `raw_store`
* `privacy`
* `processing`
* `events`
* `patterns`
* `memory_worker`
* `memory_store`
* `agent_gateway`
* `api`
* `cli`
* `web`
* `audit`

**[DÉCISION v0]** `agent_gateway` est une passerelle de transport. Il peut appeler la mémoire, paginer sa sortie et signaler séparément un état technique. Il NE DOIT PAS ajouter des observations, des événements récents ou un résumé lié à la tâche dans le contexte sémantique retourné par `MemoryStore`.

---

# 10. Collecte macOS

## 10.1 Sources initiales

La v0 collecte :

1. l’application active ;
2. l’identifiant de l’application ;
3. la fenêtre active ;
4. le titre de la fenêtre lorsque son accès est autorisé ;
5. la durée passée dans une application ou une fenêtre ;
6. l’état actif, inactif, verrouillé ou endormi du Mac ;
7. des captures d’écran sélectives ;
8. les propositions envoyées par l’agent.

## 10.2 Permissions macOS

CONTX doit gérer explicitement les permissions suivantes :

* enregistrement de l’écran ;
* accessibilité ;
* automatisation éventuelle ;
* accès aux informations de fenêtre.

L’interface d’installation doit :

* expliquer pourquoi chaque permission est nécessaire ;
* détecter lorsqu’une permission manque ;
* fournir le chemin vers les réglages macOS ;
* continuer à fonctionner avec des capacités réduites lorsque cela est possible ;
* ne pas boucler silencieusement en cas de refus.

## 10.3 Déclenchement des captures

**[INVARIANT]** CONTX ne capture pas l’écran à une cadence fixe aveugle.

Une capture PEUT être déclenchée par :

* changement d’application ;
* changement de fenêtre ;
* modification visuelle significative ;
* retour de l’utilisateur après une période d’inactivité ;
* intervalle maximal sans capture pendant une activité continue ;
* demande manuelle ;
* événement système pertinent.

## 10.4 Paramètres initiaux configurables

Les valeurs suivantes sont des valeurs de départ et non des invariants :

```text
screenshot_min_interval_seconds: 15
screenshot_max_interval_seconds: 120
idle_threshold_seconds: 300
visual_change_threshold: à calibrer
```

Ces paramètres doivent être modifiables sans modifier le code.

## 10.5 Déduplication

Le système doit éviter de conserver plusieurs captures quasi identiques.

La déduplication peut utiliser :

* hash exact ;
* hash perceptuel ;
* comparaison de régions ;
* changement de fenêtre ;
* variation sémantique estimée localement ;
* temporisation minimale.

## 10.6 Regroupement en sessions

Les observations doivent être regroupées en sessions d’activité.

Une session peut être définie par :

* continuité temporelle ;
* même application ;
* même fenêtre ou même projet ;
* absence de longue période d’inactivité ;
* similarité sémantique.

La logique exacte doit rester configurable et testable.

---

# 11. Exclusions et contrôle de la collecte

## 11.1 Politique par défaut

**[DÉCISION v0]** La collecte est autorisée par défaut avec une liste d’exclusion.

## 11.2 Exclusions initiales

CONTX doit exclure par défaut :

* gestionnaires de mots de passe ;
* applications bancaires ;
* pages ou fenêtres de paiement détectables ;
* écrans de connexion ;
* navigation privée détectable ;
* écran verrouillé ;
* saisie de mots de passe lorsque détectable ;
* applications explicitement ajoutées par l’utilisateur ;
* fenêtres explicitement ajoutées par l’utilisateur ;
* périodes de pause.

## 11.3 Comportement dans une zone exclue

Lorsqu’une application ou une fenêtre est exclue :

* aucune capture d’écran ne doit être prise ;
* aucun titre de fenêtre sensible ne doit être stocké ;
* aucun contenu ne doit être transmis à un modèle ;
* une observation minimale de type `excluded_activity` PEUT être conservée pour préserver la continuité temporelle.

Cette observation minimale peut contenir :

```text
date de début
date de fin
application exclue ou catégorie générique
raison de l’exclusion
```

L’utilisateur doit pouvoir désactiver même cette observation minimale.

## 11.4 Pause

L’utilisateur doit pouvoir :

* mettre CONTX en pause immédiatement ;
* définir une durée de pause ;
* reprendre manuellement ;
* vérifier clairement que la collecte est suspendue.

Un indicateur visible doit refléter l’état réel de la collecte.

---

# 12. Stockage brut

## 12.1 Rôle

Le stockage brut conserve temporairement la matière première nécessaire à :

* l’audit ;
* la correction d’un traitement ;
* le développement ;
* la comparaison entre source et événement ;
* la réexécution d’un pipeline amélioré.

## 12.2 Rétention

**[INVARIANT]** Une donnée brute ne doit jamais être conservée plus de 48 heures après sa capture.

Chaque donnée brute doit posséder un champ `expires_at`.

Un processus de purge doit :

* s’exécuter régulièrement ;
* s’exécuter au démarrage ;
* supprimer les fichiers expirés ;
* supprimer ou anonymiser les références associées ;
* journaliser le résultat de la purge ;
* signaler les erreurs.

La valeur de 48 heures est un maximum. L’utilisateur peut choisir une durée plus courte.

## 12.3 Sauvegardes

Les données brutes ne doivent pas être incluses dans les sauvegardes par défaut.

Les sauvegardes peuvent inclure :

* configuration ;
* événements enrichis ;
* patterns ;
* souvenirs candidats ;
* mémoire finale ;
* historique des décisions ;
* journaux techniques non sensibles.

## 12.4 Suppression

La suppression sur SSD ne peut pas garantir un effacement physique forensique. CONTX doit :

* supprimer le fichier ;
* supprimer sa référence active ;
* ne plus le rendre accessible dans l’interface ;
* éviter les copies cachées ;
* documenter clairement cette limite.

---

# 13. Frontière d’interprétation locale

## 13.1 Principe

**[INVARIANT]** Aucun contenu utilisateur, brut ou enrichi, ne peut quitter le Mac dans la v0.

Toute interprétation sémantique doit passer par un modèle multimodal local derrière une interface typée.

## 13.2 Traitements locaux minimaux

Le traitement local doit pouvoir :

* identifier l’application ;
* récupérer les métadonnées de fenêtre ;
* interpréter les captures autorisées et leurs métadonnées avec un modèle local ;
* produire des événements structurés conformes à un schéma strict ;
* identifier les projets, sujets et entités utiles ;
* détecter des catégories sensibles ;
* attribuer un niveau de sensibilité ;
* distinguer les faits observés des interprétations ;
* conserver le modèle, sa version, la version du prompt, le schéma de sortie, la latence et la provenance des sources ;
* refuser toute sortie invalide sans enregistrer son contenu dans les logs.

## 13.3 Données sensibles et secrets

La v0 ne possède pas de pipeline déterministe parallèle pour extraire, détecter ou masquer des secrets avant l’inférence. Des secrets peuvent donc apparaître dans les entrées du modèle local.

La sécurité repose sur les frontières suivantes :

* exclusions appliquées avant toute capture ;
* traitement exclusivement local ;
* absence de transport sortant de contenu utilisateur ;
* absence de contenu brut, d’entrée de modèle ou de sortie de modèle dans les logs techniques ;
* classification locale de la sensibilité ;
* refus de promouvoir délibérément un secret ou une donnée trop sensible vers la mémoire durable ;
* audit inspectable des références d’entrée, de la transformation et de la décision de promotion.

Les fixtures synthétiques doivent au minimum représenter :

* clés API ;
* tokens ;
* mots de passe ;
* secrets SSH ;
* cookies ;
* numéros bancaires ;
* identifiants fiscaux ;
* données médicales ;
* codes de récupération ;
* informations explicitement interdites par l’utilisateur.

## 13.4 Absence de traitement distant en v0

La v0 ne contient ni fournisseur de modèle distant ni chemin réseau capable de transporter du contenu utilisateur. Le protocole loopback vers un runtime situé sur le même Mac constitue un traitement local.

Tout traitement distant futur nécessite une nouvelle décision produit, un ADR et une frontière de sécurité dédiée avant que du contenu utilisateur puisse quitter le Mac.

---

# 14. Construction des événements

## 14.1 Objectif

Le moteur d’événements transforme les observations en unités compréhensibles.

Il doit répondre à des questions comme :

* que faisait l’utilisateur ?
* pendant combien de temps ?
* dans quelle application ?
* sur quel projet ou sujet ?
* avec quel niveau de certitude ?
* quelles observations soutiennent cette conclusion ?

## 14.2 Exemple

```json
{
  "id": "event_01428",
  "started_at": "2026-08-01T14:10:00+02:00",
  "ended_at": "2026-08-01T14:55:00+02:00",
  "type": "project_work",
  "summary": "Emi travaille sur l’architecture de CONTX.",
  "applications": ["Visual Studio Code"],
  "projects": ["CONTX"],
  "entities": ["OptMem"],
  "epistemic_status": "inferred",
  "confidence": 0.87,
  "source_observation_ids": ["obs_81", "obs_82", "obs_83"],
  "sensitivity": "normal"
}
```

## 14.3 Exigences

Un événement doit :

* posséder une période ;
* citer ses observations sources ;
* distinguer fait et interprétation ;
* pouvoir être recalculé ;
* pouvoir être corrigé ;
* ne pas dépendre d’un fournisseur de modèle précis ;
* être idempotent lorsque le même lot est retraité.

---

# 15. Détection de patterns et de changements

## 15.1 Rôle

Le moteur de patterns analyse plusieurs événements afin de détecter des régularités ou des évolutions.

## 15.2 Patterns visés en v0

* projet récurrent ;
* projet repris après une période d’inactivité ;
* projet apparemment abandonné ;
* augmentation ou diminution du temps consacré à un sujet ;
* répétition d’un blocage ;
* horaires de travail récurrents ;
* changement d’application principale ;
* concentration inhabituelle sur un domaine ;
* alternance fréquente entre plusieurs tâches ;
* nouvelle activité apparaissant plusieurs fois.

## 15.3 Fenêtres temporelles

Les analyses devraient pouvoir comparer :

* la journée actuelle ;
* les trois derniers jours ;
* les sept derniers jours ;
* les trente derniers jours ;
* une période antérieure comparable.

Les fenêtres exactes doivent être configurables.

## 15.4 Garde-fous

Un pattern ne doit pas être enregistré comme fait durable lorsqu’il repose sur trop peu d’évidence.

Le moteur doit pouvoir :

* différer une conclusion ;
* réduire sa confiance ;
* marquer une durée de validité ;
* remplacer un pattern devenu obsolète ;
* conserver les événements qui soutiennent l’inférence.

---

# 16. Souvenirs candidats

## 16.1 Production

Les souvenirs candidats peuvent provenir :

* d’un événement important ;
* de plusieurs événements cohérents ;
* d’un pattern ;
* d’une correction utilisateur ;
* d’une proposition de l’agent ;
* d’un changement durable ;
* d’une décision explicitement exprimée.

## 16.2 Critères de sélection

Le worker mémoire doit évaluer au minimum :

* utilité future pour l’agent ;
* importance ;
* durabilité ;
* nouveauté ;
* confiance ;
* répétition ;
* impact sur un projet ;
* capacité à expliquer un changement ;
* sensibilité ;
* redondance ;
* niveau d’ambiguïté.

## 16.3 Logique conceptuelle

Une logique de score peut être utilisée :

```text
score =
  utilité
+ importance
+ durabilité
+ nouveauté
+ récurrence
+ confiance
- sensibilité
- ambiguïté
- redondance
```

Les poids ne doivent pas être considérés comme définitifs. Ils doivent être configurables et évalués sur des données réelles.

## 16.4 Rejets automatiques

Un candidat doit être rejeté ou différé s’il est :

* trivial ;
* purement momentané ;
* redondant ;
* insuffisamment soutenu ;
* trop sensible ;
* formulé comme une spéculation ;
* centré sur une autre personne sans utilité claire ;
* constitué principalement d’un secret ;
* impossible à relier à une provenance.

## 16.5 Forme

Les souvenirs doivent être courts et autonomes.

Exemple incorrect :

> A travaillé dessus aujourd’hui.

Exemple correct :

> Emi a repris activement le développement de CONTX début août 2026.

La limite actuelle d’OptMem est une ligne courte. CONTX ne doit pas dépendre globalement d’une taille fixe, mais l’adaptateur OptMem doit respecter les contraintes du backend utilisé.

---

# 17. Worker mémoire

## 17.1 Responsabilités

Le worker mémoire doit :

* lire les événements récents ;
* lire les patterns ;
* produire des candidats ;
* vérifier les doublons ;
* évaluer la qualité ;
* fusionner les formulations proches ;
* différer les conclusions fragiles ;
* accepter ou rejeter ;
* transmettre les souvenirs au MemoryStore ;
* gérer les propositions des agents ;
* gérer les corrections ;
* déclencher les compressions nécessaires ;
* journaliser ses décisions.

## 17.2 Fréquence

Valeurs initiales proposées :

```text
traitement léger des observations: continu ou toutes les quelques minutes
enrichissement sémantique: par batch toutes les 15 minutes
analyse des patterns: toutes les quelques heures
sélection mémoire: toutes les 2 heures
consolidation longue: quotidienne
```

Ces valeurs sont des hypothèses.

## 17.3 Avant le réveil d’un agent

La commande de réveil doit intégrer les souvenirs déjà validés.

Elle PEUT déclencher un traitement léger des candidats en attente, mais elle ne doit pas provoquer une analyse lourde de toutes les captures brutes ni bloquer le démarrage pendant une longue période.

---

# 18. Couche mémoire finale

## 18.1 Principe

**[INVARIANT]** OptMem ou une version améliorée d’OptMem constitue le contexte mémoire final.

La mémoire doit :

* survivre aux sessions ;
* survivre aux changements de modèle ;
* survivre au remplacement de l’agent ;
* être locale ;
* être exportable ;
* conserver une continuité temporelle ;
* compresser progressivement le passé ;
* laisser davantage de détail au présent ;
* permettre la recherche et la navigation.

## 18.2 État actuel d’OptMem

OptMem propose actuellement :

* un journal append-only ;
* des souvenirs courts ;
* une identité fondée sur la position dans le journal ;
* un arbre binaire de résumés ;
* une commande de réveil ;
* une recherche textuelle par expression régulière ;
* une navigation dans l’arbre ;
* la suppression et reconstruction des résumés ;
* aucune dépendance Python externe.

## 18.3 Limites identifiées

OptMem ne gère pas nativement :

* métadonnées riches ;
* provenance structurée ;
* niveau de confiance ;
* statut courant ou obsolète ;
* relation de correction ;
* relation de remplacement ;
* recherche sémantique ;
* sélection selon la requête ;
* invalidation propre d’un souvenir brut ;
* politiques de sensibilité.

## 18.4 Stratégie d’intégration

Le projet doit introduire une interface `MemoryStore`.

```python
class MemoryStore:
    def append(self, text, metadata): ...
    def wake(self): ...
    def recall(self, query): ...
    def zoom(self, node_id): ...
    def propose_correction(self, memory_id, replacement): ...
    def invalidate_summary(self, node_id): ...
    def export(self, format): ...
```

Les modules de CONTX ne doivent pas appeler directement les fichiers internes d’OptMem.

## 18.5 Expérimentation requise

Trois options doivent être comparées.

### Option A : OptMem inchangé

* intégration rapide ;
* très faible complexité ;
* corrections ajoutées comme nouveaux souvenirs ;
* métadonnées conservées dans une base latérale.

### Option B : adaptateur OptMem avec métadonnées latérales

* OptMem conserve la mémoire textuelle ;
* CONTX conserve provenance, confiance et relations ;
* les corrections restent append-only ;
* les résumés peuvent être reconstruits.

### Option C : version améliorée d’OptMem

* prise en charge native des corrections ;
* statut `superseded` ou équivalent ;
* filtrage des souvenirs invalidés ;
* amélioration du réveil ;
* métadonnées structurées ;
* recherche enrichie éventuelle.

**[OUVERT]** Le choix final doit être fait après un prototype et une évaluation, pas avant.

## 18.6 Corrections

Le système doit pouvoir représenter :

* une information devenue fausse ;
* une information devenue obsolète ;
* une correction explicite ;
* un souvenir remplacé ;
* un résumé incorrect.

Une stratégie append-only acceptable est :

```text
souvenir original
    ↓
nouvelle entrée de correction
    ↓
relation supersedes
    ↓
reconstruction des résumés
```

La mémoire fournie à l’agent ne doit pas présenter un ancien souvenir et sa correction comme deux vérités équivalentes.

---

# 19. Intégration de l’agent

## 19.1 Principe

L’agent accède à CONTX par une interface stable.

La v0 privilégie une CLI compatible avec les agents capables d’exécuter des commandes.

## 19.2 Commandes cibles

```text
contx wake
contx recall <requête>
contx zoom <nœud>
contx propose "<souvenir proposé>"
contx correct <id> "<correction>"
contx status
contx pause
contx resume
```

Les commandes exactes peuvent évoluer, mais leurs responsabilités doivent rester séparées.

## 19.3 Réveil

`contx wake` doit :

* retourner la mémoire finale ;
* respecter un budget configurable ;
* inclure davantage de détail sur le présent ;
* compresser les périodes anciennes ;
* signaler les opérations obligatoires ;
* fonctionner sans accès réseau.

**[DÉCISION v0]** Le corps sémantique retourné par `contx wake` provient directement de `MemoryStore`. Les statuts techniques éventuels sont séparés de cette sortie et ne constituent pas une seconde source de contexte.

## 19.4 Proposition d’un souvenir

`contx propose` ne doit pas écrire directement dans OptMem.

Le flux est :

```text
Agent
    ↓
Proposition
    ↓
Validation CONTX
    ↓
Déduplication et provenance
    ↓
MemoryStore
```

## 19.5 Sous-agents

Les sous-agents ne doivent pas écrire dans la mémoire principale.

L’agent principal peut transmettre à CONTX une conclusion produite par un sous-agent, mais il doit en assumer la sélection.

## 19.6 Compatibilité

L’intégration doit pouvoir être expliquée par un petit bloc d’instructions destiné à :

* Hermes ;
* OpenClaw ;
* Claude Code ;
* Codex ;
* autres agents capables d’exécuter des commandes.

MCP peut être ajouté plus tard, mais n’est pas requis pour valider la v0.

---

# 20. Interface utilisateur

## 20.1 Forme

**[DÉCISION v0]** CONTX possède :

* un service local en arrière-plan ;
* une CLI ;
* une interface web locale.

## 20.2 Accès

L’interface web doit écouter uniquement sur `127.0.0.1` par défaut.

Aucune ouverture réseau ne doit être activée automatiquement.

## 20.3 Espaces principaux

### Tableau de bord

* état de CONTX ;
* collecte active ou en pause ;
* permissions ;
* dernières erreurs ;
* volume brut ;
* mémoire utilisée ;
* dernier traitement.

### Activité

* applications récentes ;
* sessions de travail ;
* captures ;
* observations ;
* événements produits ;
* chronologie.

### Patterns

* tendances détectées ;
* preuves ;
* confiance ;
* durée de validité ;
* évolution dans le temps.

### Mémoire

* souvenirs candidats ;
* souvenirs acceptés ;
* souvenirs rejetés ;
* corrections ;
* provenance ;
* aperçu du `wake`.

### Confidentialité

* exclusions ;
* fichiers bruts ;
* date d’expiration ;
* exécutions du modèle local ;
* références des entrées autorisées ;
* modèle, version du prompt et schéma de sortie ;
* sorties validées et niveau de sensibilité ;
* décisions de promotion ou de rejet.

### Agent

* agent principal configuré ;
* dernières commandes ;
* propositions reçues ;
* erreurs d’intégration ;
* bloc d’instructions à installer.

### Réglages

* fréquence de capture ;
* rétention ;
* modèles ;
* API ;
* exclusions ;
* budget mémoire ;
* export.

## 20.4 Actions utilisateur

L’utilisateur doit pouvoir :

* mettre en pause ;
* reprendre ;
* exclure une application ;
* exclure une fenêtre ;
* supprimer immédiatement les données brutes ;
* corriger un événement ;
* corriger un souvenir ;
* accepter ou rejeter un candidat ;
* relancer un traitement ;
* voir les preuves ;
* exporter ;
* supprimer toutes les données.

L’utilisation quotidienne ne doit pas exiger de valider chaque souvenir.

---

# 21. Modèle de données conceptuel

## 21.1 Observation

```text
id
source_type
captured_at
started_at
ended_at
app_name
app_bundle_id
window_title
artifact_path
content_hash
perceptual_hash
excluded
exclusion_reason
processing_status
expires_at
created_at
```

## 21.2 Event

```text
id
type
summary
started_at
ended_at
epistemic_status
confidence
sensitivity
projects
entities
source_observation_ids
processing_version
created_at
updated_at
```

## 21.3 Pattern

```text
id
type
summary
window_start
window_end
confidence
evidence_count
source_event_ids
valid_from
valid_until
status
created_at
```

## 21.4 MemoryCandidate

```text
id
text
source_type
source_ids
importance
durability
novelty
confidence
sensitivity
score
status
rejection_reason
created_at
processed_at
```

## 21.5 AgentProposal

```text
id
agent_id
text
proposal_type
reference
status
created_at
processed_at
```

## 21.6 MemoryLink

```text
id
memory_backend_id
candidate_id
provenance
confidence
status
supersedes_memory_id
created_at
```

## 21.7 ModelTransformation

```text
id
processing_run_id
observation_id
provider
model
model_version
prompt_version
output_schema_version
input_references
output_reference
sensitivity
latency_ms
status
created_at
```

## 21.8 ProcessingRun

```text
id
pipeline
version
started_at
ended_at
status
input_count
output_count
error
```

## 21.9 ExclusionRule

```text
id
rule_type
pattern
scope
enabled
created_at
```

---

# 22. Stack technique de référence

Les choix suivants constituent une base d’implémentation v0. Ils ne sont pas des invariants produit.

## 22.1 Backend

**[DÉCISION v0]**

* Python 3.12, verrouillé par `uv` ;
* FastAPI pour l’API locale lorsqu’elle possède un consommateur réel ;
* Pydantic pour les schémas ;
* SQLite en mode WAL ;
* SQLAlchemy 2, avec des modèles de persistance distincts des schémas Pydantic ;
* Alembic pour les migrations ;
* Typer pour la CLI ;
* pytest pour les tests.

## 22.2 macOS

* PyObjC pour l’accès aux frameworks macOS ;
* NSWorkspace pour les changements d’application ;
* Accessibility API pour les fenêtres ;
* ScreenCaptureKit ou CoreGraphics pour les captures ;
* détection d’inactivité via les API système ;
* lancement en arrière-plan via `launchd`.

## 22.3 Interprétation multimodale

**[DÉCISION v0]** L’interprétation des captures et métadonnées autorisées dépend d’un modèle multimodal local. Un OCR autonome n’est pas nécessaire au chemin de production de la v0.

Un OCR local autonome peut être évalué plus tard comme optimisation derrière une interface remplaçable. Il ne doit pas devenir un fallback sémantique silencieux.

## 22.4 Modèles

Le système doit proposer une interface commune :

```python
class ModelProvider:
    def interpret(self, request: ModelRequest) -> ModelResult: ...
```

Backends locaux possibles :

* modèle local via MLX ;
* modèle local via Ollama ;
* modèle local via `llama.cpp`.

Le choix du runtime et du modèle doit être fondé sur des benchmarks réalisés sur un Mac M4 avec 16 Go de RAM. Le fournisseur local doit retourner une sortie conforme à un schéma strict et enregistrer une provenance inspectable sans journaliser les contenus privés.

Les fournisseurs distants et les règles déterministes sans modèle sont exclus du chemin sémantique de la v0.

## 22.5 Frontend

**[DÉCISION v0]** Une SPA locale simple en TypeScript peut être construite avec React et Vite.

Le contrat API doit permettre de remplacer le frontend sans modifier le cœur.

## 22.6 Fichiers

* SQLite pour les données structurées ;
* système de fichiers local pour les captures temporaires ;
* répertoires privés avec permissions restrictives ;
* données durables dans `~/Library/Application Support/CONTX/` ;
* données brutes temporaires dans `~/Library/Caches/CONTX/` ;
* journaux techniques dans `~/Library/Logs/CONTX/` ;
* racines isolées et remplaçables pour les tests et le développement ;
* format JSONL pour certains exports ;
* Markdown pour les exports humains ;
* format natif du backend mémoire pour la mémoire.

---

# 23. Organisation du dépôt

```text
contx/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── cahier_des_charges.md
├── AGENTS.md
├── pyproject.toml
├── apps/
│   ├── api/
│   ├── cli/
│   └── web/
├── contx/
│   ├── collectors/
│   │   └── macos/
│   ├── raw_store/
│   ├── privacy/
│   ├── processing/
│   ├── events/
│   ├── patterns/
│   ├── candidates/
│   ├── memory_worker/
│   ├── memory_store/
│   │   ├── base.py
│   │   ├── optmem.py
│   │   └── improved.py
│   ├── agent_gateway/
│   ├── audit/
│   ├── models/
│   ├── settings/
│   └── db/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── privacy/
│   ├── replay/
│   └── performance/
├── fixtures/
├── scripts/
└── docs/
    ├── implementation-plan.md
    ├── architecture/
    ├── adr/
    ├── evaluation/
    ├── third-party/
    └── threat-model/
```

---

# 24. API locale indicative

## Système

```text
GET  /api/status
POST /api/pause
POST /api/resume
```

## Sources

```text
GET    /api/sources
GET    /api/exclusions
POST   /api/exclusions
DELETE /api/exclusions/{id}
```

## Observations

```text
GET    /api/observations
GET    /api/observations/{id}
DELETE /api/observations/{id}
```

## Événements

```text
GET   /api/events
GET   /api/events/{id}
PATCH /api/events/{id}
```

## Patterns

```text
GET /api/patterns
GET /api/patterns/{id}
```

## Candidats

```text
GET  /api/memory-candidates
POST /api/memory-candidates/{id}/accept
POST /api/memory-candidates/{id}/reject
```

## Mémoire

```text
GET  /api/memory
GET  /api/memory/wake
POST /api/memory/proposals
POST /api/memory/corrections
GET  /api/memory/recall
```

## Confidentialité

```text
GET /api/privacy/model-transformations
GET /api/privacy/raw-artifacts
```

## Traitements

```text
GET  /api/processing/runs
POST /api/processing/retry
```

Cette API est indicative. Les contrats doivent être versionnés.

---

# 25. Fiabilité

## 25.1 Principes

Le système doit être robuste face à :

* arrêt brutal ;
* redémarrage ;
* mise en veille ;
* perte de permission ;
* modèle indisponible ;
* traitement interrompu ;
* doublon ;
* corruption partielle ;
* mise à jour de schéma ;
* capture invalide.

## 25.2 Exigences

* les opérations critiques doivent être idempotentes ;
* les écritures doivent être transactionnelles ;
* les fichiers temporaires doivent être atomiquement renommés ;
* les tâches doivent pouvoir reprendre après un crash ;
* les traitements doivent posséder une version ;
* une observation ne doit pas être traitée deux fois sans raison ;
* une erreur ne doit pas bloquer toute la file ;
* les tâches échouées doivent être inspectables ;
* les résumés mémoire doivent pouvoir être reconstruits.

## 25.3 Journalisation

Les logs doivent :

* être locaux ;
* éviter les contenus sensibles ;
* être structurés ;
* être tournants ;
* inclure l’identifiant du traitement ;
* permettre de diagnostiquer sans révéler les données privées.

---

# 26. Sécurité et confidentialité

## 26.1 Principes

* aucune télémétrie par défaut ;
* aucun contenu utilisateur distant ;
* permissions minimales ;
* interface locale uniquement ;
* exclusions avant capture ;
* modèle multimodal local obligatoire ;
* rétention limitée ;
* transparence des traitements ;
* possibilité de suppression complète.

## 26.2 Permissions de fichiers

Les répertoires de données doivent être accessibles uniquement à l’utilisateur local.

## 26.3 Chiffrement

La v0 peut s’appuyer sur FileVault et les protections du compte macOS.

L’application doit détecter ou recommander l’activation de FileVault.

Le chiffrement applicatif supplémentaire reste une amélioration possible, à évaluer selon le coût de complexité.

## 26.4 Données de tiers

CONTX peut observer des informations relatives à d’autres personnes.

Le système doit :

* éviter de transformer automatiquement ces informations en profil de tiers ;
* limiter leur conservation ;
* appliquer une pénalité de sensibilité ;
* éviter les inférences personnelles non nécessaires ;
* permettre leur suppression ;
* conserver uniquement ce qui est pertinent pour comprendre le contexte de l’utilisateur.

---

# 27. Performance et sobriété

## 27.1 Machine cible

* Mac Apple Silicon M4 ;
* 16 Go de RAM ;
* fonctionnement quotidien en arrière-plan.

## 27.2 Objectifs initiaux

Les objectifs suivants doivent être mesurés.

### Collecteur

* CPU moyen au repos inférieur à 2 % ;
* mémoire inférieure à 250 Mo ;
* détection d’un changement d’application en moins de 2 secondes.

### Service hors modèle

* mémoire totale inférieure à 500 Mo en usage normal ;
* démarrage en moins de 5 secondes ;
* interface locale réactive.

### Traitement

* événements disponibles dans les minutes suivant l’activité ;
* mémoire actualisée en moins de 2 heures en fonctionnement normal ;
* modèles lourds déchargés lorsqu’ils ne sont pas utilisés ;
* traitement différé lorsque la machine est sur batterie ou fortement sollicitée.

### Stockage

* volume brut borné par la rétention de 48 heures ;
* cible initiale inférieure à 5 Go dans un usage normal ;
* avertissement lorsque le budget disque est dépassé.

Ces valeurs sont des objectifs, pas encore des garanties.

---

# 28. Évaluation de la qualité

## 28.1 Principe

Le projet ne sera pas validé parce qu’il collecte des données ou produit des résumés.

Il sera validé si la mémoire améliore réellement le comportement de l’agent.

## 28.2 Protocole pilote

Une période pilote de 7 à 14 jours doit être utilisée.

L’utilisateur conserve une vérité terrain minimale :

* projets importants ;
* changements de priorité ;
* décisions majeures ;
* périodes de travail ;
* événements que l’agent devrait connaître.

L’agent est ensuite évalué avec et sans CONTX.

## 28.3 Scénario A : reprise de projet

Questions possibles :

* sur quoi travaillais-je récemment ?
* où en est CONTX ?
* quelles décisions importantes ont été prises ?
* quels problèmes sont encore ouverts ?

Mesures :

* exactitude ;
* couverture ;
* quantité de clarification demandée ;
* informations inventées ;
* pertinence.

## 28.4 Scénario B : compréhension récente

Questions possibles :

* qu’est-ce qui a principalement occupé mes derniers jours ?
* quels projets ont reçu le plus d’attention ?
* quels sujets ont été récurrents ?

Mesures :

* corrélation avec la vérité terrain ;
* équilibre entre détail et synthèse ;
* bruit ;
* omissions.

## 28.5 Scénario C : détection de changement

Questions possibles :

* quel projet ai-je repris ?
* quelle activité a augmenté ?
* quelle habitude semble nouvelle ?
* qu’est-ce qui a changé par rapport à la semaine précédente ?

Mesures :

* délai de détection ;
* confiance ;
* faux positifs ;
* qualité de la preuve.

## 28.6 Métriques techniques

* précision des souvenirs ;
* rappel des événements importants ;
* taux de souvenirs inutiles ;
* taux de souvenirs faux ;
* taux de doublons ;
* taux de corrections manuelles ;
* proportion de souvenirs avec provenance ;
* taux de contenus sensibles promus à tort ;
* taux de sorties de modèle invalides ;
* taille du contexte ;
* temps de réveil ;
* consommation de ressources.

## 28.7 Seuils initiaux

Avant une première release utilisable :

* 100 % des souvenirs acceptés doivent avoir une provenance ;
* aucune donnée brute ne doit dépasser 48 heures ;
* aucun contenu utilisateur ne doit emprunter un transport distant ;
* aucune application exclue ne doit produire de capture ;
* le taux de souvenirs matériellement faux doit rester inférieur à 10 % sur le pilote ;
* la majorité des événements jugés importants par l’utilisateur doit être retrouvée ;
* le contexte doit rester sous le budget configuré ;
* l’utilisateur doit pouvoir comprendre pourquoi un souvenir existe.

Les seuils seront révisés après le premier pilote.

---

# 29. Stratégie de tests

## 29.1 Tests unitaires

* règles d’exclusion ;
* calcul d’expiration ;
* déduplication ;
* segmentation de sessions ;
* score de candidat ;
* validation de schéma ;
* validation des sorties du modèle local ;
* liens de provenance ;
* transitions d’état.

## 29.2 Tests d’intégration

* capture vers observation ;
* observation vers événement ;
* événement vers candidat ;
* candidat vers mémoire ;
* proposition agent vers mémoire ;
* correction vers mémoire ;
* purge 48 heures ;
* redémarrage après interruption.

## 29.3 Tests de confidentialité

Une suite de captures synthétiques doit contenir :

* clés API ;
* mots de passe ;
* tokens ;
* numéros bancaires ;
* données médicales ;
* écran de connexion ;
* gestionnaire de mots de passe ;
* navigation privée.

La suite doit vérifier :

* absence de capture lorsque l’exclusion s’applique ;
* absence de transmission distante ;
* absence de promotion durable des secrets synthétiques ;
* rejet des sorties de modèle invalides ;
* absence de contenu sensible dans les logs.

## 29.4 Tests de replay

Le pipeline doit pouvoir rejouer un ensemble figé d’observations afin de comparer :

* deux versions du traitement ;
* deux modèles ;
* deux stratégies de mémoire ;
* OptMem original et version modifiée.

## 29.5 Tests de performance

* capture prolongée ;
* 48 heures de données synthétiques ;
* milliers d’événements ;
* milliers de souvenirs ;
* réveil avec mémoire longue ;
* crash pendant écriture ;
* disque presque plein ;
* permissions retirées.

---

# 30. Roadmap par jalons

## Jalon 0 : fondations

Livrables :

* dépôt ;
* architecture modulaire ;
* schémas de données ;
* migrations ;
* CLI minimale ;
* configuration ;
* journal de décisions ;
* tests de base.

Critère de sortie :

* le projet démarre ;
* la base est créée ;
* les modules sont séparés ;
* la configuration est versionnée.

## Jalon 1 : collecte macOS

Livrables :

* application active ;
* fenêtre active ;
* durée ;
* détection d’inactivité ;
* captures sélectives ;
* exclusions ;
* stockage brut ;
* purge.

Critère de sortie :

* une journée d’activité peut être collectée ;
* les exclusions sont respectées ;
* les données expirent correctement.

## Jalon 2 : interprétation par modèle local

Livrables :

* interface typée `ModelProvider` ;
* backend multimodal local ;
* schémas stricts d’entrée et de sortie ;
* niveaux de sensibilité ;
* provenance du modèle, du prompt et des sources ;
* audit des transformations locales.

Critère de sortie :

* aucun contenu utilisateur n’est traité à distance ;
* toutes les fixtures autorisées passent uniquement par le modèle local ;
* les sources exclues n’atteignent jamais le modèle ;
* les sorties invalides échouent sans fuite dans les logs ;
* les transformations sont inspectables et rejouables.

## Jalon 3 : événements

Livrables :

* sessionisation ;
* événements structurés ;
* entités ;
* projets ;
* provenance ;
* confiance.

Critère de sortie :

* une chronologie intelligible peut être produite à partir d’une journée.

## Jalon 4 : patterns et candidats

Livrables :

* détection de répétitions ;
* comparaison temporelle ;
* candidats ;
* score ;
* déduplication ;
* décisions du worker.

Critère de sortie :

* le système produit des souvenirs candidats utiles à partir de plusieurs jours.

## Jalon 5 : mémoire et agent

Livrables :

* interface `MemoryStore` ;
* adaptateur OptMem ;
* `wake` ;
* `recall` ;
* `zoom` ;
* propositions agent ;
* corrections ;
* bloc d’instructions.

Critère de sortie :

* un agent réel peut utiliser CONTX au début d’une session ;
* le contexte provient directement de la mémoire.

## Jalon 6 : interface web

Livrables :

* tableau de bord ;
* activité ;
* patterns ;
* mémoire ;
* confidentialité ;
* réglages ;
* aperçu du réveil.

Critère de sortie :

* l’utilisateur peut contrôler et comprendre le système sans terminal.

## Jalon 7 : pilote réel

Livrables :

* pilote 7 à 14 jours ;
* vérité terrain ;
* comparaison avec et sans CONTX ;
* analyse des erreurs ;
* décision OptMem.

Critère de sortie :

* les trois comportements principaux sont évalués ;
* les limites sont documentées ;
* la stratégie mémoire est choisie.

## Jalon 8 : v1 — durcissement et préparation à la diffusion

Livrables :

* corrections ;
* optimisation ;
* documentation ;
* installation ;
* désinstallation ;
* export ;
* sauvegarde ;
* politique de version.

Critère de sortie :

* installation, mise à jour et désinstallation documentées et vérifiées ;
* sauvegarde, restauration, export et récupération vérifiés ;
* distribution macOS et politique de compatibilité définies ;
* licences du projet et des composants tiers vérifiées ;
* version v1 utilisable quotidiennement et prête à diffuser.

---

# 31. Risques principaux

## 31.1 Effet de surveillance

Risque : l’utilisateur se sent observé par son propre système.

Mesures :

* pause immédiate ;
* indicateur visible ;
* exclusions ;
* captures sélectives ;
* rétention courte ;
* transparence.

## 31.2 Promotion d’informations sensibles

Risque : le modèle local interprète un secret ou une information sensible, puis le pipeline la promeut à tort vers une donnée durable.

Mesures :

* exclusions avant capture ;
* aucun transport distant de contenu utilisateur ;
* classification locale de la sensibilité ;
* validation stricte des sorties ;
* décision explicite de promotion ou de rejet ;
* suite de tests spécialisée ;
* provenance et audit des transformations.

## 31.3 Faux souvenirs

Risque : une mauvaise interprétation devient une vérité durable.

Mesures :

* niveaux épistémiques ;
* confiance ;
* provenance ;
* candidats différés ;
* correction ;
* reconstruction des résumés ;
* évaluation régulière.

## 31.4 Mémoire auto-confirmante

Risque : une interprétation ancienne influence les interprétations suivantes.

Mesures :

* accès aux preuves ;
* séparation entre événements et mémoire ;
* expiration des inférences ;
* comparaison avec données récentes ;
* possibilité de supersession.

## 31.5 Pollution de la mémoire

Risque : trop de détails inutiles réduisent la qualité.

Mesures :

* score ;
* déduplication ;
* compression ;
* seuil de durabilité ;
* rejet des événements triviaux ;
* métriques de bruit.

## 31.6 Surconsommation

Risque : captures et modèles consomment trop de ressources.

Mesures :

* capture adaptative ;
* batch ;
* cache ;
* modèle déchargé ;
* traitement lorsque la machine est disponible ;
* budgets CPU, RAM et disque.

## 31.7 Fragilité des API macOS

Risque : permissions, changements système ou limitations.

Mesures :

* adaptateurs ;
* détection de capacité ;
* tests sur versions macOS ;
* fonctionnement dégradé ;
* documentation.

## 31.8 Dépendance à OptMem

Risque : les limites d’OptMem bloquent la correction ou la pertinence.

Mesures :

* interface `MemoryStore` ;
* métadonnées latérales ;
* replay ;
* possibilité de fork ;
* possibilité de réimplémentation.

## 31.9 Données de tiers

Risque : mémorisation injustifiée d’autres personnes.

Mesures :

* minimisation ;
* sensibilité ;
* exclusion ;
* suppression ;
* pas de profil tiers automatique.

## 31.10 Dérive de périmètre

Risque : CONTX devient une plateforme générale de vie numérique.

Mesures :

* périmètre Mac ;
* objectifs mesurables ;
* jalons ;
* hors périmètre explicite ;
* décisions d’architecture documentées.

---

# 32. Décisions encore ouvertes

Les points suivants ne bloquent pas le début du développement.

## Mémoire

* OptMem inchangé, adapté ou forké ;
* format exact des corrections ;
* filtrage des souvenirs superseded ;
* recherche sémantique éventuelle ;
* évolution du budget de réveil.

## Capture

* seuil de changement visuel ;
* format d’image ;
* fréquence optimale ;
* détection de navigation privée ;
* granularité des fenêtres.

## Traitement

* taille du modèle ;
* utilisation de MLX ou Ollama ;
* fréquence des batchs ;
* compromis entre précision, latence et mémoire ;
* utilité éventuelle d’un OCR local comme optimisation.

## Patterns

* algorithmes ;
* fenêtres temporelles ;
* poids ;
* seuils ;
* expiration des inférences.

## Interface

* design final ;
* visualisation de la provenance ;
* niveau de détail par défaut ;
* mécanisme de notification.

## Distribution

* installateur ;
* signature macOS ;
* système de mise à jour ;
* licences et notices des composants tiers ;
* compatibilité minimale macOS.

Chaque décision structurante doit faire l’objet d’un ADR.

---

# 33. Critères d’acceptation de la v0

La v0 est considérée comme fonctionnelle lorsque :

1. CONTX s’installe et fonctionne sur un Mac M4 avec 16 Go de RAM.
2. Il détecte l’application et la fenêtre actives.
3. Il mesure la durée passée dans les contextes.
4. Il produit des captures sélectives.
5. Il respecte les exclusions avant capture.
6. Il peut être mis en pause instantanément.
7. Les données brutes sont supprimées dans les 48 heures.
8. Aucun contenu utilisateur, y compris les secrets de la suite de tests, ne quitte jamais la machine.
9. Les observations deviennent des événements structurés.
10. Les événements possèdent une provenance.
11. Le système détecte au moins certains patterns simples.
12. Le worker produit et filtre des souvenirs candidats.
13. Les agents ne peuvent pas écrire directement dans la mémoire.
14. OptMem ou son remplaçant produit directement le contexte final.
15. Un agent réel peut exécuter `wake`.
16. L’agent peut rechercher et explorer la mémoire.
17. L’utilisateur peut inspecter la chaîne de transformation.
18. L’utilisateur peut corriger un souvenir.
19. Le système fonctionne sans API distante et exige un modèle multimodal local pour le traitement sémantique.
20. L’interface locale permet de contrôler les fonctions principales.
21. Le pilote démontre une amélioration sur les trois comportements cibles.
22. Le système reste suffisamment léger pour un usage quotidien.

## 33.1 Critères d’acceptation de la v1

La v1 est considérée comme prête à diffuser lorsque :

1. tous les critères de la v0 restent satisfaits après le durcissement ;
2. un nouvel utilisateur peut installer, comprendre les permissions, mettre en pause, inspecter, exporter et désinstaller CONTX avec les parcours documentés ;
3. les mises à jour et les retours arrière protègent la mémoire, la provenance et la configuration ;
4. la sauvegarde, la restauration, l’export et la suppression complète sont vérifiés ;
5. les incidents critiques de sécurité et de confidentialité identifiés pendant le pilote sont résolus ;
6. chaque composant tiers distribué possède une licence et les notices requises ;
7. les objectifs de performance sont mesurés sur le Mac cible ;
8. aucun chemin temporaire de production ou mécanisme manuel de récupération indispensable ne subsiste ;
9. les artefacts de distribution sont reproductibles et passent la suite de validation complète.

---

# 34. Définition de terminé

Une fonctionnalité n’est terminée que si :

* son comportement est documenté ;
* ses données sont modélisées ;
* ses erreurs sont gérées ;
* ses logs ne révèlent pas de secrets ;
* ses tests unitaires existent ;
* ses tests d’intégration existent lorsque nécessaire ;
* son impact sur la confidentialité est évalué ;
* son impact sur les ressources est mesuré ;
* l’interface permet de comprendre son état ;
* elle respecte la politique de rétention ;
* elle fonctionne sans réseau lorsque cela est requis ;
* elle possède une stratégie de migration si elle modifie les données persistantes.

---

# 35. Gouvernance du projet

## 35.1 Journal de décisions

Les décisions importantes doivent être enregistrées dans `docs/adr/`.

Un ADR doit contenir :

* contexte ;
* problème ;
* options ;
* décision ;
* raisons ;
* conséquences ;
* stratégie de retour arrière.

## 35.2 Versions du cahier des charges

Toute modification d’un invariant, du périmètre ou de l’architecture principale nécessite :

1. une discussion ;
2. un ADR ;
3. une mise à jour de version ;
4. une mise à jour du changelog ;
5. une vérification des tests concernés.

## 35.3 Principe de réversibilité

Une technologie ne doit pas devenir une dépendance structurelle lorsqu’une interface simple peut l’isoler.

Les éléments suivants doivent être remplaçables :

* modèle local ;
* runtime du modèle local ;
* OCR local optionnel ;
* stockage brut ;
* moteur d’événements ;
* moteur de patterns ;
* backend mémoire ;
* frontend ;
* intégration agent.

---

# 36. Formulation finale de référence

> CONTX est une infrastructure personnelle, local-first et open source qui observe de manière sélective l’activité d’un utilisateur sur son Mac afin de construire automatiquement la mémoire de travail de son agent personnel. Le système distingue les observations brutes, les événements interprétés, les patterns, les inférences et les souvenirs candidats. Les données brutes restent locales et sont supprimées au plus tard après 48 heures. Un modèle multimodal local obligatoire interprète les captures et métadonnées autorisées ; aucun contenu utilisateur n’est traité à distance dans la v0. Les informations interprétées sont consolidées avant d’entrer dans OptMem ou dans un moteur mémoire dérivé. Cette mémoire constitue directement le contexte fourni à l’agent. CONTX vise à permettre à l’agent de reprendre les projets de l’utilisateur, de comprendre ce qui a occupé sa période récente et de détecter les changements significatifs dans son activité, sans transformer le produit en système de surveillance, en orchestrateur d’agents ou en mémoire générale de toute sa vie.

---

# 37. Séquence d’exécution actuelle

La première étape consiste à achever la base décisionnelle : ADR, modèle de versions, schéma d’architecture cohérent, licence du projet et provenance d’OptMem.

Le développement lance ensuite le **Jalon 0**, puis produit le fil vertical v0.0.1 :

```text
application active
    ↓
observation locale
    ↓
événement simple
    ↓
souvenir candidat
    ↓
OptMem
    ↓
contx wake
```

Ce premier fil vertical doit être fonctionnel et vérifié avant d’ajouter l’interprétation multimodale locale, les patterns complexes, un daemon de collecte ou une interface riche. Les détails, critères de sortie et points d’approbation sont définis dans `docs/implementation-plan.md`.
