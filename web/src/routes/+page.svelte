<script>
	import { api, upload, fmtAgo, STATUS_LABEL, viewerUrl, mainPly } from '#lib/api';
	import { overall, DEFAULT_SETTINGS, chipClass } from '#lib/progress';
	import SystemCard from '#lib/SystemCard.svelte';
	import SettingsForm from '#lib/SettingsForm.svelte';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	let projects = $state([]);
	let loaded = $state(false);

	let name = $state('');
	let file = $state(null);
	let settings = $state(DEFAULT_SETTINGS());
	let startNow = $state(true);
	let busy = $state(false);
	let sent = $state(0);
	let error = $state('');
	let dragging = $state(false);
	let readOnly = $state(false);

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

	function pick(f) {
		if (!f) return;
		file = f;
		if (!name) name = slug(f.name);
	}

	async function create() {
		error = '';
		busy = true;
		try {
			await api('/projects', { method: 'POST', body: { name, settings } });
			if (file) {
				await upload(`/projects/${name}/video`, file, (p) => (sent = p));
				if (startNow) await api(`/projects/${name}/start`, { method: 'POST' });
			}
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

	{#if !readOnly}
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
				pick(e.dataTransfer?.files?.[0]);
			}}
			onkeydown={(e) => e.key === 'Enter' && document.getElementById('vid').click()}
			onclick={() => document.getElementById('vid').click()}
		>
			{#if file}
				<strong>{file.name}</strong> <span class="mut">· {(file.size / 1e9).toFixed(2)} GB</span>
			{:else}
				Trascina qui il video oppure clicca per sceglierlo
			{/if}
			<input id="vid" type="file" accept="video/*" hidden onchange={(e) => pick(e.target.files[0])} />
		</div>
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
				{busy ? (sent > 0 && sent < 1 ? `Caricamento ${Math.round(sent * 100)}%` : 'Creazione…') : 'Crea progetto'}
			</button>
		</div>
		{#if busy && sent > 0}<div class="bar"><i style="width: {sent * 100}%"></i></div>{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
	{/if}

	<div class="grid">
		<h2>Progetti</h2>
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
					{#if cur}<span>· {cur.label}{cur.detail ? ` — ${cur.detail}` : ''}</span>{/if}
					{#if p.external}<span>· monitorato</span>{/if}
					{#if p.status === 'blocked'}<span style="color: var(--warn)">· serve una tua decisione</span>{/if}
					{#if p.error}<span style="color: var(--bad)">· {p.error.split('\n')[0].slice(0, 90)}</span>{/if}
				</div>
			</a>
		{/each}
	</div>
</div>
