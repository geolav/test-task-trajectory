-- Разовая миграция со старой схемы (4 JSONB-массива в abstract_customer) на новую.
-- Payload переносится как есть: каждая запись массива становится строкой
-- новой таблицы и целиком попадает в колонку data.

BEGIN;

CREATE TABLE amo_customer (
    crm_id     bigint PRIMARY KEY,
    abc_id     bigint NOT NULL REFERENCES abstract_customer (id) ON DELETE CASCADE,
    login      text,
    name       text,
    data       jsonb NOT NULL,
    created_at timestamp DEFAULT now(),
    updated_at timestamp
);
CREATE INDEX ix_amo_customer_abc_id ON amo_customer (abc_id);
CREATE INDEX ix_amo_customer_login ON amo_customer (login);
CREATE INDEX ix_amo_customer_name ON amo_customer (name);

CREATE TABLE amo_lead (
    crm_id     bigint PRIMARY KEY,
    abc_id     bigint NOT NULL REFERENCES abstract_customer (id) ON DELETE CASCADE,
    login      text,
    name       text,
    data       jsonb NOT NULL,
    created_at timestamp DEFAULT now(),
    updated_at timestamp
);
CREATE INDEX ix_amo_lead_abc_id ON amo_lead (abc_id);
CREATE INDEX ix_amo_lead_login ON amo_lead (login);
CREATE INDEX ix_amo_lead_name ON amo_lead (name);

CREATE TABLE amo_contact (
    abc_id     bigint NOT NULL REFERENCES abstract_customer (id) ON DELETE CASCADE,
    crm_id     bigint NOT NULL,
    name       text,
    data       jsonb NOT NULL,
    created_at timestamp DEFAULT now(),
    updated_at timestamp,
    PRIMARY KEY (abc_id, crm_id)
);
CREATE INDEX ix_amo_contact_name ON amo_contact (name);

CREATE TABLE amo_company (
    abc_id     bigint NOT NULL REFERENCES abstract_customer (id) ON DELETE CASCADE,
    crm_id     bigint NOT NULL,
    name       text,
    data       jsonb NOT NULL,
    created_at timestamp DEFAULT now(),
    updated_at timestamp,
    PRIMARY KEY (abc_id, crm_id)
);
CREATE INDEX ix_amo_company_name ON amo_company (name);

-- --- перенос данных ---------------------------------------------------------
-- ON CONFLICT DO NOTHING: в старых массивах один crm_id мог лежать
-- в нескольких abc-записях, побеждает первая попавшаяся.

INSERT INTO amo_customer (crm_id, abc_id, login, name, data)
SELECT (e -> 'general' ->> 'id')::bigint,
       a.id,
       e -> 'general' ->> 'login',
       e -> 'general' ->> 'name',
       e
FROM abstract_customer a
CROSS JOIN LATERAL jsonb_array_elements(a.amo_customers) e
WHERE a.amo_customers IS NOT NULL
  AND jsonb_typeof(a.amo_customers) = 'array'
  AND e -> 'general' ->> 'id' IS NOT NULL
ON CONFLICT (crm_id) DO NOTHING;

INSERT INTO amo_lead (crm_id, abc_id, login, name, data)
SELECT (e -> 'general' ->> 'id')::bigint,
       a.id,
       e -> 'general' ->> 'login',
       e -> 'general' ->> 'name',
       e
FROM abstract_customer a
CROSS JOIN LATERAL jsonb_array_elements(a.amo_leads) e
WHERE a.amo_leads IS NOT NULL
  AND jsonb_typeof(a.amo_leads) = 'array'
  AND e -> 'general' ->> 'id' IS NOT NULL
ON CONFLICT (crm_id) DO NOTHING;

INSERT INTO amo_contact (abc_id, crm_id, name, data)
SELECT a.id,
       (e -> 'general' ->> 'id')::bigint,
       e -> 'general' ->> 'name',
       e
FROM abstract_customer a
CROSS JOIN LATERAL jsonb_array_elements(a.amo_contacts) e
WHERE a.amo_contacts IS NOT NULL
  AND jsonb_typeof(a.amo_contacts) = 'array'
  AND e -> 'general' ->> 'id' IS NOT NULL
ON CONFLICT (abc_id, crm_id) DO NOTHING;

INSERT INTO amo_company (abc_id, crm_id, name, data)
SELECT a.id,
       (e -> 'general' ->> 'id')::bigint,
       e -> 'general' ->> 'name',
       e
FROM abstract_customer a
CROSS JOIN LATERAL jsonb_array_elements(a.amo_companies) e
WHERE a.amo_companies IS NOT NULL
  AND jsonb_typeof(a.amo_companies) = 'array'
  AND e -> 'general' ->> 'id' IS NOT NULL
ON CONFLICT (abc_id, crm_id) DO NOTHING;

-- --- старые колонки больше не нужны -----------------------------------------

ALTER TABLE abstract_customer
    DROP COLUMN amo_customers,
    DROP COLUMN amo_leads,
    DROP COLUMN amo_companies,
    DROP COLUMN amo_contacts;

COMMIT;
