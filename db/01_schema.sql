-- =====================================================================
-- VoiceCRM schema
--
-- contacts       people and companies we sell to (leads until they buy)
-- opportunities  deals, each sitting in one pipeline stage
-- tasks          follow-ups, created by people, the voice agent, or automations
-- activities     the timeline of everything that happened, and who did it.
--                It doubles as the audit trail.
--
-- "source" records who made a change. The API derives it from the credential
-- used (web session, agent API key, signed webhook), never from the request body.
-- =====================================================================

-- Trigram matching, so a name heard slightly wrong ("Jon Smith") still matches.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE contacts (
    id          SERIAL PRIMARY KEY,
    full_name   TEXT NOT NULL CHECK (length(btrim(full_name)) BETWEEN 2 AND 120),
    email       TEXT UNIQUE,
    phone       TEXT,
    company     TEXT,
    status      TEXT NOT NULL DEFAULT 'lead' CHECK (status IN ('lead', 'customer')),
    source      TEXT NOT NULL DEFAULT 'ui'
                CHECK (source IN ('ui', 'voice', 'webhook', 'automation', 'seed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE opportunities (
    id          SERIAL PRIMARY KEY,
    contact_id  INT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    title       TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 2 AND 160),
    value       NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (value >= 0),
    stage       TEXT NOT NULL DEFAULT 'new'
                CHECK (stage IN ('new', 'contacted', 'qualified', 'proposal', 'won', 'lost')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX opportunities_contact_idx ON opportunities (contact_id);

CREATE TABLE tasks (
    id              SERIAL PRIMARY KEY,
    contact_id      INT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    opportunity_id  INT REFERENCES opportunities(id) ON DELETE SET NULL,
    title           TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 2 AND 160),
    due_date        DATE NOT NULL,
    done            BOOLEAN NOT NULL DEFAULT false,
    source          TEXT NOT NULL DEFAULT 'ui'
                    CHECK (source IN ('ui', 'voice', 'webhook', 'automation', 'seed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tasks_open_idx ON tasks (done, due_date);

CREATE TABLE activities (
    id              SERIAL PRIMARY KEY,
    contact_id      INT REFERENCES contacts(id) ON DELETE CASCADE,
    opportunity_id  INT REFERENCES opportunities(id) ON DELETE SET NULL,
    -- lead_created | stage_changed | task_created | task_done | webhook
    kind            TEXT NOT NULL,
    message         TEXT NOT NULL,
    source          TEXT NOT NULL
                    CHECK (source IN ('ui', 'voice', 'webhook', 'automation', 'seed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX activities_recent_idx ON activities (created_at DESC);
