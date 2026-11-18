#!/usr/bin/env python3
"""
Encrypt the private job database for the public site, then (optionally) push.

Reads   <pipeline>/jobs_db.json    (private: postings, fit notes, cover letters)
Reads   <pipeline>/.site_key       (private passphrase; created on first run)
Writes  data/jobs.enc.json         (AES-256-GCM, key from PBKDF2-SHA256; safe to publish)

Usage:
    python3 scripts/publish.py --pipeline /path/to/job_search/pipeline [--push]
Needs:  pip install --user cryptography
"""
import argparse
import base64
import datetime as dt
import gzip
import json
import os
import secrets
import subprocess
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 310_000
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Fields the site needs; anything else in the db stays local.
SITE_FIELDS = ["id", "source", "title", "employer", "location", "url", "posted", "closes", "salary",
               "salary_min", "fit", "fit_reason", "added", "drafted", "draft_date", "letter", "skills",
               "strengths", "warnings", "description", "draft_at", "tracker_applied", "applied_date"]


def b64(b):
    return base64.b64encode(b).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    key_path = os.path.join(a.pipeline, ".site_key")
    if not os.path.exists(key_path):
        with open(key_path, "w") as f:
            f.write(secrets.token_urlsafe(15) + "\n")
        os.chmod(key_path, 0o600)
        print("Created a new site passphrase at", key_path)
    passphrase = open(key_path).read().strip()

    db = json.load(open(os.path.join(a.pipeline, "jobs_db.json")))
    jobs = [{k: j.get(k) for k in SITE_FIELDS} for j in db.get("jobs", [])]
    payload = {"updated": db.get("updated") or dt.datetime.now().isoformat(timespec="minutes"), "jobs": jobs}
    raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())

    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, raw, None)  # ciphertext || 16-byte tag (what WebCrypto expects)

    out = {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "cipher": "AES-256-GCM", "gzip": True,
           "updated": payload["updated"], "count": len(jobs), "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}
    os.makedirs(os.path.join(REPO, "data"), exist_ok=True)
    path = os.path.join(REPO, "data", "jobs.enc.json")
    json.dump(out, open(path, "w"))
    print(f"Wrote {path} ({len(jobs)} jobs, {os.path.getsize(path)//1024} KB)")

    if a.push:
        def git(*args):
            return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)
        git("add", "data/jobs.enc.json")
        c = git("commit", "-m", f"Daily jobs update {dt.date.today().isoformat()}")
        if "nothing to commit" in (c.stdout + c.stderr):
            print("No changes to commit.")
            return
        p = git("push")
        if p.returncode != 0:
            print("git push failed:\n" + p.stderr, file=sys.stderr)
            sys.exit(1)
        print("Pushed to GitHub.")


if __name__ == "__main__":
    main()
