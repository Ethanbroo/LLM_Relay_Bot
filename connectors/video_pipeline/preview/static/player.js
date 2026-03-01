/**
 * Video Pipeline Preview Player
 *
 * Handles WebSocket frame streaming, canvas rendering,
 * timeline scrubbing, playback controls, and effect toggles.
 */

(function () {
    "use strict";

    // --- State ---
    let ws = null;
    let canvas, ctx;
    let totalFrames = 0;
    let fps = 30;
    let currentFrame = 0;
    let isPlaying = false;
    let playbackSpeed = 1.0;
    let timelineData = null;

    // --- DOM refs ---
    const $ = (sel) => document.querySelector(sel);

    // --- Init ---
    document.addEventListener("DOMContentLoaded", async () => {
        canvas = $("#preview-canvas");
        ctx = canvas.getContext("2d");

        await loadTimeline();
        connectWebSocket();
        bindControls();
        updateCacheStats();
        setInterval(updateCacheStats, 5000);
    });

    // --- Timeline loading ---
    async function loadTimeline() {
        try {
            const resp = await fetch("/api/timeline");
            if (!resp.ok) throw new Error("Failed to load timeline");
            timelineData = await resp.json();

            totalFrames = timelineData.total_frames;
            fps = timelineData.fps;

            // Set canvas size
            const scale = timelineData.preview_scale || 0.5;
            canvas.width = Math.round(timelineData.resolution.width * scale);
            canvas.height = Math.round(timelineData.resolution.height * scale);

            // Update UI
            $("#resolution-label").textContent =
                `${timelineData.resolution.width}x${timelineData.resolution.height}`;
            $("#fps-label").textContent = `${fps} fps`;
            $("#duration-label").textContent = formatTime(timelineData.duration_ms);

            const scrubber = $("#scrubber");
            scrubber.max = totalFrames - 1;
            scrubber.value = 0;
            $("#total-time").textContent = formatTime(timelineData.duration_ms);
            $("#frame-label").textContent = `Frame: 0 / ${totalFrames}`;

            // Populate clips
            renderClipList(timelineData.clips);

            // Populate effects
            renderEffectList(timelineData);
        } catch (err) {
            console.error("Timeline load error:", err);
        }
    }

    // --- WebSocket ---
    function connectWebSocket() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(`${protocol}//${location.host}/ws/frames`);
        ws.binaryType = "arraybuffer";

        ws.onopen = () => {
            console.log("WebSocket connected");
            seekFrame(0);
        };

        ws.onmessage = (event) => {
            if (typeof event.data === "string") {
                const msg = JSON.parse(event.data);
                if (msg.type === "playback_ended") {
                    stopPlayback();
                }
                return;
            }

            // Binary: first 4 bytes = frame number, rest = JPEG
            const buffer = event.data;
            const view = new DataView(buffer);
            const frameNum = view.getUint32(0);
            const jpegData = buffer.slice(4);

            currentFrame = frameNum;
            renderFrame(jpegData);
            updateFrameUI(frameNum);
        };

        ws.onclose = () => {
            console.log("WebSocket disconnected, reconnecting in 2s...");
            setTimeout(connectWebSocket, 2000);
        };

        ws.onerror = (err) => {
            console.error("WebSocket error:", err);
        };
    }

    // --- Frame rendering ---
    function renderFrame(jpegData) {
        const blob = new Blob([jpegData], { type: "image/jpeg" });
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            URL.revokeObjectURL(url);
        };
        img.src = url;
    }

    // --- Transport commands ---
    function seekFrame(frame) {
        frame = Math.max(0, Math.min(frame, totalFrames - 1));
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "seek", frame: frame }));
        }
    }

    function startPlayback() {
        if (isPlaying) return;
        isPlaying = true;
        $("#btn-play").textContent = "⏸";
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "play",
                start_frame: currentFrame,
                speed: playbackSpeed,
            }));
        }
    }

    function stopPlayback() {
        if (!isPlaying) return;
        isPlaying = false;
        $("#btn-play").textContent = "▶";
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "stop" }));
        }
    }

    function togglePlayback() {
        if (isPlaying) {
            stopPlayback();
        } else {
            startPlayback();
        }
    }

    // --- Control bindings ---
    function bindControls() {
        // Play/Pause
        $("#btn-play").addEventListener("click", togglePlayback);

        // Frame step
        $("#btn-prev").addEventListener("click", () => {
            stopPlayback();
            seekFrame(currentFrame - 1);
        });
        $("#btn-next").addEventListener("click", () => {
            stopPlayback();
            seekFrame(currentFrame + 1);
        });

        // Start/End
        $("#btn-start").addEventListener("click", () => {
            stopPlayback();
            seekFrame(0);
        });
        $("#btn-end").addEventListener("click", () => {
            stopPlayback();
            seekFrame(totalFrames - 1);
        });

        // Scrubber
        const scrubber = $("#scrubber");
        scrubber.addEventListener("input", () => {
            stopPlayback();
            seekFrame(parseInt(scrubber.value));
        });

        // Speed select
        $("#speed-select").addEventListener("change", (e) => {
            playbackSpeed = parseFloat(e.target.value);
            if (isPlaying) {
                // Restart playback at new speed
                stopPlayback();
                startPlayback();
            }
        });

        // Scale select
        $("#scale-select").addEventListener("change", async (e) => {
            const scale = parseFloat(e.target.value);
            await fetch("/api/preview/scale", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ scale }),
            });
            // Reload timeline to get new canvas size
            await loadTimeline();
            seekFrame(currentFrame);
        });

        // Keyboard shortcuts
        document.addEventListener("keydown", (e) => {
            if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;

            switch (e.key) {
                case " ":
                    e.preventDefault();
                    togglePlayback();
                    break;
                case "ArrowLeft":
                    e.preventDefault();
                    stopPlayback();
                    seekFrame(currentFrame - (e.shiftKey ? 10 : 1));
                    break;
                case "ArrowRight":
                    e.preventDefault();
                    stopPlayback();
                    seekFrame(currentFrame + (e.shiftKey ? 10 : 1));
                    break;
                case "Home":
                    e.preventDefault();
                    stopPlayback();
                    seekFrame(0);
                    break;
                case "End":
                    e.preventDefault();
                    stopPlayback();
                    seekFrame(totalFrames - 1);
                    break;
            }
        });
    }

    // --- UI updates ---
    function updateFrameUI(frameNum) {
        const scrubber = $("#scrubber");
        scrubber.value = frameNum;
        const timeMs = (frameNum / fps) * 1000;
        $("#current-time").textContent = formatTime(timeMs);
        $("#frame-label").textContent = `Frame: ${frameNum} / ${totalFrames}`;
    }

    function formatTime(ms) {
        const totalSeconds = ms / 1000;
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${seconds.toFixed(3).padStart(6, "0")}`;
    }

    // --- Clip list ---
    function renderClipList(clips) {
        const list = $("#clip-list");
        list.innerHTML = "";
        clips.forEach((clip) => {
            const li = document.createElement("li");
            li.className = "clip-item";
            li.innerHTML = `
                <span class="clip-id">${clip.clip_id}</span>
                <span class="clip-meta">${clip.source_type} · ${clip.duration_ms}ms · ${clip.transition_in}</span>
            `;
            list.appendChild(li);
        });
    }

    // --- Effect toggles ---
    function renderEffectList(data) {
        const list = $("#effect-list");
        list.innerHTML = "";

        // Collect all unique effect names from clips + global
        const effectNames = new Set();
        data.clips.forEach((c) => {
            (c.effects || []).forEach((e) => effectNames.add(e));
        });
        (data.global_effects || []).forEach((e) => effectNames.add(e));

        if (effectNames.size === 0) {
            list.innerHTML = "<li class='no-effects'>No effects</li>";
            return;
        }

        effectNames.forEach((name) => {
            const li = document.createElement("li");
            li.className = "effect-item";

            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = true;
            checkbox.id = `effect-${name}`;
            checkbox.addEventListener("change", async () => {
                await fetch("/api/effects/toggle", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        effect: name,
                        enabled: checkbox.checked,
                    }),
                });
                // Re-render current frame with updated effects
                seekFrame(currentFrame);
            });

            const label = document.createElement("label");
            label.htmlFor = `effect-${name}`;
            label.textContent = name;

            li.appendChild(checkbox);
            li.appendChild(label);
            list.appendChild(li);
        });

        // Load initial disabled state
        loadDisabledEffects();
    }

    async function loadDisabledEffects() {
        try {
            const resp = await fetch("/api/effects/disabled");
            const data = await resp.json();
            (data.disabled_effects || []).forEach((name) => {
                const cb = $(`#effect-${name}`);
                if (cb) cb.checked = false;
            });
        } catch (err) {
            console.error("Failed to load disabled effects:", err);
        }
    }

    // --- Cache stats ---
    async function updateCacheStats() {
        try {
            const resp = await fetch("/api/cache/stats");
            const stats = await resp.json();
            if (stats.error) return;

            $("#cache-size").textContent = stats.size;
            $("#cache-max").textContent = stats.max_size;
            $("#cache-hit-rate").textContent = `${(stats.hit_rate * 100).toFixed(1)}%`;
            $("#cache-memory").textContent = `${stats.memory_estimate_mb} MB`;
        } catch (err) {
            // Server not ready yet
        }
    }
})();
