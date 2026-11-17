#!/usr/bin/env python3
"""
Morning job fetch for the Job Search Hub.

Pulls postings from the job boards in config/search.json, applies the hard
filters (area, full-time, age, closed, obvious non-fit roles) and writes
candidates for Claude to score and draft.

Standard library only. Usage:
    python3 scripts/fetch_jobs.py --out /path/to/pipeline/candidates.json
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) JobSearchHub/1.0 (personal job search)"
TODAY = dt.date.today()
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "..", "config", "search.json")))
MAX_AGE = CONFIG["filters"]["max_posting_age_days"]

# Cities within ~35 miles of Ann Arbor (lowercase).
AREA = {
    "ann arbor", "ypsilanti", "ypsilanti township", "saline", "dexter", "chelsea", "milan",
    "manchester", "whitmore lake", "pinckney", "brighton", "howell", "hamburg", "south lyon",
    "novi", "northville", "plymouth", "canton", "belleville", "van buren", "romulus", "westland",
    "livonia", "wayne", "garden city", "inkster", "dearborn", "dearborn heights", "taylor",
    "farmington", "farmington hills", "wixom", "milford", "tecumseh", "adrian", "monroe",
    "redford", "allen park", "southgate", "lincoln park", "flat rock", "new boston", "fowlerville",
    "grass lake", "dundee", "carleton", "commerce", "walled lake", "lyon township",
    "superior township", "pittsfield township", "scio township", "washtenaw", "washtenaw county",
}
REMOTE_WORDS = re.compile(r"\b(remote|statewide|telework|mobile/remote)\b", re.I)

# Titles that are clearly not a fit (keeps volume down; Claude scores the rest).
EXCLUDE_TITLE = re.compile(
    r"\b(nurse|nursing|\brn\b|lpn|physician|surgeon|hospitalist|pharmac\w*|dental|dentist|hygienist|"
    r"radiolog\w*|anesthe\w*|sonograph\w*|respiratory|phlebotom\w*|perfusion|"
    r"advanced practice|nurse practitioner|physician assistant|\bcrna\b|"
    r"custodian|custodial|cook|food service|dietary|bus driver|driver|mechanic|electrician|plumber|hvac|"
    r"maintenance|groundskeeper|carpenter|painter|"
    r"engineer|developer|programmer|software|devops|"
    r"accountant|accounting|payroll|auditor|actuar\w*|"
    r"faculty|professor|lecturer|instructor|postdoc\w*|post-doc\w*|fellow|fellowship|resident physician|"
    r"intern|internship|student assistant|temporary|temp\b|part[- ]time|\bprn\b|on[- ]call|seasonal|"
    r"psychotherapist|therapist|psychologist|psychiatrist|"
    r"teacher|paraprofessional|parapro|substitute|aftercare|lunch|recess|athletic|"
    r"attorney|referee|police|deputy|corrections officer|firefighter|"
    r"veterinar\w*|animal|laborator\w* tech\w*|lab tech\w*|histotech\w*)\b",
    re.I,
)


# ---------------------------------------------------------------- helpers
def get(url, data=None, headers=None, tries=3):
    h = {"User-Agent": UA}
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def text_of(s):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>|</tr>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def lines_of(page):
    t = text_of(page)
    return [l.strip() for l in t.split("\n") if l.strip()]


def after(lines, label, n=1):
    for i, l in enumerate(lines):
        if l.lower() == label.lower():
            return " ".join(lines[i + 1:i + 1 + n]).strip()
    return ""


def iso(d):
    return d.isoformat() if d else None


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return dt.date.fromisoformat(m.group(1))
    m = re.search(r"(\d{1,2} \w{3} \d{4})", s)  # RFC 822 style, e.g. 'Tue, 24 Nov 2026 17:00'
    if m:
        return dt.datetime.strptime(m.group(1), "%d %b %Y").date()
    return None


def money(lo, hi, interval):
    """Return (display text, annual_min, annual_max)."""
    try:
        lo = float(lo) if lo not in (None, "") else None
        hi = float(hi) if hi not in (None, "") else None
    except ValueError:
        return ("Not listed", None, None)
    if lo is None and hi is None:
        return ("Not listed", None, None)
    mult = {"hour": 2080, "hourly": 2080, "biweekly": 26, "week": 52, "month": 12, "year": 1, "annual": 1}
    m = mult.get((interval or "year").lower(), 1)
    if lo and hi and hi < lo:
        hi = None
    a_lo = lo * m if lo else None
    a_hi = hi * m if hi else None

    def f(x):
        return f"${x:,.2f}" if x < 1000 else f"${x:,.0f}"

    rng = f"{f(lo)} – {f(hi)}" if lo and hi else f(lo or hi)
    if m == 1:
        return (rng, a_lo, a_hi)
    ann = f"${(a_lo or a_hi)/1000:.1f}k" + (f" – ${a_hi/1000:.1f}k" if a_lo and a_hi else "")
    unit = {2080: "/hr", 26: " biweekly", 52: "/wk", 12: "/mo"}[m]
    return (f"{rng}{unit} (~{ann}/yr)", a_lo, a_hi)


def smart_case(t):
    letters = [c for c in t.split("(")[0] if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        t = t.title()
        for w in ("Cps", "Dd", "Whp", "Up", "Cmh", "Hr", "It"):
            t = re.sub(r"\b%s\b" % w, w.upper(), t)
    return t


def job(**kw):
    base = dict(id=None, source=None, title=None, employer=None, location=None, city=None,
                remote=False, url=None, posted=None, closes=None, salary="Not listed",
                salary_min=None, salary_max=None, full_time=None, description="")
    base.update(kw)
    base["description"] = (base["description"] or "")[:7000]
    return base


def in_area(j):
    city = (j.get("city") or "").lower().strip()
    loc = (j.get("location") or "").lower()
    if city in AREA:
        return True
    if re.search(r"\b(macomb|clinton township|oakland county|wayne county|lansing)\b", loc):
        return False
    return any(re.search(r"\b%s\b" % re.escape(c), loc) for c in AREA)


# ---------------------------------------------------------------- sources
def src_umich(seen_detail):
    out = []
    rss = get("https://careers.umich.edu/search/feed/advanced")
    root = ET.fromstring(rss.encode())
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        m = re.search(r"\((\d+)\)\s*$", title)
        jid = m.group(1) if m else link
        title = re.sub(r"\s*\(\d+\)\s*$", "", title)
        if EXCLUDE_TITLE.search(title):
            continue
        key = "umich:" + jid
        if key in seen_detail:
            out.append(seen_detail[key])
            continue
        try:
            page = get(link)
        except Exception as e:  # noqa: BLE001
            print("  U-M detail failed", jid, e, file=sys.stderr)
            continue
        time.sleep(0.4)
        L = lines_of(page)
        wl = after(L, "Work Location", 2)
        modes = ""
        for i, l in enumerate(L):
            if l.lower() == "modes of work" and i + 1 < len(L) and len(L[i + 1]) <= 30:
                modes = L[i + 1]
                break
        ft = after(L, "Full/Part Time")
        reg = after(L, "Regular/Temporary")
        dates = after(L, "Posting Begin/End Date")
        sal = after(L, "Salary")
        dept = after(L, "Department")
        working = after(L, "Working Title") or title
        d1 = d2 = None
        mm = re.findall(r"\d{2}/\d{2}/\d{4}", dates)
        if mm:
            d1 = parse_date(mm[0])
            d2 = parse_date(mm[1]) if len(mm) > 1 else None
        sm = re.findall(r"\$([\d,]+(?:\.\d+)?)", sal)
        if sm:
            nums = [float(x.replace(",", "")) for x in sm]
            salary, smin, smax = money(nums[0], nums[-1] if len(nums) > 1 else None, "year")
        else:
            salary, smin, smax = ("Not listed", None, None)
        t = text_of(page)
        a = t.find("Job Summary")
        desc = t[a:] if a >= 0 else t
        cut = desc.find("Background Screening")
        desc = desc[:cut] if cut > 0 else desc
        city = "Ann Arbor" if "Ann Arbor" in wl else (wl.split(",")[0].split()[-1] if wl else "")
        if "Dearborn" in wl:
            city = "Dearborn"
        if "Flint" in wl:
            city = "Flint"
        j = job(id=key, source="U-M Careers", title=working.title() if working.isupper() else working,
                employer="University of Michigan" + (f" – {dept}" if dept else ""),
                location=re.sub(r"^Ann Arbor Campus Ann Arbor, MI", "Ann Arbor, MI", wl) + (f" ({modes})" if modes else ""), city=city,
                remote="remote" in modes.lower(), url=link, posted=iso(d1), closes=iso(d2),
                salary=salary, salary_min=smin, salary_max=smax,
                full_time=(ft.lower().startswith("full") and reg.lower() != "temporary") if ft else None,
                description=desc)
        seen_detail[key] = j
        out.append(j)
    return out


def src_state():
    out = []
    xml = get("https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=michigan")
    root = ET.fromstring(xml.lstrip("﻿").encode())
    ns = "{http://www.neogov.com/namespaces/JobListing}"
    for it in root.iter("item"):
        g = lambda k: (it.findtext(ns + k) or "").strip()  # noqa: E731
        title = (it.findtext("title") or "").strip()
        if EXCLUDE_TITLE.search(title):
            continue
        dept = g("department")
        loc = g("location")
        jtype = g("jobType")
        salary, smin, smax = money(g("minimumSalary"), g("maximumSalary"), g("salaryInterval"))
        desc = text_of(" ".join([it.findtext("description") or "", g("examplesofduties"), g("qualifications"),
                                 g("supplementalinformation")]))
        closes = parse_date(g("advertiseToDateTime"))
        if g("continuous") == "Yes":
            closes = None
        remote = bool(REMOTE_WORDS.search(loc) or REMOTE_WORDS.search(title))
        out.append(job(id="som:" + g("jobId"), source="State of Michigan", title=title,
                       employer="State of Michigan – " + dept, location=loc, city=loc, remote=remote,
                       url=it.findtext("link"), posted=iso(parse_date(g("advertiseFromDate"))),
                       closes=iso(closes), salary=salary, salary_min=smin, salary_max=smax,
                       full_time=("full time" in jtype.lower()) if jtype else None, description=desc))
    return out


CNS_BOARD = "https://recruiting.ultipro.com/COM1068COMN/JobBoard/5509fc5e-5a4f-4a28-bb60-7fe9d1e0a361"


def src_cns():
    out = []
    payload = {"opportunitySearch": {"Top": 200, "Skip": 0, "QueryString": "",
                                     "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                                     "Filters": []},
               "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                 "hasNoLicenses": False, "SkippedSkills": []}}
    data = json.loads(get(CNS_BOARD + "/JobBoardView/LoadSearchResults", data=payload,
                          headers={"Content-Type": "application/json"}))
    for o in data.get("opportunities", []):
        title = o.get("Title", "")
        if EXCLUDE_TITLE.search(title):
            continue
        locs = o.get("Locations") or []
        city = ((locs[0].get("Address") or {}).get("City") if locs else "") or ""
        loc = ", ".join(sorted({((l.get("Address") or {}).get("City") or l.get("LocalizedName") or "") for l in locs}))
        out.append(job(id="cns:" + o["Id"], source="CNS Healthcare", title=title, employer="CNS Healthcare",
                       location=loc + ", MI" if loc else "", city=city,
                       url=CNS_BOARD + "/OpportunityDetail?opportunityId=" + o["Id"],
                       posted=iso(parse_date(o.get("PostedDate"))), full_time=o.get("FullTime"),
                       description=o.get("BriefDescription") or ""))
    return out


WASH = "https://secure.ewashtenaw.org/hrjobs/"


def src_washtenaw():
    out = []
    page = get(WASH + "AppJobPostingList.do")
    posted = closes = None
    for m in re.finditer(r"Jobs Posted\s*:\s*([\d/]+).*?Filing Deadline:\s*([\d/]+)|postingId=(\d+)'>\s*([^<]+)</a>",
                         page, re.S):
        if m.group(1):
            posted, closes = parse_date(m.group(1)), parse_date(m.group(2))
            continue
        pid, raw = m.group(3), " ".join(m.group(4).split())
        title = re.sub(r"\s*-\s*Control No\.?:.*$", "", raw)
        if EXCLUDE_TITLE.search(title):
            continue
        url = WASH + "AppViewPosting.do?postingId=" + pid
        desc, salary, smin, smax, ft = "", "Not listed", None, None, None
        try:
            d = text_of(get(url))
            time.sleep(0.4)
            desc = d
            sm = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:-|to|–)\s*\$?\s?([\d,]+(?:\.\d+)?)\s*(per hour|hourly|/hr|annually|per year|biweekly)?", d, re.I)
            if sm:
                lo, hi = float(sm.group(1).replace(",", "")), float(sm.group(2).replace(",", ""))
                interval = "hour" if (sm.group(3) or "").lower() in ("per hour", "hourly", "/hr") or hi < 200 else (
                    "biweekly" if (sm.group(3) or "").lower() == "biweekly" else "year")
                salary, smin, smax = money(lo, hi, interval)
            if re.search(r"part[- ]time|temporary", d[:1500], re.I):
                ft = False
            elif re.search(r"full[- ]time", d, re.I):
                ft = True
        except Exception as e:  # noqa: BLE001
            print("  Washtenaw detail failed", pid, e, file=sys.stderr)
        out.append(job(id="wash:" + pid, source="Washtenaw County", title=smart_case(title),
                       employer="Washtenaw County", location="Washtenaw County, MI", city="Washtenaw County",
                       url=url, posted=iso(posted), closes=iso(closes), salary=salary, salary_min=smin,
                       salary_max=smax, full_time=ft, description=desc))
    return out


def src_livingston():
    out = []
    q = urllib.parse.urlencode({"getParams": json.dumps({"isInternal": 0})})
    data = json.loads(get("https://cmhliv.applicantpool.com/core/jobs/3152?" + q))
    for o in (data.get("data") or {}).get("jobs", []):
        title = o.get("title", "")
        if EXCLUDE_TITLE.search(title):
            continue
        et = (o.get("employmentType") or "")
        pay = o.get("payDetails") or o.get("payRate") or ""
        url = f"https://cmhliv.applicantpool.com/jobs/{o['id']}"
        desc = ""
        try:
            desc = text_of(get(url))
            time.sleep(0.4)
        except Exception:  # noqa: BLE001
            pass
        closes = None if o.get("untilFilled") else parse_date(o.get("endDateRef"))
        out.append(job(id=f"liv:{o['id']}", source="Livingston County CMH", title=title,
                       employer="Livingston County Community Mental Health", location=f"{o.get('city','')}, MI",
                       city=o.get("city", ""), remote=(o.get("workplaceType") or "").lower() == "remote", url=url,
                       posted=iso(parse_date(o.get("startDateRef"))), closes=iso(closes),
                       salary=text_of(str(pay))[:120] or "Not listed",
                       full_time=("full" in et.lower()) if et else None, description=desc))
    return out


def src_emu():
    out = []
    page = get("https://careers.emich.edu/jobs/search")
    seen = set()
    for m in re.finditer(r'href="(https://careers\.emich\.edu/jobs/([a-z0-9-]+))"[^>]*>(.*?)</a>', page, re.S):
        url, slug, label = m.group(1), m.group(2), text_of(m.group(3))
        if slug in seen or slug == "search":
            continue
        seen.add(slug)
        title = label or slug.replace("-", " ").title()
        if EXCLUDE_TITLE.search(title) or EXCLUDE_TITLE.search(slug.replace("-", " ")):
            continue
        try:
            d = text_of(get(url))
            time.sleep(0.4)
        except Exception as e:  # noqa: BLE001
            print("  EMU detail failed", slug, e, file=sys.stderr)
            continue
        ft = None
        if re.search(r"\bpart[- ]time\b|\btemporary\b", d[:3000], re.I):
            ft = False
        elif re.search(r"\bfull[- ]time\b", d, re.I):
            ft = True
        sm = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:-|to|–)\s*\$?\s?([\d,]+(?:\.\d+)?)", d)
        salary, smin, smax = ("Not listed", None, None)
        if sm:
            lo, hi = float(sm.group(1).replace(",", "")), float(sm.group(2).replace(",", ""))
            salary, smin, smax = money(lo, hi, "hour" if hi < 200 else "year")
        closes = None
        cm = re.search(r"(?:closing date|close date|deadline|posting end)[^\n]{0,40}?(\w+ \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{2,4})", d, re.I)
        if cm:
            closes = parse_date(cm.group(1))
        out.append(job(id="emu:" + slug[:80], source="Eastern Michigan University", title=title,
                       employer="Eastern Michigan University", location="Ypsilanti, MI", city="Ypsilanti",
                       url=url, closes=iso(closes), salary=salary, salary_min=smin, salary_max=smax,
                       full_time=ft, description=d))
    return out


def src_redrover():
    out = []
    page = get("https://jobs.redroverk12.com/org/1417").replace('\\"', '"')
    seen = set()
    for m in re.finditer(r'"__typename":"SimpleJobPosting",(.*?)"allowsRemote":(true|false)', page):
        blob = m.group(1)
        f = lambda k: (re.search(r'"%s":"?([^",}]*)"?' % k, blob) or [None, None])[1]  # noqa: E731
        jid, name, org, oid = f("id"), f("name"), f("organizationName"), f("orgId")
        if not jid or jid in seen:
            continue
        seen.add(jid)
        if f("closedOnDateUtc") not in (None, "null"):
            continue
        name = html.unescape(name or "")
        if EXCLUDE_TITLE.search(name):
            continue
        ptype = (f("jobPostingTypeId") or "").upper()
        lo, hi, pt = f("minPay"), f("maxPay"), (f("payTypeId") or "").lower()
        salary, smin, smax = money(None if lo in (None, "null") else lo, None if hi in (None, "null") else hi,
                                   "hour" if pt == "hourly" else "year")
        url = f"https://jobs.redroverk12.com/org/{oid}/opening/{jid}"
        desc = ""
        try:
            desc = text_of(get(url))
            time.sleep(0.4)
        except Exception:  # noqa: BLE001
            pass
        out.append(job(id="rr:" + jid, source="Washtenaw ISD (RedRover)", title=name, employer=html.unescape(org or ""),
                       location="Washtenaw County, MI", city="Washtenaw County", remote=m.group(2) == "true",
                       url=url, posted=iso(parse_date(f("activePublicOnDateUtc"))), salary=salary,
                       salary_min=smin, salary_max=smax,
                       full_time=(ptype == "FULL_TIME") if ptype else None, description=desc))
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="candidates.json path (keep it outside the public repo)")
    ap.add_argument("--cache", help="detail cache path (default: next to --out)")
    a = ap.parse_args()
    cache_path = a.cache or os.path.join(os.path.dirname(a.out), "detail_cache.json")
    try:
        cache = json.load(open(cache_path))
    except Exception:  # noqa: BLE001
        cache = {}

    sources = [("U-M Careers", lambda: src_umich(cache)), ("State of Michigan", src_state), ("CNS Healthcare", src_cns),
               ("Washtenaw County", src_washtenaw), ("Livingston County CMH", src_livingston),
               ("Eastern Michigan University", src_emu), ("Washtenaw ISD", src_redrover)]
    report, allj = {}, []
    for name, fn in sources:
        try:
            got = fn()
            report[name] = {"fetched": len(got)}
            allj += got
        except Exception as e:  # noqa: BLE001
            report[name] = {"error": str(e)[:200]}
            print(f"[{name}] FAILED: {e}", file=sys.stderr)

    kept, dropped = [], {}

    def drop(reason):
        dropped[reason] = dropped.get(reason, 0) + 1

    seen_keys = set()
    for j in allj:
        k = re.sub(r"\W+", "", (j["title"] + j["employer"]).lower())
        if k in seen_keys:
            drop("duplicate"); continue
        if j["full_time"] is False:
            drop("not full-time"); continue
        if not (in_area(j) or j["remote"]):
            drop("outside area"); continue
        if j["posted"] and (TODAY - dt.date.fromisoformat(j["posted"])).days > MAX_AGE:
            drop("older than 30 days"); continue
        if j["closes"] and dt.date.fromisoformat(j["closes"]) < TODAY:
            drop("closed"); continue
        if len(j["description"]) < 200 and j["source"] not in ("CNS Healthcare", "Livingston County CMH"):
            drop("thin description"); continue
        seen_keys.add(k)
        kept.append(j)

    # Trim cache to recent entries.
    if len(cache) > 3000:
        cache = dict(list(cache.items())[-3000:])
    json.dump(cache, open(cache_path, "w"))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump({"generated": dt.datetime.now().isoformat(timespec="seconds"), "sources": report,
               "dropped": dropped, "count": len(kept), "jobs": kept}, open(a.out, "w"), indent=1)
    print(json.dumps({"sources": report, "dropped": dropped, "kept": len(kept)}, indent=1))


if __name__ == "__main__":
    main()
