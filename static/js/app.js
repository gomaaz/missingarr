// Missingarr — Alpine.js stores & SSE setup

document.addEventListener('alpine:init', () => {

    // ── Toast store ──────────────────────────────────────────────────────────
    Alpine.store('toasts', {
        items: [],
        _id: 0,
        add(message, type = 'info', duration = 4000) {
            const id = ++this._id;
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), duration);
        },
        remove(id) {
            this.items = this.items.filter(t => t.id !== id);
        }
    });

    // ── Log store ─────────────────────────────────────────────────────────────
    Alpine.store('logs', {
        enabled: true,
        debug: false,
        entries: [],
        maxEntries: 500,
        _evtSource: null,

        init() {
            this.connect();
        },

        connect() {
            if (this._evtSource) this._evtSource.close();
            const url = `/api/activity/stream?debug=${this.debug ? 1 : 0}`;
            this._evtSource = new EventSource(url);
            this._evtSource.onmessage = (e) => {
                if (!this.enabled) return;
                try {
                    const entry = JSON.parse(e.data);
                    this.entries.unshift(entry);
                    if (this.entries.length > this.maxEntries) {
                        this.entries.splice(this.maxEntries);
                    }
                } catch {}
            };
            this._evtSource.onerror = () => {
                // Reconnect after 5s
                setTimeout(() => this.connect(), 5000);
            };
        },

        toggleDebug() {
            this.debug = !this.debug;
            this.connect(); // reconnect with new debug param
        },

        toggleEnabled() {
            this.enabled = !this.enabled;
            if (!this.enabled && this._evtSource) {
                this._evtSource.close();
                this._evtSource = null;
            } else if (this.enabled) {
                this.connect();
            }
        },

        clear() {
            this.entries = [];
        },

        levelClass(level) {
            return `level-${level}`;
        },

        formatTime(ts) {
            if (!ts) return '-';
            return ts.replace('T', ' ').substring(0, 19);
        }
    });
});

// ── Countdown helper ──────────────────────────────────────────────────────────
function countdownComponent(nextRunIso, status) {
    return {
        nextRun: nextRunIso ? new Date(nextRunIso) : null,
        status: status || 'unknown',
        display: '--:--',
        _timer: null,

        init() {
            this.update();
            this._timer = setInterval(() => this.update(), 1000);
        },
        destroy() {
            clearInterval(this._timer);
        },
        update() {
            if (!this.nextRun || this.status === 'off' || this.status === 'running') {
                this.display = this.status === 'running' ? 'Running...' : '--:--';
                return;
            }
            const diff = Math.max(0, Math.floor((this.nextRun - Date.now()) / 1000));
            const h = Math.floor(diff / 3600);
            const m = Math.floor((diff % 3600) / 60);
            const s = diff % 60;
            if (h > 0) {
                this.display = `${h}h ${String(m).padStart(2, '0')}m`;
            } else {
                this.display = `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
            }
        }
    };
}

// ── Form helpers ──────────────────────────────────────────────────────────────
function instanceForm(instanceType) {
    return {
        type: instanceType || 'sonarr',
        upgradesEnabled: false,

        showMissingMode() { return this.type === 'sonarr'; },
        showUpgradeSource() { return this.type === 'radarr' && this.upgradesEnabled; },

        async testConnection(url, apiKey) {
            try {
                const resp = await fetch('/api/instances/' + window._editInstanceId + '/test');
                const data = await resp.json();
                if (resp.ok) {
                    Alpine.store('toasts').add(`Connected — ${data.appName} v${data.version}`, 'success');
                } else {
                    Alpine.store('toasts').add(data.detail || 'Connection failed', 'error');
                }
            } catch {
                Alpine.store('toasts').add('Connection test failed', 'error');
            }
        }
    };
}

// ── Force trigger ─────────────────────────────────────────────────────────────
async function forceRun(instanceId, skill = 'search_missing') {
    try {
        const resp = await fetch(`/api/instances/${instanceId}/trigger?skill=${skill}&force=true`, {
            method: 'POST'
        });
        if (resp.ok) {
            Alpine.store('toasts').add('Run triggered!', 'success');
        } else {
            Alpine.store('toasts').add('Failed to trigger run', 'error');
        }
    } catch {
        Alpine.store('toasts').add('Network error', 'error');
    }
}

async function toggleInstance(instanceId, enabled) {
    try {
        const resp = await fetch(`/api/instances/${instanceId}/toggle?enabled=${enabled}`, {
            method: 'POST'
        });
        if (resp.ok) {
            Alpine.store('toasts').add(enabled ? 'Instance enabled' : 'Instance disabled', 'info');
            setTimeout(() => location.reload(), 800);
        }
    } catch {
        Alpine.store('toasts').add('Failed to toggle instance', 'error');
    }
}
