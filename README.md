# SANS Index Builder

A small local tool for building your index for SANS/GIAC open-book exams.
It needs only Python 3 (no packages to install) and runs entirely on your machine.

*Unofficial: not affiliated with or endorsed by the SANS Institute or GIAC.*

## Download

Click **Code → Download ZIP** at the top of this page and unzip it, or:

```bash
git clone https://github.com/BinofWilly/sans-index-builder.git
```

If macOS won't open `start.command` the first time, go to **System Settings → Privacy & Security** and click
**Open Anyway**, or start the tool from Terminal as shown below.

## Run

```bash
python3 sans_index.py
```

…or double-click `start.command` (Mac) / `start.bat` (Windows). Your browser opens at http://127.0.0.1:8765.
Stop it with Ctrl+C in the terminal.

## Fast entry

Type **Book**, **Page**, **Term** and **Description**, then press **Enter**. The term and description
clear, while book and page stay so you can keep indexing the same page.

| Key | Action |
| --- | --- |
| Enter | Save the entry |
| ⌥/Alt + ↑ / ↓ | Page +1 / −1 |
| ⌥/Alt + 1…9 | Switch book |
| Esc | Cancel edit / clear the form |
| ⌘/Ctrl + K or `/` | Search |
| Double-click a row | Edit it |

As you type a term, the app shows entries already in your index. Click one to reuse its spelling and description.

## Export

**Export / Import** gives you:

- **Word (.docx)**: alphabetical table with letter headers, rows shaded by book color, and a book legend
- **Print / PDF**: the same layout, ready to print. Turn on "Background graphics" to keep the colors.
- **CSV** and **JSON backup**

Options: merge identical terms into one row (e.g. `Mimikatz | B1: 23, 30; B3: 140`), shade by book,
sort `$MFT` under M instead of under symbols, include or omit descriptions, Letter or A4.

## Import

Import a CSV/TSV file, or paste rows copied straight from Excel / Google Sheets. Column headers like
Term/Topic/Keyword, Book, Page, Description/Notes are recognized. Without a header row, columns are
read as Term, Book, Page, Description. Exact duplicates (same term + book + page) are skipped.

## Using it on your Mac and Windows PC

Each computer runs its own copy of the tool, and both share one data folder through a sync service.

1. **Put the whole tool folder in a synced folder**: iCloud Drive, OneDrive, Dropbox or Google Drive.
   - *iCloud Drive*: on Windows, install **iCloud** from the Microsoft Store and turn on iCloud Drive.
     On the Mac, right-click the folder → **Keep Downloaded**.
   - *OneDrive*: built into Windows 11; install the OneDrive app on the Mac.
2. **Mac**: double-click `start.command`.
3. **Windows**: install Python 3 from [python.org](https://www.python.org/downloads/) (tick **"Add python.exe to PATH"**),
   then double-click `start.bat` in the synced folder.

Each save merges with whatever the other computer has written: the newest edit of each entry wins, deletions
are remembered, and nothing either computer added gets overwritten. An open tab checks for changes every
5 seconds and whenever you switch back to it. It also writes back any of your edits that the sync service
dropped from the file. If the sync service makes a conflicting copy (e.g. `SEC504 2.json`), the tool merges it
back in automatically and keeps the copy in `data/backups/<index>/conflicts/`.

Don't duplicate an index file by hand inside `data/`: a copy that still carries the same index name is treated
as a sync conflict and merged back. To keep the code in one place and only the data in the cloud, start with
`python3 sans_index.py --data "/path/to/synced/folder"`.

If double-clicking `start.command` stops working after syncing (some services drop the "executable" flag), run
`chmod +x start.command` once in Terminal, or start it with `python3 sans_index.py`.

## Your data

- `data/<index>.json`: one file per index (e.g. SEC504, FOR508). Saved automatically as you type.
- `data/backups/<index>/`: a snapshot every 10 minutes while you edit (last 30 kept).
- `data/trash/`: deleted indexes are moved here, never erased.

The server only listens on 127.0.0.1 and rejects requests coming from other websites.

Your index is built from licensed course material, so keep it to yourself. `data/` is listed in `.gitignore`,
so it stays out of git even if you fork this repository.

## License

MIT. See [LICENSE](LICENSE).
