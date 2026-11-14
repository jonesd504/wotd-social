#!/usr/bin/env python3
"""Render today's word as social-ready images + a short vertical video.

Usage:
  python generate.py                 # uses today's date from words.csv
  python generate.py --date 2026-11-16
  python generate.py --all           # render every row (for batch scheduling tools)

Outputs into output/<date>/:
  square.png   1080x1350  Instagram feed, Threads, Bluesky, X, Facebook
  story.png    1080x1920  Stories, Pinterest idea pins
  reel.mp4     1080x1920, ~8s, word -> pronunciation -> definition -> CTA
               (TikTok, Reels, YouTube Shorts)
  caption.txt  platform-neutral caption with hashtags

Edit BRAND below to match the app.
"""
import argparse, csv, datetime as dt, os, subprocess, textwrap
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

BRAND = {
    "app_name": os.environ.get("APP_NAME", "Word of the Day"),
    "cta": os.environ.get("APP_CTA", "A new word every morning. Free on Android."),
    "handle": os.environ.get("APP_HANDLE", "wordoftheday.uk"),
    # The app's own dark theme (src/global.css)
    "bg": (0x17, 0x14, 0x10),
    "fg": (0xF3, 0xEC, 0xE0),
    "accent": (0xD9, 0x9A, 0x6C),
    "muted": (0xA8, 0x9C, 0x8C),
    "hashtags": "#wordoftheday #vocabulary #words #englishwords #logophile #learnenglish",
}

FONT_DIR = "/usr/share/fonts/truetype"
def font(name, size):
    paths = {
        "serif_bold": f"{FONT_DIR}/dejavu/DejaVuSerif-Bold.ttf",
        "serif_italic": f"{FONT_DIR}/liberation/LiberationSerif-Italic.ttf",
        "sans": f"{FONT_DIR}/dejavu/DejaVuSans.ttf",
        "sans_bold": f"{FONT_DIR}/dejavu/DejaVuSans-Bold.ttf",
    }
    return ImageFont.truetype(paths[name], size)


def fit_word(draw, word, max_w, start=150, min_size=60):
    size = start
    while size > min_size:
        f = font("serif_bold", size)
        if draw.textlength(word, font=f) <= max_w:
            return f
        size -= 4
    return font("serif_bold", min_size)


def centered(draw, y, text, f, fill, W):
    w = draw.textlength(text, font=f)
    draw.text(((W - w) / 2, y), text, font=f, fill=fill)
    bbox = f.getbbox(text)
    return y + (bbox[3] - bbox[1])


def wrapped(draw, y, text, f, fill, W, width_chars, gap=14):
    for line in textwrap.wrap(text, width=width_chars):
        y = centered(draw, y, line, f, fill, W) + gap
    return y


def render(row, W, H, stage=3):
    """stage: 0=label only, 1=+word, 2=+pronunciation/pos, 3=+definition/example, 4=CTA."""
    img = Image.new("RGB", (W, H), BRAND["bg"])
    d = ImageDraw.Draw(img)
    pad = 90
    top = int(H * (0.28 if H / W > 1.5 else 0.22))

    centered(d, int(H * 0.08), BRAND["app_name"].upper(), font("sans_bold", 34), BRAND["accent"], W)
    date_txt = dt.date.fromisoformat(row["date"]).strftime("%A %-d %B")
    centered(d, int(H * 0.08) + 52, date_txt, font("sans", 30), BRAND["muted"], W)

    if stage >= 1:
        wf = fit_word(d, row["word"], W - 2 * pad)
        y = centered(d, top, row["word"], wf, BRAND["fg"], W) + 40
    if stage >= 2:
        meta = f"/{row['pronunciation']}/  ·  {row['part_of_speech']}"
        y = centered(d, y, meta, font("serif_italic", 44), BRAND["accent"], W) + 50
        d.line([(W / 2 - 80, y), (W / 2 + 80, y)], fill=BRAND["accent"], width=4)
        y += 60
    if stage >= 3:
        y = wrapped(d, y, row["definition"], font("sans", 50), BRAND["fg"], W, 32, gap=20) + 50
        if row.get("example"):
            wrapped(d, y, f"“{row['example']}”", font("serif_italic", 40), BRAND["muted"], W, 40, gap=14)
    if stage >= 4 or stage == 3:
        centered(d, H - 150, BRAND["cta"], font("sans_bold", 34), BRAND["accent"], W)
        centered(d, H - 100, BRAND["handle"], font("sans", 30), BRAND["muted"], W)
    return img


def make_video(row, outdir):
    """Stitch staged frames into a ~8s reveal video. No music (add trending audio
    natively in TikTok/IG if posting manually; API posts go out silent)."""
    W, H = 1080, 1920
    timeline = [(0, 0.8), (1, 1.6), (2, 1.6), (3, 4.0)]
    listfile = os.path.join(outdir, "frames.txt")
    with open(listfile, "w") as fh:
        for stage, secs in timeline:
            p = os.path.join(outdir, f"_stage{stage}.png")
            render(row, W, H, stage).save(p)
            fh.write(f"file '{p}'\nduration {secs}\n")
        fh.write(f"file '{p}'\n")  # ffmpeg concat quirk: repeat last frame
    out = os.path.join(outdir, "reel.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listfile,
        "-f", "lavfi", "-t", "8", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest",
        "-movflags", "+faststart", out,
    ], check=True)
    for stage, _ in timeline:
        os.remove(os.path.join(outdir, f"_stage{stage}.png"))
    os.remove(listfile)


def caption(row):
    return (
        f"{row['word']} (/{row['pronunciation']}/, {row['part_of_speech']})\n\n"
        f"{row.get('plain') or row['definition']}\n\n"
        + (f"“{row['example']}”\n\n" if row.get("example") else "")
        + (f"Origin: {row['etymology']}\n\n" if row.get("etymology") else "")
        + "Use it in a sentence below 👇\n\n"
        + f"{BRAND['cta']}\n\n{BRAND['hashtags']}"
    )


def build(row):
    outdir = os.path.join(HERE, "output", row["date"])
    os.makedirs(outdir, exist_ok=True)
    render(row, 1080, 1350).save(os.path.join(outdir, "square.png"))
    render(row, 1080, 1920).save(os.path.join(outdir, "story.png"))
    make_video(row, outdir)
    with open(os.path.join(outdir, "caption.txt"), "w") as fh:
        fh.write(caption(row))
    return outdir


# Mirrors src/utils/daily-word.ts: a straight queue starting 22 Sep 2026,
# index = days since epoch, clamped to the last word. Never wraps.
APP_EPOCH = dt.date(2026, 9, 22)
WORDS_API = os.environ.get("WORDS_API", "https://wordoftheday.uk/words")
SITE = os.environ.get("SITE_URL", "https://wordoftheday.uk")


def load_rows(days_ahead=0):
    """Rows for the app's live queue (same word the app shows that day).
    Falls back to words.csv if the API is unreachable or WORDS_API=off."""
    if WORDS_API != "off":
        try:
            import json, urllib.request
            with urllib.request.urlopen(WORDS_API, timeout=30) as r:
                words = json.load(r)
            rows = []
            for i, w in enumerate(words):
                date = APP_EPOCH + dt.timedelta(days=i)
                rows.append({
                    "date": date.isoformat(), "id": w["id"], "word": w["word"],
                    "pronunciation": w["pronunciation"], "part_of_speech": w["partOfSpeech"],
                    "definition": w["definition"], "plain": w.get("plainMeaning", ""),
                    "example": (w.get("examples") or [""])[0],
                    "etymology": w.get("etymology", ""),
                    "link": f"{SITE}/get?w={w['id']}",
                })
            return rows
        except Exception as e:  # noqa: BLE001
            print(f"API unavailable ({e}); using words.csv")
    with open(os.path.join(HERE, "words.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    rows = load_rows()
    todo = rows if a.all else [r for r in rows if r["date"] == a.date]
    if not todo:
        raise SystemExit(f"No word scheduled for {a.date} in words.csv")
    for r in todo:
        print(build(r))
