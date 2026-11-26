<script>
	import { fmtClock } from '#lib/api';

	// report: l'esito del recupero per un video (vedi video_report nel backend); needVideo: infittire non basta più
	let { report, needVideo = false } = $props();

	const by = (status) => report.sections.filter((s) => s.status === status);
	const secs = (s) => `${s.toLocaleString('it-IT', { maximumFractionDigits: 1 })} s`;
	const o = $derived(report.original);
	const pct = (n) => (o.total ? Math.round((100 * n) / o.total) : 0);
	const fixed = $derived(by('fixed'));
	const improved = $derived(by('improved'));
	const left = $derived([...by('unchanged'), ...by('new')].sort((a, b) => a.start_s - b.start_s));
	const pending = $derived(by('pending'));
</script>

<div class="grid" style="gap: 4px">
	<div class="small">
		{#if report.support}
			<span class="mut">Video di supporto, immagini agganciate</span> {o.now} su {o.total}
			<span class="chip {pct(o.now) >= 85 ? 'ok' : pct(o.now) >= 60 ? 'warn' : 'bad'}">{pct(o.now)}%</span>
		{:else}
			<span class="mut">Camere agganciate (frame di partenza)</span>
			{#if o.before !== o.now}{o.before} → <strong>{o.now}</strong>{:else}{o.now}{/if}
			su {o.total}
			<span class="chip {pct(o.now) >= 85 ? 'ok' : pct(o.now) >= 60 ? 'warn' : 'bad'}">{pct(o.now)}%</span>
		{/if}
		{#if report.added.total}
			<span class="mut">· frame aggiunti nei tratti deboli: {report.added.registered} agganciati su {report.added.total}</span>
		{/if}
	</div>
	{#if fixed.length}
		<div class="small" style="color: var(--ok)">
			✓ Sistemati ({fixed.length}):
			{#each fixed as s, i}{i ? ', ' : ' '}<strong>{fmtClock(s.start_s)}–{fmtClock(s.end_s)}</strong>{/each}
		</div>
	{/if}
	{#if improved.length}
		<div class="small" style="color: var(--warn)">
			◐ Migliorati ma non del tutto ({improved.length}):
			{#each improved as s, i}{i ? ', ' : ' '}<strong>{fmtClock(s.start_s)}–{fmtClock(s.end_s)}</strong>
				(scoperti da {secs(s.before_s)} a {secs(s.after_s)}){/each}
		</div>
	{/if}
	{#if left.length}
		<div class="small" style="color: var(--bad)">
			✕ Ancora poco coperti ({left.length}){needVideo ? ', serve un video nuovo di questi punti' : ''}:
			{#each left as s, i}{i ? ', ' : ' '}<strong>{fmtClock(s.start_s)}–{fmtClock(s.end_s)}</strong> ({secs(s.after_s)}){/each}
		</div>
	{/if}
	{#if pending.length}
		<div class="small" style="color: var(--warn)">
			Poco coperti ({pending.length}){needVideo ? '' : ', da sistemare con il recupero'}:
			{#each pending as s, i}{i ? ', ' : ' '}<strong>{fmtClock(s.start_s)}–{fmtClock(s.end_s)}</strong>{/each}
		</div>
	{/if}
	{#if !report.sections.length}
		<div class="small" style="color: var(--ok)">✓ Nessun tratto poco coperto.</div>
	{/if}
</div>
