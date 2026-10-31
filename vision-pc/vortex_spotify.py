"""Read only public Spotify track metadata through the worker's socket guard.

No browser session, bundled API credentials, preview audio or Spotify stream is
used. The existing spotDL matcher selects audio from YouTube/YouTube Music.
"""
from html.parser import HTMLParser
import json
import re


class EmbedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.collecting = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__':
            self.collecting = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.collecting = False

    def handle_data(self, data):
        if self.collecting:
            self.parts.append(data)


def parse_track(html, track_id):
    from vortex_worker import WorkerError, number, text, safe_url
    try:
        parser = EmbedData()
        parser.feed(html)
        entity = json.loads(''.join(parser.parts))['props']['pageProps']['state']['data']['entity']
        if entity.get('type') != 'track' or entity.get('uri') != 'spotify:track:' + track_id:
            raise ValueError('identity')
        name = text(entity.get('name') or entity.get('title'))
        artists = [text(a.get('name')) for a in entity.get('artists', []) if isinstance(a, dict) and text(a.get('name'))]
        duration = number(entity.get('duration'))
        if not name or not artists or not duration:
            raise ValueError('incomplete metadata')
        images = (entity.get('visualIdentity') or {}).get('image') or []
        cover = next((safe_url(i.get('url')) for i in images if isinstance(i, dict) and safe_url(i.get('url'))), None)
        return dict(name=name, artists=artists, duration=duration / 1000, cover_url=cover,
                    explicit=bool(entity.get('isExplicit')), song_id=track_id,
                    url='https://open.spotify.com/track/' + track_id)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise WorkerError('Spotify did not return complete metadata for this public track.', category='spotify_metadata') from None


def public_track(track_id):
    import requests
    from vortex_worker import WorkerError
    if not re.fullmatch(r'[A-Za-z0-9]{22}', track_id):
        raise WorkerError('Paste a single Spotify track link.', category='spotify_link')
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get('https://open.spotify.com/embed/track/' + track_id,
                             timeout=(10, 20), stream=True, allow_redirects=False) as response:
                if response.status_code == 429:
                    raise WorkerError('Spotify is limiting requests. Try again shortly.', category='rate_limited')
                if response.status_code != 200:
                    raise WorkerError('Spotify could not return public track metadata.', category='spotify_metadata')
                body = bytearray()
                for chunk in response.iter_content(16 * 1024):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise WorkerError('Spotify returned an oversized metadata response.', category='spotify_metadata')
        return parse_track(body.decode('utf-8'), track_id)
    except (requests.RequestException, UnicodeError):
        raise WorkerError('Spotify metadata could not be reached from FUPCJ Server.', category='spotify_metadata') from None


def matching_song(metadata):
    from spotdl.types.song import Song
    return Song(name=metadata['name'], artists=metadata['artists'], artist=metadata['artists'][0],
                duration=metadata['duration'], song_id=metadata['song_id'], url=metadata['url'],
                cover_url=metadata['cover_url'], explicit=metadata['explicit'], genres=[],
                disc_number=1, disc_count=1, album_name='', album_artist=metadata['artists'][0],
                year=0, date='', track_number=1, tracks_count=1, publisher='', isrc=None, copyright_text=None)
