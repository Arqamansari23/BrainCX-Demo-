-- =====================================================================
-- Demo data. Fictional people and companies.
--
-- John Smith's deal starts in "contacted" so the headline voice command
-- ("Move John Smith to Qualified...") has something to do. John Park exists
-- so that "Move John to..." is ambiguous and the agent has to ask which one.
-- Task dates are relative to today so "due today" and "overdue" always show.
-- =====================================================================

INSERT INTO contacts (full_name, email, phone, company, status, source) VALUES
    ('John Smith',      'john.smith@acme.example',        '+1 415 555 0101', 'Acme Corp',            'lead',     'seed'),
    ('John Park',       'john.park@globex.example',       '+1 415 555 0102', 'Globex',               'lead',     'seed'),
    ('Sarah Chen',      'sarah.chen@brightline.example',  '+1 415 555 0103', 'Brightline Logistics', 'lead',     'seed'),
    ('Priya Patel',     'priya.patel@nimbus.example',     '+1 415 555 0104', 'Nimbus Health',        'lead',     'seed'),
    ('Michael Brown',   'michael.brown@oakridge.example', '+1 415 555 0105', 'Oakridge Realty',      'lead',     'seed'),
    ('Emma Wilson',     'emma.wilson@vertexfit.example',  '+1 415 555 0106', 'Vertex Fitness',       'customer', 'seed'),
    ('David Garcia',    'david.garcia@harbor.example',    '+1 415 555 0107', 'Harbor Foods',         'lead',     'seed'),
    ('Olivia Martinez', 'olivia.martinez@summit.example', '+1 415 555 0108', 'Summit Legal',         'lead',     'seed'),
    ('James Lee',       'james.lee@quantum.example',      '+1 415 555 0109', 'Quantum Retail',       'lead',     'seed');

INSERT INTO opportunities (contact_id, title, value, stage, updated_at)
SELECT c.id, v.title, v.value, v.stage, now() - v.age
FROM (VALUES
    ('john.smith@acme.example',        'Acme CRM rollout',           24000, 'contacted', INTERVAL '2 days'),
    ('john.park@globex.example',       'Globex analytics dashboard',  9500, 'new',       INTERVAL '1 day'),
    ('sarah.chen@brightline.example',  'Fleet tracking pilot',       12500, 'new',       INTERVAL '3 hours'),
    ('priya.patel@nimbus.example',     'Patient portal build',       48000, 'qualified', INTERVAL '4 days'),
    ('michael.brown@oakridge.example', 'Lead routing automation',     8000, 'proposal',  INTERVAL '5 days'),
    ('emma.wilson@vertexfit.example',  'Member app',                 15000, 'won',       INTERVAL '9 days'),
    ('david.garcia@harbor.example',    'Voice ordering line',        30000, 'lost',      INTERVAL '12 days'),
    ('olivia.martinez@summit.example', 'Client intake chatbot',      18000, 'contacted', INTERVAL '6 days'),
    ('james.lee@quantum.example',      'Loyalty program revamp',     22000, 'proposal',  INTERVAL '3 days')
) AS v(email, title, value, stage, age)
JOIN contacts c ON c.email = v.email;

INSERT INTO tasks (contact_id, opportunity_id, title, due_date, source)
SELECT o.contact_id, o.id, v.title, CURRENT_DATE + v.days, v.source
FROM (VALUES
    ('Fleet tracking pilot',    'Send case studies',          0, 'seed'),
    ('Loyalty program revamp',  'Call about budget sign-off', -1, 'seed'),
    ('Patient portal build',    'Prepare proposal',           2, 'automation'),
    ('Lead routing automation', 'Chase decision',             1, 'automation'),
    ('Client intake chatbot',   'Discovery call',             3, 'seed')
) AS v(deal, title, days, source)
JOIN opportunities o ON o.title = v.deal;

INSERT INTO activities (contact_id, opportunity_id, kind, message, source, created_at)
SELECT o.contact_id, o.id, v.kind, v.message, v.source, now() - v.age
FROM (VALUES
    ('Member app',           'stage_changed', 'Emma Wilson: Member app moved from Proposal to Won', 'seed', INTERVAL '9 days'),
    ('Patient portal build', 'stage_changed', 'Priya Patel: Patient portal build moved from Contacted to Qualified', 'seed', INTERVAL '4 days'),
    ('Acme CRM rollout',     'stage_changed', 'John Smith: Acme CRM rollout moved from New to Contacted', 'seed', INTERVAL '2 days'),
    ('Fleet tracking pilot', 'lead_created',  'New lead Sarah Chen (Brightline Logistics): Fleet tracking pilot', 'seed', INTERVAL '3 hours')
) AS v(deal, kind, message, source, age)
JOIN opportunities o ON o.title = v.deal;
