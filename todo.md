# TODO

## Review sources imported on 2026-11-12

437 channels that the collector accounts are members of (but not admins in) were bulk-imported into `sources` with placeholder values:

- `destination` = Test (`-1001391125365`)
- `is_active` = false
- `display_name`, `bias`, `invite`, `detail_id`, `rating`, `description` empty

Find them with:

```sql
SELECT channel_id, channel_name, username, api_id
FROM sources
WHERE destination = -1001391125365 AND NOT is_active
ORDER BY channel_name;
```

For each channel:

- [ ] Assign a proper default `destination` (regional channel, or a topic channel like IT is interesting), or delete the row and leave the channel if it is not worth following.
- [ ] Fill `display_name` (short Latin-script name shown in captions) and `bias` (flag emoji of the side/country it reports for).
- [ ] Add better metadata: a factuality/reliability rating (`rating`), political leaning, language, primary region/topic, and a short `description`. Decide on a rating scale first and check whether the schema needs new columns (e.g. `factuality`, `language`, `leaning`) beyond `rating`.
- [ ] Set `is_active = true` only after the above is done, then restart `tg-nn-collector`.

## Account channel slots

Each collector account must keep at least 10 free channel slots (limit is 500 joined channels + supergroups without Telegram Premium). Michael is at 490/500 as of 2026-11-12. Join new sources with Martin, or move inactive sources from Michael to Martin first.

## mix-sv

mix-sv edits the same `sources`/`destinations` tables but in its own Neon database, not `nn-db` on nyx. Sync the reviewed sources (and the IT is interesting destination) there, or point both at one database.
