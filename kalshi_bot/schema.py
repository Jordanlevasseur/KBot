"""Transactional migration of the former per-market purchase-count constraint."""
import re


def remove_purchase_cap(db):
    row=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='markets'").fetchone()
    if not row:return False
    sql=row[0]
    pattern=r'CHECK\s*\(\s*buys\s*<=\s*2\s*\)'
    if not re.search(pattern,sql,re.I):return False
    if db.in_transaction:raise RuntimeError('Purchase-cap migration requires an idle transaction')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='markets_uncapped'").fetchone():
        raise RuntimeError('Unexpected migration table; manual review required')
    # These journals have no dependent views or foreign keys. Fail closed if
    # another schema introduces one rather than silently breaking its links.
    for kind,name,definition in db.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view')"):
        if kind=='view' and re.search(r'\bmarkets\b',definition or '',re.I):
            raise RuntimeError('Dependent market view requires manual migration')
        if kind=='table':
            quoted='"'+name.replace('"','""')+'"'
            if any(fk[2]=='markets' for fk in db.execute('PRAGMA foreign_key_list('+quoted+')')):
                raise RuntimeError('Dependent market foreign key requires manual migration')
    updated=re.sub(pattern,'CHECK(buys>=0)',sql,flags=re.I)
    updated,n=re.subn(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"markets"|markets)(?=\s*\()',
                      'CREATE TABLE markets_uncapped',updated,count=1,flags=re.I)
    if n!=1:raise RuntimeError('Unexpected markets table definition')
    columns=','.join('"'+r[1].replace('"','""')+'"' for r in db.execute('PRAGMA table_info(markets)'))
    extras=[r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='markets' AND type IN ('index','trigger') AND sql IS NOT NULL")]
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(updated)
        db.execute('INSERT INTO markets_uncapped('+columns+') SELECT '+columns+' FROM markets')
        db.execute('DROP TABLE markets')
        db.execute('ALTER TABLE markets_uncapped RENAME TO markets')
        for statement in extras:db.execute(statement)
    return True
