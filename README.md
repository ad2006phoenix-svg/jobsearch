# Job Search Hub

A personal job-search dashboard hosted on GitHub Pages.

- `index.html` – the site (four tabs: jobs, detail, draft, actions).
- `data/jobs.enc.json` – the daily job list, **encrypted** (AES-256-GCM). It is unlocked in the browser with a passphrase that is not stored in this repo.
- `scripts/fetch_jobs.py` – pulls postings from public job boards each morning.
- `scripts/publish.py` – encrypts the private job database and pushes the update.
- `config/search.json` – search area, sources and filters.

No personal data (resume, letters, tracker) is stored here in readable form.
