"""Additive v2 schema; legacy and v1 event records remain intact."""


def upgrade(c, now):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS operators(id INTEGER PRIMARY KEY, name TEXT NOT NULL,role TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS preferences(customer_id TEXT PRIMARY KEY REFERENCES wallet_customers(id), channel TEXT NOT NULL DEFAULT 'in_app', support INTEGER NOT NULL DEFAULT 1, marketing INTEGER NOT NULL DEFAULT 0, destination TEXT, verified INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, action_id INTEGER UNIQUE REFERENCES actions(id), entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',owner TEXT,priority TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,resolution TEXT);
    CREATE TABLE IF NOT EXISTS incident_notes(id INTEGER PRIMARY KEY,incident_id INTEGER NOT NULL REFERENCES incidents(id),author TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS overrides(id INTEGER PRIMARY KEY,action_id INTEGER REFERENCES actions(id),entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,actor TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY,execution_key TEXT UNIQUE NOT NULL REFERENCES executions(execution_key),channel TEXT NOT NULL,destination TEXT,status TEXT NOT NULL,provider_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,last_error TEXT,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS delivery_receipts(id TEXT PRIMARY KEY,delivery_id INTEGER REFERENCES deliveries(id),status TEXT NOT NULL,received_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settlements(id TEXT PRIMARY KEY,transaction_id TEXT NOT NULL UNIQUE REFERENCES transactions(id),due_at TEXT NOT NULL,settled_at TEXT,amount_minor INTEGER NOT NULL,status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS refunds(id TEXT PRIMARY KEY,transaction_id TEXT NOT NULL REFERENCES transactions(id),amount_minor INTEGER NOT NULL CHECK(amount_minor>0),status TEXT NOT NULL,requested_at TEXT NOT NULL,resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS complaints(id INTEGER PRIMARY KEY,customer_id TEXT NOT NULL REFERENCES wallet_customers(id),text TEXT NOT NULL,category TEXT NOT NULL,sentiment TEXT NOT NULL,confidence REAL NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS batch_runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,as_of TEXT NOT NULL,input_rows INTEGER,output_path TEXT,error TEXT,created_at TEXT NOT NULL,finished_at TEXT);
    CREATE TABLE IF NOT EXISTS batch_metrics(run_id TEXT NOT NULL REFERENCES batch_runs(id),entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,metrics TEXT NOT NULL,PRIMARY KEY(run_id,entity_type,entity_id));
    CREATE TABLE IF NOT EXISTS prediction_log(id INTEGER PRIMARY KEY,model TEXT NOT NULL,entity_id TEXT NOT NULL,version TEXT NOT NULL,as_of TEXT NOT NULL,probability REAL NOT NULL,features TEXT NOT NULL,target INTEGER,evaluated_at TEXT);
    CREATE TABLE IF NOT EXISTS monitoring_runs(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,report TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL,horizon_days INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'active');
    CREATE TABLE IF NOT EXISTS assignments(experiment_id TEXT NOT NULL REFERENCES experiments(id),customer_id TEXT NOT NULL REFERENCES wallet_customers(id),arm TEXT NOT NULL,assigned_at TEXT NOT NULL,PRIMARY KEY(experiment_id,customer_id));
    """)
    c.execute("INSERT OR IGNORE INTO schema_versions VALUES(2,?)", (now,))
