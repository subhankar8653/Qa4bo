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
"""

import time
import requests

STATUS_FINISHED = 4
STATUS_FAILED = (5, 6)


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

    def upload_video(self, video_id: str, file_path: str) -> None:
        """Uploads the raw file bytes to a previously created video slot."""
        with open(file_path, "rb") as f:
            resp = requests.put(
                f"{self.base_url}/videos/{video_id}",
                headers={**self.headers, "Content-Type": "application/octet-stream"},
                data=f,
                timeout=None,
            )
        resp.raise_for_status()

    def create_video_from_url(self, source_url: str, title: str) -> str:
        """Bunny 'Fetch' API — Bunny server khud URL se video download karega.
        Local download/upload step bilkul bypass ho jaata hai."""
        resp = requests.post(
            f"{self.base_url}/videos/fetch",
            headers={**self.headers, "content-type": "application/json"},
            json={"url": source_url, "title": title},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["guid"]

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
        """Polls until status == Finished. Raises on failure/timeout."""
        elapsed = 0
        while elapsed < timeout:
            data = self.get_video(video_id)
            status = data.get("status")
            progress = data.get("encodeProgress", 0)

            if on_progress:
                on_progress(status, progress)

            if status == STATUS_FINISHED:
                return data
            if status in STATUS_FAILED:
                raise RuntimeError(f"Bunny encoding failed (status={status})")

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError("Bunny encoding did not finish within the timeout window")

    @staticmethod
    def available_resolutions(video_data: dict) -> list:
        raw = video_data.get("availableResolutions") or ""
        return [r for r in raw.split(",") if r]

    # ---------- download ----------

    def mp4_url(self, video_id: str, resolution: str) -> str:
        """resolution like '720p' — note: MP4 fallback tops out at 720p
        unless only a higher single resolution is enabled in the library."""
        return f"https://{self.pull_zone}/{video_id}/play_{resolution}.mp4"

    def download_resolution(self, video_id: str, resolution: str, dest_path: str) -> str:
        url = self.mp4_url(video_id, resolution)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest_path

    def delete_video(self, video_id: str) -> None:
        requests.delete(f"{self.base_url}/videos/{video_id}", headers=self.headers, timeout=30)
