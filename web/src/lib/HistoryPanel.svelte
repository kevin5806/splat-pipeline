<script>
	import { api, fmtDuration } from '#lib/api';
	import { onDestroy } from 'svelte';

	// stages: per tradurre gli id delle fasi nei loro nomi
	let { name, stages = [] } = $props();

	let rows = $state(null);
	let error = $state('');
	let timer;

	async function load() {
		try {
			rows = await api(`/projects/${name}/history`);
			error = '';
		} catch (e) {
			error = e.message;
		}
	}
	function toggle(open) {
		clearInterval(timer);
		if (open) {
			load();
			timer = setInterval(load, 10000);
		}
	}
	onDestroy(() => clearInterval(timer));

	const stage = (id) => stages.find((s) => s.id === id)?.label ?? id;
	const took = (e) => (e.duration != null ? ` (${fmtDuration(e.duration)})` : '');
	const TEXT = {
		queued: () => 'Messo in coda',
		video_added: () => 'Video aggiunto',
		support_video: () => 'Aggiunto un video di supporto',
		stage_start: (e) => `Iniziata: ${stage(e.stage)}`,
		stage_done: (e) => `Finita: ${stage(e.stage)}${took(e)}${e.psnr ? ` · PSNR ${e.psnr} dB, SSIM ${e.ssim}, LPIPS ${e.lpips}` : ''}`,
		blocked: (e) => `In attesa di una decisione: ${stage(e.stage)}. ${e.detail ?? ''}`,
		failed: (e) => `Errore in ${stage(e.stage)}${took(e)}: ${e.detail ?? ''}`,
		stopped: () => 'Fermato dall’utente',
		interrupted: () => 'Interrotto da un riavvio',
		done: () => 'Completato',
		reset: (e) => `«Rifai da qui»: ${stage(e.stage)}`,
		forced: (e) => `Continuato senza risolvere: ${stage(e.stage)}`,
		recovery_approved: (e) => `Confermato il giro di recupero ${e.number}`,
		recovery_round: (e) =>
			`${e.kind === 'support' ? 'Video di supporto integrato' : `Giro di recupero ${e.number}`}: ${e.new_registered} immagini agganciate su ${e.extra_frames}` +
			`${e.weak_s_after != null ? `, tratti deboli da ${e.weak_s_before} a ${e.weak_s_after} s` : ''}${e.accepted === false ? ' (scartato)' : ''}`,
		job_start: (e) => `Avviato: ${e.job}`,
		job_end: (e) => `${e.job}: ${e.status === 'done' ? 'completata' : 'non riuscita'}${took(e)}`,
		crop: (e) => `Creato un ritaglio (${e.kept} gaussiane su ${e.total})`,
		scale_export: () => 'Esportato lo splat in metri',
		disk_clean: (e) => `Liberati ${Math.round(e.freed_mb)} MB`
	};
	const when = (ts) => new Date(ts * 1000).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
</script>

<details class="card" ontoggle={(e) => toggle(e.currentTarget.open)}>
	<summary style="cursor: pointer"><strong>Cronologia</strong></summary>
	<div class="grid" style="margin-top: 12px; gap: 4px; max-height: 360px; overflow: auto">
		{#if rows?.length}
			{#each [...rows].reverse().filter((r) => r.event !== 'stage_start') as e}
				<div class="small" style="overflow-wrap: anywhere">
					<span class="mut" style="font-variant-numeric: tabular-nums">{when(e.ts)}</span>
					{(TEXT[e.event] ?? (() => e.event))(e)}
				</div>
			{/each}
		{:else if rows}
			<div class="small mut">Si registra da adesso: gli eventi precedenti all’aggiornamento non ci sono (i tempi delle fasi già fatte restano nell’elenco delle fasi).</div>
		{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
</details>
