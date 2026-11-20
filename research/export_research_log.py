#!/usr/bin/env python3
"""Export the bot's sanitized research log for offline analysis.

Usage: python3 research/export_research_log.py DB_PATH OUTPUT.csv
The database is opened read-only and is never modified.
"""
import csv, json, sqlite3, sys

def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: export_research_log.py DB_PATH OUTPUT.csv')
    db_path, out_path = sys.argv[1:]
    db = sqlite3.connect('file:' + db_path + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute('SELECT at,kind,payload FROM research_log ORDER BY at,id')
    with open(out_path, 'w', newline='', encoding='utf-8') as out:
        writer = csv.writer(out)
        writer.writerow(['timestamp_utc_epoch', 'kind', 'ticker', 'payload_json'])
        for row in rows:
            payload = json.loads(row['payload'])
            writer.writerow([row['at'], row['kind'], payload.get('ticker', ''), row['payload']])
    db.close()
    print(out_path)

if __name__ == '__main__':
    main()
