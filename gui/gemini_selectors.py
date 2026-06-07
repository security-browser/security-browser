"""
Gemini web-UI selectors — the single brittle surface of the automation engine.

Google does NOT publish stable selectors for the consumer Gemini chat UI; the
Angular-Material DOM uses obfuscated, frequently-changing class names. So every
anchor here is an ORDERED LIST of candidate selectors, tried in turn until one
resolves (see automation.py::first_locator). Prefer accessibility/role anchors
(aria-label, role, placeholder, semantic tags) over CSS classes — they survive
restyles far better than `.mat-mdc-xyz123`.

To refine these against the live UI, run a `dump` job (automation.dump_page):
it saves the page HTML + a screenshot so the real anchors can be read off and
pasted here. Only this file should need editing when Google changes the DOM.
"""

# URL the automation navigates to. {slot} is the Google account slot (/u/N).
# hl=en forces an English UI so aria-label anchors are stable regardless of the
# profile's proxy geo (observed: a Spanish UI when the proxy exits in LATAM).
APP_URL = "https://gemini.google.com/u/{slot}/app?hl=en"
APP_URL_DEFAULT = "https://gemini.google.com/app?hl=en"

# ── Prompt input box ──────────────────────────────────────────────────────────
# CONFIRMED live: Quill rich-text editor inside <rich-textarea> in <input-area-v2>.
PROMPT_INPUT = [
    "rich-textarea div.ql-editor[contenteditable='true']",   # confirmed
    "div.ql-editor[contenteditable='true']",
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true']",
]

# ── Send / submit button ──────────────────────────────────────────────────────
# CONFIRMED live: button.send-button; fonticon='send' is locale-independent.
SEND_BUTTON = [
    "button.send-button",                                    # confirmed
    "button:has(mat-icon[fonticon='send'])",                # locale-independent
    "button[aria-label*='Send' i]",
    "button[aria-label*='Enviar' i]",
    "button[aria-label*='发送']",
]

# ── File upload ───────────────────────────────────────────────────────────────
# CONFIRMED live: Gemini keeps hidden, aria-hidden trigger buttons with stable
# data-test-ids (and a `xapfileselectortrigger` attr). Force-clicking one opens
# the OS file chooser directly — no need to open the '+' menu. Prefer the generic
# file button (accepts video too), then the image-only one.
FILE_SELECTOR_TRIGGERS = [
    'button[data-test-id="hidden-local-file-upload-button"]',
    'button[data-test-id="hidden-local-image-upload-button"]',
    "button[xapfileselectortrigger]",
]

# Fallback: a real hidden <input type=file> if one is present in the DOM.
UPLOAD_INPUT = [
    "input[type='file']",
]
# The visible "+" button that opens the upload/tools menu. CONFIRMED live:
# aria-label="Upload & tools" (English UI via hl=en).
UPLOAD_TRIGGER = [
    "button[aria-label*='Upload' i]",            # confirmed: "Upload & tools"
    "button[aria-label*='Additional actions' i]",
    "button[aria-label*='Add files' i]",
    "button[aria-label*='添加']",
    "uploader button",
]
# Menu item (inside the opened menu) that triggers the OS file chooser.
UPLOAD_MENU_ITEM = [
    "[role='menuitem']:has-text('Upload')",
    "[role='menuitem']:has-text('file')",
    "[role='menuitem']:has-text('photo')",
    "[role='menuitem']:has-text('image')",
    "button:has-text('Upload files')",
]

# ── Tool / mode toggles ───────────────────────────────────────────────────────
# Some accounts must explicitly pick an image or video tool before prompting.
# These are best-effort; if absent the plain prompt path is used.
IMAGE_TOOL_TOGGLE = [
    "button[aria-label*='image' i]",
    "button[aria-label*='Create images' i]",
    "button:has-text('Image')",
]
VIDEO_TOOL_TOGGLE = [
    "button[aria-label*='video' i]",
    "button[aria-label*='Veo' i]",
    "button:has-text('Video')",
]

# ── Model response container (latest turn) ────────────────────────────────────
RESPONSE_CONTAINER = [
    "model-response",
    "message-content.model-response-text",
    "div.response-container",
    "[data-test-id='conversation-turn']",
]

# ── Generated image in the latest response ────────────────────────────────────
# Full-resolution generated images. We read the <img src> (a session-protected
# googleusercontent URL) and download it via the page's authenticated context.
# NOTE: these are best-effort until confirmed against a real generation dump;
# run_job auto-dumps the response DOM when none match, so they can be tightened.
RESULT_IMAGE = [
    "message-content img[src*='googleusercontent']",
    "model-response img[src*='googleusercontent']",
    "image-element img",
    "generated-image img",
    "single-image img",
    "img.image",
    "img[src*='googleusercontent']",
    "model-response img",
]

# ── Generated video in the latest response ────────────────────────────────────
RESULT_VIDEO = [
    "model-response video",
    "video[src]",
    "generated-video video",
    "video source[src]",
]
# A download control sometimes needed to obtain the full-res video file.
VIDEO_DOWNLOAD_BUTTON = [
    "button[aria-label*='Download' i]",
    "button[aria-label*='下载']",
    "a[download]",
]

# ── "Still generating" / progress indicators ─────────────────────────────────
# Presence means the model is still working; absence (with a result present)
# means done.
GENERATING_INDICATOR = [
    "button[aria-label*='Stop' i]",
    "button[aria-label*='停止']",
    ".blinking-cursor",
    "mat-progress-bar",
    "[aria-label*='Generating' i]",
]

# ── Human-verification / challenge state ──────────────────────────────────────
# When any of these are visible the job must pause for a human to solve it in
# the visible window (we never try to auto-solve captchas).
VERIFICATION_MARKERS = [
    "iframe[src*='recaptcha']",
    "iframe[title*='recaptcha' i]",
    "iframe[src*='challenge']",
    "div#captcha",
    "text=/verify (you're|you are) human/i",
    "text=/I'm not a robot/i",
    "text=/确认您是真人/",
    "text=/unusual traffic/i",
]

# ── Signed-out / login-required markers ───────────────────────────────────────
SIGNED_OUT_MARKERS = [
    "a[href*='accounts.google.com/ServiceLogin']",
    "text=/Sign in/i",
    "text=/登录/",
]
