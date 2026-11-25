// Client dell'API del backend. Se il backend richiede un token, lo chiede una volta e lo ricorda.
const KEY = 'splat_token';

export const getToken = () => {
	try {
		return localStorage.getItem(KEY) || '';
	} catch {
		return '';
	}
};
export const setToken = (t) => {
	try {
		localStorage.setItem(KEY, t);
	} catch {
		/* storage non disponibile: il token vale solo per questa sessione */
	}
};

export class ApiError extends Error {
	constructor(status, message) {
		super(message);
		this.status = status;
	}
}

export async function api(path, { method = 'GET', body, raw = false } = {}) {
	const headers = {};
	const token = getToken();
	if (token) headers['X-Token'] = token;
	let payload = body;
	if (body && !(body instanceof FormData)) {
		headers['Content-Type'] = 'application/json';
		payload = JSON.stringify(body);
	}
	const res = await fetch('/api' + path, { method, headers, body: payload });
	if (!res.ok) {
		let msg = res.statusText;
		try {
			const j = await res.json();
			msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
		} catch {
			/* risposta non JSON */
		}
		throw new ApiError(res.status, msg);
	}
	return raw ? res.text() : res.json();
}

/** Carica un file con avanzamento (fetch non lo espone). */
export function upload(path, file, onProgress) {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		xhr.open('POST', '/api' + path);
		const token = getToken();
		if (token) xhr.setRequestHeader('X-Token', token);
		xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
		xhr.onload = () => {
			if (xhr.status >= 200 && xhr.status < 300) return resolve(JSON.parse(xhr.responseText));
			let msg = xhr.statusText;
			try {
				msg = JSON.parse(xhr.responseText).detail || msg;
			} catch {
				/* non JSON */
			}
			reject(new ApiError(xhr.status, msg));
		};
		xhr.onerror = () => reject(new ApiError(0, 'Connessione interrotta durante il caricamento'));
		const fd = new FormData();
		fd.append('file', file);
		xhr.send(fd);
	});
}

export const fmtDuration = (s) => {
	if (s == null || !isFinite(s)) return '–';
	s = Math.round(s);
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const sec = s % 60;
	return h ? `${h} h ${String(m).padStart(2, '0')} min` : m ? `${m} min ${String(sec).padStart(2, '0')} s` : `${sec} s`;
};

export const fmtAgo = (ts) => {
	if (!ts) return '–';
	const d = Date.now() / 1000 - ts;
	if (d < 60) return 'adesso';
	if (d < 3600) return `${Math.floor(d / 60)} min fa`;
	if (d < 86400) return `${Math.floor(d / 3600)} h fa`;
	return `${Math.floor(d / 86400)} g fa`;
};

export const STATUS_LABEL = {
	idle: 'Pronto',
	queued: 'In coda',
	running: 'In corso',
	blocked: 'In attesa di una decisione',
	failed: 'Errore',
	stopped: 'Fermato',
	interrupted: 'Interrotto',
	done: 'Completato',
	pending: 'Da fare'
};

/** Link al viewer 3D (servito dal pannello) per un file risultante di un progetto. */
export function viewerUrl(name, path) {
	const token = getToken();
	const src = `/api/projects/${name}/files/${path}${token ? `?token=${encodeURIComponent(token)}` : ''}`;
	return `/viewer/?src=${encodeURIComponent(src)}&name=${encodeURIComponent(name)}&back=${encodeURIComponent('/p/' + name)}`;
}

/** Il file .ply da mostrare di preferenza: quello ritagliato, altrimenti il primo .ply. */
export const mainPly = (artifacts) =>
	artifacts?.find((x) => x.path === 'splat_clean.ply') ?? artifacts?.find((x) => x.path.endsWith('.ply'));
