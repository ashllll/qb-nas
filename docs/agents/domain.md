# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

This is a single-context repo.

Expected domain docs:

- `CONTEXT.md` at the repo root
- `docs/adr/` for architecture decision records

If these files don't exist, proceed silently. The producer skills create them lazily when terms or decisions actually get resolved.

## Before exploring, read these

- `CONTEXT.md` at the repo root, if present
- ADRs under `docs/adr/` that touch the area you're about to work in, if present

## Domain vocabulary

Use the glossary's vocabulary when a domain concept exists in `CONTEXT.md`. For this repo, likely domain concepts include:

- Magnet item
- Crawl pipeline
- qB lifecycle
- Classification helper rules
- Local classification
- Download state sync

If the concept you need isn't in the glossary yet, either reconsider the wording or note it for `/grill-with-docs`.

## ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (example decision) — but worth reopening because..._
