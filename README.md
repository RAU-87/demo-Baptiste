# Démo Baptiste — suivi des cadeaux de Noël

## État du projet
Cadrage initial du 21 novembre 2026. Aucune application, collecte réelle ou infrastructure n'est encore déployée. Les choix techniques ci-dessous sont proposés, pas encore implémentés.

## Objectif
Créer un site privé permettant de définir des cadeaux pour les enfants et de comparer automatiquement les offres de marchands prédéfinis, trois fois par jour. Afficher la meilleure offre **parmi les sources interrogées**, avec sa date de vérification et les limites de la comparaison.

## Périmètre MVP
- Liste de cadeaux : libellé, destinataire sous pseudonyme, référence exacte (marque/modèle, EAN si disponible), variante, quantité, budget cible, date limite de livraison et statut à acheter/acheté.
- Liste de marchands activables ; Amazon France et AliExpress sont des candidats, sous réserve d'accès validé.
- Saisie d'un lien produit ou d'une référence ; recherche de candidats via chaque fournisseur. Validation humaine des correspondances ambiguës avant surveillance.
- Tableau comparatif : prix article, livraison, taxes si non incluses, remise applicable, total, vendeur, état neuf/occasion, variante, stock, livraison estimée, lien et date de collecte.
- Trois contrôles quotidiens, proposition 08:00, 14:00 et 20:00 Europe/Paris, horaires configurables.
- Historique et seuils de prix seulement dans les limites de conservation autorisées par chaque fournisseur.
- Signalement dans l'application d'une baisse ou d'un seuil atteint. Canal de notification externe à choisir plus tard.
- Aucun achat automatique.

## Règles de comparaison
1. Comparer uniquement les références et variantes confirmées, à quantité et état identiques.
2. Calculer le total livré en EUR pour la destination configurée. Stocker les montants en décimal ou centimes, jamais en flottants.
3. Ne jamais considérer une livraison ou une taxe inconnue comme zéro. Une offre au total incomplet reste visible mais hors classement des totaux confirmés.
4. Distinguer remises publiques, coupons à activer, avantages abonnés et offres nouveaux clients. Exclure par défaut les remises dont l'éligibilité n'est pas confirmée.
5. Une livraison annoncée après la date limite est hors sélection ; date inconnue = livraison à confirmer, pas une promesse pour Noël.
6. Une erreur de collecte n'est ni une rupture de stock ni un prix nul. Conserver le dernier état connu en l'indiquant comme ancien.
7. Afficher les sources non interrogées ou en erreur. Ne pas prétendre couvrir tout le marché.

## Architecture proposée
- Frontend React + TypeScript : cadeaux, comparaison, réglages et état des collectes.
- Backend ASP.NET Core .NET 10 : API REST pour le MVP, validation et règles métier.
- EF Core et base relationnelle : SQLite pour développement mono-instance ; PostgreSQL pour un hébergement multi-instance.
- Worker .NET séparé de l'API : collecte planifiée, reprise et calcul des meilleures offres.
- Un adaptateur par marchand derrière une interface commune ; adaptateur fictif déterministe pour développement et tests.
- Déploiement Docker envisagé ; hébergement à décider après estimation. Azure Container Apps et jobs planifiés sont une option à étudier, sans présumer d'une gratuité.
- Identité et authentification obligatoires avant exposition du site ; autorisation par foyer, secrets côté serveur uniquement.

### Modèle de données envisagé
Gift, Merchant, ProductMatch, OfferSnapshot, CollectionRun, AlertRule, AlertEvent.
Chaque offre référence le cadeau, le marchand, le produit, la variante, le vendeur, les montants et leur complétude, la devise, le stock, la livraison, l'URL et observedAt.
Chaque collecte conserve son statut, ses erreurs, ses compteurs et un identifiant de créneau unique.

### Planification et fiabilité
- Planifier selon Europe/Paris avec prise en compte des changements d'heure ; stocker les instants en UTC.
- Verrou et clé d'idempotence par créneau/marchand/produit pour éviter les doublons.
- Temporisation, quotas par fournisseur, reprises limitées avec backoff et gestion de 429/Retry-After.
- Isoler les échecs par marchand. Afficher dernière réussite, prochaine exécution et état dégradé.
- Pas de contournement de CAPTCHA ou de protections. Si aucune source autorisée n'est accessible, désactiver le connecteur et proposer un lien ou une saisie manuelle.
- Limiter les URL aux domaines marchands acceptés, bloquer réseaux privés/redirections non autorisées si un téléchargement serveur est ajouté.

## Faisabilité des sources — vérification documentaire du 21 novembre 2026
### Amazon
La documentation officielle Creators API demande un compte Amazon Associates accepté et mentionne des ventes qualifiantes. Un accès automatique n'est donc pas acquis pour un outil familial neuf.
À vérifier avant intégration : éligibilité réelle du compte, marketplace France, usages autorisés pour ce service, quotas, règles d'affichage et de conservation des prix. Ne pas bâtir le projet sur l'ancienne PA-API sans revoir sa migration.
Source : https://affiliate-program.amazon.com/creatorsapi/docs/en-us/onboarding/register-for-creators-api
Documentation : https://affiliate-program.amazon.com/creatorsapi/docs/

### AliExpress
La documentation officielle publie aliexpress.affiliate.product.query et productdetail.get. La recherche expose notamment devise, langue et pays de livraison. Cela ne garantit ni l'accès du compte, ni la couverture de tous les produits, ni un total livré complet.
À vérifier : inscription/application, permissions, catalogue accessible, variantes, frais réels, quotas et conditions de stockage.
Source : https://developer.alibaba.com/docs/api.htm?apiId=45803

### Décision
Commencer par une preuve d'accès sur quelques références exactes, puis choisir le premier connecteur exploitable. Une API ou un flux tiers autorisé peut être étudié si l'accès direct ne convient pas, avec devis et couverture vérifiés. Aucun coût fournisseur n'est engagé à ce stade.

## Plan de réalisation
### 1 — Valider les sources (priorité bloquante pour les prix réels)
Tester un petit échantillon de produits, consigner champs disponibles, exactitude des variantes, total livré, conditions et quotas. Livrable : matrice de faisabilité et choix du premier marchand.
Acceptation : au moins une source autorisée et exploitable, ou décision explicite de rester en démonstration.

### 2 — Construire une tranche fonctionnelle en mode démonstration
Créer solution .NET, front React, base et migrations, CRUD cadeaux et adaptateur fictif. Afficher clairement « données de démonstration ».
Acceptation : ajout, modification, suppression d'un cadeau ; persistance après redémarrage ; classement correct d'offres fictives.

### 3 — Intégrer les offres réelles
Implémenter le premier adaptateur, la recherche de correspondances et leur validation, puis un second marchand pour la comparaison.
Acceptation : bon modèle et bonne variante ; aucune offre fictive mélangée aux résultats réels ; données manquantes et erreurs visibles.

### 4 — Automatiser les trois contrôles
Worker, planification, reprises, journal des collectes et alertes internes sans doublons.
Acceptation : exécution sans navigateur ouvert, trois créneaux par jour local, reprise après échec, aucune double alerte pour la même observation.

### 5 — Préparer l'hébergement privé
Authentification, secrets, sauvegarde/restauration, logs sans données sensibles, Docker et CI build/tests.
Acceptation : accès non autorisé rejeté, restauration testée, budget d'hébergement et de collecte documenté avant déploiement.

## Tests métier à prévoir
Mauvaise variante exclue ; frais inconnus non convertis en zéro ; coupon conditionnel non appliqué par défaut ; livraison tardive exclue ; erreur fournisseur sans faux prix ; deux exécutions du même créneau sans doublon ; passage heure été/hiver ; isolation entre foyers.

## Budget et décisions ouvertes
Charge indicative : 20 cadeaux × 3 marchands × 3 passages = 180 vérifications produit/marchand par jour, hors recherche, pagination et reprises. Ce n'est pas nécessairement 180 appels API.
Principaux coûts à chiffrer : accès données, compute API/worker, stockage, logs et notifications.
À préciser au fil du développement : marchands prioritaires, produits test, destination de livraison et canal d'alerte. Ne pas inscrire de noms d'enfants, adresses, clés ou données personnelles dans ce dépôt public.
