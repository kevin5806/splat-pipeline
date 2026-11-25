<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, fmtDuration, fmtAgo, STATUS_LABEL, getToken, viewerUrl, mainPly } from '#lib/api';
	import { overall, chipClass } from '#lib/progress';
	import SettingsForm from '#lib/SettingsForm.svelte';
	import { onMount } from 'svelte';

	const name = $derived(page.params.name);

	let p = $state(null);
	let sys = $state(null);
	let error = $state('');
	let notFound = $state(false);
	let busy = $state(false);
	let now = $state(Date.now() / 1000);
	let logStage = $state('');
	let logText = $state('');
	let settings = $state(null);
	let settingsDirty = $state(false);

	async function load() {
		try {
			const next = await api(`/projects/${name}`);
			p = next;
			if (!settingsDirty) settings = structuredClone(next.settings);
			if (!logStage) logStage = next.current_stage || next.stages.find((s) => s.status !== 'pending')?.id || 'probe';
			notFound = false;
		} catch (e) {
			if (e.status === 404) notFound = true;
			else error = e.message;
		}
	}
	async function loadSys() {
		try {
			sys = await api('/system');
		} catch {
			/* il pannello Sistema mostra già l'errore */
		}
	}
	async function loadLog() {
		if (!logStage) return;
		try {
			logText = await api(`/projects/${name}/log/${logStage}?tail=300`, { raw: true });
		} catch {
			logText = '';
		}
	}

	onMount(() => {
		load();
		loadSys();
		loadLog();
		const a = setInterval(load, 2000);
		const b = setInterval(loadSys, 5000);
		const c = setInterval(loadLog, 2500);
		const d = setInterval(() => (now = Date.now() / 1000), 1000);
		return () => [a, b, c, d].forEach(clearInterval);
	});

	async function act(path, method = 'POST') {
		error = '';
		busy = true;
		try {
			p = await api(`/projects/${name}${path}`, { method });
			settingsDirty = false;
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}

	async function putSettings() {
		error = '';
		busy = true;
		try {
			p = await api(`/projects/${name}/settings`, { method: 'PUT', body: settings });
			settingsDirty = false;
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}
	async function remove() {
		if (!confirm(`Eliminare il progetto "${name}" e tutti i suoi file? L'operazione non si può annullare.`)) return;
		await api(`/projects/${name}`, { method: 'DELETE' }).then(() => goto('/')).catch((e) => (error = e.message));
	}
	async function viewer(on) {
		error = '';
		try {
			if (on) await api(`/projects/${name}/viewer`, { method: 'POST' });
			else await api('/viewer', { method: 'DELETE' });
			await loadSys();
		} catch (e) {
			error = e.message;
		}
	}

	const nsViewerUrl = $derived(sys ? `http://${location.hostname}:${sys.viewer_port}` : '#');
	const tokenQ = () => (getToken() ? `?token=${encodeURIComponent(getToken())}` : '');
	// finché non sappiamo se il pannello è in sola lettura (sys non ancora caricato) i controlli restano nascosti
	const locked = $derived(!!p?.external || !sys || !!sys.read_only);
	const active = $derived(p?.active || p?.status === 'running' || p?.status === 'queued');
	const anyDone = $derived(p?.stages.some((s) => s.status === 'done'));
	const blocked = $derived(p?.stages.find((s) => s.status === 'blocked'));
	const trainRunning = $derived(p?.stages.find((s) => s.id === 'train')?.status === 'running');
	const viewerOnThis = $derived(sys?.viewer?.project === name);
	const m = (id) => p?.stages.find((s) => s.id === id)?.metrics ?? {};
	const dur = (s) => (s.started ? fmtDuration((s.ended ?? now) - s.started) : '');
	const icon = { done: '✓', running: '●', failed: '✕', blocked: '!', interrupted: '⏸', pending: '○' };
</script>

<div class="wrap grid" style="gap: 16px">
	<div><a href="/">← Tutti i progetti</a></div>

	{#if notFound}
		<div class="card">Progetto non trovato.</div>
	{:else if !p}
		<p class="mut">Caricamento…</p>
	{:else}
		<div class="card grid">
			<div class="row">
				<h1>{p.name}</h1>
				<span class="chip {chipClass(p.status)}">{STATUS_LABEL[p.status] ?? p.status}</span>
				<span class="grow"></span>
				<span class="small mut">aggiornato {fmtAgo(p.updated)}</span>
			</div>
			<div class="bar {p.status === 'done' ? 'ok' : ''}"><i style="width: {overall(p)}%"></i></div>
			<div class="row">
				<span class="mut small">{overall(p)}% complessivo</span>
				<span class="grow"></span>
				{#if !locked}
					{#if active}
						<button class="danger" disabled={busy} onclick={() => act('/stop')}>Ferma</button>
					{:else if p.status === 'blocked'}
						<button class="primary" disabled={busy} onclick={() => act('/continue')}>Continua comunque</button>
					{:else if p.status !== 'done'}
						<button class="primary" disabled={busy || !p.video} onclick={() => act('/start')}>
							{anyDone ? 'Riprendi' : 'Avvia'}
						</button>
					{/if}
					<button class="danger" disabled={active || busy} onclick={remove}>Elimina</button>
				{/if}
			</div>
			{#if p.external}
				<div class="banner small">
					<strong>Progetto monitorato in sola lettura.</strong> Il lavoro gira fuori dal pannello (script sul server): qui puoi seguirne
					l'avanzamento, ma non fermarlo né riavviarlo.
				</div>
			{:else if sys?.read_only}
				<div class="banner small"><strong>Pannello in sola lettura.</strong> Si può guardare, non modificare.</div>
			{/if}
			{#if !p.video && !p.external}<div class="banner warn small">Nessun video caricato. Elimina il progetto e ricrealo con un video.</div>{/if}
			{#if error}<div class="banner bad small">{error}</div>{/if}
		</div>

		{#if blocked}
			<div class="banner warn grid" style="gap: 8px">
				<strong>Serve una tua decisione</strong>
				<span>{blocked.detail}</span>
				<span class="small mut">
					Il controllo di qualità serve a non sprecare ore di training con pose sbagliate. Puoi cambiare le impostazioni e rifare la fase
					"{blocked.label}", oppure continuare comunque.
				</span>
			</div>
		{/if}
		{#if p.status === 'failed' && p.error}
			<div class="banner bad grid" style="gap: 6px">
				<strong>Errore</strong>
				<pre class="log mono">{p.error}</pre>
			</div>
		{/if}
		{#if p.status === 'interrupted'}
			<div class="banner warn small">Il pannello è stato riavviato mentre questo progetto girava. Premi "Riprendi": riparte dall'ultimo punto salvato.</div>
		{/if}

		<div class="grid two">
			<div class="card grid">
				<h3>Fasi</h3>
				{#each p.stages as s (s.id)}
					<div class="grid" style="gap: 5px; padding: 8px 0; border-top: 1px solid var(--line)">
						<div class="row">
							<span class="chip {chipClass(s.status)}" style="min-width: 28px; text-align: center">{icon[s.status] ?? '○'}</span>
							<strong>{s.label}</strong>
							<span class="grow"></span>
							<span class="small mut">{dur(s)}</span>
						</div>
						{#if s.status === 'running'}
							<div class="bar"><i style="width: {s.progress}%"></i></div>
						{/if}
						{#if s.detail}<div class="small mut">{s.detail}</div>{/if}
						<div class="row" style="gap: 6px">
							{#each Object.entries(s.metrics).filter(([k]) => !['gate', 'skipped'].includes(k)) as [k, v]}
								<span class="chip small">{k}: {v}</span>
							{/each}
							<span class="grow"></span>
							<button class="small" style="padding: 3px 9px" onclick={() => ((logStage = s.id), loadLog())}>Log</button>
							{#if !locked && !active && s.status !== 'pending'}
								<button class="small" style="padding: 3px 9px" disabled={busy} onclick={() => act(`/reset/${s.id}`)}>Rifai da qui</button>
							{/if}
						</div>
					</div>
				{/each}
			</div>

			<div class="grid" style="align-content: start">
				<div class="card grid">
					<h3>Riepilogo</h3>
					{#if m('probe').width}
						<div class="small">
							<span class="mut">Video</span> {m('probe').width}×{m('probe').height} · {m('probe').fps} fps · {fmtDuration(m('probe').duration_s)}
						</div>
					{/if}
					{#if m('refine').registered}
						<div class="small">
							<span class="mut">Camere ricostruite</span> {m('refine').registered} su {m('refine').total}
							({Math.round(100 * m('refine').ratio)}%)
						</div>
					{/if}
					{#if m('test').psnr}
						<div class="small">
							<span class="mut">Test di qualità</span> PSNR {m('test').psnr} dB · SSIM {m('test').ssim} · LPIPS {m('test').lpips}
							<span class="chip {m('test').psnr >= (m('test').gate ?? 0) ? 'ok' : 'warn'}">soglia {m('test').gate}</span>
						</div>
					{/if}
					{#if !m('probe').width && !m('refine').registered && !m('test').psnr}
						<div class="small mut">Compare qui man mano che le fasi finiscono.</div>
					{/if}
				</div>

				<div class="card grid">
					<h3>Risultati e viewer</h3>
					{#if trainRunning}
						<div class="small">Il training sta girando: puoi guardarlo in diretta.</div>
						<a class="btn" href={nsViewerUrl} target="_blank" rel="noopener">Apri il viewer live ↗</a>
					{:else if p.stages.find((s) => s.id === 'train')?.status === 'done'}
						{#if viewerOnThis}
							<a class="btn" href={nsViewerUrl} target="_blank" rel="noopener">Apri il viewer ↗</a>
							<button onclick={() => viewer(false)}>Ferma il viewer</button>
						{:else}
							<button disabled={locked || !!sys?.running} onclick={() => viewer(true)}>Avvia il viewer sul modello</button>
						{/if}
					{:else}
						<div class="small mut">Disponibile quando parte il training.</div>
					{/if}
					{#if mainPly(p.artifacts)}
						<a class="btn primary" href={viewerUrl(p.name, mainPly(p.artifacts).path)} target="_blank" rel="noopener">
							Apri nel viewer 3D ↗
						</a>
						<div class="small mut">
							Si apre nel browser e carica {mainPly(p.artifacts).path} ({mainPly(p.artifacts).size_mb} MB): all'inizio può volerci un po'.
							Dentro puoi camminare con WASD o girare attorno; se la stanza risulta storta cambia "Alto = asse".
						</div>
					{/if}
					{#each p.artifacts as a}
						<div class="row">
							<a href="/api/projects/{p.name}/files/{a.path}{tokenQ()}" download>{a.path}</a>
							<span class="small mut">{a.size_mb} MB</span>
							{#if a.path.endsWith('.ply')}
								<a class="small" href={viewerUrl(p.name, a.path)} target="_blank" rel="noopener">apri nel viewer</a>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		</div>

		<div class="card grid">
			<div class="row">
				<h3>Log</h3>
				<select bind:value={logStage} onchange={loadLog}>
					{#each p.stages as s}<option value={s.id}>{s.label}</option>{/each}
				</select>
			</div>
			<pre class="log mono">{logText || '(vuoto)'}</pre>
		</div>

		<details class="card">
			<summary style="cursor: pointer"><strong>Impostazioni</strong> <span class="mut small">· si possono cambiare solo a progetto fermo</span></summary>
			<div style="margin-top: 12px" class="grid">
				{#if settings}
					<div oninput={() => (settingsDirty = true)} onchange={() => (settingsDirty = true)}>
						<SettingsForm bind:value={settings} disabled={active || locked} />
					</div>
					<div class="row">
						<button class="primary" disabled={locked || active || busy || !settingsDirty} onclick={putSettings}>Salva le impostazioni</button>
						<span class="small mut">Per applicarle ai passi già fatti usa "Rifai da qui" sulla fase interessata.</span>
					</div>
				{/if}
			</div>
		</details>
	{/if}
</div>
