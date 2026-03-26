# Plzeň Cyklo-počítadla — widget updater

## Files

| File | Purpose |
|---|---|
| `cyklo-counter.html` | The self-contained widget (deploy this) |
| `template.html` | HTML without data — do not edit the `{{CSV_DATA}}` marker |
| `update.py` | Downloads fresh XLSX, converts, rebuilds the widget |
| `run_update.sh` | Cron-friendly wrapper with logging |

---

## Setup

### 1. Install dependencies

```bash
pip install requests openpyxl
```

### 2. Test a manual run

```bash
python3 update.py
```

You should see output like:
```
2026-03-25 10:00:01  INFO     Downloading XLSX from https://opendata.plzen.eu/...
2026-03-25 10:00:03  INFO     Downloaded 2841.2 KB
2026-03-25 10:00:05  INFO     Converted 56958 data rows
2026-03-25 10:00:05  INFO     Widget written to cyklo-counter.html (2618.4 KB)
2026-03-25 10:00:05  INFO     Done ✓
```

### 3. Optional — use a local XLSX instead of downloading

```bash
python3 update.py --xlsx /path/to/ecocounter.xlsx
```

---

## Cron setup

The city publishes data daily at **04:00**. Run the update at **04:15** to be safe.

```bash
# Edit crontab:
crontab -e
```

Add this line (adjust the path):

```cron
15 4 * * * /path/to/run_update.sh >> /path/to/update.log 2>&1
```

Check the log any time:
```bash
tail -f /path/to/update.log
```

---

## Deploying the widget

`cyklo-counter.html` is a single self-contained file — no server-side logic needed.
Just serve it as a static file, or embed it in an `<iframe>`:

```html
<iframe src="cyklo-counter.html" width="900" height="420"
        frameborder="0" style="border:none;"></iframe>
```

---

## Custom output path

If your web root is e.g. `/var/www/html/`:

```bash
python3 update.py --out /var/www/html/cyklo-counter.html
```

Or set a fixed path in `run_update.sh`:

```bash
"$PYTHON" update.py --out /var/www/html/cyklo-counter.html "$@" >> "$LOG" 2>&1
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ValueError: Could not find columns` | XLSX column names changed — check the log for available columns and update `aliases` in `update.py` |
| `Server returned HTML instead of XLSX` | The download URL has changed — check `https://opendata.plzen.eu/public/opendata/dataset/198` |
| Numbers show as `0` for yesterday | Data not yet published (before 04:00), or date offset logic needs adjustment |
