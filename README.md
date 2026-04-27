# Timesheet MCP Server

An MCP (Model Context Protocol) server that lets you log work hours to a Google Sheets timesheet using natural language through Claude or any MCP-compatible client.

Say things like _"log 8 hours of work today on the inference pipeline"_ or _"I took yesterday off"_ and the server handles the rest — mapping your input to the right sheet columns, dates, and dropdown values.

## Features

- **Natural language time logging** — describe your day and let the AI figure out the row breakdown
- **Read, append, update, and delete** timesheet entries
- **Per-user context** — configure your name, default client/project, and location once; the server fills them in automatically
- **Flexible date parsing** — supports `20-Apr-26`, `2026-04-20`, `yesterday`, and more
- **Google Sheets integration** — writes directly to a shared team spreadsheet via a service account

## Tools

| Tool | Description |
|---|---|
| `get_user_context` | Returns the user's configured profile (name, client, project, location) |
| `set_user_context` | Creates or updates the user's profile |
| `get_today_entries` | Reads existing entries for a given date |
| `append_timesheet_rows` | Appends one or more time entries to the sheet |
| `update_timesheet_row` | Updates a specific row by row number |
| `delete_timesheet_row` | Clears a row and resets it to placeholder state |

## Setup

### Prerequisites

- Python 3.10+
- A Google Cloud service account with access to your timesheet spreadsheet
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) or another MCP client

### 1. Clone and install dependencies

```bash
git clone https://github.com/<your-username>/timesheet-mcp.git
cd timesheet-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

Create a `.env` file:

```
SHEET_ID=<your-google-sheet-id>
GOOGLE_CREDS_PATH=/path/to/service-account.json
```

Place your Google Cloud service account JSON key at the path specified above.

### 3. Set up your user context

On first use, the AI will call `set_user_context` to save your profile to `context.json`. You can also create it manually:

```json
{
  "employee": "Your Name",
  "default_client": "ClientName",
  "default_project": "ProjectName",
  "default_location": "In Office",
  "notes": "Brief description of your role and typical work.",
  "projects": [
    {
      "client": "ClientName",
      "project": "ProjectName",
      "description": "Primary project"
    }
  ]
}
```

### 4. Register with Claude Code

```bash
claude mcp add timesheet -- /path/to/timesheet-mcp/.venv/bin/python /path/to/timesheet-mcp/server.py
```

## Sheet Structure

The server expects a Google Sheet with:

- **Monthly tabs** named like `April 26`, `May 26`, etc.
- **Headers in row 4**: Date, Day, Month, Location, Time Spent Hrs, Client, Project, Type, Description of Work, Employee
- **Placeholder rows** with `"Please Fill Account"` in the Client column, which the server overwrites when appending

### Valid dropdown values

**Location**: `In Office` | `WFH` | `Client Site` | `NA`

**Type**: `Work Time` | `Break from Work` | `StandUp` | `Internal Call` | `Client Call` | `Team Event` | `Interview` | `Holiday` | `Leave`

## Default Day Breakdown

Unless told otherwise, a normal workday is logged as:

| Type | Hours |
|---|---|
| Work Time | 7.5 |
| Break from Work | 1.0 |
| StandUp | 0.5 |

## License

MIT
