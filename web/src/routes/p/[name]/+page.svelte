<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, upload, fmtDuration, fmtClock, fmtHours, fmtAgo, STATUS_LABEL, getToken, viewerUrl, mainPly } from '#lib/api';
	import { overall, chipClass } from '#lib/progress';
	import SettingsForm from '#lib/SettingsForm.svelte';
	import CameraHelp from '#lib/CameraHelp.svelte';
	import RecoveryPanel from '#lib/RecoveryPanel.svelte';
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
	let uploading = $state('');
	async function addVideos(e) {
		const list = [...e.target.files];
		e.target.value = '';
		if (!list.length) return;
		if (anyDone && !confirm("Aggiungere video fa ricominciare tutta l'elaborazione da capo. Continuare?")) return;
		error = '';
		busy = true;
		try {
			for (let i = 0; i < list.length; i++) {
				await upload(`/projects/${name}/videos`, list[i], (pr) => (uploading = `Caricamento ${i + 1}/${list.length}: ${Math.round(pr * 100)}%`));
			}
			await load();
		} catch (err) {
			error = err.message;
		} finally {
			busy = false;
			uploading = '';
		}
	}
	// video di supporto: si caricano tutti, poi il lavoro riparte dal recupero tenendo la ricostruzione già fatta
	async function addSupport(files, device = 1) {
		const trained = p.stages.find((s) => s.id === 'train')?.status === 'done';
		const warning = trained
			? ' Il training si rifà con le immagini in più; il modello di prima viene conservato per il confronto.'
			: '';
		if (!confirm(`Aggiungo ${files.length} video di supporto: si agganciano al modello esistente, senza rifare la ricostruzione.${warning} Continuare?`)) return;
		error = '';
		busy = true;
		try {
			for (let i = 0; i < files.length; i++) {
				await upload(`/projects/${name}/support-videos`, files[i], (pr) => (uploading = `Caricamento ${i + 1}/${files.length}: ${Math.round(pr * 100)}%`), {
					device
				});
			}
			p = await api(`/projects/${name}/start`, { method: 'POST' });
		} catch (err) {
			error = err.message;
			await load();
		} finally {
			busy = false;
			uploading = '';
		}
	}
	function pickSupport(e) {
		const files = [...e.target.files];
		e.target.value = '';
		if (files.length) addSupport(files);
	}
	async function patchVideo(id, body) {
		error = '';
		try {
			p = await api(`/projects/${name}/videos/${id}`, { method: 'PATCH', body });
		} catch (e) {
			error = e.message;
		}
	}
	async function removeVideo(v) {
		if (!confirm(`Togliere "${v.name || v.filename}"? L'elaborazione ricomincia da capo.`)) return;
		error = '';
		try {
			p = await api(`/projects/${name}/videos/${v.id}`, { method: 'DELETE' });
		} catch (e) {
			error = e.message;
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
	const recover = $derived(p?.stages.find((s) => s.id === 'recover'));
	const camerasReady = $derived(p?.stages.find((s) => s.id === 'refine')?.status === 'done');
	const showRecovery = $derived(!!recover && !!(recover.metrics.weak?.length || recover.metrics.rounds?.length || recover.status === 'blocked'));
	// nei riquadri delle fasi vanno solo i numeri semplici, non elenchi e dettagli
	const chips = (metrics) => Object.entries(metrics).filter(([k, v]) => !['gate', 'skipped', 'decision'].includes(k) && typeof v !== 'object');
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
				{#if p.estimate && p.status !== 'done' && !p.external}
					<span class="small">
						· restano circa <strong>{fmtHours(p.estimate.remaining.est)}</strong>
						<span class="mut">(tra {fmtHours(p.estimate.remaining.low)} e {fmtHours(p.estimate.remaining.high)})</span>
					</span>
				{/if}
				{#if p.status === 'queued'}<span class="small" style="color: var(--warn)">· in coda{p.queue_position ? ` (${p.queue_position}°)` : ''}</span>{/if}
				<span class="grow"></span>
				{#if !locked}
					{#if active}
						<button class="danger" disabled={busy} onclick={() => act('/stop')}>Ferma</button>
					{:else if p.status === 'blocked'}
						{#if blocked?.id !== 'recover'}
							<button class="primary" disabled={busy} onclick={() => act('/continue')}>Continua comunque</button>
						{/if}
					{:else if p.status !== 'done'}
						<button class="primary" disabled={busy || !p.videos.length} onclick={() => act('/start')}>
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
			{#if !p.videos.length && !p.external}<div class="banner warn small">Nessun video caricato. Elimina il progetto e ricrealo con un video.</div>{/if}
			{#if error}<div class="banner bad small">{error}</div>{/if}
		</div>

		{#if showRecovery}
			<RecoveryPanel
				name={p.name}
				stage={recover}
				{busy}
				{locked}
				onapprove={() => act('/recover/approve')}
				onskip={() => act('/continue')}
				onsupport={addSupport}
			/>
		{/if}
		{#if blocked && blocked.id !== 'recover'}
			<div class="banner warn grid" style="gap: 8px">
				<strong>Serve una tua decisione</strong>
				<span>{blocked.detail}</span>
				<span class="small mut">
					Il controllo di qualità serve a non sprecare ore di training. Puoi cambiare le impostazioni e rifare la fase "{blocked.label}",
					aggiungere un video di supporto per le zone poco coperte, oppure continuare comunque.
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

		{#each p.estimate?.warnings ?? [] as w}
			<div class="banner warn small"><strong>Attenzione.</strong> {w}</div>
		{/each}
		{#if p.estimate && !p.external}
			<div class="card grid" style="gap: 6px">
				<div class="row">
					<h3 class="grow">Tempi stimati</h3>
					<span class="chip">{p.estimate.frames} frame</span>
				</div>
				<div class="small">
					Totale circa <strong>{fmtHours(p.estimate.total.est)}</strong>
					<span class="mut">(tra {fmtHours(p.estimate.total.low)} e {fmtHours(p.estimate.total.high)})</span>
					{#if p.status !== 'done'}· rimanente circa <strong>{fmtHours(p.estimate.remaining.est)}</strong>{/if}
				</div>
				<div class="small mut">
					Tarata sulle misure fatte su una {p.estimate.reference}: su altro hardware cambia. Le fasi di COLMAP crescono più che proporzionalmente
					con i frame, quindi con molti frame la stima è meno sicura (le fasi marcate «estrapolato» sono oltre l'intervallo misurato). Si
					ricalibra da sola sui tempi reali delle fasi già finite{p.estimate.calibration.cpu !== 1 ? ` (finora: CPU ×${p.estimate.calibration.cpu})` : ''}.
				</div>
			</div>
		{:else if p.videos.length && !p.external}
			<div class="small mut">La stima dei tempi compare quando i video sono stati analizzati.</div>
		{/if}

		<div class="card grid">
			<div class="row">
				<h3 class="grow">Video ({p.videos.length})</h3>
				{#if uploading}<span class="small mut">{uploading}</span>{/if}
				{#if !locked && !active}
					{#if camerasReady}
						<label class="btn primary" style="cursor: pointer" title="Si aggancia al modello esistente: la ricostruzione non si rifà">
							+ Video di supporto
							<input type="file" accept="video/*" multiple hidden onchange={pickSupport} />
						</label>
					{/if}
					<label class="btn" style="cursor: pointer" title="Tutta l'elaborazione ricomincia da capo">
						+ Aggiungi video{camerasReady ? ' (ricomincia da capo)' : ''}
						<input type="file" accept="video/*" multiple hidden onchange={addVideos} />
					</label>
				{/if}
			</div>
			{#if !p.videos.length && !p.external}
				<div class="small mut">Nessun video. Aggiungine almeno uno per poter avviare.</div>
			{/if}
			{#each p.videos as v (v.id)}
				{@const info = m('probe').videos?.[v.id]}
				{@const cov = m('recover').per_video?.[v.id] ?? m('refine').per_video?.[v.id]}
				<div class="grid" style="gap: 6px; padding: 8px 0; border-top: 1px solid var(--line)">
					<div class="row" style="gap: 8px">
						<strong style="overflow-wrap: anywhere">{v.name || v.filename}</strong>
						<span class="small mut">{v.size_mb} MB</span>
						{#if v.support}<span class="chip">supporto</span>{/if}
						{#if info}<span class="small mut">· {info.width}×{info.height} · {info.fps} fps · {fmtDuration(info.duration_s)}</span>{/if}
						<span class="grow"></span>
						{#if !locked && !active}
							<label class="small row" style="gap: 4px">
								Dispositivo
								<select value={v.device} onchange={(e) => patchVideo(v.id, { device: +e.target.value })}>
									{#each [1, 2, 3, 4, 5, 6, 7, 8] as d}<option value={d}>{d}</option>{/each}
								</select>
							</label>
							<select value={v.camera_model ?? ''} onchange={(e) => patchVideo(v.id, { camera_model: e.target.value || null })}>
								<option value="">modello predefinito</option>
								<option value="OPENCV">OPENCV</option>
								<option value="OPENCV_FISHEYE">OPENCV_FISHEYE</option>
								<option value="SIMPLE_RADIAL">SIMPLE_RADIAL</option>
							</select>
							<button class="small danger" style="padding: 3px 9px" onclick={() => removeVideo(v)}>Togli</button>
						{:else}
							<span class="chip">dispositivo {v.device}</span>
							{#if v.camera_model}<span class="chip">{v.camera_model}</span>{/if}
						{/if}
					</div>
					<div class="row small" style="gap: 8px">
						<span class="mut">Frame</span>
						{#if info}<span>uno ogni {info.step} → circa {info.expected_frames}</span>{/if}
						{#if !locked && !active}
							<label class="row" style="gap: 4px">
								passo
								<input
									title="Un frame ogni N del video. Vuoto = automatico (dalle impostazioni). 1 = tutti i frame (sconsigliato: pesantissimo)."
									type="number"
									min="1"
									max="300"
									style="width: 5.5em; padding: 3px 6px"
									value={v.frame_step ?? ''}
									placeholder="auto"
									onchange={(e) => patchVideo(v.id, { frame_step: e.target.value ? +e.target.value : null })}
								/>
							</label>
						{:else if v.frame_step}
							<span class="chip">passo fissato: {v.frame_step}</span>
						{/if}
					</div>
					{#if cov}
						<div class="small">
							<span class="mut">Camere agganciate</span> {cov.registered} su {cov.total}
							<span class="chip {cov.ratio >= 0.85 ? 'ok' : cov.ratio >= 0.6 ? 'warn' : 'bad'}">{Math.round(100 * cov.ratio)}%</span>
						</div>
						{#if cov.missing_ranges?.length}
							<div class="small" style="color: var(--warn)">
								Tratti senza camere agganciate, da riprendere con più sovrapposizione:
								{#each cov.missing_ranges as r, i}{i ? ', ' : ' '}<strong>{fmtClock(r[0])}–{fmtClock(r[1])}</strong>{/each}
							</div>
						{/if}
					{/if}
				</div>
			{/each}
			{#if !p.external}<CameraHelp />{/if}
			{#if p.videos.length > 1}
				<div class="small mut">
					I video dello stesso dispositivo condividono una camera; dispositivi diversi ne hanno una ciascuno. Se COLMAP non riesce a
					collegare un video agli altri (nessuna zona in comune), le sue camere risultano 0.
				</div>
			{/if}
		</div>

		<div class="grid two">
			<div class="card grid">
				<h3>Fasi</h3>
				{#each p.stages as s (s.id)}
					<div class="grid" style="gap: 5px; padding: 8px 0; border-top: 1px solid var(--line)">
						<div class="row">
							<span class="chip {chipClass(s.status)}" style="min-width: 28px; text-align: center">{icon[s.status] ?? '○'}</span>
							<strong>{s.label}</strong>
							<span class="grow"></span>
							<span class="small mut">
								{#if s.status === 'done' || (s.status !== 'running' && s.started)}{dur(s)}
								{:else if p.estimate?.stages?.[s.id]}
									{s.status === 'running' ? 'restano ' : '~'}{fmtHours(s.status === 'running' ? p.estimate.stages[s.id].remaining : p.estimate.stages[s.id].est)}
								{/if}
							</span>
						</div>
						{#if s.status === 'running'}
							<div class="bar"><i style="width: {s.progress}%"></i></div>
						{/if}
						{#if s.detail}<div class="small mut">{s.detail}</div>{/if}
						<div class="row" style="gap: 6px">
							{#each chips(s.metrics) as [k, v]}
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
