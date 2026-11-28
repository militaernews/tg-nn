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

## Making Newsmix more openly accessible

Ideas to widen access to the aggregated news without turning it into a closed product. Keep the
privacy promise: no accounts and no tracking for read-only use.

- [ ] Publish a **source registry**: channel name, region/topic, language, factuality rating and a
      short description for every active source. A static JSON file plus a readable page is enough.
      Let readers suggest corrections or new sources through public issues, with a documented
      acceptance checklist.
- [ ] Add **per-region RSS/Atom feeds** for the topic channels so any feed reader can follow a region
      without a Telegram account. Include the original source link and timestamp on every item.
- [ ] Offer a **read-only JSON API** with a generous anonymous rate limit (even 20 requests/minute
      would cover most curiosity), plus optional keys for higher limits. Document the endpoints and
      publish a small OpenAPI description.
- [ ] Build a **public read-only web view** of the regional feeds: no login, no cookies, no analytics,
      just search, region filters and clear attribution back to the original channel.
- [ ] Document the **selection and translation method** on a public page: how sources are rated, how
      duplicates and promo lines are removed, how translation is done, and what errors can happen.
      Transparency is cheaper than perfect accuracy and builds trust.
- [ ] Add a **feedback loop on every item**: "wrong translation", "wrong region", "outdated source"
      links that go to a public issue template or a bot command. Feed confirmed fixes back into the
      source data.
- [ ] Open-source the **non-secret parts**: the source schema, the rating scale, dedup rules and
      translation prompts. Keep credentials and collector internals private.
- [ ] Publish a **weekly open digest** (aggregate counts and top stories per region, no personal
      data) under a CC licence for journalists, researchers and educators.
- [ ] Provide **embeddable widgets** for other publishers — a small region feed card with a
      "Powered by Newsmix" credit and no tracking scripts.
- [ ] Create a **partner access programme** with a simple, published criteria list for small news
      outlets, universities and NGOs; self-service where possible, no sales call required.
- [ ] Add a **status and freshness page** showing when each regional feed last received an item, so
      readers can tell a quiet day from a broken collector.
- [ ] Support **graduated access**: anonymous read → contributor (can suggest sources and fixes) →
      partner (higher API limits), without forcing casual readers to register.
- [ ] Write **quickstart docs in German and English** with copy-paste examples for RSS, JSON and
      Telegram topic-channel subscriptions.
