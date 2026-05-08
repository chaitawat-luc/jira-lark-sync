"""Jira REST API v3 client."""
import requests
from requests.auth import HTTPBasicAuth

JIRA_BASE_URL = "https://{domain}"


def _auth(cfg: dict) -> HTTPBasicAuth:
    return HTTPBasicAuth(cfg["JIRA_EMAIL"], cfg["JIRA_TOKEN"])


def _url(cfg: dict, path: str) -> str:
    return f"https://{cfg['JIRA_DOMAIN']}{path}"


def fetch_all_issues(cfg: dict) -> list:
    """Fetch all Epic + Story issues from the Jira project."""
    issues, next_token = [], None
    fields = ["summary", "issuetype", "assignee", "customfield_10015",
              "duedate", "customfield_10174", "parent", "status"]
    while True:
        payload = {
            "jql": f"project={cfg['JIRA_PROJECT']} AND issuetype in (Epic,Story) ORDER BY key ASC",
            "maxResults": 100,
            "fields": fields,
        }
        if next_token:
            payload["nextPageToken"] = next_token
        resp = requests.post(_url(cfg, "/rest/api/3/search/jql"),
                             json=payload, auth=_auth(cfg))
        resp.raise_for_status()
        data = resp.json()
        issues.extend(data.get("issues", []))
        if data.get("isLast", True) or not data.get("issues"):
            break
        next_token = data.get("nextPageToken")
    return issues


def get_account_ids(cfg: dict) -> dict:
    """Return {displayName: accountId} for all assignees in the project."""
    resp = requests.post(_url(cfg, "/rest/api/3/search/jql"),
                         json={"jql": f"project={cfg['JIRA_PROJECT']} AND assignee is not EMPTY",
                               "maxResults": 200, "fields": ["assignee"]},
                         auth=_auth(cfg))
    resp.raise_for_status()
    return {i["fields"]["assignee"]["displayName"]: i["fields"]["assignee"]["accountId"]
            for i in resp.json().get("issues", [])
            if i["fields"].get("assignee")}


def create_issue(cfg: dict, issuetype: str, summary: str,
                 start_date=None, due_date=None,
                 assignee_id=None, parent_key=None) -> str:
    fields = {
        "project": {"key": cfg["JIRA_PROJECT"]},
        "issuetype": {"name": issuetype},
        "summary": summary,
    }
    if start_date:   fields["customfield_10015"] = start_date
    if due_date:     fields["duedate"] = due_date
    if assignee_id:  fields["assignee"] = {"id": assignee_id}
    if parent_key:   fields["parent"] = {"key": parent_key}
    resp = requests.post(_url(cfg, "/rest/api/3/issue"),
                         json={"fields": fields}, auth=_auth(cfg))
    resp.raise_for_status()
    return resp.json()["key"]


def update_issue(cfg: dict, key: str, fields: dict) -> None:
    if not fields:
        return
    resp = requests.put(_url(cfg, f"/rest/api/3/issue/{key}"),
                        json={"fields": fields}, auth=_auth(cfg))
    resp.raise_for_status()


def delete_issue(cfg: dict, key: str) -> None:
    resp = requests.delete(_url(cfg, f"/rest/api/3/issue/{key}"),
                           auth=_auth(cfg))
    resp.raise_for_status()


def move_story(cfg: dict, story_key: str, new_parent_key: str) -> None:
    update_issue(cfg, story_key, {"parent": {"key": new_parent_key}})
