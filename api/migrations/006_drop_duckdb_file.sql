-- Migration 006: Drop the legacy clients.duckdb_file column.
-- DuckDB / Evidence dashboards were never wired up — only the legacy
-- /api/data/* endpoint referenced this column, and that router has been
-- removed (security: H1, raw-SQL endpoint removable surface).
-- All operational data lives in PostgreSQL; no historical value to keep.

ALTER TABLE clients DROP COLUMN IF EXISTS duckdb_file;
