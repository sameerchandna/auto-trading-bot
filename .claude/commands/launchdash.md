# Launch Dashboard

Kill any running dashboard process and relaunch it fresh.

## Steps

### 1. Kill the existing process on port 8050

Run this to find and kill whatever is holding port 8050:

```bash
for /f "tokens=5" %a in ('netstat -aon ^| findstr :8050 ^| findstr LISTENING') do taskkill /F /PID %a
```

On bash/Unix-style shell (Git Bash, WSL):

```bash
pid=$(netstat -ano 2>/dev/null | grep ':8050.*LISTENING' | awk '{print $NF}' | head -1); [ -n "$pid" ] && taskkill //F //PID $pid 2>/dev/null; true
```

Use whichever works. Ignore errors if nothing is running.

### 2. Relaunch the dashboard in the background

From the project root `c:\Users\Sameer\Coding Projects\auto-trading-bot`:

```bash
cd "c:/Users/Sameer/Coding Projects/auto-trading-bot" && nohup python main.py dashboard > /dev/null 2>&1 &
```

### 3. Confirm

Tell the user: "Dashboard relaunched at http://localhost:8050"
