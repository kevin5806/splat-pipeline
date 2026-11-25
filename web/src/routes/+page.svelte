<script>
	import { api, upload, fmtAgo, fmtHours, STATUS_LABEL, viewerUrl, mainPly } from '#lib/api';
	import { overall, DEFAULT_SETTINGS, chipClass } from '#lib/progress';
	import SystemCard from '#lib/SystemCard.svelte';
	import SettingsForm from '#lib/SettingsForm.svelte';
	import CameraHelp from '#lib/CameraHelp.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	let projects = $state([]);
	let loaded = $state(false);

	let name = $state('');
	// elenco dei video da caricare: { file, device, camera_model }
	let files = $state([]);
	let idx = $state(0);
	let settings = $state(DEFAULT_SETTINGS());
	let startNow = $state(true);
	let busy = $state(false);
	let sent = $state(0);
	let error = $state('');
	let dragging = $state(false);
	// null = non ancora saputo: finché non lo sappiamo i controlli di modifica restano nascosti (niente lampo all'avvio)
	let readOnly = $state(null);

	async function load() {
		try {
			projects = await api('/projects');
		} finally {
			loaded = true;
		}
	}
	onMount(() => {
		api('/system').then((s) => (readOnly = !!s.read_only)).catch(() => {});
		load();
		const t = setInterval(load, 3000);
		return () => clearInterval(t);
	});

	const slug = (s) => s.trim().replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);

	function pick(list) {
		for (const f of Array.from(list || [])) {
			if (files.length >= 12) break;
			if (!files.some((x) => x.file.name === f.name && x.file.size === f.size)) files.push({ file: f, device: 1, camera_model: '' });
		}
		if (!name && files.length) name = slug(files[0].file.name);
	}
	const remove = (i) => files.splice(i, 1);

	// ---- pulizia dei progetti completati
	const finished = $derived(projects.filter((p) => p.status === 'done' && !p.external));
	const finishedGb = $derived(finished.reduce((a, p) => a + (p.size_mb || 0), 0) / 1000);
	async function cleanup() {
		error = '';
		try {
			const dry = await api('/projects/cleanup', { method: 'POST', body: { dry_run: true } });
			const list = dry.would_delete || [];
			if (!list.length) return;
			const gb = (dry.freed_mb / 1000).toFixed(1);
			const names = list.map((x) => `${x.name} (${(x.size_mb / 1000).toFixed(1)} GB)`).join(', ');
			if (!confirm(`Eliminare ${list.length} elaborazioni completate e liberare circa ${gb} GB?\n\n${names}\n\nI file esportati (.ply) verranno eliminati: scaricali prima se ti servono. I progetti monitorati non vengono toccati.`)) return;
			await api('/projects/cleanup', { method: 'POST', body: {} });
			await load();
		} catch (e) {
			error = e.message;
		}
	}

	async function create() {
		error = '';
		busy = true;
		try {
			await api('/projects', { method: 'POST', body: { name, settings } });
			for (let i = 0; i < files.length; i++) {
				idx = i;
				sent = 0;
				const f = files[i];
				await upload(`/projects/${name}/videos`, f.file, (p) => (sent = p), { device: f.device, camera_model: f.camera_model });
			}
			if (files.length && startNow) await api(`/projects/${name}/start`, { method: 'POST' });
			goto(`/p/${name}`);
		} catch (e) {
			error = e.message;
			await load();
		} finally {
			busy = false;
		}
	}
</script>

<div class="wrap grid" style="gap: 18px">
	<div class="row">
		<h1 class="grow">splat-pipeline</h1>
		<span class="mut small">da un video a un modello 3D (Gaussian Splatting)</span>
	</div>

	<SystemCard />

	{#if readOnly === false}
	<div class="card grid">
		<h2>Nuovo progetto</h2>
		<div
			class="card"
			style="border-style: dashed; text-align: center; padding: 26px; {dragging ? 'border-color: var(--acc)' : ''}"
			role="button"
			tabindex="0"
			ondragover={(e) => {
				e.preventDefault();
				dragging = true;
			}}
			ondragleave={() => (dragging = false)}
			ondrop={(e) => {
				e.preventDefault();
				dragging = false;
				pick(e.dataTransfer?.files);
			}}
			onkeydown={(e) => e.key === 'Enter' && document.getElementById('vid').click()}
			onclick={() => document.getElementById('vid').click()}
		>
			Trascina qui i video oppure clicca per sceglierli
			<div class="small mut" style="margin-top: 4px">Anche più di uno: vengono uniti nello stesso modello (massimo 12).</div>
			<input id="vid" type="file" accept="video/*" multiple hidden onchange={(e) => { pick(e.target.files); e.target.value = ''; }} />
		</div>
		{#if files.length}
			<div class="grid" style="gap: 6px">
				{#each files as f, i (f.file.name + f.file.size)}
					<div class="row card" style="padding: 8px 10px; gap: 8px">
						<strong class="grow" style="overflow-wrap: anywhere">{f.file.name}</strong>
						<span class="small mut">{(f.file.size / 1e9).toFixed(2)} GB</span>
						<label class="small row" style="gap: 4px" title="Stesso numero = stessa camera e stesse impostazioni (stesso zoom/obiettivo). Telefono e GoPro: 1 e 2.">
							Dispositivo
							<select bind:value={f.device}>{#each [1, 2, 3, 4, 5, 6, 7, 8] as d}<option value={d}>{d}</option>{/each}</select>
						</label>
						<select bind:value={f.camera_model} title="Modello della camera (vuoto = quello delle impostazioni)">
							<option value="">modello predefinito</option>
							<option value="OPENCV">OPENCV</option>
							<option value="OPENCV_FISHEYE">OPENCV_FISHEYE</option>
							<option value="SIMPLE_RADIAL">SIMPLE_RADIAL</option>
						</select>
						<button class="small danger" style="padding: 3px 9px" onclick={() => remove(i)}>Togli</button>
					</div>
				{/each}
				{#if files.length > 1}
					<div class="small mut">
						Più video dello stesso <strong>dispositivo</strong> condividono una sola camera; dispositivi diversi (telefono e GoPro, per
						esempio) ne hanno una ciascuno. I video devono riprendere in parte le <strong>stesse zone</strong>, altrimenti COLMAP non
						riesce a unirli.
					</div>
				{/if}
			</div>
		{/if}
		{#if files.length}<CameraHelp />{/if}
		<label class="f">
			Nome del progetto
			<input bind:value={name} placeholder="es. camera" maxlength="40" />
		</label>
		<details>
			<summary style="cursor: pointer">Impostazioni avanzate (i valori predefiniti sono quelli verificati)</summary>
			<div style="margin-top: 12px"><SettingsForm bind:value={settings} /></div>
		</details>
		<div class="row">
			<label class="row small" style="gap: 6px"><input type="checkbox" bind:checked={startNow} /> Avvia subito dopo il caricamento</label>
			<span class="grow"></span>
			<button class="primary" disabled={busy || !name} onclick={create}>
				{busy ? (sent > 0 && sent < 1 ? `Video ${idx + 1}/${files.length}: ${Math.round(sent * 100)}%` : 'Creazione…') : 'Crea progetto'}
			</button>
		</div>
		{#if busy && files.length}<div class="bar"><i style="width: {((idx + sent) / files.length) * 100}%"></i></div>{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
	{/if}

	<div class="grid">
		<div class="row">
			<h2 class="grow">Progetti</h2>
			{#if readOnly === false && finished.length}
				<button class="small" onclick={cleanup} title="Elimina in blocco i progetti completati per liberare spazio">
					Rimuovi le completate ({finished.length} · {finishedGb.toFixed(1)} GB)
				</button>
			{/if}
		</div>
		{#if !loaded}
			<p class="mut">Caricamento…</p>
		{:else if projects.length === 0}
			<p class="mut">Nessun progetto ancora. Carica un video qui sopra.</p>
		{/if}
		{#each projects as p (p.name)}
			{@const cur = p.stages.find((s) => s.id === p.current_stage)}
			<a class="card grid" href="/p/{p.name}" style="color: inherit; gap: 8px; text-decoration: none">
				<div class="row">
					<strong style="font-size: 17px">{p.name}</strong>
					<span class="chip {chipClass(p.status)}">{STATUS_LABEL[p.status] ?? p.status}</span>
					<span class="grow"></span>
					{#if mainPly(p.artifacts)}
						<button
							class="small"
							style="padding: 3px 10px"
							onclick={(e) => {
								e.preventDefault();
								e.stopPropagation();
								window.open(viewerUrl(p.name, mainPly(p.artifacts).path), '_blank');
							}}>Viewer 3D ↗</button
						>
					{/if}
					<span class="small mut">{fmtAgo(p.updated)}</span>
				</div>
				<div class="bar {p.status === 'done' ? 'ok' : ''}"><i style="width: {overall(p)}%"></i></div>
				<div class="row small mut">
					<span>{overall(p)}%</span>
					{#if p.estimate && p.status !== 'done' && !p.external}<span>· restano circa {fmtHours(p.estimate.remaining.est)}</span>{/if}
					{#if p.status === 'queued'}<span style="color: var(--warn)">· in coda{p.queue_position ? ` (${p.queue_position}°)` : ''}</span>{/if}
					{#if p.size_mb}<span>· {(p.size_mb / 1000).toFixed(1)} GB</span>{/if}
					{#if cur}<span>· {cur.label}{cur.detail ? ` — ${cur.detail}` : ''}</span>{/if}
					{#if p.external}<span>· monitorato</span>{/if}
					{#if p.status === 'blocked'}<span style="color: var(--warn)">· serve una tua decisione</span>{/if}
					{#if p.error}<span style="color: var(--bad)">· {p.error.split('\n')[0].slice(0, 90)}</span>{/if}
				</div>
			</a>
		{/each}
	</div>
</div>
