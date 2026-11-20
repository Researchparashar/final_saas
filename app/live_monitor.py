"""
Live stream monitor for professional SaaS
Continuous recording + every 10 minutes best moments
"""
import os
import time
import signal
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from app.clipper_core import (
    transcribe, pick_heuristic, download_section, render_vertical, Progress
)
from app.database import use_tokens, add_tokens


class LiveMonitor:
    def __init__(
        self,
        job_id: int,
        user_id: int,
        url: str,
        out_dir: Path,
        clips_per_cycle: int = 3,
        min_len: int = 20,
        max_len: int = 55,
        layout: str = "crop",
        caption_lang: str = "en",
        whisper_model: str = "base",
        interval_sec: int = 600,
        progress_cb: Optional[Callable] = None,
    ):
        self.job_id = job_id
        self.user_id = user_id
        self.url = url
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.clips_per_cycle = clips_per_cycle
        self.min_len = min_len
        self.max_len = max_len
        self.layout = layout
        self.caption_lang = caption_lang
        self.whisper_model = whisper_model
        self.interval_sec = interval_sec
        self.progress_cb = progress_cb

        self.record_proc: Optional[subprocess.Popen] = None
        self.recording_file = self.out_dir / "live_recording.mp4"
        self.last_processed = 0.0
        self.running = False
        self.cycle = 0
        self._stop = threading.Event()

    def _log(self, msg: str):
        print(f"[Live job={self.job_id}] {msg}")
        if self.progress_cb:
            try:
                self.progress_cb(msg)
            except Exception:
                pass

    def start_recording(self) -> bool:
        cmd = [
            "yt-dlp", "--no-part", "--hls-use-mpegts",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
            "--merge-output-format", "mp4",
            "-o", str(self.recording_file),
            "--no-playlist", "--retries", "20",
            self.url,
        ]
        self._log("Starting live recording...")
        try:
            self.record_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(8)
            if self.record_proc.poll() is not None:
                self._log("Recording failed to start")
                return False
            self._log("Recording started")
            return True
        except Exception as e:
            self._log(f"Recording error: {e}")
            return False

    def stop_recording(self):
        if self.record_proc and self.record_proc.poll() is None:
            try:
                self.record_proc.send_signal(signal.SIGINT)
                self.record_proc.wait(timeout=10)
            except Exception:
                self.record_proc.kill()
            self.record_proc = None

    def get_duration(self) -> float:
        if not self.recording_file.exists():
            return 0.0
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(self.recording_file)],
                capture_output=True, text=True, timeout=15
            )
            return float(r.stdout.strip() or 0)
        except Exception:
            return 0.0

    def process_cycle(self):
        duration = self.get_duration()
        if duration < self.min_len + 10:
            self._log(f"Too short ({duration:.0f}s), waiting...")
            return []

        window_start = max(0.0, duration - 720)
        window_start = max(window_start, self.last_processed - 30)
        if duration - window_start < self.min_len:
            return []

        self.cycle += 1
        self._log(f"Cycle {self.cycle}: {window_start:.0f}s → {duration:.0f}s")

        # Extract recent segment
        seg = self.out_dir / f"seg_{self.cycle}.mp4"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{window_start:.1f}", "-to", f"{duration:.1f}",
                "-i", str(self.recording_file), "-c", "copy", str(seg)
            ], check=True, timeout=90)
        except Exception as e:
            self._log(f"Segment extract failed: {e}")
            return []

        prog = Progress()
        try:
            segments = transcribe(seg, self.whisper_model, prog, self.caption_lang)
        except Exception as e:
            self._log(f"Transcribe failed: {e}")
            seg.unlink(missing_ok=True)
            return []

        # Shift timestamps
        for s in segments:
            s["start"] += window_start
            s["end"] += window_start

        picks = pick_heuristic(segments, self.clips_per_cycle, self.min_len, self.max_len)
        results = []
        cost = len(picks) * 8
        if not use_tokens(self.user_id, cost):
            self._log("Not enough tokens for this cycle")
            seg.unlink(missing_ok=True)
            return []

        for i, c in enumerate(picks, 1):
            try:
                final = self.out_dir / f"live_c{self.cycle:02d}_{i:02d}.mp4"
                # Render from the full recording at absolute time
                render_vertical(self.recording_file, c, final, self.layout)
                results.append({**c, "file": str(final), "cycle": self.cycle})
                self._log(f"  Clip saved: {final.name}")
            except Exception as e:
                self._log(f"  Render failed: {e}")

        self.last_processed = duration
        seg.unlink(missing_ok=True)
        return results

    def run(self):
        self.running = True
        if not self.start_recording():
            self.running = False
            return

        self._log(f"Live monitor running (every {self.interval_sec//60} min)")
        try:
            while self.running and not self._stop.is_set():
                for _ in range(self.interval_sec // 15):
                    if self._stop.is_set() or not self.running:
                        break
                    if self.record_proc and self.record_proc.poll() is not None:
                        self._log("Stream ended")
                        self.process_cycle()
                        self.running = False
                        break
                    time.sleep(15)
                if not self.running:
                    break
                try:
                    self.process_cycle()
                except Exception as e:
                    self._log(f"Cycle error: {e}")
        finally:
            self.stop_recording()
            try:
                self.process_cycle()
            except Exception:
                pass
            self.running = False
            self._log("Live monitor stopped")

    def stop(self):
        self._stop.set()
        self.running = False


_active: dict[int, LiveMonitor] = {}


def start_live(job_id: int, user_id: int, url: str, out_dir: Path, **kwargs) -> LiveMonitor:
    mon = LiveMonitor(job_id, user_id, url, out_dir, **kwargs)
    _active[job_id] = mon
    t = threading.Thread(target=mon.run, daemon=True)
    t.start()
    return mon


def stop_live(job_id: int):
    mon = _active.get(job_id)
    if mon:
        mon.stop()
        _active.pop(job_id, None)
