<p align="center">
  <img src="brand/logo.png" alt="Newsmix logo" width="160" height="160">
</p>

# tg-nn: Newsmix

Newsmix follows news channels from all over the world, translates every report into German and sorts
it into channels by region and topic.

- `bot/collector/`: collector accounts follow the source channels and forward every new post, albums
  kept together, to a backup channel.
- `bot/processor/`: picks the posts up from there, strips promo lines and source bloat, translates
  them and routes each one to its regional or topic channel (an LLM picks the region).
- `brand/`: logo and social image, made with the
  [militaernews/tools](https://github.com/militaernews/tools) brand generator.
