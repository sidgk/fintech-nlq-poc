"""
Google Sheets export. Creates spreadsheets in YOUR Drive (OAuth) and writes the
resolver's rows into tabs. Numbers are written raw; you format/chart in Sheets.
"""

from googleapiclient.discovery import build

from google_auth import get_credentials


def _services():
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _safe_title(s: str, maxlen: int = 90) -> str:
    s = (s or "Sheet").strip().replace("\n", " ")
    return s[:maxlen] or "Sheet"


def _rows_to_values(rows: list) -> list:
    """[{'a.b':1,...}] -> [[headers], [row], ...] with short column names."""
    if not rows:
        return [["(no data)"]]
    headers = list(rows[0].keys())
    short = [h.split(".")[-1] for h in headers]
    values = [short]
    for r in rows:
        values.append([r.get(h, "") for h in headers])
    return values


def _existing_tabs(svc, spreadsheet_id: str) -> list:
    meta = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def _unique_tab(svc, spreadsheet_id: str, base: str) -> str:
    existing = set(_existing_tabs(svc, spreadsheet_id))
    if base not in existing:
        return base
    i = 2
    while f"{base} ({i})" in existing:
        i += 1
    return f"{base} ({i})"


def _write_tab(svc, spreadsheet_id: str, tab: str, rows: list):
    svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab}'!A1",
        valueInputOption="RAW",
        body={"values": _rows_to_values(rows)},
    ).execute()


def url_for(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def create_spreadsheet(title: str, tab_title: str, rows: list):
    """Create a new spreadsheet with one tab holding the rows. Returns (id, url)."""
    svc = _services()
    tab = _safe_title(tab_title)
    ss = svc.spreadsheets().create(
        body={"properties": {"title": _safe_title(title)},
              "sheets": [{"properties": {"title": tab}}]},
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()
    sid = ss["spreadsheetId"]
    _write_tab(svc, sid, tab, rows)
    return sid, ss["spreadsheetUrl"], tab


def add_tab(spreadsheet_id: str, tab_title: str, rows: list) -> str:
    """Add a new tab (deduping the name) and write rows. Returns the tab name."""
    svc = _services()
    tab = _unique_tab(svc, spreadsheet_id, _safe_title(tab_title))
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
    ).execute()
    _write_tab(svc, spreadsheet_id, tab, rows)
    return tab


def replace_tab(spreadsheet_id: str, tab: str, rows: list) -> str:
    """Clear an existing tab and rewrite it (for refinements)."""
    svc = _services()
    svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'"
    ).execute()
    _write_tab(svc, spreadsheet_id, tab, rows)
    return tab
