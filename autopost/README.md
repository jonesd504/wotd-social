# Daily word autoposter

Renders the app's own word for each day (same queue and epoch as `src/utils/daily-word.ts`,
pulled from https://wordoftheday.uk/words) and posts it to every platform with credentials set.

- `generate.py`: card (1080x1350), story (1080x1920), 8s reveal video, caption. Falls back to `words.csv` if the API is unreachable.
- `post.py`: Bluesky, Mastodon, Threads, Instagram Reel, Pinterest. Each link is the word's smart link `wordoftheday.uk/get?w=<id>`, which opens the word in the app or the store.
- `../.github/workflows/daily-word.yml`: runs daily at 07:00 UTC on GitHub Actions (free). Put this folder at `autopost/` in a **public** repo so Threads/Instagram can fetch the media.

Untested against live platform APIs (no credentials yet); the render step and dry-run are tested.
