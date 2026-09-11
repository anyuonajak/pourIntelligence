-- Product type on each check (concrete slab vs masonry). Safe to re-run.

alter table pour_checks
  add column if not exists product text not null default 'concrete';

create index if not exists pour_checks_product_idx on pour_checks (product);
