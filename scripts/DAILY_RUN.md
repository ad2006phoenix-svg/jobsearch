# Daily 8am run (instructions for Claude)

Paths are relative to the folder that contains both `job-site/` and `job_search/`.
Private files live in `job_search/` and must never be copied into `job-site/` except through `publish.py`'s encrypted output.

## 1. Fetch
```
cd job-site
python3 scripts/fetch_jobs.py --out ../job_search/pipeline/candidates.json
```
If a source reports an error, continue with the rest and note it in the run log.

## 2. Read context
- `job_search/career_profile.md`: target roles, scoring rules, skills bank, proof points, voice, and the user's answers (section 8).
- `job_search/pipeline/jobs_db.json`: jobs already shown. Don't re-score these unless a field changed.
- `job_search/Application Tracker.xlsx`: rows marked Priority "Yes" show what the user likes. `merge_run.py` drops jobs already applied to.
- For exact resume wording, read the resume pages of the newest resume PDF in `job_search/`.

## 3. Score new candidates
For every job in `candidates.json` whose `id` isn't already in `jobs_db.json` (skip jobs whose link is already in the Application Tracker; don't draft letters for them):
- Hard-exclude (leave it out of scored.json): psychotherapist or therapist-titled roles, post-MSW fellowships or training programs, part-time, temporary or PRN, and anything plainly unrelated to the profile (trades, clinical medicine, IT, finance, food service).
- `fit` 0–100 using profile section 4. 80+ means strong (a target role family, entry-to-mid level, and an employer you like). Below 45 is dropped automatically.
- `fit_reason`: one sentence on why it ranks where it does.
- `strengths`: 2–3 short bullets. `warnings`: 2–3 short bullets covering seniority gaps, licensure timing (LLMSW Dec 2026), salary under $50,000, commute, closing soon, application quirks, and whether you've applied to a similar posting before.

## 4. Draft the top 5
Rank the newly scored jobs by fit, plus 4 if posted in the last 3 days and plus 6 if closing within 7 days. For the top 5:
- `letter`: the full cover letter text, starting with the letter header given in `career_profile.md` section 9 (name, contact line, date, employer block, "Dear Hiring Committee,").
  Follow profile section 7: open with a values-driven hook, then a turn, then "As the <Title>…". Include the credentials line, a CSU paragraph tailored to the job, a Level Up Lab paragraph, something specific the user admires about the employer, and a close. Use one page (5–6 paragraphs) and at least one number. Use only facts from the profile or resume; never invent experience. Vary phrasing from previous letters. Sign off with the name given in the profile.
- `skills`: exactly 9 entries chosen from the profile's skills bank (section 5), reworded lightly to match the posting's language and ordered by relevance.
- For U-M roles, add a warning suggesting the U-M email from the profile.

If fewer than 5 new jobs score 65 or higher, draft only those.

## 5. Write, merge, publish
Write `job_search/pipeline/scored.json` (replace the whole file):
```json
{"date": "YYYY-MM-DD", "jobs": [{"id": "...", "fit": 0, "fit_reason": "", "strengths": [], "warnings": [], "letter": "(top 5 only)", "skills": ["(top 5 only)"]}]}
```
Then:
```
python3 scripts/merge_run.py --pipeline ../job_search/pipeline --tracker "../job_search/Application Tracker.xlsx"
python3 scripts/publish.py --pipeline ../job_search/pipeline --push
```

## 6. Log
Append one line to `job_search/pipeline/run_log.txt`: date, the counts per source, how many were added and drafted, and any errors. Don't send notifications.
