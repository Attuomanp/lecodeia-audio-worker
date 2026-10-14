#!/usr/bin/env python3
"""Worker Edge TTS distant : Hostinger ne lance que PHP/MySQL et stocke les MP3."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import edge_tts

API_URL = os.environ.get("LECODEIA_AUDIO_API_URL", "").strip()
TOKEN = os.environ.get("LECODEIA_AUDIO_TOKEN", "").strip()
VOICE = os.environ.get("LECODEIA_AUDIO_VOICE", "fr-FR-VivienneMultilingualNeural").strip()
RATE = os.environ.get("LECODEIA_AUDIO_RATE", "-5%").strip()
MAX_PARTS = max(1, min(20, int(os.environ.get("LECODEIA_AUDIO_MAX_PARTS", "8"))))
ENQUEUE_EXISTING = os.environ.get("ENQUEUE_EXISTING_ARTICLES", "false").strip().lower() in {"1", "true", "yes", "on"}
RETRY_FAILED = os.environ.get("RETRY_FAILED_ARTICLES", "false").strip().lower() in {"1", "true", "yes", "on"}


class WorkerError(RuntimeError):
    pass


def endpoint(action: str) -> str:
    return API_URL + ("&" if "?" in API_URL else "?") + urlencode({"action": action})


def request_json(action: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = Request(endpoint(action), data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=45) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise WorkerError(f"API {action}: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError) as exc:
        raise WorkerError(f"API {action}: erreur réseau: {exc}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerError(f"API {action}: réponse JSON illisible") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise WorkerError(f"API {action}: réponse négative")
    return result


def upload_audio(job: dict, path: Path) -> None:
    content = path.read_bytes()
    if len(content) < 256:
        raise WorkerError("Edge TTS a renvoyé un MP3 vide ou incomplet")
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "audio/mpeg",
        "Accept": "application/json",
        "X-Audio-Article-Id": str(job["article_id"]),
        "X-Audio-Part-Index": str(job["part_index"]),
        "X-Audio-Source-Hash": str(job["source_hash"]),
        "X-Audio-Lease": str(job["lease_token"]),
    }
    req = Request(endpoint("complete"), data=content, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=60) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise WorkerError(f"Upload MP3: HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError) as exc:
        raise WorkerError(f"Upload MP3: erreur réseau: {exc}") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerError("Upload MP3: réponse JSON illisible") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise WorkerError("Upload MP3: le serveur a refusé le fichier")


async def synthesize(text: str, target: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if target.exists():
                target.unlink()
            communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE)
            await communicate.save(str(target))
            if target.is_file() and target.stat().st_size >= 256:
                return
            raise WorkerError("Fichier MP3 trop petit")
        except Exception as exc:  # service en ligne : petites reprises si réseau instable
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
    assert last_error is not None
    raise WorkerError(str(last_error))


def report_failure(job: dict, message: str) -> None:
    payload = {
        "article_id": job.get("article_id"),
        "source_hash": job.get("source_hash"),
        "part_index": job.get("part_index"),
        "lease_token": job.get("lease_token"),
        "error": str(message)[:900],
    }
    try:
        request_json("fail", "POST", payload)
    except Exception as exc:
        print(f"Impossible de signaler l'échec au site : {exc}", file=sys.stderr)


async def main() -> int:
    if not API_URL.startswith("https://"):
        print("LECODEIA_AUDIO_API_URL doit être une URL HTTPS.", file=sys.stderr)
        return 2
    if len(TOKEN) < 32:
        print("LECODEIA_AUDIO_TOKEN doit être un secret aléatoire d'au moins 32 caractères.", file=sys.stderr)
        return 2

    if ENQUEUE_EXISTING or RETRY_FAILED:
        summary = request_json("enqueue-all", "POST", {"retry_failed": RETRY_FAILED})
        result = summary.get("result", {})
        print(f"Articles publiés détectés : {result.get('published', '?')} ; mis en file : {result.get('queued', '?')}")

    processed = 0
    with tempfile.TemporaryDirectory(prefix="lecodeia-edge-tts-") as tmp:
        target_dir = Path(tmp)
        while processed < MAX_PARTS:
            claim = request_json("claim")
            job = claim.get("job")
            if job is None:
                print(f"File vide ; parties générées pendant cette exécution : {processed}")
                break
            required = {"article_id", "source_hash", "part_index", "text", "lease_token"}
            if not isinstance(job, dict) or not required.issubset(job):
                raise WorkerError("Le site a renvoyé une réservation incomplète")

            article_id = int(job["article_id"])
            part_index = int(job["part_index"])
            print(f"Synthèse article #{article_id}, partie {part_index + 1}…")
            target = target_dir / "result.mp3"
            try:
                await synthesize(str(job["text"]), target)
                upload_audio(job, target)
            except Exception as exc:
                report_failure(job, str(exc))
                raise
            processed += 1
            print(f"Partie {part_index + 1} de l'article #{article_id} enregistrée.")
            # Petit intervalle pour ne pas marteler le service Edge TTS.
            await asyncio.sleep(0.8)

    print(f"Terminé : {processed} partie(s) envoyée(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except WorkerError as exc:
        print(f"Erreur worker : {exc}", file=sys.stderr)
        raise SystemExit(1)
