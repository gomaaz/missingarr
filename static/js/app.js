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
        _nextId: 0,
        _buffer: [],
        _flushTimer: null,

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
                    entry._id = this._nextId++;
                    this._buffer.push(entry);
                    if (!this._flushTimer) {
                        this._flushTimer = setTimeout(() => this._flush(), 150);
                    }
                } catch {}
            };
            this._evtSource.onerror = () => {
                setTimeout(() => this.connect(), 5000);
            };
        },

        _flush() {
            this._flushTimer = null;
            if (!this._buffer.length) return;
            // Reverse so the newest message ends up at index 0 after unshift
            const toAdd = this._buffer.splice(0).reverse();
            this.entries.unshift(...toAdd);
            if (this.entries.length > this.maxEntries) {
                this.entries.splice(this.maxEntries);
            }
        },

        toggleDebug() {
            this.debug = !this.debug;
            this.connect();
        },

        toggleEnabled() {
            this.enabled = !this.enabled;
            if (!this.enabled && this._evtSource) {
                this._evtSource.close();
                this._evtSource = null;
                clearTimeout(this._flushTimer);
                this._flushTimer = null;
                this._buffer = [];
            } else if (this.enabled) {
                this.connect();
            }
        },

        clear() {
            this.entries = [];
            this._buffer = [];
            clearTimeout(this._flushTimer);
            this._flushTimer = null;
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

// ── Card live-update (called by htmx after every /status poll) ────────────────
function updateCardState(instanceId, responseText) {
    try {
        const data = JSON.parse(responseText);
        const state = data.agent_state || {};
        const card = document.getElementById(`icard-${instanceId}`);
        if (!card) return;

        // Update Alpine countdown component (nextRun + status)
        const alpineData = Alpine.$data(card);
        if (alpineData) {
            alpineData.nextRun = state.next_run_at ? new Date(state.next_run_at) : null;
            alpineData.status = state.status || 'unknown';
        }

        // Status badge
        const badgeEl = card.querySelector('[data-status-badge]');
        if (badgeEl) {
            const s = state.status || 'off';
            if (s === 'running') {
                badgeEl.className = 'badge badge-running';
                badgeEl.textContent = 'RUNNING';
            } else if (s === 'quiet') {
                badgeEl.className = 'badge badge-unknown';
                badgeEl.textContent = 'QUIET';
            } else if (s === 'off') {
                badgeEl.className = 'badge badge-unknown';
                badgeEl.textContent = 'OFF';
            } else {
                badgeEl.className = 'badge badge-scheduled';
                badgeEl.textContent = 'WAIT';
            }
        }

        // Connection badge
        const connEl = card.querySelector('[data-conn-badge]');
        if (connEl) {
            const c = data.connection_status || 'unknown';
            const cls = c === 'online' ? 'badge-online' : c === 'offline' ? 'badge-offline' : c === 'error' ? 'badge-error' : 'badge-unknown';
            connEl.className = `badge ${cls}`;
            connEl.textContent = c;
        }

        // Rate bar
        const rateCap = state.rate_cap || 1;
        const rateUsed = state.rate_used || 0;
        const ratePct = Math.min(100, Math.round((rateUsed / rateCap) * 100));
        const rateBar = card.querySelector('[data-rate-bar]');
        if (rateBar) {
            rateBar.style.width = ratePct + '%';
            rateBar.classList.toggle('danger', ratePct >= 80);
        }
        const rateUsedEl = card.querySelector('[data-rate-used]');
        if (rateUsedEl) rateUsedEl.textContent = `${rateUsed} / ${rateCap}`;

        // Stats
        card.querySelectorAll('[data-stat]').forEach(el => {
            const key = el.dataset.stat;
            if (key === 'last_wanted') el.textContent = state.last_wanted ?? '-';
            else if (key === 'last_triggered') el.textContent = state.last_triggered ?? '-';
            else if (key === 'last_sync') el.textContent = state.last_sync || '-';
        });
    } catch (_) {}
}

// ── Force trigger ─────────────────────────────────────────────────────────────
async function forceRun(instanceId, skill = 'search_missing') {
    try {
        const resp = await fetch(`/api/instances/${instanceId}/trigger?skill=${skill}&force=true`, {
            method: 'POST'
        });
        // If auth redirected us to /login the response will be the login page HTML.
        // Detect this by checking resp.redirected or the final URL.
        if (resp.redirected || resp.url.includes('/login')) {
            location.reload();
            return;
        }
        if (resp.ok) {
            Alpine.store('toasts').add('Run triggered!', 'success');
        } else {
            const data = await resp.json().catch(() => ({}));
            Alpine.store('toasts').add(data.detail || 'Failed to trigger run', 'error');
        }
    } catch {
        Alpine.store('toasts').add('Network error', 'error');
    }
}

async function testCardConnection(instanceId, btn) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '…';
    try {
        const resp = await fetch(`/api/instances/${instanceId}/test`);
        const data = await resp.json();
        if (resp.ok) {
            Alpine.store('toasts').add(`Online — ${data.appName} v${data.version}`, 'success');
        } else {
            Alpine.store('toasts').add(data.detail || 'Connection failed', 'error');
        }
    } catch {
        Alpine.store('toasts').add('Connection test failed', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function toggleSkill(instanceId, skill, currentlyEnabled, btn) {
    const newEnabled = !currentlyEnabled;
    try {
        const resp = await fetch(`/api/instances/${instanceId}/toggle-skill?skill=${skill}&enabled=${newEnabled}`, {
            method: 'POST'
        });
        if (resp.ok) {
            btn.className = btn.className.replace(
                newEnabled ? 'btn-toggle-off' : 'btn-toggle-on',
                newEnabled ? 'btn-toggle-on' : 'btn-toggle-off'
            );
            btn.setAttribute('onclick', `toggleSkill(${instanceId}, '${skill}', ${newEnabled}, this)`);
            const name = skill.charAt(0).toUpperCase() + skill.slice(1);
            Alpine.store('toasts').add(`${name} ${newEnabled ? 'enabled' : 'disabled'}`, 'info');
        } else {
            Alpine.store('toasts').add('Failed to toggle skill', 'error');
        }
    } catch {
        Alpine.store('toasts').add('Failed to toggle skill', 'error');
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
