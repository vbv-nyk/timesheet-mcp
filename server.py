import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gspread

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("timesheet")

SHEET_ID = os.environ["SHEET_ID"]
CREDS_PATH = os.environ["GOOGLE_CREDS_PATH"]
CONTEXT_PATH = os.path.join(os.path.dirname(__file__), "context.json")

# Fallback defaults from .env (overridden by context.json when present)
_ENV_EMPLOYEE = os.environ.get("EMPLOYEE_NAME", "")
_ENV_CLIENT   = os.environ.get("DEFAULT_CLIENT", "")
_ENV_PROJECT  = os.environ.get("DEFAULT_PROJECT", "")
_ENV_LOCATION = os.environ.get("DEFAULT_LOCATION", "In Office")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gc():
    return gspread.service_account(filename=CREDS_PATH)


def _tab_name(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.strftime('%y')}"


def _fmt_date(dt: datetime) -> str:
    return f"{dt.day}-{dt.strftime('%b')}-{dt.strftime('%y')}"


def _parse_date(date_str: str | None) -> datetime:
    if not date_str:
        return datetime.today()
    s = date_str.strip().lower()
    if s == "yesterday":
        return datetime.today() - timedelta(days=1)
    for fmt in ["%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse date '{date_str}'. Use formats like '20-Apr-26', '2026-04-20', or 'yesterday'."
    )


def _load_context() -> dict:
    if os.path.exists(CONTEXT_PATH):
        with open(CONTEXT_PATH) as f:
            return json.load(f)
    return {}


def _effective(ctx: dict, key: str, env_val: str) -> str:
    return ctx.get(key) or env_val or ""


def _normalize_date_str(s: str) -> str:
    """Normalize a date string to the same format as _fmt_date (no leading zero in day)."""
    for fmt in ["%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"]:
        try:
            return _fmt_date(datetime.strptime(s.strip(), fmt))
        except ValueError:
            continue
    return s.strip()


_PLACEHOLDER_CLIENTS  = {"please fill account", "plese fill account"}
_PLACEHOLDER_PROJECTS = {"please fill project", "plese fill project"}


def _is_placeholder(row) -> bool:
    client  = row[5].strip().lower() if len(row) > 5 else ""
    project = row[6].strip().lower() if len(row) > 6 else ""
    return client in _PLACEHOLDER_CLIENTS or project in _PLACEHOLDER_PROJECTS


def _purge_placeholder_rows(ws, all_vals) -> list:
    """Clear all rows that contain placeholder client/project values."""
    row_numbers = [
        i + 5
        for i, row in enumerate(all_vals[4:])
        if row and _is_placeholder(row)
    ]
    if row_numbers:
        ranges = [f"A{r}:J{r}" for r in row_numbers]
        ws.batch_clear(ranges)
        for r in row_numbers:
            all_vals[4 + r - 5] = []
    return all_vals


def _find_next_available_row(ws, date_str: str, needed: int = 1) -> int:
    """1-based row number of the next available row for the given date.

    A row is available if its Type column (H, index 7) is empty. Type is always
    explicitly set when logging, so an empty Type means the row is unused.

    Placeholder rows (old buggy "Please Fill Account" junk) are purged first.

    Priority:
    1. Rows pre-populated with the target date that are not yet filled.
    2. Truly blank rows (no date at all).
    3. Append new rows if the sheet has no room.
    """
    all_vals = ws.get_all_values()
    all_vals = _purge_placeholder_rows(ws, all_vals)

    def _is_available(row):
        entry_type = row[7].strip() if len(row) > 7 else ""
        return not entry_type

    date_rows = [
        i + 5
        for i, row in enumerate(all_vals[4:])
        if row and _normalize_date_str(row[0]) == date_str and _is_available(row)
    ]
    if len(date_rows) >= needed:
        return date_rows[0]

    blank_rows = [
        i + 5
        for i, row in enumerate(all_vals[4:])
        if not row or not row[0].strip()
    ]
    combined = date_rows + blank_rows
    if len(combined) >= needed:
        return combined[0]

    shortfall = needed - len(combined)
    ws.add_rows(shortfall)
    return combined[0] if combined else len(all_vals) + 1


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_user_context() -> str:
    """
    Return this user's configured context: name, role, clients, projects, and work description.

    Call this at the start of a conversation to understand who the user is and which
    client/project values to default to when logging entries.
    """
    ctx = _load_context()

    employee = _effective(ctx, "employee", _ENV_EMPLOYEE)
    role     = ctx.get("role", "")
    client   = _effective(ctx, "default_client", _ENV_CLIENT)
    project  = _effective(ctx, "default_project", _ENV_PROJECT)
    location = _effective(ctx, "default_location", _ENV_LOCATION)

    lines = [
        f"Employee : {employee or '(not set)'}",
        f"Role     : {role or '(not set)'}",
        f"Client   : {client or '(not set)'}",
        f"Project  : {project or '(not set)'}",
        f"Location : {location}",
    ]

    if ctx.get("projects"):
        lines.append("\nProjects:")
        for p in ctx["projects"]:
            lines.append(f"  [{p.get('client')} / {p.get('project')}] {p.get('description', '')}")

    if ctx.get("notes"):
        lines.append(f"\nNotes: {ctx['notes']}")

    if not ctx:
        lines.append("\nNo context.json found — call set_user_context to configure this user.")

    return "\n".join(lines)


@mcp.tool()
def set_user_context(
    employee: str = None,
    role: str = None,
    default_client: str = None,
    default_project: str = None,
    default_location: str = None,
    notes: str = None,
    projects: list[dict] = None,
) -> str:
    """
    Save context about who this user is and what they work on.
    Only the fields you provide are updated; others are left unchanged.

    Call this once to set up a new user, or again to update any details.

    `employee`        : name as it should appear in the timesheet (e.g. "Vaibhav")
    `role`            : job title / role (e.g. "Engineer", "Designer", "PM")
    `default_client`  : client they log most time to — must be one of the valid client values
    `default_project` : their primary project — must be a valid project for that client
    `default_location`: usual work location — "In Office" | "WFH" | "Client Site" | "NA"
    `notes`           : free-text description of their work, team, responsibilities,
                        typical activities — the richer this is, the better the AI can
                        map natural language updates to the right entries
    `projects`        : list of all projects they work on, each as
                        {"client": "...", "project": "...", "description": "..."}

    Valid dropdown values (from sheet):
      Location : "In Office" | "WFH" | "Client Site" | "NA"
      Client   : "BB" | "IPRD" | "ECS" | "Satch" | "Seastar" | "ApraLabs" |
                 "HealthFactor" | "Jaystar" | "GoS"
      Project per client:
        BB          → BluSKY-Web, AIC, PR, Sensor Farm, FirmWare, BBNVR, UDC, LVSM, QA, Automation
        IPRD        → CTBW, CCBW, RegenMed
        ECS         → Axis_Dashboard, RQM, RDC, Edge Vision
        Satch       → Timphany, MFI
        Seastar     → Panomor
        ApraLabs    → Edge Vision, Aprapipes
        GoS         → Retainer
        HealthFactor→ Support
        Jaystar     → Jaystar Dev
    """
    ctx = _load_context()

    if employee is not None:
        ctx["employee"] = employee
    if role is not None:
        ctx["role"] = role
    if default_client is not None:
        ctx["default_client"] = default_client
    if default_project is not None:
        ctx["default_project"] = default_project
    if default_location is not None:
        ctx["default_location"] = default_location
    if notes is not None:
        ctx["notes"] = notes
    if projects is not None:
        ctx["projects"] = projects

    with open(CONTEXT_PATH, "w") as f:
        json.dump(ctx, f, indent=2)

    return f"Context saved for {ctx.get('employee', '(unknown)')}."


@mcp.tool()
def get_today_entries(date: str = None) -> str:
    """
    Read existing timesheet entries for a given date (default: today).
    Always call this before append_timesheet_rows to avoid logging duplicates.
    Row numbers shown as [row N] are used by update_timesheet_row and delete_timesheet_row.

    `date`: optional date string like "20-Apr-26" or "yesterday". Defaults to today.
    """
    dt = _parse_date(date)
    target = _fmt_date(dt)
    gc = _gc()
    ws = gc.open_by_key(SHEET_ID).worksheet(_tab_name(dt))

    all_vals = ws.get_all_values()
    headers = all_vals[3]
    matching = [
        (i + 5, dict(zip(headers, row)))
        for i, row in enumerate(all_vals[4:])
        if row and row[0].strip() == target
    ]

    if not matching:
        return f"No entries yet for {target}."

    lines = [f"Existing entries for {target}:"]
    for row_num, r in matching:
        lines.append(
            f"  [row {row_num}] {r.get('Type', '')}: {r.get('Time Spent Hrs', '')}h"
            f"  {r.get('Description of Work', '')}"
        )
    return "\n".join(lines)


@mcp.tool()
def append_timesheet_rows(rows: list[dict], date: str = None) -> str:
    """
    Append one or more rows to the timesheet Google Sheet.

    Always call get_today_entries first to check for duplicates.
    Call get_user_context at the start of a session to know the right defaults.

    Each row must have:
      - type (str)         : entry type — see valid values below
      - hours (float)      : time spent
      - description (str)  : what was done — empty string fine for Break/StandUp
      - client (str, opt)  : defaults to user's default_client from context
      - project (str, opt) : defaults to user's default_project from context
      - location (str, opt): defaults to user's default_location from context

    Valid field values (from sheet dropdowns):
      Location : "In Office" | "WFH" | "Client Site" | "NA"
      Type     : "Work Time" | "Break from Work" | "StandUp" | "Internal Call" |
                 "Client Call" | "Team Event" | "Interview" | "Holiday" | "Leave"
      Client   : "BB" | "IPRD" | "ECS" | "Satch" | "Seastar" | "ApraLabs" |
                 "HealthFactor" | "Jaystar" | "GoS"
      Project  : depends on client — call get_user_context to see the user's projects

    DEFAULT breakdown for a normal office workday (use unless user says otherwise):
      1. Work Time        — 7.5h — description = summary of what they worked on
      2. Break from Work  — 1.0h — description = ""
      3. StandUp          — 0.5h — description = "" (or brief summary if topics mentioned)

    Add "Internal Call" rows for internal meetings (e.g. "1h weekly with Akhil").
    Add "Client Call" rows for external client meetings.
    For a half-day : Work Time = 3.5h, Break = 0.5h.
    For Leave      : single row, type="Leave", hours=9, location="NA".
    For Holiday    : single row, type="Holiday", hours=9.

    `date`: date string like "20-Apr-26", "2026-04-20", or "yesterday". Defaults to today.
    """
    ctx = _load_context()
    def_client   = _effective(ctx, "default_client", _ENV_CLIENT)
    def_project  = _effective(ctx, "default_project", _ENV_PROJECT)
    def_location = _effective(ctx, "default_location", _ENV_LOCATION) or "In Office"

    dt = _parse_date(date)
    gc = _gc()
    ws = gc.open_by_key(SHEET_ID).worksheet(_tab_name(dt))

    date_str = _fmt_date(dt)
    next_row = _find_next_available_row(ws, date_str, needed=len(rows))

    day_name   = dt.strftime("%A")   # e.g. "Tuesday"
    month_name = dt.strftime("%B")   # e.g. "May"
    employee   = _effective(ctx, "employee", _ENV_EMPLOYEE)

    for i, row in enumerate(rows):
        r = next_row + i
        ws.update(
            values=[[
                date_str,
                day_name,
                month_name,
                row.get("location", def_location),
                row["hours"],
                row.get("client", def_client),
                row.get("project", def_project),
                row["type"],
                row.get("description", ""),
                row.get("employee", employee),
            ]],
            range_name=f"A{r}:J{r}",
            value_input_option="USER_ENTERED",
        )

    return f"Added {len(rows)} rows to '{_tab_name(dt)}' tab for {date_str}."


@mcp.tool()
def update_timesheet_row(
    row_number: int,
    date: str = None,
    type: str = None,
    hours: float = None,
    description: str = None,
    location: str = None,
    client: str = None,
    project: str = None,
    employee: str = None,
) -> str:
    """
    Update an existing timesheet entry by its row number.

    Always call get_today_entries first — row numbers appear as [row N] in the output.
    Only supply the fields you want to change; unspecified fields keep their current values.

    `row_number`: sheet row number from get_today_entries output.
    `date`      : date string like "20-Apr-26" or "yesterday". Defaults to today (selects tab).
    `type`      : "Work Time" | "Break from Work" | "StandUp" | "Internal Call" |
                  "Client Call" | "Team Event" | "Interview" | "Holiday" | "Leave"
    `hours`     : time spent (float)
    `description`: what was done
    `location`  : "In Office" | "WFH" | "Client Site" | "NA"
    `client`    : "BB" | "IPRD" | "ECS" | "Satch" | "Seastar" | "ApraLabs" |
                  "HealthFactor" | "Jaystar" | "GoS"
    `project`   : must be valid for the given client
    `employee`  : employee name as it should appear in the timesheet
    """
    dt = _parse_date(date)
    gc = _gc()
    ws = gc.open_by_key(SHEET_ID).worksheet(_tab_name(dt))

    row_data = ws.row_values(row_number)
    while len(row_data) < 10:
        row_data.append("")

    # 0-based column indices: A=0, B=1, C=2, D=3, E=4, F=5, G=6, H=7, I=8, J=9
    if location is not None:
        row_data[3] = location
    if hours is not None:
        row_data[4] = hours
    if client is not None:
        row_data[5] = client
    if project is not None:
        row_data[6] = project
    if type is not None:
        row_data[7] = type
    if description is not None:
        row_data[8] = description
    if employee is not None:
        row_data[9] = employee
    ws.update(
        values=[[row_data[0], row_data[1], row_data[2], row_data[3], row_data[4],
                 row_data[5], row_data[6], row_data[7], row_data[8], row_data[9]]],
        range_name=f"A{row_number}:J{row_number}",
        value_input_option="USER_ENTERED",
    )
    return f"Updated row {row_number}."


@mcp.tool()
def delete_timesheet_row(row_number: int, date: str = None) -> str:
    """
    Delete an existing timesheet entry by its row number.

    Always call get_today_entries first — row numbers appear as [row N] in the output.
    The row is cleared and restored to placeholder state so it can be reused.

    `row_number`: sheet row number from get_today_entries output.
    `date`      : date string like "20-Apr-26" or "yesterday". Defaults to today (selects tab).
    """
    dt = _parse_date(date)
    gc = _gc()
    ws = gc.open_by_key(SHEET_ID).worksheet(_tab_name(dt))

    ws.batch_clear([f"A{row_number}:J{row_number}"])
    return f"Deleted row {row_number}."


if __name__ == "__main__":
    mcp.run()
