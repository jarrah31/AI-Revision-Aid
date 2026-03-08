# AI Revision Aid

> AI-powered revision tool for UK secondary school students (Years 7–13).
> Upload PDF knowledge organisers, let Claude extract Q&A pairs, then quiz yourself with intelligent spaced repetition.

[![CI](https://github.com/jarrah31/AI-Revision-Aid/actions/workflows/ci.yml/badge.svg)](https://github.com/jarrah31/AI-Revision-Aid/actions/workflows/ci.yml)
[![Publish](https://github.com/jarrah31/AI-Revision-Aid/actions/workflows/publish.yml/badge.svg)](https://github.com/jarrah31/AI-Revision-Aid/actions/workflows/publish.yml)
[![Docker Image](https://ghcr-badge.egpl.dev/jarrah31/ai-revision-aid/latest_tag?label=ghcr.io)](https://github.com/jarrah31/AI-Revision-Aid/pkgs/container/ai-revision-aid)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Features

### 📄 PDF Processing
- Upload any PDF knowledge organiser and select a page range
- Claude AI vision extracts question/answer pairs and identifies diagrams automatically
- Images are cropped from the source page and linked to the relevant questions
- Original PDF pages stay accessible for review alongside the generated Q&As

### 🧠 Spaced Repetition Quizzing
- **Flashcard** — reveal the answer, self-rate your confidence (Again / Hard / Good / Easy)
- **Multiple Choice** — four options with AI-generated distractors
- **Typed Answer** — Claude judges free-text answers for semantic correctness
- **Mixed mode** — all three formats in one session
- Scheduling uses the SM-2 algorithm: overdue cards first, then due-today, then up to 5 new cards per session

### 📚 Subjects & Categories
- Create subjects with a custom emoji icon and colour
- Organise questions into categories within each subject
- Quiz by subject or narrow down to a single category

### 🤝 Year-Group Sharing
- Mark an upload as **Shared** so classmates in the same year group can browse and import it
- Imported question sets are fully independent copies (their own SRS progress, editable, deletable)

### 📊 Dashboard & Cost Tracking
- Per-subject breakdown of questions, accuracy, and cards due today
- Full quiz history with session stats
- Claude API cost tracking in USD / GBP (live exchange rate, cached hourly)

### 🛡️ Admin Panel
- **Users** — view all accounts, edit year group / display name / admin status, delete users
- **Content** — browse and edit any user's batches and questions, toggle sharing, re-process PDFs
- **Settings** — update the Anthropic API key and JWT secret without touching the server
- **Stats** — system-wide usage and cost overview

### 📱 Offline / PWA
- Service worker caches the app shell for offline use
- Approved questions sync to IndexedDB so flashcard and MCQ quizzes work without internet
- Pending answers queue locally and sync when back online

---

## Self-Hosting with Docker

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| Docker + Docker Compose | [Install Docker](https://docs.docker.com/get-docker/) |
| Anthropic API key | [Get one here](https://console.anthropic.com/) — Claude Sonnet is used by default. You can also leave this blank and enter it via the admin panel after first login. |

### Quick Start

**1. Download the compose file**
```bash
mkdir ai-revision-aid && cd ai-revision-aid
curl -o docker-compose.yml https://raw.githubusercontent.com/jarrah31/AI-Revision-Aid/main/docker-compose.yml
```

**2. Edit `docker-compose.yml` to use the published image**

Open the file and make these two changes:
```yaml
services:
  web:
    image: ghcr.io/jarrah31/ai-revision-aid:latest   # ← uncomment this line
    # build: .                                        # ← comment this out
```

**3. (Optional) Set your Anthropic API key**

Either export it in your shell:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```
Or create a `.env` file in the same directory:
```env
ANTHROPIC_API_KEY=sk-ant-...
```

**4. Start the container**
```bash
docker compose up -d
```

**5. Open the app**

Navigate to **http://localhost:8000** and register the first account — it automatically becomes the admin.

> **Tip:** If you skipped step 3, go to **Admin → Settings** after logging in and paste your API key there.

---

### Updating to a New Version

```bash
docker compose pull
docker compose up -d
```

Your data (SQLite database, uploaded PDFs, extracted images) is stored in the `./data` volume and is preserved across updates.

---

### Pinning to a Specific Version

Replace `latest` with a version tag to avoid unexpected changes:
```yaml
image: ghcr.io/jarrah31/ai-revision-aid:1.0.0
```

All available tags are listed on the [Packages page](https://github.com/jarrah31/AI-Revision-Aid/pkgs/container/ai-revision-aid).

---

### Data & Backups

All persistent data lives in `./data/` relative to your `docker-compose.yml`:

```
./data/
├── revisionaid.db   ← SQLite database (users, questions, quiz history)
├── pdfs/            ← Original uploaded PDFs
└── images/          ← Extracted and cropped images
```

Back this directory up regularly. To restore, stop the container, replace `./data/`, and start again.

---

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *(empty)* | Claude API key. Can also be set via Admin → Settings. |

The JWT secret is auto-generated on first start and stored in the database. You can rotate it any time via **Admin → Settings** (this logs all users out).

---

### Running on a Different Port

```yaml
ports:
  - "3000:8000"   # Maps host port 3000 → container port 8000
```

---

### Reverse Proxy (nginx / Traefik)

The app listens on port 8000 inside the container. Point your reverse proxy there and enable HTTPS. Example nginx snippet:

```nginx
server {
    listen 443 ssl;
    server_name revision.example.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 50M;   # Allow large PDF uploads
    }
}
```

---

## Development Setup

```bash
git clone https://github.com/jarrah31/AI-Revision-Aid.git
cd AI-Revision-Aid

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Create a .env file
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Start the dev server (auto-reload on file changes)
python run.py
```

App is at **http://localhost:8000**. The SQLite database and uploaded files are created in `./data/` automatically on first run.

### Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

126 tests covering auth, subjects, questions, quiz engine, admin, sharing, dashboard, and the SM-2 spaced repetition algorithm. No API key required — Claude calls are stubbed.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python · FastAPI · SQLite |
| AI | Anthropic Claude (Sonnet) |
| PDF processing | PyMuPDF |
| Frontend | Alpine.js · Tailwind CSS (CDN, no build step) |
| Auth | bcrypt · JWT |
| Spaced repetition | SM-2 algorithm |
| Offline | PWA · Service Worker · IndexedDB |
| Container | Docker · GitHub Container Registry |
| CI/CD | GitHub Actions |

---

## License

MIT — see [LICENSE](LICENSE) for details.
