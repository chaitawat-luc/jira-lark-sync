"""
Core sync logic — 4 independent operations.
Each returns a SyncResult with counts.
"""
import re
import difflib
from dataclasses import dataclass, field
from datetime import datetime
import lark_api
import jira_api

# ── Field names in Lark Base ──────────────────────────────────────────────────
F_TITLE     = "Title"
F_START     = "Timeline - Start"
F_END       = "Timeline - End"
F_PROGRESS  = "Progress"
F_ASSIGNEE  = "Assignee"
F_JIRA_KEY  = "Jira Key"
F_JIRA_URL  = "Jira URL"
F_TYPE      = "Type"
F_PARENT    = "Parent items"

JIRA_TO_LARK_ASSIGNEE = {
    "Tawan Vongsombun":        "Tawan",
    "Thet Swe Lin":            "Lin",
    "Benyapha Kasemtanakitti": "Nurse",
    "Moe Pyae Pyae Kyaw":      "Iris",
    "Waritsara Matnok":        "Min",
}
LARK_TO_JIRA_ASSIGNEE = {v: k for k, v in JIRA_TO_LARK_ASSIGNEE.items()}


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors:  list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors":  self.errors,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _fuzzy(needle: str, candidates: list, threshold=0.75) -> "str | None":
    best, best_score = None, 0.0
    for c in candidates:
        score = difflib.SequenceMatcher(None, _norm(c), _norm(needle)).ratio()
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= threshold else None


def _jira_date_to_lark_ts(date_str: "str | None") -> "int | None":
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _lark_ts_to_jira_date(ts_ms) -> "str | None":
    if not ts_ms:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return None


def _parse_progress(val):
    if val is None:
        return None
    try:
        f = float(str(val).replace("%", "").strip())
        return f if f <= 1 else f / 100.0
    except Exception:
        return None


def _lark_text(field_val) -> "str | None":
    """Lark text fields return as list of text objects or plain string."""
    if field_val is None:
        return None
    if isinstance(field_val, str):
        return field_val or None
    if isinstance(field_val, list):
        parts = []
        for item in field_val:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts) or None
    return str(field_val) or None


def _lark_select(field_val) -> "str | None":
    """Lark select fields return as plain string, {id, text} dict, or list."""
    if field_val is None:
        return None
    if isinstance(field_val, str):
        return field_val or None
    if isinstance(field_val, dict):
        return field_val.get("text") or field_val.get("name")
    if isinstance(field_val, list) and field_val:
        item = field_val[0]
        return item.get("text") or item.get("name") if isinstance(item, dict) else str(item)
    return None


def _lark_link_titles(field_val) -> list:
    """Parent items field returns list of linked record objects."""
    if not field_val:
        return []
    if isinstance(field_val, list):
        titles = []
        for item in field_val:
            if isinstance(item, dict):
                t = item.get("text") or item.get("link_record_title", "")
                titles.append(t)
        return titles
    return []


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_lark(cfg: dict) -> list:
    token = lark_api.get_token(cfg["LARK_APP_ID"], cfg["LARK_APP_SECRET"])
    return lark_api.fetch_all_records(token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"])


def _load_jira(cfg: dict) -> list:
    return jira_api.fetch_all_issues(cfg)


def _lark_token(cfg: dict) -> str:
    return lark_api.get_token(cfg["LARK_APP_ID"], cfg["LARK_APP_SECRET"])


# ── Operation 1: Jira Issues → Lark (create/delete) ─────────────────────────

def sync_jira_issues_to_lark(cfg: dict) -> SyncResult:
    """
    - Jira issue exists, no Lark record → create Lark record
    - Lark record has Jira Key, but that key no longer exists in Jira → delete Lark record
    """
    result = SyncResult()
    token = _lark_token(cfg)

    lark_records = _load_lark(cfg)
    jira_issues  = _load_jira(cfg)

    jira_keys = {i["key"] for i in jira_issues}
    jira_by_key = {i["key"]: i for i in jira_issues}

    # Index Lark records by Jira Key (only typed records)
    lark_by_jira_key = {}
    for rec in lark_records:
        t = _lark_select(rec["fields"].get(F_TYPE))
        if not t:
            continue
        jk = _lark_text(rec["fields"].get(F_JIRA_KEY))
        if jk:
            lark_by_jira_key[jk] = rec

    # Delete Lark records whose Jira issue no longer exists
    for jk, rec in lark_by_jira_key.items():
        if jk not in jira_keys:
            try:
                lark_api.delete_record(token, cfg["LARK_BASE_TOKEN"],
                                       cfg["LARK_TABLE_ID"], rec["record_id"])
                result.deleted += 1
            except Exception as e:
                result.errors.append(f"Delete Lark {rec['record_id']}: {e}")

    # Create Lark records for Jira issues not yet in Lark
    for key in jira_keys:
        if key in lark_by_jira_key:
            result.skipped += 1
            continue
        issue = jira_by_key[key]
        jf = issue["fields"]
        assignee_name = (jf.get("assignee") or {}).get("displayName")
        itype = jf["issuetype"]["name"]  # "Epic" or "Story"
        fields = {
            F_TITLE:    jf.get("summary", ""),
            F_JIRA_KEY: key,
            F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{key}",
            F_TYPE:     itype,
        }
        if assignee_name and assignee_name in JIRA_TO_LARK_ASSIGNEE:
            fields[F_ASSIGNEE] = [JIRA_TO_LARK_ASSIGNEE[assignee_name]]
        start = _jira_date_to_lark_ts(jf.get("customfield_10015"))
        end   = _jira_date_to_lark_ts(jf.get("duedate"))
        prog  = _parse_progress(jf.get("customfield_10174"))
        if start is not None: fields[F_START]    = start
        if end is not None:   fields[F_END]      = end
        if prog is not None:  fields[F_PROGRESS] = prog
        try:
            lark_api.create_record(token, cfg["LARK_BASE_TOKEN"],
                                   cfg["LARK_TABLE_ID"], fields)
            result.created += 1
        except Exception as e:
            result.errors.append(f"Create Lark for {key}: {e}")

    return result


# ── Operation 2: Jira Progress & Assignee → Lark ────────────────────────────

def sync_jira_progress_assignee_to_lark(cfg: dict) -> SyncResult:
    """
    For each matched pair: push Jira progress + assignee → Lark.
    Jira wins for both fields.
    """
    result = SyncResult()
    token = _lark_token(cfg)

    lark_records = _load_lark(cfg)
    jira_issues  = _load_jira(cfg)
    jira_by_key  = {i["key"]: i for i in jira_issues}

    for rec in lark_records:
        t = _lark_select(rec["fields"].get(F_TYPE))
        if not t:
            continue
        jk = _lark_text(rec["fields"].get(F_JIRA_KEY))
        if not jk or jk not in jira_by_key:
            result.skipped += 1
            continue

        jf = jira_by_key[jk]["fields"]
        updates = {}

        # Progress: Jira wins (only update if Jira has a value)
        jira_prog = _parse_progress(jf.get("customfield_10174"))
        lark_prog = _parse_progress(rec["fields"].get(F_PROGRESS))
        if jira_prog is not None and jira_prog != lark_prog:
            updates[F_PROGRESS] = jira_prog

        # Assignee: Jira wins
        assignee_name = (jf.get("assignee") or {}).get("displayName")
        lark_target = JIRA_TO_LARK_ASSIGNEE.get(assignee_name) if assignee_name else None
        lark_current = _lark_select(rec["fields"].get(F_ASSIGNEE))
        if lark_target != lark_current:
            if lark_target:
                updates[F_ASSIGNEE] = [lark_target]
            else:
                updates[F_ASSIGNEE] = None

        if updates:
            try:
                lark_api.update_record(token, cfg["LARK_BASE_TOKEN"],
                                       cfg["LARK_TABLE_ID"], rec["record_id"], updates)
                result.updated += 1
            except Exception as e:
                result.errors.append(f"Update Lark {rec['record_id']}: {e}")
        else:
            result.skipped += 1

    return result


# ── Operation 3: Lark Issues → Jira (create/delete) ─────────────────────────

def sync_lark_issues_to_jira(cfg: dict) -> SyncResult:
    """
    - Lark record (typed) has no Jira Key → create in Jira + write key back to Lark
    - Lark record has Jira Key that was manually cleared → (skip, only create new ones)
    - Jira issue deleted externally → not handled here (use operation 1 for that direction)
    """
    result = SyncResult()
    token = _lark_token(cfg)

    lark_records = _load_lark(cfg)
    jira_issues  = _load_jira(cfg)
    account_ids  = jira_api.get_account_ids(cfg)

    jira_by_key    = {i["key"]: i for i in jira_issues}
    jira_epics     = {i["key"]: i for i in jira_issues if i["fields"]["issuetype"]["name"] == "Epic"}
    jira_epic_sums = {_norm(i["fields"]["summary"]): k for k, i in jira_epics.items()}

    # Build record_id → jira_key map (for parent resolution)
    lark_id_to_jira_key: dict = {}
    lark_epics, lark_stories = [], []

    for rec in lark_records:
        t = _lark_select(rec["fields"].get(F_TYPE))
        if not t:
            continue
        jk = _lark_text(rec["fields"].get(F_JIRA_KEY))
        if jk:
            lark_id_to_jira_key[rec["record_id"]] = jk
        if t == "Epic":
            lark_epics.append(rec)
        elif t == "Story":
            lark_stories.append(rec)

    # Process Epics first
    for rec in lark_epics:
        rid   = rec["record_id"]
        title = _lark_text(rec["fields"].get(F_TITLE)) or ""
        jk    = _lark_text(rec["fields"].get(F_JIRA_KEY))
        if jk:
            lark_id_to_jira_key[rid] = jk
            result.skipped += 1
            continue

        # Match or create
        matched = jira_epic_sums.get(_norm(title))
        if not matched:
            best = _fuzzy(title, [i["fields"]["summary"] for i in jira_epics.values()])
            if best:
                matched = jira_epic_sums.get(_norm(best))

        if matched:
            lark_id_to_jira_key[rid] = matched
            try:
                lark_api.update_record(token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"],
                                       rid, {F_JIRA_KEY: matched,
                                             F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{matched}"})
                result.updated += 1
            except Exception as e:
                result.errors.append(f"Write Jira Key to Lark {rid}: {e}")
        else:
            assignee_lark = _lark_select(rec["fields"].get(F_ASSIGNEE))
            assignee_id = account_ids.get(LARK_TO_JIRA_ASSIGNEE.get(assignee_lark, ""))
            try:
                new_key = jira_api.create_issue(
                    cfg, "Epic", title or f"[Lark] {rid}",
                    start_date=_lark_ts_to_jira_date(rec["fields"].get(F_START)),
                    due_date=_lark_ts_to_jira_date(rec["fields"].get(F_END)),
                    assignee_id=assignee_id)
                lark_id_to_jira_key[rid] = new_key
                lark_api.update_record(token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"],
                                       rid, {F_JIRA_KEY: new_key,
                                             F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{new_key}"})
                result.created += 1
            except Exception as e:
                result.errors.append(f"Create Jira Epic for {title!r}: {e}")

    # Process Stories
    jira_stories_all = {i["key"]: i for i in jira_issues if i["fields"]["issuetype"]["name"] == "Story"}
    jira_story_sums  = {_norm(i["fields"]["summary"]): k for k, i in jira_stories_all.items()}

    for rec in lark_stories:
        rid   = rec["record_id"]
        title = _lark_text(rec["fields"].get(F_TITLE)) or ""
        jk    = _lark_text(rec["fields"].get(F_JIRA_KEY))

        # Resolve parent Epic key
        parent_titles = _lark_link_titles(rec["fields"].get(F_PARENT))
        correct_epic_key = None
        for rec2 in lark_epics:
            epic_title = _lark_text(rec2["fields"].get(F_TITLE)) or ""
            if any(_norm(epic_title) in _norm(pt) or _norm(pt) in _norm(epic_title)
                   for pt in parent_titles):
                correct_epic_key = lark_id_to_jira_key.get(rec2["record_id"]) or \
                                   _lark_text(rec2["fields"].get(F_JIRA_KEY))
                break

        if not correct_epic_key:
            result.skipped += 1
            continue

        if jk:
            # Already linked — check parent
            story_issue = jira_by_key.get(jk)
            if story_issue:
                current_parent = (story_issue["fields"].get("parent") or {}).get("key")
                if current_parent != correct_epic_key:
                    try:
                        jira_api.move_story(cfg, jk, correct_epic_key)
                        result.updated += 1
                    except Exception as e:
                        result.errors.append(f"Move {jk}: {e}")
                else:
                    result.skipped += 1
            lark_id_to_jira_key[rid] = jk
            continue

        # Not linked — search globally then create
        matched = jira_story_sums.get(_norm(title))
        if not matched:
            best = _fuzzy(title, [i["fields"]["summary"] for i in jira_stories_all.values()])
            if best:
                matched = jira_story_sums.get(_norm(best))

        if matched:
            story_issue = jira_by_key.get(matched)
            current_parent = (story_issue["fields"].get("parent") or {}).get("key") if story_issue else None
            if current_parent != correct_epic_key:
                try:
                    jira_api.move_story(cfg, matched, correct_epic_key)
                    result.updated += 1
                except Exception as e:
                    result.errors.append(f"Move {matched}: {e}")
            try:
                lark_api.update_record(token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"],
                                       rid, {F_JIRA_KEY: matched,
                                             F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{matched}"})
            except Exception as e:
                result.errors.append(f"Write key back to Lark {rid}: {e}")
            lark_id_to_jira_key[rid] = matched
        else:
            assignee_lark = _lark_select(rec["fields"].get(F_ASSIGNEE))
            assignee_id = account_ids.get(LARK_TO_JIRA_ASSIGNEE.get(assignee_lark, ""))
            try:
                new_key = jira_api.create_issue(
                    cfg, "Story", title or f"[Lark] {rid}",
                    start_date=_lark_ts_to_jira_date(rec["fields"].get(F_START)),
                    due_date=_lark_ts_to_jira_date(rec["fields"].get(F_END)),
                    assignee_id=assignee_id,
                    parent_key=correct_epic_key)
                lark_api.update_record(token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"],
                                       rid, {F_JIRA_KEY: new_key,
                                             F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{new_key}"})
                result.created += 1
            except Exception as e:
                result.errors.append(f"Create Jira Story for {title!r}: {e}")

    return result


# ── Operation 4: Lark Start/End Dates → Jira ────────────────────────────────

def sync_lark_dates_to_jira(cfg: dict) -> SyncResult:
    """
    For each matched pair: push Lark Timeline-Start + Timeline-End → Jira.
    Lark wins. If Lark date is empty, leave Jira as-is.
    """
    result = SyncResult()

    lark_records = _load_lark(cfg)
    jira_issues  = _load_jira(cfg)
    jira_by_key  = {i["key"]: i for i in jira_issues}

    for rec in lark_records:
        t = _lark_select(rec["fields"].get(F_TYPE))
        if not t:
            continue
        jk = _lark_text(rec["fields"].get(F_JIRA_KEY))
        if not jk or jk not in jira_by_key:
            result.skipped += 1
            continue

        jf = jira_by_key[jk]["fields"]
        updates = {}

        lark_start = _lark_ts_to_jira_date(rec["fields"].get(F_START))
        if lark_start and lark_start != jf.get("customfield_10015"):
            updates["customfield_10015"] = lark_start

        lark_end = _lark_ts_to_jira_date(rec["fields"].get(F_END))
        if lark_end and lark_end != jf.get("duedate"):
            updates["duedate"] = lark_end

        if updates:
            try:
                jira_api.update_issue(cfg, jk, updates)
                result.updated += 1
            except Exception as e:
                result.errors.append(f"Update Jira {jk} dates: {e}")
        else:
            result.skipped += 1

    return result


# ── Operation 5: Sync Structure (Epics & Stories) ────────────────────────────

def sync_structure(cfg: dict) -> SyncResult:
    """
    Re-runnable first-sync — run anytime Lark structure changes.

    Epics (Type="Epic"):
      - Has Jira Key → skip (already linked)
      - No Jira Key → match by title (exact/fuzzy) or CREATE in Jira
        → Write Jira Key + URL back to Lark

    Stories (Type="Story"):
      - Resolve correct parent Epic key from Lark Parent items
      - Has Jira Key + correct Epic → skip
      - Has Jira Key + wrong Epic → MOVE to correct Epic
      - No Jira Key → search Jira globally, MOVE if found or CREATE
        → Write Jira Key + URL back to Lark
    """
    result = SyncResult()
    token = _lark_token(cfg)

    lark_records = _load_lark(cfg)
    jira_issues  = _load_jira(cfg)
    account_ids  = jira_api.get_account_ids(cfg)

    jira_by_key     = {i["key"]: i for i in jira_issues}
    jira_epics      = {k: i for k, i in jira_by_key.items()
                       if i["fields"]["issuetype"]["name"] == "Epic"}
    jira_stories    = {k: i for k, i in jira_by_key.items()
                       if i["fields"]["issuetype"]["name"] == "Story"}
    jira_epic_sums  = {_norm(i["fields"]["summary"]): k for k, i in jira_epics.items()}
    jira_story_sums = {_norm(i["fields"]["summary"]): k for k, i in jira_stories.items()}

    lark_epics   = [r for r in lark_records if _lark_select(r["fields"].get(F_TYPE)) == "Epic"]
    lark_stories = [r for r in lark_records if _lark_select(r["fields"].get(F_TYPE)) == "Story"]

    # record_id → jira_key, built as we go (stories need parent Epic key)
    rid_to_jira_key = {}
    for r in lark_records:
        jk = _lark_text(r["fields"].get(F_JIRA_KEY))
        if jk:
            rid_to_jira_key[r["record_id"]] = jk

    # ── Step A: Epics ─────────────────────────────────────────────────────────
    for rec in lark_epics:
        rid   = rec["record_id"]
        title = _lark_text(rec["fields"].get(F_TITLE)) or ""
        jk    = _lark_text(rec["fields"].get(F_JIRA_KEY))

        if jk:
            # Already linked — push Lark data to Jira and count correctly
            rid_to_jira_key[rid] = jk
            pushed = _push_lark_fields_to_jira(cfg, jk, rec, account_ids)
            if pushed:
                result.updated += 1
            else:
                result.skipped += 1
            continue

        matched = jira_epic_sums.get(_norm(title))
        if not matched:
            best = _fuzzy(title, [i["fields"]["summary"] for i in jira_epics.values()])
            if best:
                matched = jira_epic_sums.get(_norm(best))

        if matched:
            rid_to_jira_key[rid] = matched
            try:
                lark_api.update_record(
                    token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"], rid,
                    {F_JIRA_KEY: matched,
                     F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{matched}"})
                _push_lark_fields_to_jira(cfg, matched, rec, account_ids)
                result.updated += 1
            except Exception as e:
                result.errors.append(f"Write key to Lark Epic {rid}: {e}")
        else:
            assignee_lark = _lark_select(rec["fields"].get(F_ASSIGNEE))
            assignee_id = account_ids.get(LARK_TO_JIRA_ASSIGNEE.get(assignee_lark, ""))
            try:
                new_key = jira_api.create_issue(
                    cfg, "Epic", title or f"[Lark] {rid}",
                    start_date=_lark_ts_to_jira_date(rec["fields"].get(F_START)),
                    due_date=_lark_ts_to_jira_date(rec["fields"].get(F_END)),
                    assignee_id=assignee_id)
                rid_to_jira_key[rid] = new_key
                lark_api.update_record(
                    token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"], rid,
                    {F_JIRA_KEY: new_key,
                     F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{new_key}"})
                result.created += 1
            except Exception as e:
                result.errors.append(f"Create Jira Epic for {title!r}: {e}")

    # ── Step B: Stories ───────────────────────────────────────────────────────
    for rec in lark_stories:
        rid   = rec["record_id"]
        title = _lark_text(rec["fields"].get(F_TITLE)) or ""
        jk    = _lark_text(rec["fields"].get(F_JIRA_KEY))

        correct_epic_key = _resolve_parent_epic(rec, lark_epics, rid_to_jira_key)
        if not correct_epic_key:
            result.skipped += 1
            continue

        if jk:
            story_issue = jira_by_key.get(jk)
            if story_issue:
                current_parent = (story_issue["fields"].get("parent") or {}).get("key")
                if current_parent != correct_epic_key:
                    try:
                        jira_api.move_story(cfg, jk, correct_epic_key)
                        result.updated += 1
                    except Exception as e:
                        result.errors.append(f"Move {jk} → {correct_epic_key}: {e}")
                else:
                    pushed = _push_lark_fields_to_jira(cfg, jk, rec, account_ids)
                    if pushed:
                        result.updated += 1
                    else:
                        result.skipped += 1
                    rid_to_jira_key[rid] = jk
                    continue
            # Always push Lark field data to Jira (when parent was moved)
            _push_lark_fields_to_jira(cfg, jk, rec, account_ids)
            rid_to_jira_key[rid] = jk
            continue

        matched = jira_story_sums.get(_norm(title))
        if not matched:
            best = _fuzzy(title, [i["fields"]["summary"] for i in jira_stories.values()])
            if best:
                matched = jira_story_sums.get(_norm(best))

        if matched:
            story_issue = jira_by_key.get(matched)
            current_parent = (story_issue["fields"].get("parent") or {}).get("key") if story_issue else None
            if current_parent != correct_epic_key:
                try:
                    jira_api.move_story(cfg, matched, correct_epic_key)
                    result.updated += 1
                except Exception as e:
                    result.errors.append(f"Move {matched}: {e}")
            try:
                lark_api.update_record(
                    token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"], rid,
                    {F_JIRA_KEY: matched,
                     F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{matched}"})
            except Exception as e:
                result.errors.append(f"Write key to Lark Story {rid}: {e}")
            pushed = _push_lark_fields_to_jira(cfg, matched, rec, account_ids)
            if pushed and current_parent == correct_epic_key:
                result.updated += 1
            rid_to_jira_key[rid] = matched
        else:
            assignee_lark = _lark_select(rec["fields"].get(F_ASSIGNEE))
            assignee_id = account_ids.get(LARK_TO_JIRA_ASSIGNEE.get(assignee_lark, ""))
            try:
                new_key = jira_api.create_issue(
                    cfg, "Story", title or f"[Lark] {rid}",
                    start_date=_lark_ts_to_jira_date(rec["fields"].get(F_START)),
                    due_date=_lark_ts_to_jira_date(rec["fields"].get(F_END)),
                    assignee_id=assignee_id,
                    parent_key=correct_epic_key)
                lark_api.update_record(
                    token, cfg["LARK_BASE_TOKEN"], cfg["LARK_TABLE_ID"], rid,
                    {F_JIRA_KEY: new_key,
                     F_JIRA_URL: f"https://{cfg['JIRA_DOMAIN']}/browse/{new_key}"})
                rid_to_jira_key[rid] = new_key
                result.created += 1
            except Exception as e:
                result.errors.append(f"Create Jira Story for {title!r}: {e}")

    return result


def _push_lark_fields_to_jira(cfg: dict, jira_key: str, lark_rec: dict, account_ids: dict) -> bool:
    """Push Lark-owned fields (title, dates) to a Jira issue. Returns True if any update was sent."""
    updates = {}
    title = _lark_text(lark_rec["fields"].get(F_TITLE))
    if title:
        updates["summary"] = title
    start = _lark_ts_to_jira_date(lark_rec["fields"].get(F_START))
    if start:
        updates["customfield_10015"] = start
    end = _lark_ts_to_jira_date(lark_rec["fields"].get(F_END))
    if end:
        updates["duedate"] = end
    if updates:
        try:
            jira_api.update_issue(cfg, jira_key, updates)
            return True
        except Exception:
            pass
    return False


def _resolve_parent_epic(story_rec: dict, lark_epics: list, rid_to_jira_key: dict):
    """Find the Jira key of the parent Epic for a Story, using Lark Parent items."""
    parent_titles = _lark_link_titles(story_rec["fields"].get(F_PARENT))
    if not parent_titles:
        return None
    for epic_rec in lark_epics:
        epic_title = _lark_text(epic_rec["fields"].get(F_TITLE)) or ""
        if epic_title and any(
                _norm(epic_title) in _norm(pt) or _norm(pt) in _norm(epic_title)
                for pt in parent_titles):
            return (rid_to_jira_key.get(epic_rec["record_id"])
                    or _lark_text(epic_rec["fields"].get(F_JIRA_KEY)))
    return None
