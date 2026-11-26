// Strumenti del viewer: misure e scala reale, raddrizzamento, punto di partenza, ritaglio, confronto tra modelli.
// Le impostazioni si salvano nel progetto (API /view) e valgono per il modello su cui sono state fatte.
import * as THREE from 'three';

const fmt = (v, d = 3) => v.toLocaleString('it-IT', { maximumFractionDigits: d });

/** La stessa base usata dal server per il ritaglio e per la mesh (mapdata.level_matrix): righe = nuovi assi, Z = up. */
export function levelBasis(up) {
	const z = new THREE.Vector3().fromArray(up).normalize();
	const a = Math.abs(z.x) < 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
	const x = a.clone().addScaledVector(z, -a.dot(z)).normalize();
	const y = new THREE.Vector3().crossVectors(z, x);
	return [x, y, z];
}

export function initTools(host) {
	// host: { project, token, file, camera, renderer, viewer(), setMode, getUp, setUpVector, setUpAxis, getPose, setStart,
	//         compare: { load(url, transform), toggle(), active() }, fileUrl(rel) }
	const panel = document.getElementById('tools');
	const button = document.getElementById('bTools');
	const overlay = new THREE.Scene();
	let cfg = {}; // { up, reference, scale, start, box }
	let info = { models: [], alignment: {} };
	let picking = null; // { need, points, done(points) }
	let boxHelper = null;
	let extent = null;

	const api = async (path, { method = 'GET', body } = {}) => {
		const headers = {};
		if (host.token) headers['X-Token'] = host.token;
		if (body) headers['Content-Type'] = 'application/json';
		const res = await fetch(`/api/projects/${encodeURIComponent(host.project)}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.statusText);
		return data;
	};

	const el = (id) => document.getElementById(id);
	const say = (id, text, bad = false) => {
		el(id).textContent = text;
		el(id).className = bad ? 'note bad' : 'note';
	};

	// ---------------------------------------------------------------- salvataggio
	async function save(changes, messageId, message) {
		const next = { up: cfg.up, reference: cfg.reference, start: cfg.start, box: cfg.box, ...changes };
		for (const k of Object.keys(next)) if (next[k] == null) delete next[k];
		try {
			cfg = (await api('/view', { method: 'PUT', body: next })).view;
			if (messageId) say(messageId, message);
		} catch (e) {
			if (messageId) say(messageId, 'Non salvato: ' + e.message, true);
		}
		showScale();
	}

	// ---------------------------------------------------------------- selezione di punti
	function markerAt(p) {
		const m = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffd000, depthTest: false }));
		m.position.copy(p);
		m.userData.marker = true;
		overlay.add(m);
		return m;
	}
	function lineThrough(points, closed = false) {
		const pts = closed ? [...points, points[0]] : points;
		const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xffd000, depthTest: false }));
		line.userData.marker = true;
		overlay.add(line);
	}
	function clearMarkers() {
		for (const o of [...overlay.children]) if (o.userData.marker) overlay.remove(o);
	}
	function pickAt(x, y) {
		const viewer = host.viewer();
		const hits = [];
		viewer.raycaster.setFromCameraAndScreenPosition(host.camera, new THREE.Vector2(x, y), new THREE.Vector2(innerWidth, innerHeight));
		viewer.raycaster.intersectSplatMesh(viewer.splatMesh, hits);
		const color = new THREE.Vector4();
		for (const h of hits) {
			viewer.splatMesh.getSplatColor(h.splatIndex, color);
			if (color.w >= 100) return h.origin.clone(); // si saltano le gaussiane quasi trasparenti ("nebbia")
		}
		return hits.length ? hits[0].origin.clone() : null;
	}
	function startPicking(need, messageId, prompt, done) {
		clearMarkers();
		host.setMode('orbit');
		picking = { need, points: [], done, messageId, prompt };
		say(messageId, `${prompt} (0 di ${need})`);
	}
	function stopPicking() {
		picking = null;
	}
	let down = null;
	host.renderer.domElement.addEventListener('pointerdown', (e) => (down = [e.clientX, e.clientY]));
	host.renderer.domElement.addEventListener('pointerup', (e) => {
		if (!picking || e.button !== 0 || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
		const p = pickAt(e.clientX, e.clientY);
		if (!p) return say(picking.messageId, 'Qui non c’è niente da agganciare: clicca su una superficie del modello.', true);
		picking.points.push(p);
		markerAt(p);
		if (picking.points.length > 1) lineThrough(picking.points.slice(-2));
		if (picking.points.length < picking.need) return say(picking.messageId, `${picking.prompt} (${picking.points.length} di ${picking.need})`);
		const { done, points } = picking;
		stopPicking();
		done(points);
	});

	// ---------------------------------------------------------------- misura e scala
	let measured = null;
	function describe(units) {
		return cfg.scale ? `${fmt(units * cfg.scale)} m` : `${fmt(units)} unità (scala non impostata)`;
	}
	function showScale() {
		el('scaleNow').textContent = cfg.scale ? `Scala: 1 unità = ${fmt(cfg.scale)} m` : 'Scala non impostata: le misure sono in unità arbitrarie.';
	}
	el('bMeasure').onclick = () =>
		startPicking(2, 'measureMsg', 'Clicca i due estremi', ([a, b]) => {
			measured = { a, b, units: a.distanceTo(b) };
			say('measureMsg', `Distanza: ${describe(measured.units)}`);
			el('scaleForm').hidden = false;
		});
	el('bSetScale').onclick = async () => {
		const meters = parseFloat(el('realDist').value.replace(',', '.'));
		if (!measured) return say('measureMsg', 'Prima misura due punti.', true);
		if (!(meters > 0)) return say('measureMsg', 'Scrivi la distanza vera in metri (es. 0,80).', true);
		await save({ reference: { a: measured.a.toArray(), b: measured.b.toArray(), meters } }, 'measureMsg', `Scala salvata: quel tratto è ${fmt(meters)} m.`);
	};

	// ---------------------------------------------------------------- raddrizzare
	el('bLevel').onclick = () =>
		startPicking(3, 'levelMsg', 'Clicca tre punti del pavimento, ben distanti', async ([a, b, c]) => {
			const n = new THREE.Vector3().subVectors(b, a).cross(new THREE.Vector3().subVectors(c, a));
			if (n.length() < 1e-5) return say('levelMsg', 'I tre punti sono quasi in fila: riprova.', true);
			n.normalize();
			if (n.dot(new THREE.Vector3().subVectors(host.camera.position, a)) < 0) n.negate(); // la camera sta sopra il pavimento
			host.setUpVector(n.toArray());
			clearMarkers();
			removeBox();
			await save({ up: n.toArray(), box: null }, 'levelMsg', 'Stanza raddrizzata e salvata. Se è capovolta, rifallo guardando il pavimento dall’alto.');
		});
	el('bLevelReset').onclick = async () => {
		host.setUpAxis('z+');
		removeBox();
		await save({ up: null, box: null }, 'levelMsg', 'Tornati alla verticale automatica.');
	};

	// ---------------------------------------------------------------- partenza
	el('bStart').onclick = async () => {
		const pose = host.getPose();
		host.setStart(pose);
		await save({ start: pose }, 'startMsg', 'Questa inquadratura è il nuovo punto di partenza.');
	};

	// ---------------------------------------------------------------- ritaglio
	function modelExtent(basis) {
		const mesh = host.viewer().splatMesh;
		const n = mesh.getSplatCount();
		const every = Math.max(1, Math.floor(n / 40000));
		const v = new THREE.Vector3();
		const values = [[], [], []];
		for (let i = 0; i < n; i += every) {
			mesh.getSplatCenter(i, v);
			basis.forEach((axis, k) => values[k].push(axis.dot(v)));
		}
		values.forEach((a) => a.sort((x, y) => x - y));
		const at = (a, q) => a[Math.min(a.length - 1, Math.floor(q * a.length))];
		return { lo: values.map((a) => at(a, 0.001)), hi: values.map((a) => at(a, 0.999)) };
	}
	const AXES = ['x', 'y', 'z'];
	function readBox() {
		return { min: AXES.map((a) => +el(`min_${a}`).value), max: AXES.map((a) => +el(`max_${a}`).value) };
	}
	function removeBox() {
		if (boxHelper) overlay.remove(boxHelper);
		boxHelper = null;
		el('cropForm').hidden = true;
	}
	function drawBox() {
		const { min, max } = readBox();
		const basis = levelBasis(host.getUp());
		if (boxHelper) overlay.remove(boxHelper);
		const box = new THREE.Box3(new THREE.Vector3().fromArray(min), new THREE.Vector3().fromArray(max));
		boxHelper = new THREE.Group();
		boxHelper.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(basis[0], basis[1], basis[2]));
		boxHelper.add(new THREE.Box3Helper(box, 0xffd000));
		overlay.add(boxHelper);
	}
	el('bCrop').onclick = () => {
		if (!el('cropForm').hidden) return removeBox();
		const basis = levelBasis(host.getUp());
		extent = modelExtent(basis);
		const saved = cfg.box;
		AXES.forEach((a, k) => {
			const pad = (extent.hi[k] - extent.lo[k]) * 0.02;
			for (const end of ['min', 'max']) {
				const input = el(`${end}_${a}`);
				input.min = extent.lo[k] - pad;
				input.max = extent.hi[k] + pad;
				input.step = (extent.hi[k] - extent.lo[k]) / 400 || 0.001;
				input.value = saved ? saved[end][k] : end === 'min' ? extent.lo[k] - pad : extent.hi[k] + pad;
				input.oninput = drawBox;
			}
		});
		el('cropForm').hidden = false;
		drawBox();
		say('cropMsg', 'La scatola gialla mostra cosa resta. Z è l’altezza.');
	};
	el('bCropApply').onclick = async () => {
		const box = readBox();
		if (box.max.some((h, k) => h <= box.min[k])) return say('cropMsg', 'Su ogni asse il massimo deve superare il minimo.', true);
		say('cropMsg', 'Ritaglio in corso…');
		try {
			const r = await api('/crop', { method: 'POST', body: { source: host.sourceName, min: box.min, max: box.max, up: host.getUp() } });
			await save({ box });
			say('cropMsg', `Creato ${r.file}: ${r.kept.toLocaleString('it-IT')} gaussiane su ${r.total.toLocaleString('it-IT')}. L’originale è intatto.`);
			const link = el('cropOpen');
			link.href = host.viewerLink(r.file);
			link.hidden = false;
		} catch (e) {
			say('cropMsg', e.message, true);
		}
	};

	// ---------------------------------------------------------------- confronto
	let compareReady = false;
	async function startCompare(rel) {
		say('compareMsg', 'Carico il secondo modello…');
		try {
			await host.compare.load(rel, info.alignment[rel] ?? null);
			compareReady = true;
			el('bSwap').hidden = false;
			const exact = info.alignment[rel]?.exact;
			say('compareMsg', 'Premi B (o il pulsante) per passare da uno all’altro.' + (exact ? '' : ' L’allineamento è approssimato.'));
			showWhich();
		} catch (e) {
			say('compareMsg', 'Confronto non riuscito: ' + e.message, true);
		}
	}
	function showWhich() {
		el('which').textContent = host.compare.active() ? 'Stai vedendo: il modello di confronto' : `Stai vedendo: ${host.sourceName}`;
		el('which').hidden = !compareReady;
	}
	function swap() {
		if (!compareReady) return;
		host.compare.toggle();
		showWhich();
	}
	el('bSwap').onclick = swap;
	addEventListener('keydown', (e) => {
		if (e.code === 'KeyB' && !/INPUT|SELECT/.test(e.target.tagName)) swap();
		if (e.code === 'Escape' && picking) {
			stopPicking();
			clearMarkers();
		}
	});
	el('compareSelect').onchange = (e) => e.target.value && startCompare(e.target.value);

	// ---------------------------------------------------------------- avvio (a modello caricato)
	button.onclick = () => {
		panel.hidden = !panel.hidden;
		button.classList.toggle('on', !panel.hidden);
		if (panel.hidden) {
			stopPicking();
			clearMarkers();
			removeBox();
		}
	};

	async function loadSettings() {
		const data = await api('/view');
		info = data;
		cfg = data.view?.stale ? {} : (data.view ?? {});
		el('stale').hidden = !data.view?.stale;
		showScale();
		return cfg;
	}

	async function ready(compareWith) {
		button.hidden = false;
		const select = el('compareSelect');
		for (const m of info.models.filter((m) => m !== host.sourceName)) {
			const o = document.createElement('option');
			o.value = m;
			const v = m.match(/^versions\/v(\d+)\//);
			o.textContent = v ? `Versione ${v[1]}` : m;
			select.appendChild(o);
		}
		el('compareBox').hidden = select.options.length < 2;
		if (compareWith && info.models.includes(compareWith)) {
			panel.hidden = false;
			button.classList.add('on');
			select.value = compareWith;
			await startCompare(compareWith);
		}
	}

	/** Disegna segnalini e scatola sopra lo splat, mantenendo i segnalini della stessa grandezza sullo schermo. */
	function render() {
		if (!overlay.children.length) return;
		for (const o of overlay.children) if (o.isMesh) o.scale.setScalar(host.camera.position.distanceTo(o.position) * 0.006);
		const r = host.renderer;
		r.autoClear = false;
		r.clearDepth();
		r.render(overlay, host.camera);
		r.autoClear = true;
	}

	return { loadSettings, ready, render, isPicking: () => !!picking, pickAt, config: () => cfg };
}
