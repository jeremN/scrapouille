# 🎯 Business Ideas Scraper & Disruption Scanner

Suite complète pour détecter des opportunités business :
1. **Scraper** — Collecte des idées depuis Reddit, HN, Product Hunt, Indie Hackers
2. **Disruption Scanner** — Trouve des apps vieillissantes mûres pour être remplacées
3. **Runner** — Pipeline quotidien avec stockage SQLite, détection de nouveautés, notifications
4. **Dashboard** — Interface web pour visualiser les résultats

## Quick Start

```bash
# 1. Setup
cp .env.example .env    # Configure tes notifications
pip install -r requirements.txt

# 2. Premier scan
python runner.py

# 3. Dashboard
python runner.py --dashboard
# → http://localhost:8080

# 4. Mode quotidien
python runner.py --schedule --dashboard
```

## Quick Start (GitHub Actions — gratuit)

Le plus simple : le scan tourne chaque jour via GitHub Actions, les rapports sont sur GitHub Pages.

```bash
# 1. Créer un repo GitHub et push le code
git init && git add -A && git commit -m "init"
gh repo create disruption-scanner --private --push

# 2. Activer GitHub Pages
# Settings > Pages > Source: GitHub Actions

# 3. (Optionnel) Ajouter tes webhooks de notification
# Settings > Secrets > Actions > New repository secret
# → DISCORD_WEBHOOK_URL, NTFY_TOPIC, etc.

# 4. Lancer le premier scan manuellement
# Actions > Daily Disruption Scan > Run workflow

# 5. Les rapports sont sur :
# https://<ton-user>.github.io/<ton-repo>/
```

Le workflow :
- Tourne chaque jour à 8h (Paris) via cron
- Persiste le SQLite en committant dans le repo
- Déploie les rapports HTML sur GitHub Pages
- Envoie des notifications (Discord/Slack/Email/Ntfy)
- Lancement manuel possible depuis l'UI GitHub

---

## Quick Start (Docker)

```bash
cp .env.example .env
# Configure .env avec tes webhooks Discord/Slack
docker compose up -d
# → Dashboard sur http://localhost:8080
```

---

## Architecture

```
runner.py              ← Orchestrateur principal
├── scraper.py         ← Scrape idées business (Reddit, HN, PH...)
├── disruption_scanner.py  ← Trouve apps à disruper (G2, Capterra...)
├── store.py           ← SQLite storage + diff detection
├── notifier.py        ← Discord / Slack / Email / Ntfy
├── config.py          ← Configuration (.env)
├── data/
│   ├── disruption.db  ← Base SQLite
│   └── reports/       ← Rapports HTML quotidiens
├── Dockerfile
└── docker-compose.yml
```

---

# 📡 Modes d'exécution

```bash
# Run once (scan → store → notify → exit)
python runner.py

# Schedule quotidien (cron dans .env, défaut: 8h)
python runner.py --schedule

# Dashboard web seul
python runner.py --dashboard

# Schedule + Dashboard (recommandé pour prod)
python runner.py --schedule --dashboard
```

---

# 🔔 Notifications

Configure au moins un channel dans `.env` :

| Channel | Config | Description |
|---|---|---|
| **Discord** | `DISCORD_WEBHOOK_URL` | Rich embeds avec top opportunités |
| **Slack** | `SLACK_WEBHOOK_URL` | Message structuré avec blocks |
| **Email** | `SMTP_*` + `EMAIL_*` | Rapport HTML par email |
| **Ntfy** | `NTFY_TOPIC` | Push notification mobile (gratuit!) |

### Ntfy (le plus simple)
Le plus rapide à configurer — juste un topic unique :
```
NTFY_TOPIC=mon-scanner-secret-123
```
Puis installe l'app ntfy.sh sur ton tel et abonne-toi au même topic.

---

# 🌐 API Dashboard

Le dashboard expose une API JSON :

| Endpoint | Description |
|---|---|
| `GET /api/stats` | Stats agrégées |
| `GET /api/top?limit=20&min_score=30` | Top opportunités |
| `GET /api/new` | Nouvelles des dernières 24h |
| `GET /api/trending` | Score en hausse |
| `GET /api/runs` | Historique des scans |
| `GET /reports/report-2025-01-15.html` | Rapports HTML |

---

# 🔍 Business Ideas Scraper

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scrape toutes les sources
python scraper.py

# Sources spécifiques uniquement
python scraper.py --sources reddit hn

# Filtrer par score minimum
python scraper.py --min-score 50

# Rechercher par mots-clés
python scraper.py --search "saas crypto tax"

# Limiter le nombre de résultats par source
python scraper.py --limit 10

# Nom de fichier custom
python scraper.py --output mes-idees
```

## Sources disponibles

| Source | Flag | API |
|---|---|---|
| Reddit | `reddit` | JSON API (no auth) |
| Hacker News | `hn` | Algolia API |
| Product Hunt | `producthunt` | HTML scraping |
| Indie Hackers | `indiehackers` | HTML scraping |
| Exploding Topics | `exploding` | HTML scraping |

## Output

- **`business-ideas.json`** — Données brutes structurées
- **`business-ideas.html`** — Rapport interactif avec :
  - Stats globales
  - Tag cloud par catégorie (SaaS, AI, Fintech, DevTools, Crypto...)
  - Filtre texte en temps réel
  - Filtre par source et score minimum
  - Liens directs vers les posts originaux

## Exemples de workflows

```bash
# Idées SaaS populaires
python scraper.py --sources reddit hn --search "saas" --min-score 20

# Tendances crypto/fintech
python scraper.py --search "crypto tax fintech"

# Veille hebdo complète
python scraper.py --min-score 10 --output "veille-$(date +%Y-%m-%d)"
```

---

# 🎯 Disruption Scanner (NEW)

Second script dédié à trouver des **apps vieillissantes mûres pour être remplacées**. Scanne les avis négatifs, feature requests, et signaux de frustration.

## Usage

```bash
# Scan complet (toutes les sources)
python disruption_scanner.py

# Sources spécifiques
python disruption_scanner.py --sources g2 alternativeto reddit

# Scanner une catégorie précise
python disruption_scanner.py --category "project-management"

# Cibler les apps avec des mauvaises notes
python disruption_scanner.py --max-rating 3.0 --min-reviews 100
```

## Sources scannées

| Source | Méthode | Ce qu'on cherche |
|---|---|---|
| **G2** | HTML + review analysis | Apps mal notées avec beaucoup d'avis |
| **Capterra** | HTML scraping | Idem, cross-validation |
| **AlternativeTo** | Scraping | Apps avec le plus d'alternatives = gens qui veulent partir |
| **GitHub Issues** | API (no auth) | Feature requests les plus upvotées = besoins non couverts |
| **Canny boards** | HTML scraping | Feature requests publiques |
| **Reddit** | JSON API | Posts "alternative to X" / "replacement for X" |

## Disruption Score (0-100)

Chaque app reçoit un **score de disruption** calculé à partir de :
- ⭐ **Rating faible** + beaucoup de reviews = base frustrée
- 🔄 **Beaucoup d'alternatives** recherchées = gens qui veulent partir
- 🐛 **Thèmes négatifs** : "outdated", "slow", "expensive", "buggy"
- 📋 **Feature requests** non implémentées
- 💰 **Pain points** récurrents dans les avis

## Output

- **`disruption-report.json`** — Données brutes
- **`disruption-report.html`** — Dashboard interactif avec :
  - Score de disruption par app (🔥 high / ⚡ medium / 📊 low)
  - Pain points agrégés
  - Feature requests top
  - Filtres par source, score, texte
  - Tri par score, rating, reviews, alternatives

## Exemples avancés

```bash
# Apps CRM frustrantes avec +100 avis
python disruption_scanner.py --category crm --max-rating 3.5 --min-reviews 100

# Uniquement GitHub + Reddit (pas de scraping HTML)
python disruption_scanner.py --sources github reddit

# Export custom
python disruption_scanner.py --output "scan-$(date +%Y-%m-%d)"
```

## ⚠️ Notes importantes

- **G2/Capterra** ont des protections anti-bot. Le script gère les 403/429 avec retry + rotation de User-Agent, mais peut être bloqué sur des gros volumes.
- **GitHub API** sans auth = 60 req/h. Suffisant pour le scan par défaut.
- **Rate limiting** : 2s entre chaque requête par défaut.
- Les résultats sont meilleurs en combinant plusieurs sources (cross-validation).

## Rate limiting

Les scrapers attendent 1.5-2s entre chaque requête pour être respectueux des serveurs. Un scrape complet prend ~5-10 minutes.
