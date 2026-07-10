---
name: release-notes
description: Append a release entry to CHANGELOG.md from a version + summary.
---

# Release Notes

Add a single release entry to the project's `CHANGELOG.md`.

## Rules

1. **Newest entry on top.** The new entry goes at the **top** of the entry list
   (reverse-chronological) — directly under the file header, above all existing entries.
2. **Every entry has version, date, and category.** Each entry states its version, its release
   date, and at least one category heading from: **Added / Fixed / Changed**.
3. **Never rewrite existing entries.** Existing entries are immutable — insert above them, never
   edit, reorder, or delete them.
4. **Create the file if missing.** If `CHANGELOG.md` does not exist, create it with a
   `# Changelog` header, then add the entry.
5. **ISO dates only.** Dates are written `YYYY-MM-DD` — never a locale format.

## Output

The updated `CHANGELOG.md`.
