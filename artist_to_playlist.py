#!/usr/bin/env python3
"""
artist_to_playlist.py
Build a Spotify playlist containing an artist's complete catalogue,
with optional AI curation via the Claude API.

Setup (one time):
  1. pip install spotipy anthropic
  2. Create a free app at https://developer.spotify.com/dashboard
     - Add redirect URI:  http://127.0.0.1:8888/callback
     - Copy the Client ID and Client Secret below (or set env vars).
  3. (Optional, for --vibe) Get an API key at https://console.anthropic.com
     and set it:  export ANTHROPIC_API_KEY=sk-ant-...

Usage:
  python artist_to_playlist.py "Massive Attack"
  python artist_to_playlist.py "Massive Attack" --include-compilations
  python artist_to_playlist.py "Massive Attack" --vibe "dark, slow, late-night tracks only"
"""

import argparse
import os
import sys

import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ---------------------------------------------------------------------------
# Config — paste your Spotify app credentials here, or set the env vars
# SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET instead.
# ---------------------------------------------------------------------------
CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET", "")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "playlist-modify-private"


def get_spotify_client() -> spotipy.Spotify:
    """Authenticate via OAuth. First run opens a browser; token is cached."""
    auth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=os.path.expanduser("~/.spotify_playlist_token"),
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=3)


def find_artist(sp: spotipy.Spotify, name: str) -> dict:
    """Search for the artist and return the best match."""
    res = sp.search(q=f"artist:{name}", type="artist", limit=5)
    items = res.get("artists", {}).get("items", [])
    if not items:
        sys.exit(f"No artist found for '{name}'.")
    # Prefer exact case-insensitive name match, else take the top result.
    for a in items:
        if a["name"].lower() == name.lower():
            return a
    return items[0]


def fetch_albums(sp: spotipy.Spotify, artist_id: str, groups: str) -> list:
    """Page through all releases of the requested album groups.
    Page size is 10 — the maximum allowed under Spotify's 2026 Dev Mode rules."""
    albums, offset = [], 0
    while True:
        page = sp.artist_albums(
            artist_id, include_groups=groups, limit=10, offset=offset
        )
        albums.extend(page["items"])
        if page["next"] is None:
            break
        offset += 10
    return albums


EDITION_WORDS = (
    "deluxe", "expanded", "extended", "remastered", "remaster", "anniversary",
    "special edition", "bonus track", "collector", "super deluxe", "edition",
)


def _base_album_name(name: str) -> str:
    """Strip edition markers so 'Mezzanine (Deluxe Edition)' and 'Mezzanine'
    group together. Removes bracketed suffixes and edition keywords."""
    import re

    n = name.lower()
    n = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", n)  # drop (...) and [...] parts
    for w in EDITION_WORDS:
        n = n.replace(w, " ")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def select_album_versions(albums: list) -> list:
    """When several albums share the same base name, keep only one:
    prefer the version with the most tracks; on a tie, prefer the one
    whose title contains 'deluxe'."""
    groups: dict = {}
    for a in albums:
        groups.setdefault(_base_album_name(a["name"]), []).append(a)

    chosen = []
    for versions in groups.values():
        if len(versions) == 1:
            chosen.append(versions[0])
            continue
        versions.sort(
            key=lambda a: (
                a.get("total_tracks", 0),
                "deluxe" in a["name"].lower(),
            ),
            reverse=True,
        )
        best = versions[0]
        print(
            f"  '{_base_album_name(best['name'])}': keeping "
            f"'{best['name']}' ({best.get('total_tracks', '?')} tracks), "
            f"dropping {len(versions) - 1} other version(s)"
        )
        chosen.append(best)
    return chosen


def fetch_tracks(sp: spotipy.Spotify, albums: list, artist_id: str) -> list:
    """Pull every track from the albums, keep only ones featuring the artist,
    and deduplicate re-releases by (name, duration to the nearest second)."""
    seen, tracks = set(), []
    for album in albums:
        offset = 0
        while True:
            page = sp.album_tracks(album["id"], limit=10, offset=offset)
            for t in page["items"]:
                if artist_id not in [a["id"] for a in t["artists"]]:
                    continue  # skip other artists' tracks on compilations
                key = (t["name"].strip().lower(), round(t["duration_ms"] / 1000))
                if key in seen:
                    continue
                seen.add(key)
                tracks.append(
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "album": album["name"],
                        "release": album.get("release_date", ""),
                        "duration_ms": t["duration_ms"],
                    }
                )
            if page["next"] is None:
                break
            offset += 10
    return tracks


def curate_with_claude(tracks: list, vibe: str, artist_name: str) -> list:
    """Ask Claude to select and order a subset of tracks matching the vibe.
    Returns the curated track list (falls back to the full list on any error)."""
    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed — run: pip install anthropic")
        return tracks

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — skipping curation.")
        return tracks

    catalogue = "\n".join(
        f"{i}. {t['name']} (album: {t['album']}, released {t['release']})"
        for i, t in enumerate(tracks)
    )
    prompt = (
        f"Here is the complete track catalogue of {artist_name}:\n\n"
        f"{catalogue}\n\n"
        f"Curate a playlist matching this brief: \"{vibe}\".\n"
        "Select the tracks that fit and put them in a good listening order.\n"
        "Respond with ONLY the track numbers, comma-separated, in playback "
        "order. No other text."
    )

    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        indices = [int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()]
        curated = [tracks[i] for i in indices if 0 <= i < len(tracks)]
        if not curated:
            raise ValueError("Claude returned no usable track numbers.")
        return curated
    except Exception as e:
        print(f"Curation failed ({e}) — using the full catalogue instead.")
        return tracks


def create_playlist(sp: spotipy.Spotify, name: str, tracks: list) -> str:
    """Create a private playlist and add the tracks in batches of 100.

    Uses the endpoints introduced in Spotify's February 2026 API update
    (POST /me/playlists and POST /playlists/{id}/items) — the older
    spotipy helper methods call endpoints that were removed."""
    playlist = sp._post("me/playlists", payload={"name": name, "public": False})
    uris = [f"spotify:track:{t['id']}" for t in tracks if t["id"]]
    batch = 50  # conservative batch size for the 2026 Dev Mode limits
    for i in range(0, len(uris), batch):
        sp._post(f"playlists/{playlist['id']}/items", payload={"uris": uris[i : i + batch]})
    return playlist["external_urls"]["spotify"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Artist catalogue -> playlist")
    parser.add_argument("artist", help="Artist name, e.g. \"Massive Attack\"")
    parser.add_argument(
        "--include-compilations",
        action="store_true",
        help="Also include compilation albums (greatest hits etc.)",
    )
    parser.add_argument(
        "--include-appears-on",
        action="store_true",
        help="Also include releases the artist appears on (features, guest spots)",
    )
    parser.add_argument(
        "--vibe",
        metavar="BRIEF",
        help="Optional Claude curation brief, e.g. \"chill late-night, no remixes\"",
    )
    args = parser.parse_args()

    if "PASTE_CLIENT" in CLIENT_ID:
        sys.exit(
            "Add your Spotify Client ID/Secret at the top of the script "
            "or set SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET."
        )

    groups = ["album", "single"]
    if args.include_compilations:
        groups.append("compilation")
    if args.include_appears_on:
        groups.append("appears_on")

    sp = get_spotify_client()

    artist = find_artist(sp, args.artist)
    print(f"Artist: {artist['name']}  ({artist['id']})")

    albums = fetch_albums(sp, artist["id"], ",".join(groups))
    print(f"Releases found: {len(albums)}")

    albums = select_album_versions(albums)
    print(f"Releases after version selection: {len(albums)}")

    tracks = fetch_tracks(sp, albums, artist["id"])
    print(f"Unique tracks after dedup: {len(tracks)}")

    playlist_name = f"{artist['name']} — Complete"
    if args.vibe:
        print(f"Asking Claude to curate: \"{args.vibe}\" ...")
        tracks = curate_with_claude(tracks, args.vibe, artist["name"])
        print(f"Curated selection: {len(tracks)} tracks")
        playlist_name = f"{artist['name']} — {args.vibe[:40]}"

    url = create_playlist(sp, playlist_name, tracks)
    print(f"\nDone! Playlist created:\n{url}")


if __name__ == "__main__":
    main()
