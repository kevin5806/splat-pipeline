// Mesh .ply (triangoli con colori per vertice): il viewer degli splat non le sa mostrare, qui le disegna three.js.
import * as THREE from 'three';

const SIZES = { char: 1, int8: 1, uchar: 1, uint8: 1, short: 2, int16: 2, ushort: 2, uint16: 2, int: 4, int32: 4, uint: 4, uint32: 4, float: 4, float32: 4, double: 8, float64: 8 };
const READ = { char: 'getInt8', int8: 'getInt8', uchar: 'getUint8', uint8: 'getUint8', short: 'getInt16', int16: 'getInt16', ushort: 'getUint16', uint16: 'getUint16', int: 'getInt32', int32: 'getInt32', uint: 'getUint32', uint32: 'getUint32', float: 'getFloat32', float32: 'getFloat32', double: 'getFloat64', float64: 'getFloat64' };

/** L'intestazione del file: elementi con le loro proprietà, e dove cominciano i dati. */
export function parseHeader(bytes) {
	const text = new TextDecoder('ascii').decode(bytes.subarray(0, Math.min(bytes.length, 65536)));
	const end = text.indexOf('end_header');
	if (!text.startsWith('ply') || end < 0) throw new Error('non è un file PLY');
	const offset = text.indexOf('\n', end) + 1;
	const elements = [];
	let format = '';
	for (const line of text.slice(0, end).split(/\r?\n/)) {
		const w = line.trim().split(/\s+/);
		if (w[0] === 'format') format = w[1];
		else if (w[0] === 'element') elements.push({ name: w[1], count: +w[2], properties: [] });
		else if (w[0] === 'property') {
			const p = w[1] === 'list' ? { list: true, countType: w[2], type: w[3], name: w[4] } : { type: w[1], name: w[2] };
			elements.at(-1).properties.push(p);
		}
	}
	return { format, elements, offset, isMesh: elements.some((e) => e.name === 'face' && e.count > 0) };
}

/** Legge solo l'inizio del file per capire se è una mesh, senza scaricarlo tutto. */
export async function looksLikeMesh(url) {
	const stop = new AbortController();
	const res = await fetch(url, { signal: stop.signal });
	if (!res.ok || !res.body) return false;
	const reader = res.body.getReader();
	let head = new Uint8Array(0);
	try {
		while (head.length < 65536) {
			const { value, done } = await reader.read();
			if (done) break;
			const joined = new Uint8Array(head.length + value.length);
			joined.set(head);
			joined.set(value, head.length);
			head = joined;
			if (new TextDecoder('ascii').decode(head).includes('end_header')) break;
		}
		return parseHeader(head).isMesh;
	} catch {
		return false;
	} finally {
		stop.abort();
	}
}

export function parseMesh(buffer) {
	const header = parseHeader(new Uint8Array(buffer));
	if (header.format !== 'binary_little_endian') throw new Error('solo mesh PLY binarie');
	const view = new DataView(buffer);
	let at = header.offset;
	const take = (type) => {
		const v = view[READ[type]](at, true);
		at += SIZES[type];
		return v;
	};
	let positions = null;
	let colors = null;
	const indices = [];
	const srgb = new THREE.Color();
	for (const element of header.elements) {
		const isVertex = element.name === 'vertex';
		if (isVertex) {
			positions = new Float32Array(element.count * 3);
			if (element.properties.some((p) => p.name === 'red')) colors = new Float32Array(element.count * 3);
		}
		for (let i = 0; i < element.count; i++) {
			const row = {};
			for (const p of element.properties) {
				if (p.list) {
					const n = take(p.countType);
					const list = [];
					for (let k = 0; k < n; k++) list.push(take(p.type));
					if (element.name === 'face') for (let k = 1; k + 1 < n; k++) indices.push(list[0], list[k], list[k + 1]);
				} else row[p.name] = take(p.type);
			}
			if (isVertex) {
				positions.set([row.x, row.y, row.z], i * 3);
				if (colors) {
					srgb.setRGB(row.red / 255, row.green / 255, row.blue / 255, THREE.SRGBColorSpace);
					colors.set([srgb.r, srgb.g, srgb.b], i * 3);
				}
			}
		}
	}
	if (!positions || !indices.length) throw new Error('la mesh non ha triangoli');
	const geometry = new THREE.BufferGeometry();
	geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
	if (colors) geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
	geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1));
	geometry.computeBoundingSphere();
	return geometry;
}

export async function loadMesh(url, onProgress) {
	const res = await fetch(url);
	if (!res.ok) throw new Error(`il server risponde ${res.status}`);
	const total = +res.headers.get('content-length') || 0;
	const reader = res.body.getReader();
	const chunks = [];
	let size = 0;
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		chunks.push(value);
		size += value.length;
		if (total) onProgress?.((100 * size) / total);
	}
	const all = new Uint8Array(size);
	let at = 0;
	for (const c of chunks) {
		all.set(c, at);
		at += c.length;
	}
	const geometry = parseMesh(all.buffer);
	const material = new THREE.MeshBasicMaterial({ vertexColors: !!geometry.getAttribute('color'), color: 0xffffff, side: THREE.DoubleSide });
	const scene = new THREE.Scene();
	scene.add(new THREE.Mesh(geometry, material));
	return { scene, sphere: geometry.boundingSphere, vertices: geometry.getAttribute('position').count, triangles: geometry.index.count / 3 };
}
