-- Individual / org accounts and a link from checks. Safe to re-run.
-- Run this in the Supabase SQL editor (Project → SQL → New query), after 001–004.
-- Service-role key bypasses RLS. Do not store prices here.

create table if not exists accounts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  email text not null,
  password_hash text not null,
  kind text not null default 'individual' check (kind in ('individual', 'org')),
  plan text not null default 'free',
  trial_ends_at timestamptz not null default (now() + interval '30 days'),
  session_nonce text,
  display_name text
);

create unique index if not exists accounts_email_lower_idx
  on accounts (lower(email));

alter table pour_checks
  add column if not exists account_id uuid references accounts (id) on delete set null;

create index if not exists pour_checks_account_watching_idx
  on pour_checks (account_id)
  where watching = true and account_id is not null;

alter table accounts enable row level security;
