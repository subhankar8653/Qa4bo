"""
Thin wrapper around the Bunny.net Stream HTTP API.

Docs referenced:
- Create Video:   POST /library/{id}/videos
- Upload Video:   PUT  /library/{id}/videos/{videoId}
- Get Video:      GET  /library/{id}/videos/{videoId}
- MP4 fallback URL: https://{pull_zone}/{video_id}/play_{resolution}.mp4
  (resolution must include the trailing "p", e.g. "720p")

Video status codes (from Bunny docs):
  0 Created | 1 Uploaded | 2 Processing | 3 Transcoding
  4 Finished | 5 Error | 6 UploadFailed | 7 JitSegmenting | 8 JitPlaylistsCreated

NOTE: this module is internal / developer-facing only. Nothing in here is ever
shown to end users of the bot (see bot.py, jo saari user-facing strings ko
generic "processing engine" language mein rakhta hai).
"""

import os
import time
import requests

STATUS_FINISHED = 4
STATUS_FAILED = (5, 6)

STATUS_LABELS = {
    0: "queued",
    1: "queued",
    2: "processing",
    3: "encoding",
    4: "finished",
    5: "failed",
    6: "failed",
    7: "finalizing",
    8: "finalizing",
}


class _ProgressFileReader:
    """Wraps a file object so every .read() call reports cumulative bytes
    uploaded so far via on_progress(uploaded, total). Lets us stream a PUT
    upload with progress instead of loading the whole file into memory."""

    def __init__(self, file_obj, total_size, on_progress=None, chunk_size=1024 * 1024):
        self._f = file_obj
        self._total = total_size
        self._uploaded = 0
        self._on_progress = on_progress
        self._chunk_size = chunk_size

    def __len__(self):
        # requests uses len() to set Content-Length when possible
        return self._total

    def read(self, size=-1):
        size = self._chunk_size if size is None or size < 0 else size
        chunk = self._f.read(size)
        if chunk:
            self._uploaded += len(chunk)
            if self._on_progress:
                self._on_progress(self._uploaded, self._total)
        return chunk


class BunnyStreamClient:
    def __init__(self, library_id: str, api_key: str, pull_zone: str):
        self.library_id = library_id
        self.api_key = api_key
        # Accept either "vz-xxxx.b-cdn.net" or a full URL, normalize to bare host
        self.pull_zone = pull_zone.replace("https://", "").replace("http://", "").rstrip("/")
        self.base_url = f"https://video.bunnycdn.com/library/{library_id}"
        self.headers = {
            "AccessKey": api_key,
            "accept": "application/json",
        }

    # ---------- upload ----------

    def create_video(self, title: str) -> str:
        """Creates a video 'slot' and returns its GUID (video_id)."""
        resp = requests.post(
            f"{self.base_url}/videos",
            headers={**self.headers, "content-type": "application/json"},
            json={"title": title},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["guid"]

    def upload_video(self, video_id: str, file_path: str, on_progress=None) -> None:
        """Uploads the raw file bytes to a previously created video slot.
        on_progress(uploaded_bytes, total_bytes) is called after every chunk
        read, so callers can drive a progress bar."""
        total_size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            body = _ProgressFileReader(f, total_size, on_progress=on_progress) if on_progress else f
            resp = requests.put(
                f"{self.base_url}/videos/{video_id}",
                headers={**self.headers, "Content-Type": "application/octet-stream"},
                data=body,
                timeout=None,
            )
        resp.raise_for_status()

    def create_video_from_url(self, source_url: str, title: str) -> str:
        """Pehle ek video-slot banata hai (create_video se guid milta hai),
        phir usi guid pe Bunny ka per-video Fetch API call karta hai —
        Bunny server khud us URL se download karega, local download bypass ho jaata hai.
        (Library-level /videos/fetch endpoint guid return nahi karta, isliye ye do-step tarika chahiye.)"""
        video_id = self.create_video(title)
        resp = requests.post(
            f"{self.base_url}/videos/{video_id}/fetch",
            headers={**self.headers, "content-type": "application/json"},
            json={"url": source_url},
            timeout=60,
        )
        resp.raise_for_status()
        return video_id

    # ---------- status ----------

    def get_video(self, video_id: str) -> dict:
        resp = requests.get(
            f"{self.base_url}/videos/{video_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def wait_until_ready(self, video_id: str, poll_interval: int = 10,
                          timeout: int = 1800, on_progress=None) -> dict:
        """Polls until status == Finished. Raises on failure/timeout.
        on_progress(status_label, progress_pct) is called on every poll so
        callers can drive an encoding progress bar."""
        elapsed = 0
        while elapsed < timeout:
            data = self.get_video(video_id)
            status = data.get("status")
            progress = data.get("encodeProgress", 0)

            if on_progress:
                on_progress(STATUS_LABELS.get(status, "processing"), progress)

            if status == STATUS_FINISHED:
                return data
            if status in STATUS_FAILED:
                raise RuntimeError(f"encoding failed (status={status})")

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError("encoding did not finish within the timeout window")

    @staticmethod
    def available_resolutions(video_data: dict) -> list:
        raw = video_data.get("availableResolutions") or ""
        return [r for r in raw.split(",") if r]

    # ---------- download ----------

    def mp4_url(self, video_id: str, resolution: str) -> str:
        """resolution like '720p' — note: MP4 fallback tops out at 720p
        unless only a higher single resolution is enabled in the library."""
        return f"https://{self.pull_zone}/{video_id}/play_{resolution}.mp4"

    def download_resolution(self, video_id: str, resolution: str, dest_path: str,
                             on_progress=None, max_retries: int = 6, retry_delay: int = 5) -> str:
        """CDN pe kabhi kabhi file 'availableResolutions' mein aane ke turant baad
        propagate hone mein kuch second lagte hain (404 aata hai) — isliye
        thodi der retry karte hain. on_progress(downloaded_bytes, total_bytes) callback
        har chunk ke baad call hota hai (progress bar dikhane ke liye)."""
        url = self.mp4_url(video_id, resolution)
        last_error = None

        for attempt in range(max_retries):
            try:
                with requests.get(url, stream=True, timeout=60) as r:
                    if r.status_code == 404 and attempt < max_retries - 1:
                        last_error = requests.exceptions.HTTPError(
                            f"404 (CDN propagation ka wait, attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(retry_delay)
                        continue

                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0))
                    downloaded = 0
                    with open(dest_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if on_progress:
                                    on_progress(downloaded, total)
                    return dest_path
            except requests.exceptions.HTTPError as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        raise last_error or RuntimeError("download fail ho gaya, wajah pata nahi chali")

    def delete_video(self, video_id: str) -> None:
        requests.delete(f"{self.base_url}/videos/{video_id}", headers=self.headers, timeout=30)
