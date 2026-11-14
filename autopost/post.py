#!/usr/bin/env python3
"""Post today's rendered word to every platform that has credentials set.

Each platform is skipped silently if its env vars are missing, so you can
switch platforms on one at a time by adding GitHub Actions secrets.

  Bluesky    BSKY_HANDLE, BSKY_APP_PASSWORD
  Mastodon   MASTODON_BASE_URL, MASTODON_TOKEN
  Threads    THREADS_USER_ID, THREADS_TOKEN                (needs PUBLIC_BASE_URL)
  Instagram  IG_USER_ID, IG_TOKEN                          (needs PUBLIC_BASE_URL)
  Facebook   FB_PAGE_ID, FB_PAGE_TOKEN                     (needs PUBLIC_BASE_URL)
  Pinterest  PINTEREST_TOKEN, PINTEREST_BOARD_ID
             optional auto-refresh (access tokens last 30 days):
             PINTEREST_REFRESH_TOKEN, PINTEREST_APP_ID, PINTEREST_APP_SECRET
  Common     APP_LINK  (fallback link; normally each word uses wordoftheday.uk/get?w=<id>)
             PUBLIC_BASE_URL  (where output/ is publicly reachable, e.g.
             https://raw.githubusercontent.com/<you>/<repo>/main/autopost/output)

Threads and Instagram fetch media from a public URL; they cannot take an upload.
"""
import argparse, base64, datetime as dt, json, os, sys, time
import urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from generate import load_rows  # noqa: E402

env = os.environ.get
APP_LINK = env("APP_LINK", "")


def http(method, url, data=None, headers=None, raw=None):
    headers = dict(headers or {})
    body = raw
    if data is not None and raw is None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code}: {e.read().decode()[:500]}")


def form(method, url, params):
    return http(method, url + "?" + urllib.parse.urlencode(params))


def link_for(row):
    # Per-word smart link: opens the word in the app, or the store if not installed
    return row.get("link") or APP_LINK


def short_text(row, limit):
    t = f"{row['word']} ({row['part_of_speech']}): {row['definition']}"
    tail = f"\n\n#wordoftheday {link_for(row)}".rstrip()
    return (t[: limit - len(tail) - 1] + "…" if len(t) + len(tail) > limit else t) + tail


# ---------- Bluesky ----------
def bluesky(row, d):
    if not (env("BSKY_HANDLE") and env("BSKY_APP_PASSWORD")):
        return "skipped"
    base = "https://bsky.social/xrpc"
    s = http("POST", f"{base}/com.atproto.server.createSession",
             {"identifier": env("BSKY_HANDLE"), "password": env("BSKY_APP_PASSWORD")})
    auth = {"Authorization": f"Bearer {s['accessJwt']}"}
    with open(os.path.join(d, "square.png"), "rb") as fh:
        blob = http("POST", f"{base}/com.atproto.repo.uploadBlob",
                    headers={**auth, "Content-Type": "image/png"}, raw=fh.read())["blob"]
    text = short_text(row, 300)
    record = {
        "$type": "app.bsky.feed.post", "text": text,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "embed": {"$type": "app.bsky.embed.images", "images": [{
            "alt": f"{row['word']}: {row['definition']}", "image": blob,
            "aspectRatio": {"width": 1080, "height": 1350}}]},
    }
    tag = "#wordoftheday"
    start = text.encode().find(tag.encode())
    if start >= 0:  # make the hashtag clickable
        record["facets"] = [{"index": {"byteStart": start, "byteEnd": start + len(tag)},
                             "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "wordoftheday"}]}]
    http("POST", f"{base}/com.atproto.repo.createRecord", {"repo": s["did"],
         "collection": "app.bsky.feed.post", "record": record}, headers=auth)
    return "posted"


# ---------- Mastodon ----------
def mastodon(row, d):
    base, tok = env("MASTODON_BASE_URL"), env("MASTODON_TOKEN")
    if not (base and tok):
        return "skipped"
    boundary = "----wotd"
    with open(os.path.join(d, "square.png"), "rb") as fh:
        img = fh.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"word.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + img + f"\r\n--{boundary}--\r\n".encode()
    auth = {"Authorization": f"Bearer {tok}"}
    media = http("POST", f"{base}/api/v2/media", raw=body,
                 headers={**auth, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    http("POST", f"{base}/api/v1/statuses",
         {"status": short_text(row, 500), "media_ids": [media["id"]]}, headers=auth)
    return "posted"


# ---------- Threads ----------
def threads(row, d, public):
    uid, tok = env("THREADS_USER_ID"), env("THREADS_TOKEN")
    if not (uid and tok and public):
        return "skipped"
    base = "https://graph.threads.net/v1.0"
    c = form("POST", f"{base}/{uid}/threads", {"media_type": "IMAGE",
             "image_url": f"{public}/square.png", "text": short_text(row, 500), "access_token": tok})
    time.sleep(30)  # Meta recommends waiting before publishing
    form("POST", f"{base}/{uid}/threads_publish", {"creation_id": c["id"], "access_token": tok})
    return "posted"


# ---------- Instagram (feed image + Reel) ----------
def instagram(row, d, public, caption):
    uid, tok = env("IG_USER_ID"), env("IG_TOKEN")
    if not (uid and tok and public):
        return "skipped"
    base = f"https://graph.facebook.com/{env('META_GRAPH_VERSION', 'v21.0')}"

    def publish(params):
        c = form("POST", f"{base}/{uid}/media", {**params, "access_token": tok})
        for _ in range(30):  # wait for Meta to process the media
            st = form("GET", f"{base}/{c['id']}", {"fields": "status_code", "access_token": tok})
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                raise RuntimeError(f"Instagram processing failed: {st}")
            time.sleep(10)
        form("POST", f"{base}/{uid}/media_publish", {"creation_id": c["id"], "access_token": tok})

    publish({"media_type": "REELS", "video_url": f"{public}/reel.mp4", "caption": caption,
             "share_to_feed": "true"})
    return "posted reel"


# ---------- Facebook Page ----------
def facebook(row, d, public, caption):
    pid, tok = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")
    if not (pid and tok and public):
        return "skipped"
    base = f"https://graph.facebook.com/{env('META_GRAPH_VERSION', 'v21.0')}"
    msg = caption.replace("Link in bio.", "").rstrip() + f"\n\nGet it: {link_for(row)}"
    form("POST", f"{base}/{pid}/photos",
         {"url": f"{public}/square.png", "message": msg, "access_token": tok})
    return "posted"


# ---------- Pinterest ----------
def pinterest_token():
    """Use the refresh token when configured (access tokens expire after 30 days),
    otherwise fall back to a static PINTEREST_TOKEN."""
    rt, app_id, secret = env("PINTEREST_REFRESH_TOKEN"), env("PINTEREST_APP_ID"), env("PINTEREST_APP_SECRET")
    if rt and app_id and secret:
        basic = base64.b64encode(f"{app_id}:{secret}".encode()).decode()
        r = http("POST", "https://api.pinterest.com/v5/oauth/token",
                 raw=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": rt}).encode(),
                 headers={"Authorization": f"Basic {basic}",
                          "Content-Type": "application/x-www-form-urlencoded"})
        return r["access_token"]
    return env("PINTEREST_TOKEN")


def pinterest(row, d):
    board = env("PINTEREST_BOARD_ID")
    if not board or not (env("PINTEREST_TOKEN") or env("PINTEREST_REFRESH_TOKEN")):
        return "skipped"
    tok = pinterest_token()
    with open(os.path.join(d, "story.png"), "rb") as fh:
        data = base64.b64encode(fh.read()).decode()
    pin = {"board_id": board, "title": f"{row['word']}: word of the day"[:100],
           "description": f"{row['word']} ({row['part_of_speech']}): {row['definition']} "
                          f"Example: {row.get('example', '')}"[:500],
           "alt_text": f"{row['word']}: {row['definition']}"[:500],
           "media_source": {"source_type": "image_base64", "content_type": "image/png", "data": data}}
    if link_for(row):
        pin["link"] = link_for(row)
    http("POST", "https://api.pinterest.com/v5/pins", pin,
         headers={"Authorization": f"Bearer {tok}"})
    return "posted"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    row = next((r for r in load_rows() if r["date"] == a.date), None)
    if not row:
        sys.exit(f"No word for {a.date}")
    d = os.path.join(HERE, "output", a.date)
    with open(os.path.join(d, "caption.txt")) as fh:
        caption = fh.read()
    public = env("PUBLIC_BASE_URL", "").rstrip("/")
    public = f"{public}/{a.date}" if public else ""

    failed = False
    for name, fn in [("bluesky", lambda: bluesky(row, d)), ("mastodon", lambda: mastodon(row, d)),
                     ("threads", lambda: threads(row, d, public)),
                     ("instagram", lambda: instagram(row, d, public, caption)),
                     ("facebook", lambda: facebook(row, d, public, caption)),
                     ("pinterest", lambda: pinterest(row, d))]:
        try:
            print(f"{name}: {fn()}")
        except Exception as e:  # one platform failing must not block the others
            failed = True
            print(f"{name}: FAILED {e}")
    sys.exit(1 if failed else 0)
