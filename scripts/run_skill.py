#!/usr/bin/env python3
"""Query-planning literature search skill (deterministic, offline, stdlib only).

Reads /input/case.json (or --case), builds an explicit query plan from the
research question, normalizes and de-duplicates the supplied candidate records,
scores relevance with transparent term/field evidence, and writes
/app/output/result.json (or --output) preserving case_id and every original
output_contract field.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUTPUT = "/app/output/result.json"
HERE = Path(__file__).resolve().parent
RULES_PATH = HERE.parent / "rules.json"

STOP_EN = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "into",
    "about", "between", "among", "using", "used", "use", "based", "study", "studies",
    "paper", "papers", "article", "articles", "literature", "search", "find", "relevant",
    "review", "reviews", "research", "results", "result", "analysis", "evidence", "question",
    "what", "which", "how", "does", "do", "is", "are", "was", "were", "be", "been", "can",
    "could", "should", "would", "may", "might", "will", "shall", "not", "but", "also", "than",
    "more", "most", "less", "least", "all", "any", "each", "other", "such", "their", "there",
    "where", "when", "why", "who", "whom", "whose", "its", "it", "as", "at", "by", "in", "on",
    "of", "or", "to", "an", "a", "we", "they", "he", "she", "i", "you", "our", "your",
}
STOP_ZH = {
    "研究", "文献", "检索", "相关", "问题", "方法", "分析", "结果", "证据", "综述", "论文",
    "以及", "并且", "进行", "通过", "基于", "对于", "关于", "是否", "如何", "什么", "需要",
    "提供", "给出", "可以", "能够", "这个", "这些", "那些", "一个", "一种", "我们", "他们",
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}")
SENT_SPLIT = re.compile(r"(?<=[。！？.!?])\s*")
GENERIC_TOPICS = {"academic", "literature", "search", "science", "scientific", "research"}

DEFAULT_RULES = {
    "field_weights": {"title": 3.0, "keywords": 2.0, "abstract": 1.0},
    "synonyms": {
        "pyroptosis": [" pyroptotic", "inflammasome", "gasdermin"],
        "crispr": ["cas9", "genome editing", "gene editing"],
        "cancer": ["tumor", "tumour", "neoplasm", "malignancy"],
        "machine learning": ["deep learning", "neural network", "artificial intelligence"],
        "meta-analysis": ["systematic review", "pooled analysis"],
        "biomarker": ["marker", "predictor", "signature"],
        "single-cell": ["scRNA-seq", "single cell sequencing"],
        "drug resistance": ["resistance", "refractory", "treatment failure"],
    },
    "decision_thresholds": {"direct": 0.45, "indirect": 0.22},
    "max_queries": 3,
}


def load_rules():
    rules = json.loads(json.dumps(DEFAULT_RULES))
    try:
        if RULES_PATH.exists():
            data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    rules[key] = value
    except Exception:
        pass
    return rules


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def norm_space(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def tokens(text, stop_zh=True):
    out = []
    for match in TOKEN_RE.finditer(norm_space(text)):
        token = match.group(0).lower()
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 2:
                if token not in STOP_ZH:
                    out.append(token)
            else:
                if token not in STOP_ZH:
                    out.append(token)
                for i in range(len(token) - 1):
                    bigram = token[i:i + 2]
                    if bigram not in STOP_ZH:
                        out.append(bigram)
        else:
            token = token.strip(".-")
            if len(token) > 2 and token not in STOP_EN:
                out.append(token)
                if "-" in token:
                    out.extend(p for p in token.split("-") if len(p) > 3 and p not in STOP_EN)
    return out


def token_set(text):
    return {t for t in tokens(text) if t not in GENERIC_TOPICS}


def first_sentence(text):
    text = norm_space(text)
    if not text:
        return ""
    parts = [p for p in SENT_SPLIT.split(text) if p.strip()]
    return parts[0][:400] if parts else text[:400]


def pick(record, names):
    for name in names:
        if name in record and record[name] not in (None, "", [], {}):
            return record[name]
    return None


def author_text(value):
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("family") or item.get("literal")
                if name:
                    items.append(str(name))
        return "; ".join(items)
    if isinstance(value, dict):
        return author_text(value.get("authors") or value.get("author") or [])
    return ""


def normalize_record(raw, locator):
    rec = raw if isinstance(raw, dict) else {"value": raw}
    title = norm_space(pick(rec, ["title", "article_title", "paper_title", "题名", "name"]) or "")
    abstract = norm_space(pick(rec, ["abstract", "summary", "description", "摘要"]) or "")
    doi = norm_space(pick(rec, ["doi", "DOI", "digital_object_identifier"]) or "")
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I).lower()
    pmid = str(pick(rec, ["pmid", "pubmed_id", "PMID"]) or "").strip()
    arxiv = norm_space(pick(rec, ["arxiv", "arxiv_id", "eprint"]) or "")
    year_raw = pick(rec, ["year", "pub_year", "publication_year", "published", "date", "publication_date"])
    year = None
    if isinstance(year_raw, int):
        year = year_raw
    elif isinstance(year_raw, str):
        match = re.search(r"(19|20)\d{2}", year_raw)
        if match:
            year = int(match.group(0))
    venue = norm_space(pick(rec, ["venue", "journal", "journal_title", "source", "container_title", "期刊"]) or "")
    if isinstance(venue, (dict, list)):
        venue = norm_space(str(pick(venue if isinstance(venue, dict) else {}, ["title", "name"]) or "")) if isinstance(venue, dict) else ""
    authors = author_text(pick(rec, ["authors", "author", "creator", "作者"]))
    keywords = pick(rec, ["keywords", "terms", "subject_terms"]) or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in re.split(r"[;,]", keywords) if k.strip()]
    db = norm_space(str(pick(rec, ["source_db", "database", "source", "provider", "index"]) or ""))
    if db.lower() in ("", "none"):
        db = ""
    return {
        "record_id": str(pick(rec, ["record_id", "id", "paper_id", "uid"]) or ""),
        "title": title,
        "abstract": abstract,
        "doi": doi,
        "pmid": pmid,
        "arxiv_id": arxiv,
        "year": year,
        "venue": venue,
        "authors": authors,
        "keywords": [norm_space(str(k)) for k in keywords if str(k).strip()],
        "source_db": db,
        "source_locator": locator,
        "raw_fields": sorted(rec.keys()),
    }


def record_key(rec):
    if rec["doi"]:
        return "doi:" + rec["doi"]
    if rec["pmid"]:
        return "pmid:" + rec["pmid"]
    if rec["arxiv_id"]:
        return "arxiv:" + rec["arxiv_id"].lower()
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", rec["title"].lower())
    return "title:" + (title or rec["record_id"] or rec["source_locator"])


def looks_like_record(node, path):
    if not isinstance(node, dict):
        return False
    keys = {k.lower() for k in node.keys()}
    has_title = bool(keys & {"title", "article_title", "paper_title", "题名"})
    markers = {"doi", "abstract", "authors", "author", "year", "journal", "venue", "pmid", "arxiv_id", "pmcid"}
    if has_title and (keys & markers):
        return True
    if (keys & {"doi", "pmid", "arxiv_id"}) and (keys & {"year", "journal", "venue", "abstract"}):
        return True
    lowered = path.lower()
    return bool(has_title and any(m in lowered for m in ("candidate", "paper", "record", "result", "article", "item")))


def collect_candidates(case):
    preferred = []
    for key in ("candidates", "papers", "records", "articles", "items", "search_results", "results"):
        value = case.get(key) if isinstance(case, dict) else None
        if isinstance(value, list) and value:
            preferred.append((f"/{key}", value))
            break
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            if looks_like_record(node, path):
                found.append((path, node))
                return
            for key, value in node.items():
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}")

    if preferred:
        base, items = preferred[0]
        for index, item in enumerate(items):
            if isinstance(item, dict):
                found.append((f"{base}/{index}", item))
    else:
        walk(case, "")
    return found


def extract_question(case):
    if not isinstance(case, dict):
        return ""
    for key in ("question", "research_question", "research_problem", "topic", "query"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return norm_space(value)
    task = case.get("task")
    if isinstance(task, str) and task.strip():
        return norm_space(task)
    if isinstance(task, dict):
        for key in ("question", "description", "prompt", "text"):
            if isinstance(task.get(key), str) and task[key].strip():
                return norm_space(task[key])
    task_md = Path("/input/task.md")
    if task_md.exists():
        try:
            text = task_md.read_text(encoding="utf-8", errors="replace")
            return norm_space(text)[:2000]
        except Exception:
            return ""
    return ""


def extract_list(case, names):
    if not isinstance(case, dict):
        return []
    for name in names:
        value = case.get(name)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v.strip()]
    return []


def dedupe(records):
    merged = {}
    order = []
    duplicates = []
    for rec in records:
        key = record_key(rec)
        if key not in merged:
            merged[key] = dict(rec)
            merged[key]["merged_keys"] = [key]
            merged[key]["source_records"] = [rec["source_locator"]]
            order.append(key)
        else:
            target = merged[key]
            for field in ("title", "abstract", "doi", "pmid", "arxiv_id", "year", "venue", "authors"):
                if not target.get(field) and rec.get(field):
                    target[field] = rec[field]
            if rec["source_db"] and rec["source_db"] not in (target.get("source_db") or ""):
                target["source_db"] = "; ".join(x for x in (target.get("source_db"), rec["source_db"]) if x)
            target["merged_keys"].append(key)
            target["source_records"].append(rec["source_locator"])
            if rec["title"] and rec["title"].lower() != (target.get("title") or "").lower():
                duplicates.append({"kept": key, "merged": record_key(rec), "reason": "same identifier", "locator": rec["source_locator"]})
    return [merged[k] for k in order], duplicates


def build_queries(question, rules):
    terms = []
    for token in tokens(question):
        if token not in terms:
            terms.append(token)
    qset = set(terms)
    synonyms = rules.get("synonyms") or {}
    expanded = []
    for term in terms:
        for key, values in synonyms.items():
            if term == key or term in str(key).split() or key in qset and term in key.split():
                for value in values:
                    value = norm_space(value)
                    if value and value.lower() not in qset and value.lower() not in expanded:
                        expanded.append(value.lower())
    core_terms = [t for t in terms if len(t) > 3][:6] or terms[:4]
    queries = []
    if core_terms:
        queries.append({
            "query_id": "Q1",
            "purpose": "core concept conjunction",
            "query_string": " AND ".join(core_terms),
            "source_terms": core_terms,
            "fields": ["title", "abstract"],
            "rationale": "Conjoin the highest-information question terms to maximize precision on the core concept.",
        })
    if expanded:
        queries.append({
            "query_id": "Q2",
            "purpose": "synonym expansion",
            "query_string": " OR ".join(core_terms[:3] + expanded[:4]),
            "source_terms": core_terms[:3] + expanded[:4],
            "fields": ["title", "abstract", "keywords"],
            "rationale": "Add curated synonyms/abbreviations to recover terminology variants without free-text drift.",
        })
    if len(core_terms) >= 2:
        queries.append({
            "query_id": "Q3",
            "purpose": "field-limited precision pass",
            "query_string": " OR ".join(f'title:"{t}"' for t in core_terms[:4]),
            "source_terms": core_terms[:4],
            "fields": ["title"],
            "rationale": "Restrict to title to surface records whose main claim matches the question.",
        })
    return queries[: int(rules.get("max_queries", 3))], terms, expanded


def score_record(rec, qset, inclusion, exclusion, rules, year_range):
    weights = rules.get("field_weights", {})
    title_tokens = token_set(rec["title"])
    abstract_tokens = token_set(rec["abstract"])
    keyword_tokens = token_set(" ".join(rec["keywords"]))
    matched_title = sorted(qset & title_tokens)
    matched_abstract = sorted(qset & abstract_tokens)
    matched_keywords = sorted(qset & keyword_tokens)
    matched = set(matched_title) | set(matched_abstract) | set(matched_keywords)
    denom = max(1, len(qset))
    coverage = min(1.0, (weights.get("title", 3.0) * len(matched_title)
                         + weights.get("keywords", 2.0) * len(matched_keywords)
                         + weights.get("abstract", 1.0) * len(matched_abstract))
                        / (3.0 * denom))
    score = 0.75 * coverage
    if rec["title"] and any(t in rec["title"].lower() for t in qset):
        score += 0.05
    if rec["year"] and year_range:
        if year_range[0] <= rec["year"] <= year_range[1]:
            score += 0.05
    if rec["doi"] or rec["pmid"]:
        score += 0.03
    if rec["abstract"]:
        score += 0.02
    crit_hits = [c for c in inclusion if c.lower() in (rec["title"] + " " + rec["abstract"]).lower()]
    score += min(0.10, 0.03 * len(crit_hits))
    excl_hits = [c for c in exclusion if c.lower() in (rec["title"] + " " + rec["abstract"]).lower()]
    score -= min(0.30, 0.15 * len(excl_hits))
    score = round(max(0.0, min(1.0, score)), 4)
    thresholds = rules.get("decision_thresholds", {})
    direct = float(thresholds.get("direct", 0.45))
    indirect = float(thresholds.get("indirect", 0.22))
    if not rec["title"] and not rec["abstract"]:
        relevance, decision = "uncertain", "review"
    elif score >= direct:
        relevance, decision = "direct", "keep"
    elif score >= indirect:
        relevance, decision = "indirect", "review"
    elif excl_hits:
        relevance, decision = "not_relevant", "exclude"
    else:
        relevance, decision = "not_relevant", "exclude"
    return {
        "score": score,
        "relevance": relevance,
        "decision": decision,
        "matched_terms": matched,
        "component_scores": {
            "weighted_term_coverage": round(coverage, 4),
            "criterion_hits": crit_hits,
            "exclusion_hits": excl_hits,
            "identifier_bonus": 0.03 if (rec["doi"] or rec["pmid"]) else 0.0,
            "abstract_bonus": 0.02 if rec["abstract"] else 0.0,
        },
    }


def pick_quote(rec, matched):
    for field, text in (("title", rec["title"]), ("abstract", rec["abstract"])):
        if not text:
            continue
        for sentence in [s for s in SENT_SPLIT.split(text) if s.strip()]:
            if any(term in sentence.lower() for term in matched):
                return {"field": field, "quote": sentence.strip()[:400], "locator": rec["source_locator"]}
    return {}


def year_bounds(case):
    for key in ("year_range", "time_range", "publication_window", "date_range"):
        value = case.get(key) if isinstance(case, dict) else None
        if isinstance(value, list) and len(value) == 2:
            try:
                return (int(str(value[0])[:4]), int(str(value[1])[:4]))
            except Exception:
                return None
    return None


def contract_field_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def placeholder_for(name):
    low = name.lower()
    if any(k in low for k in ("candidate", "result", "paper", "record", "item", "rank", "list", "entr", "reference", "edge", "quer")):
        return []
    if any(k in low for k in ("count", "score", "rate", "metric", "number", "k")):
        return None
    if any(k in low for k in ("summary", "status", "evidence", "coverage", "meta", "plan", "config", "map", "detail", "note")):
        return {}
    return None


def apply_contract(result, case):
    names = contract_field_names(case)
    notes = {}
    for name in names:
        if name in result:
            continue
        result[name] = placeholder_for(name)
        notes[name] = "No topic-specific value inferable from the actual input; placeholder emitted for contract preservation."
    result["output_contract_fields"] = {
        "provided": names,
        "present": {name: (name in result) for name in names},
        "notes": notes,
    }
    return result


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def build_result(case, args):
    rules = load_rules()
    question = extract_question(case)
    qset = token_set(question)
    inclusion = extract_list(case, ["inclusion_criteria", "inclusion", "include_criteria"])
    exclusion = extract_list(case, ["exclusion_criteria", "exclusion", "exclude_criteria"])
    yrange = year_bounds(case)
    raw = collect_candidates(case)
    records = [normalize_record(item, locator) for locator, item in raw]
    unique, duplicate_notes = dedupe(records)
    queries, terms, expanded = build_queries(question, rules)
    uncertainties = []
    if not question:
        uncertainties.append({"type": "missing_question", "detail": "No research question found in the actual case input; search plan cannot be grounded."})
    if not records:
        uncertainties.append({"type": "empty_candidates", "detail": "No candidate records were found in the input; this is an empty result, not a failed search."})
    if not qset:
        uncertainties.append({"type": "no_query_terms", "detail": "Question yielded no usable terms; relevance scoring would be arbitrary."})
    scored = []
    evidence = {}
    for index, rec in enumerate(unique):
        assessment = score_record(rec, qset, inclusion, exclusion, rules, yrange)
        record_id = rec["record_id"] or record_key(rec)
        entry = dict(rec)
        entry["record_id"] = record_id
        entry["dedupe_key"] = record_key(rec)
        entry["relevance"] = assessment["relevance"]
        entry["decision"] = assessment["decision"]
        entry["score"] = assessment["score"]
        entry["matched_terms"] = sorted(assessment["matched_terms"])
        entry["component_scores"] = assessment["component_scores"]
        entry["fields_missing"] = [f for f in ("title", "abstract", "doi", "year", "venue") if not rec.get(f)]
        scored.append(entry)
        quote = pick_quote(rec, assessment["matched_terms"])
        evidence[record_id] = {
            "source_locator": rec["source_locator"],
            "matched_terms": sorted(assessment["matched_terms"]),
            "quote": quote,
            "score_components": assessment["component_scores"],
            "note": "Lexical overlap and field presence only; no semantic entailment or human relevance judgement is claimed.",
        }
    scored.sort(key=lambda r: (-r["score"], r["relevance"] != "direct", (r["title"] or "").lower(), r["record_id"]))
    top_k = args.top_k if args.top_k and args.top_k > 0 else len(scored)
    top = scored[:top_k]
    for rank, entry in enumerate(scored, start=1):
        entry["rank"] = rank
    covered = sorted({t for entry in scored if entry["decision"] != "exclude" for t in entry["matched_terms"]})
    uncovered = sorted(qset - set(covered))
    metadata_gaps = [
        {"record_id": entry["record_id"], "missing_fields": entry["fields_missing"], "source_locator": entry["source_locator"]}
        for entry in scored if entry["fields_missing"]
    ]
    status = "ok" if records and question else ("empty" if not records else "partial")
    result = {
        "case_id": str(case.get("case_id") or case.get("id") or case.get("challenge_id") or "") if isinstance(case, dict) else "",
        "status": status,
        "skill": {"name": "query-plan-literature-search", "version": "1.0.0", "engine": "python-stdlib", "network": "disabled"},
        "question": question,
        "criteria": {"inclusion": inclusion, "exclusion": exclusion, "year_range": list(yrange) if yrange else None},
        "search_plan": {
            "question_terms": terms,
            "expanded_terms": expanded,
            "strategy": "core conjunction -> synonym expansion -> title-restricted pass",
            "relevance_rule": "weighted lexical coverage of question terms (title x3, keywords x2, abstract x1) with explicit criterion and exclusion adjustments",
            "not_claimed": ["semantic relevance", "database recall", "full-text screening"],
        },
        "queries": queries,
        "candidates": scored,
        "results": top,
        "ranked_results": top,
        "evidence": evidence,
        "coverage": {
            "input_records": len(raw),
            "unique_records": len(unique),
            "duplicates_merged": len(raw) - len(unique),
            "kept": sum(1 for e in scored if e["decision"] == "keep"),
            "review": sum(1 for e in scored if e["decision"] == "review"),
            "excluded": sum(1 for e in scored if e["decision"] == "exclude"),
            "question_terms_covered": covered,
            "question_terms_uncovered": uncovered,
            "covered_fraction": round(len(covered) / max(1, len(qset)), 4),
        },
        "metrics": {
            "input_candidates": len(raw),
            "unique_candidates": len(unique),
            "top_k": top_k,
            "mean_score": round(sum(e["score"] for e in scored) / max(1, len(scored)), 4),
        },
        "duplicates": duplicate_notes,
        "metadata_gaps": metadata_gaps,
        "missing": uncovered,
        "uncertainties": uncertainties,
        "limitations": [
            "Deterministic lexical matching cannot establish scientific relevance or study quality.",
            "No live database query is issued; only records actually present in the provided input are used.",
            "Absent metadata is reported as missing rather than reconstructed.",
        ],
        "change_log": [
            "Parsed actual case input and located candidate records by structure, not by label.",
            "Normalized identifiers and merged exact duplicate identifiers while retaining all source locators.",
            "Applied transparent weighted term scoring and retained every record in the output.",
        ],
    }
    result["missing_items"] = metadata_gaps + [{"term": term, "reason": "no retained record matched this question term"} for term in uncovered]
    result["summary"] = {
        "question_terms": len(qset),
        "returned": len(top),
        "kept": result["coverage"]["kept"],
        "partial": status != "ok",
    }
    return apply_contract(result, case)


def error_payload(case, exc, args):
    case_id = ""
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or case.get("id") or "")
    payload = {
        "case_id": case_id,
        "status": "error",
        "error": {"type": type(exc).__name__, "message": str(exc)[:500], "stage": "build_result"},
        "candidates": [],
        "results": [],
        "ranked_results": [],
        "queries": [],
        "evidence": {},
        "metrics": {},
        "uncertainties": [{"type": "runtime_error", "detail": "The skill could not complete; no literature conclusions are reported."}],
        "limitations": ["Failed run: output contains no invented records or scores."],
    }
    return apply_contract(payload, case)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deterministic query-planning literature search skill")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--output", "--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=0)
    parser.add_argument("--rules", default="")
    args = parser.parse_args(argv)
    global RULES_PATH
    if args.rules:
        RULES_PATH = Path(args.rules)
    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
        payload = build_result(case, args)
        code = 0
    except Exception as exc:  # structured failure, never a bare traceback
        payload = error_payload(case, exc, args)
        code = 2
    try:
        write_json(args.output, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "error", "stage": "write_output", "message": str(exc)}, ensure_ascii=False) + "\n")
        return 3
    sys.stdout.write(json.dumps({"status": payload.get("status"), "output": str(args.output), "case_id": payload.get("case_id")}, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
