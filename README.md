# Worker vocal gratuit pour LeCodeIA

Ce dépôt ne contient que le code du worker. Ne mettez jamais votre site, votre base SQL, votre fichier `.env` ou une sauvegarde dans ce dépôt.

## Secrets du dépôt

Dans **Settings → Secrets and variables → Actions**, ajoutez :

- `LECODEIA_AUDIO_API_URL` : URL HTTPS de `audio-worker-api.php`, par exemple `https://votre-domaine.tld/audio-worker-api.php`.
- `LECODEIA_AUDIO_TOKEN` : le même secret aléatoire que `AUDIO_WORKER_TOKEN` dans le `.env` privé de LeCodeIA.

Le workflow programmé s'exécute toutes les 10 minutes et traite jusqu'à 8 segments de texte par passage. Lancez-le une première fois manuellement et cochez `enqueue_existing_articles` pour mettre en file les articles déjà publiés. Les nouveaux articles publiés par l'administration sont ajoutés à la file par le code PHP du site.

Le runner utilise Edge TTS via `edge-tts`. Ce n'est pas une API officielle Microsoft avec disponibilité garantie ; le service peut être modifié ou limité. Les runners standard GitHub Actions sont gratuits pour les dépôts publics. Gardez ce dépôt public uniquement pour le code du worker et ses fichiers de workflow, jamais pour des secrets ou une copie du site.
