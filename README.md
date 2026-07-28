# FlightDeck

Local-first dashboard for Claude Code usage, sessions, systems, and missions.

## Run locally on Windows

Requirements: Python 3.12 or newer, Node.js 20 or newer, and PowerShell.

```powershell
.\demo.ps1
```

The first run creates `.venv`, installs backend and frontend dependencies, and
starts:

- Frontend: http://127.0.0.1:5190
- Backend API: http://127.0.0.1:8010

Press `Ctrl+C` in the runner terminal to stop both services. Later runs may
skip dependency installation:

```powershell
.\demo.ps1 -SkipInstall
```

The backend reads Claude Code data from `%USERPROFILE%\.claude\projects` and
stores its local SQLite cache at `backend\audit.local.db`.

## Run locally on Linux

```bash
./demo.sh
```
