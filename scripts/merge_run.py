#!/usr/bin/env python3
"""
Merge today's scored jobs (written by Claude) into the private job database.

Inputs (all in --pipeline, outside the public repo):
  candidates.json   from fetch_jobs.py
  scored.json       {"jobs": [{id, fit, fit_reason, strengths, warnings, letter?, skills?}]}
  jobs_db.json      running database (created if missing)
And --tracker:      Application Tracker.xlsx (jobs you've applied to are dropped)

Usage:
  python3 scripts/merge_run.py --pipeline ../job_search/pipeline --tracker "../job_search/Application Tracker.xlsx"
"""
import argparse
import datetime as dt
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

MIN_FIT = 45
KEEP_CLOSED_DAYS = 14
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def applied_ids(xlsx):
    """{id: applied date or None} for tracker rows you've applied to (or interviewed for)."""
    ids = {}
    if not xlsx or not os.path.exists(xlsx):
        return ids
    z = zipfile.ZipFile(xlsx)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    for row in sheet.iter("{%s}row" % NS["m"]):
        cells = {}
        for c in row.findall("m:c", NS):
            v = c.find("m:v", NS)
            val = ""
            if c.get("t") == "s" and v is not None:
                val = ss[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
            elif v is not None:
                val = v.text or ""
            cells[re.sub(r"\d", "", c.get("r"))] = val
        status, link, when = cells.get("C", ""), cells.get("G", ""), cells.get("H", "")
        if re.search(r"completed|interviewed|applied", status, re.I) and link:
            date = None
            try:
                date = (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(when)))).isoformat()
            except (TypeError, ValueError):
                pass
            for m in re.findall(r"(?:job_detail/|jobs/|postingId=|opening/|opportunityId=)([\w-]+)", link):
                ids[m.split("-")[0] if re.match(r"^\d+-", m) else m] = date
    return ids


def raw_id(job_id):
    return job_id.split(":", 1)[1] if ":" in job_id else job_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--tracker")
    ap.add_argument("--scored", help="scored file (default: <pipeline>/scored.json)")
    a = ap.parse_args()
    today = dt.date.today().isoformat()
    P = lambda n: os.path.join(a.pipeline, n)  # noqa: E731

    try:
        cands = {j["id"]: j for j in json.load(open(P("candidates.json")))["jobs"]}
    except FileNotFoundError:
        cands = {}
    scored = json.load(open(a.scored or P("scored.json"))).get("jobs", [])
    try:
        db = json.load(open(P("jobs_db.json")))
    except FileNotFoundError:
        db = {"jobs": []}
    jobs = {j["id"]: j for j in db["jobs"]}
    applied = applied_ids(a.tracker)

    # Refresh details of jobs we already track.
    for jid, j in jobs.items():
        c = cands.get(jid)
        if c:
            for k in ("closes", "salary", "salary_min", "location", "description", "url"):
                if c.get(k):
                    j[k] = c[k]

    added = drafted = 0
    for s in scored:
        jid = s["id"]
        base = jobs.get(jid) or dict(cands.get(jid) or {})
        if not base:
            continue
        if jid not in jobs:
            if (s.get("fit") or 0) < MIN_FIT:
                continue
            base["added"] = today
            added += 1
        for k in ("fit", "fit_reason", "strengths", "warnings"):
            if s.get(k) is not None:
                base[k] = s[k]
        if s.get("letter"):
            base["letter"], base["skills"] = s["letter"], s.get("skills") or []
            base["drafted"], base["draft_date"] = True, today
            base["draft_at"] = dt.datetime.now().isoformat(timespec="seconds")
            drafted += 1
        base.setdefault("drafted", False)
        base["description"] = (base.get("description") or "")[:6000]
        jobs[jid] = base

    # Flag jobs found in the tracker as applied (kept, never deleted); drop other jobs closed long ago.
    out = []
    for j in jobs.values():
        rid = raw_id(j["id"])
        if rid in applied:
            j["tracker_applied"], j["applied_date"] = True, applied[rid] or j.get("applied_date")
            out.append(j)
            continue
        j.pop("tracker_applied", None)
        if j.get("closes") and (dt.date.today() - dt.date.fromisoformat(j["closes"])).days > KEEP_CLOSED_DAYS:
            continue
        out.append(j)
    out.sort(key=lambda j: -(j.get("fit") or 0))
    db = {"updated": dt.datetime.now().isoformat(timespec="minutes"), "jobs": out}
    json.dump(db, open(P("jobs_db.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"added": added, "drafted": drafted, "total": len(out), "in_tracker": sum(1 for j in out if j.get("tracker_applied"))}))


if __name__ == "__main__":
    main()
