"""
AutoClipper Professional SaaS
Core clipping engine (audio-first + section download ready)
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional


def mmss(sec):
    if sec is None:
        return "--:--"
    sec = int(max(sec, 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Progress:
    def __init__(self, callback: Optional[Callable] = None):
        self.cb = callback
        self.t0 = time.time()

    def update(self, stage: str, frac: float, note: str = ""):
        if self.cb:
            self.cb(stage, frac, note)


def download_audio_only(url: str, work: Path, prog: Progress) -> Path:
    """Download only audio for fast transcription. Tries multiple formats."""
    prog.update("download", 0.1, "Downloading audio only...")
    out = work / "audio.%(ext)s"
    final = work / "audio.m4a"

    # Try best audio first, then fallback
    attempts = [
        ["yt-dlp", "-f", "bestaudio", "--extract-audio", "--audio-format", "m4a", "-o", str(out), "--no-playlist", "--no-warnings", url],
        ["yt-dlp", "-f", "ba", "-o", str(work / "audio.m4a"), "--no-playlist", "--no-warnings", url],
        ["yt-dlp", "-f", "worstaudio", "-o", str(work / "audio.webm"), "--no-playlist", url],
    ]

    last_error = ""
    for cmd in attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode == 0:
                # Find the downloaded file
                for f in work.glob("audio.*"):
                    if f.suffix in (".m4a", ".webm", ".mp3", ".opus"):
                        prog.update("download", 1.0, "Audio ready")
                        return f
            last_error = result.stderr[-300:] if result.stderr else "Unknown error"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"yt-dlp failed. Update yt-dlp or try another video. Error: {last_error}")


def download_section(url: str, start: float, end: float, out_path: Path) -> Path:
    """Download only a specific time range of the video."""
    section = f"*{start:.2f}-{end:.2f}"
    cmd = [
        "yt-dlp",
        "--download-sections", section,
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", str(out_path),
        "--no-playlist",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def transcribe(audio: Path, model_size: str, prog: Progress, caption_lang: str = "en"):
    from faster_whisper import WhisperModel
    prog.update("transcribe", 0.05, "Loading model...")
    # Use base model for better accuracy (still reasonably fast)
    model = WhisperModel("base", compute_type="int8")
    
    # Always auto-detect first
    kwargs = {
        "word_timestamps": True,
        "vad_filter": True,
        "beam_size": 5,
    }
    
    # If user wants English captions from any language, translate
    if caption_lang in ("en", "translate", "english"):
        kwargs["task"] = "translate"
    elif caption_lang and caption_lang not in ("auto", ""):
        kwargs["language"] = caption_lang
    
    segs, info = model.transcribe(str(audio), **kwargs)
    detected = getattr(info, "language", "unknown")
    
    out = []
    for s in segs:
        text = s.text.strip()
        if not text:
            continue
        words = [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])]
        out.append({
            "start": s.start,
            "end": s.end,
            "text": text,
            "words": words
        })
    prog.update("transcribe", 1.0, f"Detected: {detected}")
    return out


def pick_heuristic(segments, n, min_len, max_len):
    if not segments:
        return [{"start": 10, "end": 40, "title": "Highlight", "reason": "Default"}] * n
    scored = []
    for s in segments:
        text = s["text"].lower()
        score = len(text.split())
        if any(w in text for w in ["how", "why", "what", "secret", "tip", "mistake"]):
            score += 5
        if "?" in text:
            score += 3
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    picks = []
    used = set()
    for _, s in scored:
        length = s["end"] - s["start"]
        # Expand short segments to meet min length
        start = s["start"]
        end = s["end"]
        # Force exact length within range
        target = (min_len + max_len) // 2
        end = start + target
        if end - start < min_len:
            end = start + min_len
        if end - start > max_len:
            end = start + max_len
        key = (round(start, 1), round(end, 1))
        if key in used:
            continue
        used.add(key)
        picks.append({
            "start": start,
            "end": end,
            "title": s["text"][:50] or f"Clip {len(picks)+1}",
            "reason": "Selected moment"
        })
        if len(picks) >= n:
            break
    # Fill remaining if not enough
    while len(picks) < n:
        picks.append({
            "start": 10 + len(picks)*30,
            "end": 40 + len(picks)*30,
            "title": f"Clip {len(picks)+1}",
            "reason": "Additional clip"
        })
    return picks[:n]


def render_vertical(video: Path, clip: dict, out_path: Path, layout: str = "crop", segments=None, caption_position="bottom", caption_color="yellow"):
    start = clip["start"]
    duration = max(clip["end"] - start, 1)
    vf = "crop=ih*9/16:ih" if layout == "crop" else "split[orig][copy];[copy]scale=1080:1920,boxblur=20:10[bg];[bg][orig]overlay=(W-w)/2:(H-h)/2"
    vf += ",scale=1080:1920"

    # Build simple ASS captions from segments if available
    ass_path = None
    if segments:
        ass_path = out_path.with_suffix(".ass")
        # Color mapping (ASS uses &HAABBGGRR)
        colors = {"yellow": "&H0000FFFF", "white": "&H00FFFFFF", "black": "&H00000000"}
        align = "5" if caption_position == "center" else "2"  # 5=center, 2=bottom
        col = colors.get(caption_color, "&H0000FFFF")
        bold = "-1" if caption_color == "black" else "0"
        lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "",
                 "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Alignment",
                 f"Style: Default,Arial,52,{col},&H00000000,{bold},{align}", "",
                 "[Events]", "Format: Layer, Start, End, Style, Text"]
        for s in segments:
            if s["end"] < start or s["start"] > clip["end"]:
                continue
            t1 = max(0, s["start"] - start)
            t2 = min(duration, s["end"] - start)
            text = s["text"].replace("\n", " ").replace(",", " ")[:80]
            def fmt(t):
                h, rem = divmod(int(t), 3600)
                m, s_ = divmod(rem, 60)
                return f"{h}:{m:02d}:{s_:02d}.00"
            lines.append(f"Dialogue: 0,{fmt(t1)},{fmt(t2)},Default,{text}")
        ass_path.write_text("\n".join(lines), encoding="utf-8")
        vf += f",ass={ass_path.name}"

    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", str(video),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path)
    ]
    # Run from the folder so ASS path works
    subprocess.run(cmd, check=True, capture_output=True, cwd=out_path.parent)
    if ass_path and ass_path.exists():
        ass_path.unlink(missing_ok=True)
    return out_path


def run_job(url: str, clips: int = 5, min_len: int = 20, max_len: int = 55,
            layout: str = "crop", caption_lang: str = "en", whisper: str = "tiny",
            out_dir: Path = None, progress_cb=None):
    prog = Progress(progress_cb)
    out_dir = out_dir or Path("clips")
    out_dir.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clip_"))

    try:
        audio = download_audio_only(url, work, prog)
        segments = transcribe(audio, whisper, prog, caption_lang)
        if not segments:
            raise ValueError("No speech detected")

        picks = pick_heuristic(segments, clips, min_len, max_len)
        results = []
        for i, c in enumerate(picks, 1):
            prog.update("render", (i-1)/len(picks), f"Rendering clip {i}")
            section_path = work / f"sec_{i}.mp4"
            download_section(url, c["start"], c["end"], section_path)
            final = out_dir / f"clip_{i:02d}.mp4"
            render_vertical(section_path, {"start": 0, "end": c["end"]-c["start"]}, final, layout, segments)
            results.append({**c, "file": str(final)})
        prog.update("render", 1.0, "Done")
        return results
    finally:
        shutil.rmtree(work, ignore_errors=True)
