"""
GeminiAutomation — drive the real Gemini web UI with a Camoufox/Playwright page.

These are SYNCHRONOUS Playwright flows. Because Camoufox's sync page is bound to
the thread that created it, every function here must be called ON THE WORKER
THREAD (CamoufoxWorker.run), never from the HTTP-server thread. The engine
dispatches work through the worker's job queue to guarantee that.

The single source of brittle selectors is gemini_selectors.py. Everything here
tries an ordered list of candidates (first_locator) and degrades gracefully.

`run_job` is the entry point: it mutates the passed `job` object in place
(duck-typed: .type/.prompt/.input_media/.status/.results/.text/.error/.profile)
and saves any generated media into `media_dir`.
"""

import base64
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time

import gemini_selectors as S
import watermark

# Minimum natural dimension (px) for an image to count as a real generated
# result — filters out avatars, sidebar thumbnails, and loading placeholders
# (observed: a 64×64 placeholder appears before the full image renders).
MIN_RESULT_PX = 256


# ── low-level helpers ─────────────────────────────────────────────────────────

def first_locator(page, selectors, timeout=8000):
    """Return the first selector (string) that resolves to a visible element,
    or None. Polls up to `timeout` ms across all candidates."""
    deadline = time.monotonic() + timeout / 1000.0
    while True:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return sel
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def any_present(page, selectors):
    """True if any selector currently resolves to a visible element (no wait)."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            continue
    return False


def detect_verification(page):
    return any_present(page, S.VERIFICATION_MARKERS)


def detect_signed_out(page):
    return any_present(page, S.SIGNED_OUT_MARKERS)


def wait_until_cleared(page, job, log, poll=2.0, max_wait=600):
    """Block while a human-verification challenge is on screen. The visible
    window lets a human solve it; we just wait for the markers to disappear.
    Sets job.status='needs_verification' while waiting."""
    if not detect_verification(page):
        return True
    job.status = "needs_verification"
    log(f"[{job.profile}] human verification required — waiting for a human to solve it")
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if not detect_verification(page):
            log(f"[{job.profile}] verification cleared, resuming")
            job.status = "running"
            return True
        time.sleep(poll)
    job.status = "needs_verification"
    job.error = "verification not solved within timeout"
    return False


# ── flow steps ────────────────────────────────────────────────────────────────

def open_gemini(page, slot=0):
    url = S.APP_URL.format(slot=slot) if slot is not None else S.APP_URL_DEFAULT
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # Give the SPA a moment to hydrate the composer.
    first_locator(page, S.PROMPT_INPUT, timeout=20000)


def _decode_inputs_to_files(input_media):
    """Write base64 input media to temp files; return list of file paths."""
    paths = []
    for item in input_media or []:
        data = item.get("data", "")
        mt = item.get("media_type", "application/octet-stream")
        if not data:
            continue
        raw = base64.b64decode(data)
        ext = mimetypes.guess_extension(mt) or ".bin"
        fd, path = tempfile.mkstemp(prefix="gemini_in_", suffix=ext)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        paths.append(path)
    return paths


def _try_set_hidden_input(page, file_paths):
    for sel in S.UPLOAD_INPUT:
        try:
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.set_input_files(file_paths)
                return True
        except Exception:
            continue
    return False


def attach_files(page, file_paths):
    """Attach media via Gemini's '+' (Upload & tools) menu.

    There is no <input type=file> in the DOM until the menu's upload item is
    used, so: (1) try a pre-existing hidden input; (2) open the '+' menu and try
    again; (3) click the upload menu item and capture the OS file chooser."""
    if not file_paths:
        return

    def _click_triggers_with_chooser():
        for sel in S.FILE_SELECTOR_TRIGGERS:
            btn = page.locator(sel).first
            try:
                if btn.count() == 0:
                    continue
                with page.expect_file_chooser(timeout=8000) as fc:
                    btn.click(force=True, no_wait_after=True)
                fc.value.set_files(file_paths)
                return True
            except Exception:
                continue
        return False

    # Open the '+' (Upload & tools) menu first — this activates the hidden
    # file-selector trigger buttons.
    trig = first_locator(page, S.UPLOAD_TRIGGER, timeout=8000)
    if trig:
        try:
            page.locator(trig).first.click()
            time.sleep(0.8)
        except Exception:
            pass

    # A real hidden input may now exist — settable without an OS dialog.
    if _try_set_hidden_input(page, file_paths):
        return
    # Else force-click a hidden trigger and capture the file chooser it opens.
    if _click_triggers_with_chooser():
        return
    # Last resort: a labelled menu item.
    item = first_locator(page, S.UPLOAD_MENU_ITEM, timeout=4000)
    if item:
        try:
            with page.expect_file_chooser(timeout=8000) as fc:
                page.locator(item).first.click()
            fc.value.set_files(file_paths)
            return
        except Exception:
            pass
    raise RuntimeError("could not open a file chooser for upload "
                       "(selectors may be stale — inspect with an open_upload dump)")


def type_prompt_and_send(page, prompt):
    sel = first_locator(page, S.PROMPT_INPUT, timeout=15000)
    if not sel:
        raise RuntimeError("prompt input not found")
    box = page.locator(sel).first
    box.click()
    box.fill(prompt)
    time.sleep(0.3)
    send = first_locator(page, S.SEND_BUTTON, timeout=8000)
    if send:
        page.locator(send).first.click()
    else:
        # Fallback: Enter submits in the Quill composer.
        page.keyboard.press("Enter")


def wait_for_generation_done(page, max_wait=600):
    """Wait until the 'generating' indicators disappear (best-effort)."""
    deadline = time.monotonic() + max_wait
    # Give generation a beat to start so the indicator appears first.
    time.sleep(2.0)
    while time.monotonic() < deadline:
        if not any_present(page, S.GENERATING_INDICATOR):
            return True
        time.sleep(1.5)
    return False


def _upgrade_gusercontent(src):
    """Rewrite a googleusercontent thumbnail URL to full resolution by bumping
    its size token (=s64 / =w64-h64 / =s512-c → large)."""
    if "googleusercontent" not in src and "ggpht" not in src:
        return src
    src = re.sub(r"=s\d+(-[a-z]+)*$", "=s2048", src)
    src = re.sub(r"=w\d+-h\d+(-[a-z]+)*$", "=w2048-h2048", src)
    tail = src.rsplit("/", 1)[-1]
    if "=" not in tail and not re.search(r"=s\d+|=w\d+", src):
        src = src + "=s2048"
    return src


def grab_images_via_canvas(page):
    """Extract the latest response's generated images as (raw_bytes, media_type).

    Draws each rendered <img> (>= MIN_RESULT_PX) onto a canvas and reads it back
    — this works for blob:/data: images that can't be fetched by URL, and yields
    the full rendered resolution. Cross-origin images that would taint the canvas
    are skipped (returned via the URL path instead)."""
    try:
        data_uris = page.evaluate(
            """(args) => {
                const [sels, minpx] = args;
                const out = []; const seen = new Set();
                for (const sel of sels) {
                    for (const img of document.querySelectorAll(sel)) {
                        const key = img.currentSrc || img.src || '';
                        if (seen.has(key)) continue; seen.add(key);
                        const w = img.naturalWidth || 0, h = img.naturalHeight || 0;
                        if (Math.max(w, h) < minpx) continue;
                        try {
                            const c = document.createElement('canvas');
                            c.width = w; c.height = h;
                            c.getContext('2d').drawImage(img, 0, 0);
                            out.push(c.toDataURL('image/png'));
                        } catch (e) { /* tainted / cross-origin — skip */ }
                    }
                }
                return out;
            }""",
            [S.RESULT_IMAGE, MIN_RESULT_PX],
        )
    except Exception:
        data_uris = []
    items = []
    for durl in data_uris:
        try:
            head, b64 = durl.split(",", 1)
            mt = head[5:].split(";")[0] or "image/png"
            items.append((base64.b64decode(b64), mt))
        except Exception:
            continue
    return items


def collect_result_urls(page, kind):
    """Return full-resolution media src URLs from the latest response.

    Images are filtered by natural pixel size (>= MIN_RESULT_PX) so loading
    placeholders / avatars / thumbnails are ignored, and their googleusercontent
    URLs are upgraded to full resolution. Returns [] until a real result loads.
    """
    if kind == "video":
        urls = []
        for sel in S.RESULT_VIDEO:
            try:
                loc = page.locator(sel)
                for i in range(loc.count()):
                    el = loc.nth(i)
                    src = el.get_attribute("src")
                    if not src:
                        try:
                            src = el.locator("source").first.get_attribute("src")
                        except Exception:
                            src = None
                    if src and src not in urls:
                        urls.append(src)
                if urls:
                    break
            except Exception:
                continue
        return urls

    # Images: gather candidates with natural size via one in-page evaluate.
    try:
        cands = page.evaluate(
            """(sels) => {
                const out = []; const seen = new Set();
                for (const sel of sels) {
                    for (const img of document.querySelectorAll(sel)) {
                        const src = img.currentSrc || img.getAttribute('src');
                        if (!src || seen.has(src)) continue;
                        seen.add(src);
                        out.push({src, w: img.naturalWidth || 0, h: img.naturalHeight || 0});
                    }
                }
                return out;
            }""",
            S.RESULT_IMAGE,
        )
    except Exception:
        cands = []
    urls = []
    for c in cands:
        if max(c.get("w", 0), c.get("h", 0)) < MIN_RESULT_PX:
            continue
        # Download the exact src the browser rendered (already full-res once the
        # generated image has loaded); URL rewriting risks 403s.
        u = c["src"]
        if u not in urls:
            urls.append(u)
    return urls


def latest_response_text(page):
    sel = first_locator(page, S.RESPONSE_CONTAINER, timeout=2000)
    if not sel:
        return ""
    try:
        return page.locator(sel).last.inner_text()[:4000]
    except Exception:
        return ""


def download_media(page, url, dest_dir):
    """Download a media URL using the page's authenticated browser context.
    Returns (filename, media_type, raw_bytes)."""
    os.makedirs(dest_dir, exist_ok=True)
    if url.startswith("blob:") or url.startswith("data:"):
        # Blob/data URLs can't be fetched server-side; pull bytes in-page.
        raw = _fetch_inpage_bytes(page, url)
        media_type = "application/octet-stream"
        if url.startswith("data:"):
            media_type = url[5:].split(";", 1)[0] or media_type
    else:
        resp = page.request.get(url, timeout=120000)
        if not resp.ok:
            raise RuntimeError(f"download failed {resp.status} for {url[:80]}")
        raw = resp.body()
        media_type = (resp.headers or {}).get("content-type", "application/octet-stream").split(";")[0]
    ext = mimetypes.guess_extension(media_type) or ""
    if not ext:
        ext = ".mp4" if "video" in media_type else ".bin"
    name = hashlib.sha256(raw).hexdigest()[:32] + ext
    path = os.path.join(dest_dir, name)
    with open(path, "wb") as f:
        f.write(raw)
    return name, media_type, raw


def _fetch_inpage_bytes(page, url):
    """Fetch a blob:/data: URL from inside the page and return raw bytes."""
    b64 = page.evaluate(
        """async (u) => {
            const r = await fetch(u);
            const buf = await r.arrayBuffer();
            let bin = '';
            const bytes = new Uint8Array(buf);
            for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
            return btoa(bin);
        }""",
        url,
    )
    return base64.b64decode(b64)


# ── discovery helper ──────────────────────────────────────────────────────────

def dump_page(page, out_dir):
    """Save page HTML + screenshot for live selector discovery. Returns dict of
    written paths and which known anchors currently resolve."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = hashlib.sha1(page.url.encode()).hexdigest()[:8]
    html_path = os.path.join(out_dir, f"gemini_dump_{stamp}.html")
    png_path = os.path.join(out_dir, f"gemini_dump_{stamp}.png")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        html_path = f"(failed: {e})"
    try:
        page.screenshot(path=png_path, full_page=True)
    except Exception as e:
        png_path = f"(failed: {e})"
    resolved = {}
    for name, sels in [
        ("PROMPT_INPUT", S.PROMPT_INPUT), ("SEND_BUTTON", S.SEND_BUTTON),
        ("UPLOAD_INPUT", S.UPLOAD_INPUT), ("RESULT_IMAGE", S.RESULT_IMAGE),
        ("RESULT_VIDEO", S.RESULT_VIDEO), ("VERIFICATION", S.VERIFICATION_MARKERS),
    ]:
        resolved[name] = first_locator(page, sels, timeout=500) or None
    return {"url": page.url, "html": html_path, "screenshot": png_path, "resolved": resolved}


# ── orchestration ─────────────────────────────────────────────────────────────

def run_job(page, job, media_dir, slot=0, log=print):
    """Execute one generation job against the live page, mutating `job`."""
    job.status = "running"
    in_files = []
    try:
        if job.type == "dump":
            # Navigate to Gemini first so we capture the real composer DOM.
            try:
                open_gemini(page, slot)
            except Exception as e:
                log(f"[{job.profile}] dump: open_gemini failed: {e}")
            # prompt=="open_upload" → open the '+' menu first so we can read its items.
            if job.prompt == "open_upload":
                trig = first_locator(page, S.UPLOAD_TRIGGER, timeout=6000)
                if trig:
                    try:
                        page.locator(trig).first.click()
                        time.sleep(1.0)
                    except Exception as e:
                        log(f"[{job.profile}] dump: upload click failed: {e}")
            job.results = [dump_page(page, media_dir)]
            job.status = "completed"
            return

        open_gemini(page, slot)
        if detect_signed_out(page):
            raise RuntimeError("profile is signed out of Google")
        if not wait_until_cleared(page, job, log):
            return  # left in needs_verification

        in_files = _decode_inputs_to_files(job.input_media)
        if in_files:
            attach_files(page, in_files)
            time.sleep(1.0)

        type_prompt_and_send(page, job.prompt)

        # Poll for the result media to APPEAR (robust across locales/indicators),
        # pausing for any human-verification challenge that pops up mid-flight.
        # Images are read off the canvas (handles blob: srcs); videos by URL.
        max_wait = 900 if job.type == "video" else 300
        deadline = time.monotonic() + max_wait
        items = []          # list of (raw_bytes, media_type)
        dl_errors = []
        time.sleep(2.0)
        while time.monotonic() < deadline:
            if detect_verification(page):
                if not wait_until_cleared(page, job, log):
                    return
            if job.type == "video":
                for url in collect_result_urls(page, "video"):
                    try:
                        _, mt, raw = download_media(page, url, media_dir)
                        items.append((raw, mt))
                    except Exception as e:
                        dl_errors.append(f"{url[:70]} -> {type(e).__name__}: {e}")
            else:
                items = grab_images_via_canvas(page)
            if items:
                break
            time.sleep(2.0)

        job.text = latest_response_text(page)
        if not items:
            if dl_errors:
                job.status = "failed"
                job.error = "downloads failed: " + " | ".join(dl_errors[:3])
            else:
                try:
                    dump = dump_page(page, media_dir)
                    job.error = f"no media produced; response dumped to {dump.get('html')}"
                except Exception:
                    job.error = "no media produced (selectors may be stale — run a dump job)"
                job.status = "failed"
            return

        results = []
        is_video = job.type == "video"
        for raw, media_type in items:
            # Strip the visible ✦ logo (keeps SynthID). Images: in-memory before
            # hashing so filename/b64 reflect the cleaned bytes.
            if watermark.STRIP and not is_video and "image" in media_type:
                cleaned = watermark.strip_image_watermark(raw)
                if cleaned and cleaned is not raw:
                    raw, media_type = cleaned, "image/png"
            ext = mimetypes.guess_extension(media_type) or (
                ".mp4" if "video" in media_type else ".png")
            name = hashlib.sha256(raw).hexdigest()[:32] + ext
            os.makedirs(media_dir, exist_ok=True)
            path = os.path.join(media_dir, name)
            with open(path, "wb") as f:
                f.write(raw)
            if watermark.STRIP and is_video:
                if not watermark.strip_video_watermark(path):
                    log(f"[{job.profile}] video watermark removal skipped (file kept)")
            entry = {"media_type": media_type, "filename": name}
            if not is_video:  # inline images as base64
                entry["b64"] = base64.b64encode(raw).decode("ascii")
            results.append(entry)

        job.results = results
        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        log(f"[{getattr(job, 'profile', '?')}] job failed: {job.error}")
    finally:
        for p in in_files:
            try:
                os.remove(p)
            except Exception:
                pass
