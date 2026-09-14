-- Live watch metadata on each check. Safe to re-run.

alter table pour_checks
  add column if not exists watching boolean not null default true;

alter table pour_checks
  add column if not exists last_checked_at timestamptz;

alter table pour_checks
  add column if not exists watch_events jsonb not null default '[]'::jsonb;

create index if not exists pour_checks_watching_idx
  on pour_checks (watching, last_checked_at desc)
  where watching = true;
