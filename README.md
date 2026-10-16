# 📬 Local Gmail Manager — v1.5

Application **exécutée exclusivement sur ton ordinateur** (interface locale Streamlit et index SQLite) pour analyser, classer et nettoyer les emails Gmail via l'API Google OAuth2. Les échanges nécessaires s'effectuent directement entre ton PC et Gmail ; il ne s'agit pas d'un serveur hors de ton ordinateur.

## 📥 Télécharger et lancer Gmail Manager (Windows, macOS, Linux)

> **Distribution expérimentale :** les exécutables sont déjà générés par GitHub Actions, mais ne sont pas encore signés et n'ont pas été validés sur des ordinateurs utilisateurs. Il n'existe **pas encore de GitHub Release** publiée. Les fichiers actuellement disponibles sont les *artifacts* du [workflow de compilation réussi](https://github.com/cptblues/gmail-manager/actions/runs/38881199133) (connexion GitHub parfois nécessaire). Pour les futures versions stables, consulte [GitHub Releases](https://github.com/cptblues/gmail-manager/releases/latest) ou [toutes les Releases](https://github.com/cptblues/gmail-manager/releases). Les liens Releases ne proposeront des fichiers qu'après publication d'une version.

| Système | Télécharger la compilation disponible | Démarrage après extraction |
| --- | --- | --- |
| **Windows 64 bits** | [GmailManager-Windows (artifact)](https://github.com/cptblues/gmail-manager/actions/runs/38881199133/artifacts/12037113497) | Ouvrir `GmailManager/GmailManager.exe` |
| **macOS Apple Silicon (M1/M2/M3/M4…)** | [GmailManager-macOS (artifact)](https://github.com/cptblues/gmail-manager/actions/runs/38881199133/artifacts/12038190503) | Dans Terminal, lancer `./GmailManager` depuis le dossier extrait `GmailManager` |
| **Linux x86_64** | [GmailManager-Linux (artifact)](https://github.com/cptblues/gmail-manager/actions/runs/38881199133/artifacts/12037756852) | Dans Terminal, lancer `./GmailManager` depuis le dossier extrait `GmailManager` |

**Important :** les artifacts GitHub Actions peuvent expirer et sont fournis dans un ZIP GitHub qui contient lui-même l'archive de l'application (`.tar.gz` pour macOS et Linux). Extraire **les deux niveaux d'archive**, puis conserver **l'ensemble du dossier `GmailManager`** : l'exécutable ne marche pas isolément. Les binaires contiennent Python ; aucune installation de Python n'est requise pour les utiliser. La version macOS est actuellement une archive pour Apple Silicon, **pas un `.app` ou un `.dmg`** ; les Mac Intel ne sont pas encore couverts par ce build.

### Première utilisation : configurer l'accès Google (obligatoire)

Gmail Manager **ne demande jamais ton mot de passe Gmail**. Pour utiliser l'API Gmail, chaque utilisateur doit fournir son propre client OAuth Google :

1. Créer un projet sur [Google Cloud](https://console.cloud.google.com/projectcreate), puis [activer Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
2. Dans **Google Auth Platform**, configurer **Branding** et **Audience**, puis déclarer l'autorisation `https://www.googleapis.com/auth/gmail.modify` dans **Data Access** si nécessaire. Si l'application est en mode **Testing**, ajouter l'adresse Gmail du compte dans les **Test users**.
3. Dans **Clients**, créer un client OAuth de type **Desktop app / Application de bureau** (et non Web), télécharger le JSON et le renommer exactement **`credentials.json`**.
4. Pour la **version téléchargée (exécutable)**, mettre ce fichier dans le dossier utilisateur ci-dessous, à créer si nécessaire :

   | Plateforme | Emplacement de `credentials.json` |
   | --- | --- |
   | Windows | `%LOCALAPPDATA%\\LocalGmailManager\\credentials.json` |
   | macOS | `~/Library/Application Support/LocalGmailManager/credentials.json` |
   | Linux | `~/.local/share/LocalGmailManager/credentials.json` (ou `$XDG_DATA_HOME/LocalGmailManager/` si défini) |

5. Lancer le programme depuis le dossier extrait. Il démarre un serveur **uniquement sur ton PC** (`127.0.0.1`) et doit ouvrir l'interface dans le navigateur. Si elle ne s'ouvre pas, consulter l'adresse localhost affichée par le programme (en général [http://127.0.0.1:8501](http://127.0.0.1:8501) ; le port peut être différent).
6. Cliquer sur **« Se connecter à Google »** dans la barre latérale, autoriser le compte via la page Google, puis commencer par un petit scan en lecture.

**Confidentialité :** ne jamais partager `credentials.json`, `token.json` ni les bases SQLite. Le jeton d'accès et l'index sont enregistrés localement dans `LocalGmailManager`. L'application utilise l'autorisation Gmail `gmail.modify` : lire attentivement les droits accordés avant de continuer.

**Attention aux avertissements :** Windows SmartScreen et macOS Gatekeeper peuvent avertir que le programme n'est pas signé/notarisé. Vérifier la provenance depuis ce dépôt avant toute autorisation. Sur macOS et Linux, si le fichier a perdu son droit d'exécution après extraction, utiliser `chmod +x GmailManager` dans le dossier extrait. Ne jamais exécuter un binaire venant d'une source inconnue.

**Si tu utilises la version source** (`app.py`, scripts `start-linux.sh`, `start-windows.ps1`, `start-macos.sh`) : le fichier OAuth reste **à côté de `app.py`** comme auparavant. Consulter [DISTRIBUTION.md](DISTRIBUTION.md) pour les détails techniques et la section « Créer l'accès Gmail chez Google » ci-dessous pour le guide complet.


## Nouveautés de la v1.5

- **Dossiers Gmail intégrés** : l'onglet **📁 Dossiers** affiche les libellés personnels existants (avec les compteurs **locaux**, issus de SQLite) et permet d'en créer. Seuls les libellés de type utilisateur sont proposés ; les catégories système Gmail ne sont pas des destinations.
- **Déplacer vers…** : depuis les groupes sélectionnés dans Nettoyage ou la liste des emails volumineux, choisir un dossier Gmail existant et confirmer. L'application **ajoute le libellé de destination et retire `INBOX`** en une requête. Les autres libellés sont conservés et le message n'est pas supprimé. Un échec laisse la fiche locale intacte.
- **Boîte de réception / Tous les messages indexés / Dossier** : nouveau filtre en haut de l'application. **La vue par défaut est « Boîte de réception »** : un message déplacé ou archivé n'y apparaît plus. La vue « Tous les messages indexés » le montre toujours, si la fiche est conservée par l'index local, car Gmail fonctionne avec des libellés et non des dossiers exclusifs.
- **☑ Tout sélectionner** : coche les **lignes réellement affichées après filtrage**, jusqu'à 1 000 groupes, sans sélectionner les résultats cachés par une recherche ou au-delà de cette limite. Tu peux tout désélectionner en décochant. Les cases individuelles continuent à fonctionner et la sélection est réinitialisée lors d'un changement de filtre ou de vue.
- Maintien des nouveautés de la V1.4 : **Domaine + objet exact**, recherche dans les objets, choix **Archiver / Corbeille**, confirmation modale, navigation persistante et quotas Gmail limités.

### Différence Gmail : Boîte de réception et Tous les messages

Gmail ne possède pas de dossier classique exclusif. Un message déplacé vers `À conserver` **quitte bien la Boîte de réception**, mais restera visible dans **Tous les messages** Gmail, ce qui est normal et permet de le retrouver dans le dossier/libellé sélectionné.

**Important pour voir les dossiers dans l'application :** les noms des dossiers sont lus directement dans Gmail, mais les emails et compteurs affichés dans ces dossiers proviennent de **l'index SQLite local**. Pour indexer aussi les messages déjà archivés ou classés, relance une analyse avec **« Reçus + archivés »**, sans filtre restrictif et avec une limite suffisante. Après une analyse « Boîte de réception » seule, les dossiers peuvent paraître incomplets. Un scan de la boîte de réception peut retirer de l'index des emails archivés précédemment ; ils ne sont **pas supprimés de Gmail**.

## Installer la mise à jour depuis V1.4 ou antérieure (Omarchy / Linux)

1. **Arrête l'application** en cours avec `Ctrl+C`.
2. Sauvegarde ton `credentials.json` (à côté de `app.py`). Ce fichier n'est **pas inclus dans le ZIP**.
3. Décompresse `local-gmail-manager-v1.5.zip` puis copie les fichiers de `local-gmail-manager/` dans ton dossier existant (en particulier `app.py` et `core.py`), sans effacer ton `credentials.json`.
4. Relance via `./start-linux.sh`. Sur Windows, utilise `start-windows.ps1`.
5. **Conserve le dossier de données utilisateur `LocalGmailManager`** : il contient ton token et l'index SQLite. Aucun changement d'identifiants OAuth ni migration de base de données n'est nécessaire.

Pour une nouvelle installation sur Omarchy, depuis le dossier `local-gmail-manager` décompressé :

```bash
sudo pacman -S --needed python python-pip
./start-linux.sh
```

L'interface s'ouvre sur **http://127.0.0.1:8501**. Si tu n'as pas encore le fichier `credentials.json`, suis le guide Google ci-dessous.

## Utiliser le tableau de nettoyage

1. Dans le sélecteur **🧹 Nettoyage**, choisis **Domaine**, **Expéditeur** ou **Domaine + objet**.
2. En mode **Domaine + objet**, filtre éventuellement un domaine (`amazon.fr`) puis l'objet (`facture`, `commande`). Le tableau n'affiche que les **1 000 premiers groupes** correspondants et sélectionnables ; affine les filtres si nécessaire.
3. **Coche** les lignes ciblées ou utilise **« Tout sélectionner »** après avoir appliqué tes filtres. Cette case concerne au maximum 1 000 lignes affichées, et non tous les autres résultats cachés. L'app affiche le nombre précis d'emails, le volume estimé, les pièces jointes et un aperçu jusqu'à 250 emails. Un filtre et son tri sont appliqués **avant** la sélection ; les changer réinitialise les cases pour ne pas transférer la sélection à une autre ligne.
4. Choisis **Déplacer vers…** (puis le dossier), **Archiver** ou **Mettre à la corbeille**, puis clique sur « Continuer ».
5. Dans la **fenêtre de confirmation**, vérifie encore l'opération et clique sur « Confirmer ». « Annuler » ne change rien à Gmail.

**Attention :** l'aperçu est limité à 250 emails, mais l'opération concerne bien **tous les messages cochés**, uniquement dans le périmètre du dernier scan réussi. Le message déplacé disparaît de la **vue Boîte de réception**, mais reste dans Gmail sous le libellé choisi. Un scan ultérieur limité à « Boîte de réception » peut retirer ces fiches du cache local ; pour les revoir dans les dossiers, lance « Reçus + archivés ».

## Télécharger les pièces jointes

Dans **📎 Pièces jointes**, choisis tous les emails concernés ou seulement les emails sélectionnés dans le tableau, puis un dossier de destination et une action après téléchargement : **conserver**, **archiver** ou **mettre à la corbeille**. L'archivage et la mise en corbeille ne commencent qu'après téléchargement et vérification de toutes les pièces jointes du message, et après la confirmation modale. En cas d'échec, le message concerné reste en place. Le fichier `manifest.csv` contient la liste des fichiers avec leur empreinte SHA-256.

## Quota, fiabilité et sécurité

- Le limiteur local conserve le budget prudent de **3 600 unités de quota/minute** (sur 6 000 par utilisateur), avec reprise automatique pour les erreurs temporaires de Gmail. Le débit n'est pas partagé avec d'autres applications indépendantes qui utilisent le même compte.
- Le cache SQLite permet de reprendre les scans sans relire toutes les fiches. Relance un scan réussi avant toute opération si le dernier scan s'est interrompu ou a échoué.
- La corbeille Gmail supprime normalement automatiquement les messages au bout de **30 jours**. Pour une alternative moins risquée, utilise d'abord **Archiver**.
- Il n'existe **aucune fonction de suppression définitive** dans cette application ; aucune donnée n'est transmise à un autre service que Gmail.
- **45 tests automatisés du cœur métier passent en local** ; la connexion Gmail et le rendu réel dans Streamlit n'ont pas pu être testés ici. Commence par un petit lot de messages sans importance.

---

## 1. Créer l'accès Gmail chez Google (une seule fois)

1. Ouvre **https://console.cloud.google.com/projectcreate** et crée un projet (par exemple `Local Gmail Manager`).
2. Ouvre **https://console.cloud.google.com/apis/library/gmail.googleapis.com** et **active l'API Gmail** dans ce projet.
3. Va dans **Google Auth Platform → Branding** : https://console.cloud.google.com/auth/branding ; configure le nom et l'email de contact. Avec une adresse Gmail personnelle, choisis l'audience **External / Externe** (l'audience *Internal / Interne* est réservée à certaines organisations Google Workspace).
4. Dans **Audience** : https://console.cloud.google.com/auth/audience , si ton projet est en mode **Testing / Test**, ajoute **ton adresse Gmail** aux *Test users / Utilisateurs tests*.
5. Dans **Data Access / Accès aux données** : https://console.cloud.google.com/auth/data-access , si l'interface le demande, déclare la permission : `https://www.googleapis.com/auth/gmail.modify`.
6. Dans **Clients** : https://console.cloud.google.com/auth/clients , **Create client / Créer un client** → type **Desktop app / Application de bureau**. Donne-lui un nom.
7. **Télécharge le JSON** des identifiants OAuth et renomme-le **`credentials.json`**. **Version source :** mets-le à côté de `app.py`. **Exécutable téléchargé :** place-le dans le dossier utilisateur `LocalGmailManager` indiqué dans le tableau « Première utilisation » plus haut. Il ne s'agit **pas** d'un token ni de ton mot de passe.

### Mode test ou mode production ?

Si l'audience est **External** et que le statut est **Testing**, Google fait expirer les *refresh tokens* après **7 jours** pour les permissions Gmail. Il faudra se reconnecter. Tu peux passer l'application en **In production** (section Audience) pour éviter ce problème ; Google peut alors afficher un écran **« Application non vérifiée »** pour cet accès Gmail sensible/restreint. Pour un usage personnel avec quelques utilisateurs connus, Google prévoit une exception à la vérification, mais pas à l'affichage de l'avertissement ni aux limites d'utilisateurs non vérifiés. Ne diffuse pas tes identifiants OAuth ni une application non vérifiée comme un service public.

La permission `gmail.modify` autorise lecture, téléchargement et déplacement en corbeille, **mais pas** la suppression immédiate définitive. C'est volontaire.

## 2. Installer et lancer sous Omarchy / Arch Linux

Dans un terminal du dossier décompressé :

```bash
sudo pacman -S --needed python python-pip
./start-linux.sh
```

Si Python ne peut pas créer d'environnement virtuel, assure-toi que `venv` est disponible avec ta version de Python. Le script crée `.venv`, installe les dépendances **une seule fois** puis lance l'interface :

**http://127.0.0.1:8501**

Tu peux aussi faire manuellement :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

## 3. Installer et lancer sous Windows (PowerShell)

Installe Python 3.10+ si nécessaire (https://www.python.org/downloads/), ouvre PowerShell dans le dossier décompressé et exécute :

```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```

Puis ouvre **http://127.0.0.1:8501** si ton navigateur ne s'ouvre pas tout seul.

## 4. Connecter le compte et récupérer un token

1. Clique sur **« Se connecter à Google »** dans la barre latérale.
2. Une page **Google Accounts** s'ouvre dans ton navigateur ; choisis ton compte et accepte l'accès `gmail.modify`.
3. Dans le mode personnel non vérifié, Google peut afficher un écran d'avertissement. Continue uniquement si **tu as toi-même créé le projet OAuth** et comprends la permission demandée.
4. Google redirige vers un port **localhost temporaire** utilisé pour recevoir le code OAuth. Le jeton d'accès et de renouvellement est alors automatiquement stocké dans le dossier utilisateur de l'application (`token.json`). Ne partage pas ce fichier.
5. Retourne à **http://127.0.0.1:8501**. Tu es connecté et peux commencer le scan.

Tu **n'as pas besoin de créer manuellement un token** ; OAuth2 s'en charge.

## 5. Mode d'emploi recommandé

1. Lance un scan **Boîte de réception** sur **100 à 3 000 emails** pour commencer. Il est uniquement en lecture et peut être long pour les gros comptes. Garde l’option de réutilisation de l’index cochée pour les scans suivants.
2. Dans **Analyse**, inspecte les expéditeurs et domaines fréquents.
3. Dans **Nettoyage**, choisis **Domaine + objet** pour cibler une série très précise, coche les lignes utiles, puis sélectionne **Archiver** ou **Mettre à la corbeille** et valide la fenêtre de confirmation.
4. Dans **Pièces jointes**, choisis le dossier, utilise d'abord l'option **Conserver les emails** et vérifie `manifest.csv` et les fichiers.
5. Pour un second passage, choisis **Archiver** ou **Mettre à la corbeille** après sauvegarde complète et confirme dans la fenêtre. Un message ayant une pièce jointe en échec n'est pas déplacé.
6. Relance périodiquement l'analyse pour obtenir des résultats actuels, particulièrement si tu as supprimé des emails depuis un autre appareil.

**Important** : seules les pièces jointes des messages **indexés** sont traitées. Si l'analyse a atteint la limite de 3 000 messages, augmente la limite et relance le scan pour couvrir plus de messages. La taille estimée par Gmail n'équivaut pas forcément au quota libérable (libération différée tant que les messages sont en corbeille).

## Sécurité et vie privée

- L'interface Streamlit écoute **exclusivement** sur `127.0.0.1:8501`. Ne publie pas ce port et n'utilise pas de tunnel public.
- Le logiciel contacte Google pour OAuth et l'API Gmail. Les fichiers sont sauvegardés **sur ton disque**. Une connexion internet est indispensable.
- **SQLite contient localement** les identifiants de messages, noms d'expéditeurs, objets et tailles. Les **corps des emails ne sont pas enregistrés dans la base** ; les pièces jointes sont enregistrées uniquement quand tu le demandes. Lors de l'indexation `format=full`, Gmail renvoie néanmoins temporairement le contenu des messages au processus Python.
- Le token OAuth se trouve dans le dossier de données utilisateur (`LocalGmailManager`, chemin indiqué dans l'application), avec une permission fichier restrictive lorsque possible. **Il n'est pas chiffré au repos** : sécurise ton PC et sauvegardes.
- Pour révoquer complètement l'accès : **https://myaccount.google.com/connections** (accès à ton compte Google). Le bouton de déconnexion ne fait que retirer le token de ce PC.
- Le code ne contient aucune commande de suppression définitive de messages et ne vide jamais la corbeille.
- Les pièces jointes **ne sont jamais exécutées**. Ne les ouvre pas sans vérification antivirus si elles proviennent de sources inconnues.
- `manifest.csv` et les pièces jointes peuvent contenir des informations personnelles : stocke-les dans un dossier sûr.

**Tests locaux v1.5 :** 45 tests unitaires du moteur réussis (dont lecture/création de dossiers, déplacement, sélection par domaine + objet, archivage, protection des fichiers et erreurs de quota). Le rendu Streamlit et la connexion réelle à Gmail n'ont pas été vérifiés dans cet environnement ; effectue d'abord un essai sur quelques messages sans importance.

## Limites de cette version

- L'interface reste **Streamlit dans le navigateur** sur `localhost`, y compris dans les archives exécutables : ce n'est pas une fenêtre desktop native.
- L’indexation des **nouveaux emails** nécessite **une requête Gmail par email**, plus la pagination. Les requêtes sont limitées en débit et traitées avec 2 workers ; l’analyse de grandes boîtes peut donc prendre du temps. La même recherche réutilise le cache SQLite à moins que tu demandes une actualisation complète.
- Certaines PJ embarquées ou messages MIME atypiques peuvent ne pas être reconnus. Les emails concernés ne sont pas mis à la corbeille après une sauvegarde incomplète.
- Pas de planification automatique, d'export MBOX, de dédoublonnage sémantique ni de désabonnement automatique dans cette v1.5.
- Le mode « Reçus + archivés » exclut les emails marqués envoyés, y compris les messages qui sont simultanément reçus et envoyés selon leur classification Gmail.
- Ne lance pas plusieurs copies de l'application ou de scans en même temps sur le même compte.

## Diagnostic

- **Erreur `access_denied` / 403** : vérifie que ton Gmail figure bien parmi les **Test users** du projet en mode Test et que l'API Gmail est activée.
- **« App not verified »** : peut arriver sur un projet personnel non vérifié ; assure-toi d'être propriétaire du projet.
- **Nouvelle connexion demandée après une semaine** : comportement connu de Google en mode **Testing** avec les scopes Gmail. Passe en production pour un usage personnel récurrent si tu comprends les avertissements.
- **`redirect_uri_mismatch`** : il faut un client OAuth **Desktop app**, pas une application web.
- **Navigateur non ouvert (Omarchy)** : regarde l'URL d'autorisation dans le terminal, copie-la dans ton navigateur **sur le même PC**.
- **Pas de scan complet** : augmente la limite, réduis les filtres de recherche Gmail et réessaie. Le mode cache conserve les fiches déjà analysées pour la même recherche.
- **`Quota exceeded` / `rateLimitExceeded`** : utilise la v1.5, garde le limiteur à 3 600 unités/minute et patiente si une autre application consomme le quota Gmail. Le scan échoué désactive le nettoyage jusqu’à son succès.

### Références officielles Google

- Quickstart Python : https://developers.google.com/workspace/gmail/api/quickstart/python
- Liste des permissions Gmail : https://developers.google.com/workspace/gmail/api/auth/scopes
- Accès OAuth2 et durée de vie des tokens : https://developers.google.com/identity/protocols/oauth2
- Exemption personnelle à la vérification : https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- Gmail `messages.trash` : https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/trash
- Gmail `users.labels.list` : https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/list
- Gmail `users.labels.create` : https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/create
- Gmail `users.messages.modify` : https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify
