# Distribution macOS, Windows, Linux (expérimental)

Le projet reste une application **locale Streamlit** : au lancement, le programme démarre une interface sur `127.0.0.1` et ouvre le navigateur par défaut. Il ne fournit pas une fenêtre native.

## Installation pour les utilisateurs

Télécharger l'archive adaptée depuis la page **Releases** (après la première publication), la décompresser **entièrement**, puis lancer :

- **Windows** : `GmailManager/GmailManager.exe`
- **macOS** : `GmailManager/GmailManager` depuis le Terminal (après extraction)
- **Linux** : `GmailManager/GmailManager` (autoriser l'exécution si nécessaire)

Le dossier complet est nécessaire : ne pas déplacer l'exécutable seul. Python n'est pas nécessaire. Les binaires ne sont actuellement **ni signés ni notarisés**, donc les systèmes peuvent afficher un avertissement ; ne contourner les protections que pour un binaire dont la provenance est vérifiée.

**OAuth :** chacun doit créer un client OAuth de type **Desktop app** dans Google Cloud, activer l'API Gmail et télécharger son propre `credentials.json`. Dans les versions empaquetées, placer ce fichier dans le répertoire de données utilisateur `LocalGmailManager` (macOS : `~/Library/Application Support/LocalGmailManager/`, Windows : `%LOCALAPPDATA%\\LocalGmailManager\\`, Linux : `~/.local/share/LocalGmailManager/`, sauf configuration XDG particulière). Créer le dossier si nécessaire. **Ne jamais diffuser ce JSON ou le token OAuth dans une release.** Suivre le README pour le consentement Google et les limites des applications non vérifiées.

Le token et la base SQLite restent dans le dossier utilisateur habituel ; les mises à jour n'effacent pas l'index.

## Développement, sans binaire

- macOS : `bash start-macos.sh`
- Linux : `bash start-linux.sh`
- Windows : `powershell -ExecutionPolicy Bypass -File .\\start-windows.ps1`

## Fabrication des archives

Le workflow `.github/workflows/build-desktop.yml` compile **sur chaque système natif** via PyInstaller, vérifie les tests Python puis téléverse les archives comme artifacts. Il peut être lancé manuellement via **Actions → Build desktop packages → Run workflow**.

Un tag `v*` déclenche en plus une publication GitHub Release. **Tester manuellement les archives et le parcours OAuth avant de distribuer un tag public.** Les dépendances Python ne sont pas verrouillées ; des vérifications de reproductibilité et de signatures seront nécessaires pour une distribution publique fiable.

## Points à vérifier sur les trois systèmes

1. L'archive est extraite en entier et le programme ouvre une page locale.
2. Sans `credentials.json`, l'application affiche le chemin attendu, sans planter.
3. Un fichier OAuth utilisateur valide permet l'authentification sur la même machine.
4. Un petit scan en lecture fonctionne ; fermer le processus puis le relancer.
5. L'index SQLite et la connexion OAuth persistent après redémarrage.

Limites : le lancement en navigateur reste lié au processus de l'application ; le script n'installe pas de raccourci ou de service. macOS est fourni comme archive exécutable, **pas encore comme .app/.dmg**, et Linux comme archive, **pas encore comme AppImage**.
