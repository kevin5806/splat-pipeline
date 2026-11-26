<script>
	import { fmtClock, fmtHours, getToken } from '#lib/api';

	// stage: la fase "recover" del progetto; i bottoni richiamano le funzioni della pagina
	let { name, stage, busy = false, locked = false, onapprove, onskip, onsupport } = $props();

	const m = $derived(stage?.metrics ?? {});
	const waiting = $derived(stage?.status === 'blocked');
	const thumb = (file) => `/api/projects/${name}/thumb/${file}${getToken() ? `?token=${encodeURIComponent(getToken())}` : ''}`;
	const steps = (s) => Object.entries(s ?? {}).map(([v, n]) => `${v}: uno ogni ${n}`).join(', ');
	let device = $state(1);

	function pick(e) {
		const files = [...e.target.files];
		e.target.value = '';
		if (files.length) onsupport(files, device);
	}
</script>

<div class="card grid" style="gap: 10px; {waiting ? 'border-color: var(--warn)' : ''}">
	<div class="row">
		<h3 class="grow">Tratti poco coperti</h3>
		{#if m.weak_seconds != null}
			<span class="chip {m.weak_seconds ? 'warn' : 'ok'}">{Math.round(m.weak_seconds)} s di video</span>
		{/if}
	</div>

	{#if waiting}
		<div class="banner warn grid" style="gap: 8px">
			<strong>{m.decision === 'new_video' ? 'Serve un video nuovo' : 'Serve una tua conferma'}</strong>
			<span>{stage.detail}</span>
		</div>
	{/if}

	{#if waiting && m.decision === 'approve' && !locked}
		<div class="row">
			<button class="primary" disabled={busy} onclick={onapprove}>
				Infittisci i tratti deboli (+{m.plan?.extra_frames} frame, ~{fmtHours(m.plan?.est_seconds)})
			</button>
			<button disabled={busy} onclick={onskip}>Continua senza</button>
		</div>
		<div class="small mut">
			Giro {m.plan?.number}: {steps(m.plan?.step)} (solo nei tratti qui sotto, con un margine di 1,5 s). I frame in più si agganciano al modello
			già costruito; le pose calcolate finora non si muovono, e il modello di prima viene conservato. Aiuta quando mancava sovrapposizione
			(movimenti veloci); <strong>non</strong> aiuta con foto mosse o superfici lisce senza dettagli.
		</div>
	{/if}

	{#if waiting && m.decision === 'new_video' && !locked}
		<div class="grid" style="gap: 8px">
			<div class="small">
				<strong>Come registrarlo:</strong> parti inquadrando per qualche secondo una zona già venuta bene, poi avvicinati
				<em>lentamente</em> al punto da migliorare e torna indietro. Stesso telefono, stessa risoluzione, esposizione e fuoco bloccati. Sposta
				il meno possibile gli oggetti: ciò che è cambiato rispetto al primo video confonde la ricostruzione.
			</div>
			<div class="row">
				<label class="small row" style="gap: 4px">
					Dispositivo
					<select bind:value={device}>
						{#each [1, 2, 3, 4, 5, 6, 7, 8] as d}<option value={d}>{d}</option>{/each}
					</select>
				</label>
				<label class="btn primary" style="cursor: pointer">
					Carica un video di supporto
					<input type="file" accept="video/*" multiple hidden disabled={busy} onchange={pick} />
				</label>
				<button disabled={busy} onclick={onskip}>Continua senza</button>
			</div>
		</div>
	{/if}

	{#if m.rounds?.length}
		<div class="grid" style="gap: 4px">
			{#each m.rounds as r}
				<div class="small">
					<span class="mut">{r.kind === 'support' ? `Video di supporto ${r.video}` : `Giro ${r.number}`}:</span>
					{r.new_registered} immagini nuove agganciate su {r.extra_frames}
					{#if r.recovered}· {r.recovered} di quelle di prima recuperate{/if}
					{#if r.weak_s_after != null}· tratti deboli da {Math.round(r.weak_s_before)} s a {Math.round(r.weak_s_after)} s{/if}
					{#if r.accepted === false}<span class="chip bad">scartato: il modello era peggiore</span>{/if}
				</div>
			{/each}
		</div>
	{/if}

	{#if m.weak?.length}
		<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px">
			{#each m.weak as w}
				<figure style="margin: 0">
					{#if w.thumb}
						<img src={thumb(w.thumb)} alt="Tratto debole di {w.video_name}" loading="lazy" style="width: 100%; border-radius: 6px; display: block" />
					{/if}
					<figcaption class="small" style="overflow-wrap: anywhere">
						<strong>{fmtClock(w.start_s)}–{fmtClock(w.end_s)}</strong> <span class="mut">{w.video_name}</span>
					</figcaption>
				</figure>
			{/each}
		</div>
	{:else if stage?.status === 'done' && !m.skipped}
		<div class="small mut">Nessun tratto poco coperto.</div>
	{/if}
</div>
