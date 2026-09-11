-- Run this once in the Supabase SQL editor (Project → SQL → New query).
-- Service-role key bypasses RLS; anon has no policies, so the public key cannot read these tables.

create extension if not exists pgcrypto;

create table if not exists pour_checks (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  request_id text,
  source text not null default 'demo',
  api_key_id uuid,
  client_ip text,
  latitude double precision,
  longitude double precision,
  zip_code text,
  address text,
  pour_date timestamptz,
  mix_design jsonb,
  concrete_temp_f double precision,
  go_no_go_status text not null,
  risk_factors jsonb not null default '[]'::jsonb,
  metrics jsonb,
  predictions jsonb,
  recommended_mitigation text,
  location jsonb
);

create table if not exists pour_outcomes (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  check_id uuid not null references pour_checks (id) on delete cascade,
  outcome text not null check (outcome in ('success', 'cracked', 'delayed', 'other')),
  notes text,
  unique (check_id)
);

create table if not exists api_keys (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  name text not null,
  key_prefix text not null,
  key_hash text not null unique,
  active boolean not null default true,
  rate_limit_per_hour integer not null default 300
);

create table if not exists weather_cache (
  cache_key text primary key,
  payload jsonb not null,
  fetched_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists pour_checks_created_at_idx on pour_checks (created_at desc);
create index if not exists weather_cache_expires_at_idx on weather_cache (expires_at);

alter table pour_checks enable row level security;
alter table pour_outcomes enable row level security;
alter table api_keys enable row level security;
alter table weather_cache enable row level security;
