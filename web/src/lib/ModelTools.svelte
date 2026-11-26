<script>
	import { api, apiUrl, viewerUrl, fmtDuration } from '#lib/api';
	import { onMount } from 'svelte';

	// artifacts: file esportati; locked: pannello in sola lettura
	let { name, artifacts = [], locked = false, onchange = () => {} } = $props();

	let job = $state(null);
	let view = $state({});
	let depth = $state(10);
	let metric = $state(true);
	let level = $state(true);
	let error = $state('');
	let busy = $state(false);

	const has = (path) => artifacts.some((a) => a.path === path);
	const scaleOk = $derived(!!view.scale && !view.stale);
	const upOk = $derived(!!view.up && !view.stale);

	async function load() {
		try {
			const [j, v] = await Promise.all([api(`/projects/${name}/jobs`), api(`/projects/${name}/view`)]);
			const wasActive = job && ['queued', 'running'].includes(job.status);
			job = j.mesh ?? null;
			view = v.view;
			if (wasActive && job?.status === 'done') onchange();
		} catch {
			/* il pannello principale mostra già gli errori di rete */
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 4000);
		return () => clearInterval(t);
	});

	async function post(path, body) {
		error = '';
		busy = true;
		try {
			await api(path, { method: 'POST', body });
			await load();
			onchange();
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}
	const active = $derived(!!job && ['queued', 'running'].includes(job.status));
	const download = (path) => apiUrl(`/projects/${name}/files/${path}`);
</script>

{#if has('splat_clean.ply')}
	<div class="grid" style="gap: 10px; border-top: 1px solid var(--line); padding-top: 10px">
		<div class="small">
			<strong>Strumenti nel viewer 3D</strong> <span class="mut">(pulsante «Strumenti»)</span>: misure e scala reale, raddrizzare la stanza,
			punto di partenza, ritaglio.
			<div class="mut">
				{scaleOk ? `Scala impostata: 1 unità = ${view.scale.toLocaleString('it-IT', { maximumFractionDigits: 3 })} m.` : 'Scala non ancora impostata.'}
				{upOk ? 'Stanza raddrizzata.' : ''}
				{view.stale ? 'Il modello è cambiato: scala e verticale salvate non valgono più, vanno reimpostate.' : ''}
			</div>
		</div>
		<div class="row" style="gap: 8px">
			{#if has('splat_clean_precedente.ply')}
				<a class="btn" href="{viewerUrl(name, 'splat_clean.ply')}&compare=splat_clean_precedente.ply" target="_blank" rel="noopener">
					Confronta con il modello di prima ↗
				</a>
			{/if}
			{#if !locked}
				<button disabled={!scaleOk || busy} title={scaleOk ? '' : 'Prima imposta la scala nel viewer'} onclick={() => post(`/projects/${name}/scale-export`)}>
					Esporta lo splat in metri
				</button>
			{/if}
		</div>

		<div class="grid" style="gap: 6px">
			<div class="small"><strong>Mesh</strong> <span class="mut">(per Blender o MeshLab)</span></div>
			<div class="small mut">
				Approssimata, ricostruita dalle gaussiane: pareti lisce, vetri, oggetti sottili e zone riprese poco vengono male. Non sostituisce lo
				splat. Gira sulla CPU quando nessun progetto è in esecuzione.
			</div>
			{#if !locked}
				<div class="row small" style="gap: 10px">
					<label class="row" style="gap: 4px">
						dettaglio
						<select bind:value={depth}>
							<option value={9}>basso (più veloce)</option>
							<option value={10}>medio</option>
							<option value={11}>alto (più RAM)</option>
						</select>
					</label>
					<label class="row" style="gap: 4px"><input type="checkbox" bind:checked={metric} disabled={!scaleOk} /> in metri</label>
					<label class="row" style="gap: 4px"><input type="checkbox" bind:checked={level} disabled={!upOk} /> raddrizzata</label>
					<button disabled={active || busy} onclick={() => post(`/projects/${name}/mesh`, { depth, metric, level })}>
						{has('mesh.ply') ? 'Rifai la mesh' : 'Crea la mesh'}
					</button>
				</div>
			{/if}
			{#if job}
				<div class="small">
					{#if active}
						<span class="chip warn">{job.status === 'queued' ? 'in coda' : 'in corso'}</span> <span class="mut">{job.detail}</span>
					{:else if job.status === 'failed'}
						<span class="chip bad">non riuscita</span> <span class="mut">{job.detail}</span>
					{:else if job.result}
						<span class="chip ok">pronta</span>
						<span class="mut">
							{job.result.triangles?.toLocaleString('it-IT')} triangoli{job.result.scale_m ? ' · in metri' : ' · scala arbitraria'}
							{job.ended ? `· ${fmtDuration(job.ended - job.started)}` : ''}
						</span>
						{#if has('mesh.ply')}<a href={download('mesh.ply')} download>scarica mesh.ply</a>{/if}
					{/if}
				</div>
			{/if}
		</div>
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
{/if}
