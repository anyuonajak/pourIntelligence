-- Email subscribers for watch alerts and the daily digest. Safe to re-run.
-- Run this in the Supabase SQL editor (Project → SQL → New query), after 001–003.

create table if not exists watch_subscribers (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  email text not null,
  unsub_token text not null unique,
  alerts_enabled boolean not null default true,
  digest_enabled boolean not null default true,
  last_digest_at timestamptz
);

create unique index if not exists watch_subscribers_email_lower_idx
  on watch_subscribers (lower(email));

alter table pour_checks
  add column if not exists subscriber_id uuid references watch_subscribers (id) on delete set null;

create index if not exists pour_checks_subscriber_watching_idx
  on pour_checks (subscriber_id)
  where watching = true and subscriber_id is not null;

alter table watch_subscribers enable row level security;
