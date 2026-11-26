# Collecteur gratuit du dashboard

Ce petit programme tourne **gratuitement sur GitHub** 3 fois par jour. Il récupère :
- les **tweets X** de comptes choisis et des tickers suivis (avec un compte X secondaire),
- les **posts Truth Social de Trump** (archive publique trumpstruth.org),
- les posts chauds de **Reddit** (wallstreetbets, stocks, CryptoCurrency, investing),
- les **actualités mondiales à risque** et les **enlèvements liés à la crypto en France** (GDELT),
- le **calendrier de la Fed** (FOMC, discours du président, minutes).

Il écrit tout dans `data/feed.json`, que le dashboard lit tout seul.

## Installation (10 minutes, une seule fois)
1. Crée un compte gratuit sur github.com.
2. Crée un dépôt **public** nommé `perps-collector` (public = minutes GitHub Actions illimitées et gratuites).
3. Envoie dans ce dépôt tous les fichiers de ce zip (bouton « Add file » → « Upload files »), en gardant le dossier `.github/workflows`.
4. Onglet **Actions** → active les workflows → « Collecte du dashboard » → **Run workflow**.
5. Dans le dashboard → ⚙️ Réglages → colle l’adresse :
   `https://raw.githubusercontent.com/TON-PSEUDO/perps-collector/main/data/feed.json`

## Activer X/Twitter (facultatif)
X n’a plus d’accès gratuit officiel. Le collecteur utilise la bibliothèque open source **Scweet**, qui lit X comme un navigateur connecté.
1. Crée un **compte X secondaire** (jamais ton compte principal : X peut le bloquer).
2. Connecte-toi à x.com sur un ordinateur, ouvre les outils développeur → Stockage → Cookies → copie la valeur du cookie `auth_token`.
3. Copie aussi le cookie `ct0`.
4. Dans ton dépôt GitHub : Settings → Secrets and variables → Actions → **New repository secret** : nom `X_AUTH_TOKEN` (valeur = cookie auth_token), puis un 2e secret `X_CT0` (valeur = cookie ct0).

Le collecteur essaie 3 outils open source à la suite (Scweet, puis twscrape, puis twifork) : si X en casse un, les autres prennent le relais.
5. Relance le workflow. Les comptes et tickers suivis se modifient dans `config.json`.

Sans ce secret, tout le reste fonctionne quand même.
