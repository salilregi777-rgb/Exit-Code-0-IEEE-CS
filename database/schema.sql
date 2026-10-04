-- EXIT CODE 0 Database Schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Member',
    email TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    title TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL,
    code TEXT NOT NULL,
    error_type TEXT NOT NULL,
    bug_location TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    cause TEXT NOT NULL,
    correction TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 20,
    hint TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS question_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_order INTEGER DEFAULT 1,
    is_unlocked INTEGER DEFAULT 1,
    is_completed INTEGER DEFAULT 0,
    is_abandoned INTEGER DEFAULT 0,
    assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id),
    UNIQUE(team_id, question_id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    error_location TEXT,
    error_type TEXT,
    expected_output TEXT,
    cause TEXT,
    correction TEXT,
    error_loc_score REAL DEFAULT 0,
    error_type_score REAL DEFAULT 0,
    cause_score REAL DEFAULT 0,
    output_score REAL DEFAULT 0,
    correction_score REAL DEFAULT 0,
    total_score REAL DEFAULT 0,
    is_double_commit INTEGER DEFAULT 0,
    is_accepted INTEGER DEFAULT 1,
    override_reason TEXT,
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS scores (
    team_id TEXT PRIMARY KEY,
    score REAL DEFAULT 0,
    bonus_score REAL DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    last_submission_time TEXT,
    last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS powerups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    powerup_type TEXT NOT NULL,
    is_used INTEGER DEFAULT 0,
    used_at TEXT,
    target_question_id TEXT,
    is_armed INTEGER DEFAULT 0,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    UNIQUE(team_id, powerup_type)
);

CREATE TABLE IF NOT EXISTS event_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    event_status TEXT NOT NULL DEFAULT 'WAITING',
    event_start_time TEXT,
    event_end_time TEXT,
    duration_minutes INTEGER DEFAULT 40,
    is_paused INTEGER DEFAULT 0,
    pause_time TEXT,
    remaining_seconds INTEGER DEFAULT 2400
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Fast lookup indexes
CREATE INDEX IF NOT EXISTS idx_participants_team ON participants(team_id);
CREATE INDEX IF NOT EXISTS idx_submissions_team ON submissions(team_id);
CREATE INDEX IF NOT EXISTS idx_submissions_question ON submissions(question_id);
CREATE INDEX IF NOT EXISTS idx_qassign_team ON question_assignments(team_id);

-- Round state is independent from debugging scores and survives app restarts.
CREATE TABLE IF NOT EXISTS competition_controls (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    quiz_status TEXT NOT NULL DEFAULT 'WAITING',
    results_published INTEGER NOT NULL DEFAULT 0,
    generation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    question_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_activity_team_time ON team_activity(team_id, created_at);
CREATE TABLE IF NOT EXISTS quiz_sessions (
    team_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    question_started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS quiz_answers (
    team_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    answer_index INTEGER,
    points INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, question_id),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);

-- Participant activity signals; fullscreen violations are enforced separately.
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_activity_team ON activity_events(team_id);

-- Separate enforcement from historical organizer activity so old signals do not punish teams.
CREATE TABLE IF NOT EXISTS participant_security (
    team_id TEXT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    violations INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fullscreen_sessions (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    is_fullscreen INTEGER NOT NULL DEFAULT 0,
    active_event_id TEXT,
    document_id TEXT,
    document_started_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fullscreen_events (
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(team_id, event_id)
);
CREATE TABLE IF NOT EXISTS quiz_feedback (
    team_id TEXT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    ratings_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS data_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);
