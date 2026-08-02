# Spotify Catalogue-to-Playlist Builder

A Python CLI tool that assembles an artist's complete catalogue — albums, EPs,
and singles — into a single Spotify playlist, with smart deduplication and an
optional AI curation layer powered by the Claude API.

## What it does

1. **Authenticates** with the Spotify Web API via OAuth 2.0 (one-time browser
   consent, token cached locally).
2. **Fetches** every release by the artist through paginated API calls.
3. **Selects album editions** — when the same album exists in several versions
   (standard / deluxe / remastered), only the most complete edition is kept.
4. **Deduplicates tracks** across releases by name + duration, so a single
   later included on an album appears once.
5. **Creates a private playlist** and adds all tracks in batches.
6. **(Optional) AI curation** — with `--vibe "your brief"`, the full catalogue
   is sent to the Claude API, which selects and orders tracks matching a
   natural-language description (e.g. *"dark late-night tracks, no remixes"*).

## Example run

```
$ python artist_to_playlist.py "Westside Gunn"
Artist: Westside Gunn  (0ABk515kENDyATUdpCKVfW)
Releases found: 64
  'still praying': keeping 'Still Praying' (14 tracks), dropping 1 other version(s)
  ...
Releases after version selection: 60
Unique tracks after dedup: 391
Done! Playlist created: https://open.spotify.com/playlist/...
```

## Engineering notes

Built and hardened against Spotify's **February 2026 Development Mode
restrictions**, which changed the platform mid-development:

- Migrated from removed endpoints (`POST /users/{id}/playlists`,
  `POST /playlists/{id}/tracks`) to their 2026 replacements
  (`POST /me/playlists`, `POST /playlists/{id}/items`).
- Reduced page sizes to the new 10-item ceiling and adjusted pagination
  accordingly.
- Added call-budget economies (early paging cut-off using known track counts,
  request pacing) and graceful handling of daily rate quotas.

## Requirements

- Python 3.10+
- `pip install spotipy anthropic`
- A Spotify Developer app (requires Spotify Premium under 2026 rules) with
  redirect URI `http://127.0.0.1:8888/callback`
- For AI curation: an Anthropic API key in the `ANTHROPIC_API_KEY`
  environment variable

## Usage

```
python artist_to_playlist.py "Artist Name"
python artist_to_playlist.py "Artist Name" --include-compilations
python artist_to_playlist.py "Artist Name" --vibe "chill instrumentals only"
```

Set your Spotify Client ID and Secret at the top of the script or via the
`SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` environment variables.
Credentials are never stored in this repository.
