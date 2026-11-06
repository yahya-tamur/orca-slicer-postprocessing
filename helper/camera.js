// Added to the printer's PrusaLink page by printer_page.py: the camera on the dashboard, and the
// desktop layout at every window width.
//
// Printing: the camera replaces the job's thumbnail, with a horizontal progress bar under it. The
// page fills .preview-img-wrapper with the thumbnail and only rebuilds it when the thumbnail changes
// (a new job), so the camera sits inside it and the thumbnail is hidden. The page's vertical bar and
// percentage are hidden; the bar under the camera copies the page's own (hidden) percentage text, so
// it follows every update the page makes. The job details beside the camera stack into one column,
// with the job's buttons (Pause, Stop, ...) under them; in a narrow window they go below the camera.
//
// Not printing: the page hides the job (#job), and the camera moves to a section of its own in the
// same place and at the same size, without progress bar; the column where the details would be stays
// empty. There is only one camera, moved between the two places, so only one stream runs.
//
// If the page changes these parts, or Prusa renames them, the watcher below puts things back or
// leaves the page as it is. With no camera (not configured, ffmpeg missing) the page shows its
// thumbnail as usual.
//
// Tab title: the printer's state, with the progress while printing ("90% - Printing", "Printer Idle").
//
// Buttons (shown when the mouse is over the camera): play/pause and fullscreen; double-click also
// toggles fullscreen. Paused shows the last frame and stops the stream, as does hiding the page.
(() => {
    const STREAM = "/_camera/stream.mjpg";
    const ICONS = {
        pause: '<svg viewBox="0 0 24 24"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>',
        play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
        fullscreen: '<svg viewBox="0 0 24 24"><path d="M3 3h7v2H5v5H3zm11 0h7v7h-2V5h-5zM3 14h2v5h5v2H3zm16 0h2v7h-7v-2h5z"/></svg>',
        exit: '<svg viewBox="0 0 24 24"><path d="M8 3h2v7H3V8h5zm6 0h2v5h5v2h-7zM3 14h7v7H8v-5H3zm11 0h7v2h-5v5h-2z"/></svg>',
    };

    const style = document.createElement("style");
    style.textContent = `
        /* Printing: the camera instead of the thumbnail, vertical bar and percentage. */
        .preview-img-wrapper.camera-on > :not(.camera-box) { display: none !important; }
        .progress-with-img:has(.camera-on) .progress-bar,
        .progress-with-img:has(.camera-on) .progress-pct { display: none !important; }
        /* The camera side takes the spare width (at least 420 px; in a narrow window the details go
           below it), and the job details stack into one column. The idle section uses the same sizes. */
        #preview-wrapper:has(.camera-on), #camera-idle { display: flex; flex-wrap: wrap; row-gap: 16px; }
        #preview-wrapper:has(.camera-on) > .progress-with-img, #camera-idle > .camera-side { flex: 1 1 0; min-width: min(420px, 100%); }
        /* The page's main column, a flex item, otherwise won't shrink below its content's width, which
           in a window narrower than the desktop layout expects makes the page scroll sideways. */
        #root { min-width: 0; }
        /* The temperature graph is a fixed 520 px; its SVG has a viewBox, so it can scale down. */
        #graph { max-width: 100%; }
        #graph svg { max-width: 100%; height: auto; }
        .camera-box { min-width: 0 !important; }
        #preview-wrapper:has(.camera-on) > .job-details, #camera-idle > .details-space { flex: 0 1 min-content; min-width: 260px; }
        /* Pause / Stop etc. moved under the job details, stacked, instead of a row under everything. */
        .job-details > .job-buttons.moved {
            display: flex; flex-direction: column; align-items: stretch; gap: 8px; margin: 16px 0 0; padding: 0;
        }
        .job-details > .job-buttons.moved > button { width: 100%; margin: 0; justify-content: center; }
        .job-details > .job-buttons.moved > .job-buttons-separator { display: none; }

        /* Not printing: the camera's own section, shown only while the page hides the job. */
        #camera-idle { margin-bottom: 16px; column-gap: 1rem; }  /* the job area's gap before the details */
        #job:not([hidden]) ~ #camera-idle, #camera-idle:not(:has(.camera-box)) { display: none; }
        #camera-idle .camera-side { display: flex; }
        #camera-idle .camera-progress { display: none; }

        .camera-box { display: flex; flex-direction: column; flex: 1 1 auto; min-width: 320px; }
        .camera-view { position: relative; background: #000; }
        .camera-view img {
            display: block; width: 100%; height: auto; max-height: calc(100vh - 200px); object-fit: contain;
        }
        .camera-controls {
            position: absolute; right: 8px; bottom: 8px; display: flex; gap: 6px;
            opacity: 0; transition: opacity 0.2s;
        }
        .camera-view:hover .camera-controls, .camera-box.paused .camera-controls { opacity: 1; }
        .camera-controls button {
            width: 36px; height: 36px; padding: 7px; border: 0; border-radius: 4px; cursor: pointer;
            background: rgba(0, 0, 0, 0.6); fill: #fff;
        }
        .camera-controls button:hover { background: #fa6831; }

        .camera-progress {
            position: relative; height: 24px; margin-top: 6px; background: #242526; overflow: hidden;
        }
        .camera-progress .fill { position: absolute; inset: 0 auto 0 0; width: 0; background: #fa6831; transition: width 0.5s; }
        .camera-progress .label {
            position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: bold; color: #fff; text-shadow: 0 0 3px #000;
        }

        .camera-box:fullscreen, .camera-box.filled { background: #000; }
        .camera-box.filled { position: fixed; inset: 0; z-index: 10000; }
        .camera-box:fullscreen .camera-view, .camera-box.filled .camera-view { flex: 1 1 auto; min-height: 0; }
        .camera-box:fullscreen img, .camera-box.filled img { width: 100%; height: 100%; max-height: none; }
        .camera-box:fullscreen .camera-progress, .camera-box.filled .camera-progress { margin-top: 0; }`;
    document.head.appendChild(style);

    let retryAt = 0;      // after a failed stream, wait before trying again
    let userPaused = false;
    let box = null;       // the one camera, wherever it currently is

    function button(icon, title, onClick) {
        const b = document.createElement("button");
        b.type = "button";
        b.title = title;
        b.innerHTML = ICONS[icon];
        b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
        return b;
    }

    function createCamera() {
        const box = document.createElement("div");
        box.className = "camera-box";
        const view = document.createElement("div");
        view.className = "camera-view";
        const img = document.createElement("img");
        img.alt = "Camera";
        const controls = document.createElement("div");
        controls.className = "camera-controls";
        const progress = document.createElement("div");
        progress.className = "camera-progress";
        progress.innerHTML = '<div class="fill"></div><div class="label"></div>';

        const play = () => { img.src = STREAM + "?t=" + Date.now(); box.classList.remove("paused"); };
        const pause = () => {
            // Keep the last frame: copy it to a still image, which also ends the stream.
            try {
                const c = document.createElement("canvas");
                c.width = img.naturalWidth;
                c.height = img.naturalHeight;
                c.getContext("2d").drawImage(img, 0, 0);
                img.src = c.toDataURL("image/jpeg");
            } catch (e) {
                img.removeAttribute("src");
            }
            box.classList.add("paused");
        };
        const playPause = button("pause", "Pause", () => {
            userPaused = !box.classList.contains("paused");
            userPaused ? pause() : play();
            playPause.innerHTML = ICONS[userPaused ? "play" : "pause"];
            playPause.title = userPaused ? "Play" : "Pause";
        });
        // Real fullscreen where the browser allows it; embedded browsers (like Orca's Device tab) may
        // refuse, and then the camera fills the page instead.
        const showFull = () => {
            const on = document.fullscreenElement === box || box.classList.contains("filled");
            full.innerHTML = ICONS[on ? "exit" : "fullscreen"];
            full.title = on ? "Exit fullscreen" : "Fullscreen";
        };
        const fill = (on) => { box.classList.toggle("filled", on); showFull(); };
        const toggleFullscreen = () => {
            if (document.fullscreenElement) return document.exitFullscreen();
            if (box.classList.contains("filled")) return fill(false);
            if (!box.requestFullscreen) return fill(true);
            box.requestFullscreen().catch(() => fill(true));
        };
        const full = button("fullscreen", "Fullscreen", toggleFullscreen);
        view.addEventListener("dblclick", toggleFullscreen);
        document.addEventListener("fullscreenchange", showFull);
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && box.classList.contains("filled")) fill(false);
        });
        // No point streaming to a hidden page (another tab, minimized).
        document.addEventListener("visibilitychange", () => {
            if (box.isConnected && !userPaused) document.hidden ? pause() : play();
        });

        // No camera (not configured, ffmpeg missing): show the page as usual, retry in 30 s.
        img.addEventListener("error", () => {
            if (box.classList.contains("paused")) return;
            retryAt = Date.now() + 30000;
            removeCamera();
            setTimeout(update, 30000);
        });

        controls.append(playPause, full);
        view.append(img, controls);
        box.append(view, progress);
        userPaused ? pause() : play();
        return box;
    }

    function removeCamera() {
        document.querySelector(".preview-img-wrapper.camera-on")?.classList.remove("camera-on");
        box?.remove();
        box = null;
    }

    // The idle section, right after the page's job block: camera side and an empty details column.
    function idleSection(job) {
        let idle = document.getElementById("camera-idle");
        if (!idle) {
            idle = document.createElement("div");
            idle.id = "camera-idle";
            idle.innerHTML = '<div class="camera-side"></div><div class="details-space"></div>';
        }
        if (idle.previousElementSibling !== job) job.after(idle);
        return idle.querySelector(".camera-side");
    }

    // Put the camera in the job area while the page shows the job, in the idle section otherwise.
    function placeCamera() {
        const job = document.getElementById("job");
        const area = document.querySelector("#preview-wrapper .preview-img-wrapper");
        if (!job || !area || Date.now() < retryAt) return;
        const target = job.hidden ? idleSection(job) : area;
        box = box || createCamera();
        if (box.parentElement !== target) target.appendChild(box);
        area.classList.toggle("camera-on", target === area);
    }

    // Copy the page's percentage ("85%") into the bar under the camera.
    function updateProgress() {
        const bar = box?.querySelector(".camera-progress");
        const source = document.querySelector('#preview-wrapper [data-where="progress"]');
        if (!bar || !source) return;
        const text = source.textContent.trim();
        const pct = Math.max(0, Math.min(100, parseFloat(text.replace(",", ".")) || 0));
        const label = bar.querySelector(".label");
        if (label.textContent !== text) label.textContent = text;
        bar.querySelector(".fill").style.width = pct + "%";
    }

    // Move the job's buttons (Pause, Stop, ...) under the job details, beside the camera. The page
    // finds them by id, so they keep working wherever they are.
    function moveButtons() {
        const buttons = document.querySelector("#job > .job-buttons");
        const details = document.querySelector("#preview-wrapper:has(.camera-on) > .job-details");
        if (!buttons || !details) return;
        buttons.classList.add("moved");
        details.appendChild(buttons);
    }

    // The tab's title from the printer's state, so the tab says how the print is going: "90% - Printing",
    // "Printer Idle", ... The state comes from /api/v1/status every 5 s (browsers slow that down in
    // background tabs, to about once a minute after a while). update() puts the title back whenever the
    // page changes it.
    const TITLES = {
        PRINTING: (p) => `${p}% - Printing`,
        PAUSED: (p) => `${p}% - Paused`,
        ATTENTION: () => "Printer Needs Attention",
        FINISHED: () => "Printer Finished",
        STOPPED: () => "Printer Stopped",
        IDLE: () => "Printer Idle",
        READY: () => "Printer Ready",
        BUSY: () => "Printer Busy",
        ERROR: () => "Printer Error",
    };
    let title = null;

    async function updateTitle() {
        try {
            const r = await fetch("/api/v1/status", { cache: "no-store" });
            if (!r.ok) throw new Error(r.status);
            const status = await r.json();
            const state = status.printer?.state;
            const pct = Math.floor(status.job?.progress ?? 0);  // never 100% while still printing
            title = (TITLES[state] || (() => `Printer ${state}`))(pct);
        } catch (e) {
            title = "Printer Offline";
        }
        if (document.title !== title) document.title = title;
    }
    updateTitle();
    setInterval(updateTitle, 5000);

    function update() {
        placeCamera();
        updateProgress();
        moveButtons();
        if (title && document.title !== title) document.title = title;
    }

    // Below 992 px the page switches to its phone layout: 32 pt text, stacked status, menu button,
    // which looks far too zoomed in in a narrow window (half a screen, Orca's Device tab). Apply the
    // page's own desktop rules (its "min-width: 992px" blocks, read from its stylesheet, so they stay
    // current) at every width, and show again what the phone layout hides.
    function desktopLayoutAlways() {
        const css = [];
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (e) { continue; }
            for (const rule of rules) {
                if (!(rule instanceof CSSMediaRule)) continue;
                if (/min-width:\s*992px/.test(rule.media.mediaText)) {
                    css.push(`@media (max-width: 991.5px) { ${[...rule.cssRules].map((r) => r.cssText).join("\n")} }`);
                }
                if (/max-width:\s*991/.test(rule.media.mediaText)) {
                    for (const r of rule.cssRules) {
                        if (r.style?.display === "none") css.push(`@media (max-width: 991.5px) { ${r.selectorText} { display: revert; } }`);
                    }
                }
            }
        }
        if (!css.length) return false;
        const desktop = document.createElement("style");
        desktop.textContent = css.join("\n");
        document.head.appendChild(desktop);
        return true;
    }
    if (!desktopLayoutAlways()) window.addEventListener("load", desktopLayoutAlways, { once: true });

    new MutationObserver(update).observe(document.documentElement, {
        childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"],
    });
    update();
})();
