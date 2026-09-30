// Missingarr — Alpine.js stores, API helper & SSE setup.
// Top level holds function declarations only: hx-boost re-runs this file on
// every navigation, and a second `class`/`let` declaration would be a SyntaxError.

function sessionExpiredError() {
    const err = new Error('Session expired');
    err.name = 'SessionExpiredError';
    return err;
}

function isSessionExpired(err) {
    return !!err && err.name === 'SessionExpiredError';
}

// fetch() that notices a lost session. The server answers 401 for /api/*; a
// redirect to /login counts the same. Go to the login page instead of
// reporting a success that did not happen (C-L6).
async function apiFetch(url, options = {}) {
    const resp = await fetch(url, Object.assign({ credentials: 'same-origin' }, options));
    const toLogin = resp.redirected && new URL(resp.url, location.href).pathname === '/login';
    if (resp.status === 401 || toLogin) {
        location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
        throw sessionExpiredError();
    }
    return resp;
}

function toast(message, type = 'info') {
    Alpine.store('toasts').add(message, type);
}

// Same line = same instance, level and message within one second. The live
// entry's created_at comes from Python's clock, the stored one from SQLite's
// datetime('now'); both are taken separately and may straddle a second.
function logEntryKey(entry) {
    return `${entry.instance_name}|${entry.level}|${entry.message}`;
}

function logEntrySeconds(entry) {
    const t = Date.parse(String(entry.created_at || '').replace(' ', 'T'));
    return Number.isNaN(t) ? null : t / 1000;
}

function createLogStore() {
    return {
        enabled: true,
        debug: false,
        entries: [],
        maxEntries: 500,
        retryDelay: 5000,
        _evtSource: null,
        _retryTimer: null,
        _nextId: 0,
        _buffer: [],
        _flushTimer: null,

        init() {
            this.connect();
        },

        // Rows rendered into the logs page (newest first). Merged with what the
        // live stream already delivered, without duplicates (C11).
        seed(rows) {
            if (!Array.isArray(rows)) return;
            const known = new Map();
            for (const entry of this.entries) {
                const key = logEntryKey(entry);
                if (!known.has(key)) known.set(key, []);
                known.get(key).push(logEntrySeconds(entry));
            }
            const isKnown = (row) => {
                const seconds = known.get(logEntryKey(row));
                if (!seconds) return false;
                const t = logEntrySeconds(row);
                return seconds.some(s => s === null || t === null || Math.abs(s - t) <= 1);
            };
            const older = rows
                .filter(row => !isKnown(row))
                .map(row => Object.assign({}, row, { _id: this._nextId++ }));
            this.entries = this.entries
                .concat(older)
                .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
                .slice(0, this.maxEntries);
        },

        connect() {
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
            if (this._evtSource) this._evtSource.close();
            const source = new EventSource(`/api/activity/stream?debug=${this.debug ? 1 : 0}`);
            this._evtSource = source;
            source.onmessage = (e) => {
                if (!this.enabled) return;
                try {
                    const entry = JSON.parse(e.data);
                    entry._id = this._nextId++;
                    this._buffer.push(entry);
                    if (!this._flushTimer) {
                        this._flushTimer = setTimeout(() => this._flush(), 150);
                    }
                } catch (err) {}
            };
            // While CONNECTING the browser reconnects by itself. Only a CLOSED
            // source (a non-200 answer such as 401) needs us — once, and only
            // for the source that is still current (C12).
            source.onerror = () => {
                if (source !== this._evtSource || !this.enabled) return;
                if (source.readyState !== EventSource.CLOSED || this._retryTimer) return;
                this._retryTimer = setTimeout(() => this._reconnect(), this.retryDelay);
            };
        },

        async _reconnect() {
            this._retryTimer = null;
            try {
                await apiFetch('/api/activity?limit=1');
            } catch (err) {
                if (isSessionExpired(err)) return;
            }
            if (this.enabled) this.connect();
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
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
            if (!this.enabled) {
                if (this._evtSource) {
                    this._evtSource.close();
                    this._evtSource = null;
                }
                clearTimeout(this._flushTimer);
                this._flushTimer = null;
                this._buffer = [];
            } else {
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
    };
}

document.addEventListener('alpine:init', () => {
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
    Alpine.store('logs', createLogStore());
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
            if (!this.nextRun || this.status === 'off' || this.status === 'running' || this.status === 'error') {
                this.display = this.status === 'running' ? 'Running...' : '--:--';
                return;
            }
            const diff = Math.max(0, Math.floor((this.nextRun - Date.now()) / 1000));
            const h = Math.floor(diff / 3600);
            const m = Math.floor((diff % 3600) / 60);
            const s = diff % 60;
            this.display = h > 0
                ? `${h}h ${String(m).padStart(2, '0')}m`
                : `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
        }
    };
}

// ── Card live-update (called by htmx after every /status poll) ────────────────
var STATUS_BADGES = {
    running: ['badge badge-running', 'RUNNING'],
    quiet: ['badge badge-unknown', 'QUIET'],
    off: ['badge badge-unknown', 'OFF'],
    error: ['badge badge-offline', 'ERROR'],
};

function updateCardState(instanceId, responseText) {
    try {
        const data = JSON.parse(responseText);
        const state = data.agent_state || {};
        const card = document.getElementById(`icard-${instanceId}`);
        if (!card) return;

        const alpineData = Alpine.$data(card);
        if (alpineData) {
            alpineData.nextRun = state.next_run_at ? new Date(state.next_run_at) : null;
            alpineData.status = state.status || 'unknown';
        }

        const badgeEl = card.querySelector('[data-status-badge]');
        if (badgeEl) {
            const [cls, text] = STATUS_BADGES[state.status || 'off'] || ['badge badge-scheduled', 'WAIT'];
            badgeEl.className = cls;
            badgeEl.textContent = text;
        }

        const connEl = card.querySelector('[data-conn-badge]');
        if (connEl) {
            const c = data.connection_status || 'unknown';
            const cls = c === 'online' ? 'badge-online' : c === 'offline' ? 'badge-offline' : c === 'error' ? 'badge-error' : 'badge-unknown';
            connEl.className = `badge ${cls}`;
            connEl.textContent = c;
        }

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

        card.querySelectorAll('[data-stat]').forEach(el => {
            const key = el.dataset.stat;
            if (key === 'last_wanted') el.textContent = state.last_wanted ?? '-';
            else if (key === 'last_triggered') el.textContent = state.last_triggered ?? '-';
            else if (key === 'last_verified') el.textContent = state.last_verified ?? '-';
            else if (key === 'last_sync') el.textContent = state.last_sync || '-';
        });
    } catch (_) {}
}

// ── Actions ───────────────────────────────────────────────────────────────────
async function errorDetail(resp, fallback) {
    const data = await resp.json().catch(() => ({}));
    return typeof data.detail === 'string' ? data.detail : fallback;
}

async function forceRun(instanceId, skill = 'search_missing') {
    try {
        const resp = await apiFetch(
            `/api/instances/${instanceId}/trigger?skill=${encodeURIComponent(skill)}&force=true`,
            { method: 'POST' }
        );
        if (resp.ok) {
            toast('Run triggered!', 'success');
        } else if (resp.status === 409) {
            toast('Already running — wait for the current run to finish', 'info');
        } else {
            toast(await errorDetail(resp, 'Failed to trigger run'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Network error', 'error');
    }
}

async function testCardConnection(instanceId, btn) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '…';
    try {
        const resp = await apiFetch(`/api/instances/${instanceId}/test`);
        if (resp.ok) {
            const data = await resp.json();
            toast(`Online — ${data.appName} v${data.version}`, 'success');
        } else {
            toast(await errorDetail(resp, 'Connection failed'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Connection test failed', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function toggleSkill(instanceId, skill, currentlyEnabled, btn) {
    const newEnabled = !currentlyEnabled;
    try {
        const resp = await apiFetch(
            `/api/instances/${instanceId}/toggle-skill?skill=${skill}&enabled=${newEnabled}`,
            { method: 'POST' }
        );
        if (resp.ok) {
            btn.className = btn.className.replace(
                newEnabled ? 'btn-toggle-off' : 'btn-toggle-on',
                newEnabled ? 'btn-toggle-on' : 'btn-toggle-off'
            );
            btn.setAttribute('onclick', `toggleSkill(${instanceId}, '${skill}', ${newEnabled}, this)`);
            const name = skill.charAt(0).toUpperCase() + skill.slice(1);
            toast(`${name} ${newEnabled ? 'enabled' : 'disabled'}`, 'info');
        } else {
            toast(await errorDetail(resp, 'Failed to toggle skill'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Failed to toggle skill', 'error');
    }
}

async function toggleInstance(instanceId, enabled) {
    try {
        const resp = await apiFetch(`/api/instances/${instanceId}/toggle?enabled=${enabled}`, { method: 'POST' });
        if (!resp.ok) {
            toast(await errorDetail(resp, 'Failed to toggle instance'), 'error');
            return;
        }
        toast(enabled ? 'Instance enabled' : 'Instance disabled', 'info');
        htmx.ajax('GET', `/instances/${instanceId}/card`, { target: `#icard-${instanceId}`, swap: 'outerHTML' });
    } catch (err) {
        if (!isSessionExpired(err)) toast('Failed to toggle instance', 'error');
    }
}
