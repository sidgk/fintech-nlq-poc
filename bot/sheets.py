"""
Google Sheets export. Creates spreadsheets in YOUR Drive (OAuth), writes the
resolver's rows into tabs, and (on request) draws a column/bar chart with an
optional computed trend line.
"""

from googleapiclient.discovery import build

from google_auth import get_credentials


def _services():
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _safe_title(s: str, maxlen: int = 90) -> str:
    s = (s or "Sheet").strip().replace("\n", " ")
    return s[:maxlen] or "Sheet"


def _num(v):
    """Coerce numeric-looking strings to real numbers so charts can plot them."""
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f == int(f) else round(f, 4)
    except (TypeError, ValueError):
        return v


def _rows_to_values(rows: list) -> list:
    """[{'a.b':1,...}] -> [[headers], [row], ...] with short names + real numbers."""
    if not rows:
        return [["(no data)"]]
    headers = list(rows[0].keys())
    short = [h.split(".")[-1] for h in headers]
    values = [short]
    for r in rows:
        values.append([_num(r.get(h, "")) for h in headers])
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
    svc = _services()
    tab = _unique_tab(svc, spreadsheet_id, _safe_title(tab_title))
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
    ).execute()
    _write_tab(svc, spreadsheet_id, tab, rows)
    return tab


def replace_tab(spreadsheet_id: str, tab: str, rows: list) -> str:
    svc = _services()
    svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'"
    ).execute()
    _write_tab(svc, spreadsheet_id, tab, rows)
    return tab


# ── Charts ──────────────────────────────────────────────────────────────────

def _grid(gid, r0, r1, c0, c1):
    return {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _col_letter(n: int) -> str:
    """0-based column index -> A1 letter (0->A, 26->AA)."""
    s = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _linfit(ys: list) -> list:
    """Least-squares linear fit; returns fitted y for x = 0..n-1 (the trend)."""
    n = len(ys)
    if n < 2:
        return ys[:]
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    return [intercept + slope * x for x in xs]


def _sheet_id(svc, spreadsheet_id, tab):
    meta = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)"
    ).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == tab:
            return s["properties"]["sheetId"]
    return None


def _delete_charts(svc, spreadsheet_id, gid):
    meta = svc.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets(properties.sheetId,charts.chartId)"
    ).execute()
    reqs = []
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == gid:
            for ch in s.get("charts", []) or []:
                reqs.append({"deleteEmbeddedObject": {"objectId": ch["chartId"]}})
    if reqs:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": reqs}
        ).execute()


def add_chart(spreadsheet_id: str, tab: str, with_trend: bool = False,
              horizontal: bool = False) -> bool:
    """Draw a column/bar chart of column B vs column A on `tab`. If with_trend,
    compute a linear trend line and overlay it (combo chart). Returns True/False."""
    svc = _services()
    gid = _sheet_id(svc, spreadsheet_id, tab)
    if gid is None:
        return False

    values = svc.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab}'"
    ).execute().get("values", [])
    if len(values) < 2 or len(values[0]) < 2:
        return False                       # need a header + a dimension + a measure

    nrows = len(values)                    # includes header row
    ncols = len(values[0])
    measure_col = 1                        # first measure column

    _delete_charts(svc, spreadsheet_id, gid)   # avoid stacking on refine

    series = [{"series": {"sourceRange": {"sources": [_grid(gid, 0, nrows, measure_col, measure_col + 1)]}},
               "targetAxis": "LEFT_AXIS", "type": "COLUMN"}]

    if with_trend:
        ys = []
        for row in values[1:]:
            try:
                ys.append(float(row[measure_col]))
            except (IndexError, ValueError, TypeError):
                ys.append(0.0)
        trend = _linfit(ys)
        trend_col = ncols                  # append a 'Trend' column after the data
        svc.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!{_col_letter(trend_col)}1",
            valueInputOption="RAW",
            body={"values": [["Trend"]] + [[round(t, 2)] for t in trend]},
        ).execute()
        series.append({"series": {"sourceRange": {"sources": [_grid(gid, 0, nrows, trend_col, trend_col + 1)]}},
                       "targetAxis": "LEFT_AXIS", "type": "LINE"})

    chart_type = "COMBO" if with_trend else ("BAR" if horizontal else "COLUMN")
    spec = {
        "title": tab[:80],
        "basicChart": {
            "chartType": chart_type,
            "legendPosition": "BOTTOM_LEGEND",
            "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
            "domains": [{"domain": {"sourceRange": {"sources": [_grid(gid, 0, nrows, 0, 1)]}}}],
            "series": series,
        },
    }
    anchor_col = ncols + (1 if with_trend else 0) + 1   # place chart right of the data
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addChart": {"chart": {
            "spec": spec,
            "position": {"overlayPosition": {"anchorCell": {
                "sheetId": gid, "rowIndex": 1, "columnIndex": anchor_col}}},
        }}}]},
    ).execute()
    return True
