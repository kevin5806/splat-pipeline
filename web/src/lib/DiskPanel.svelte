<script>
	import { api } from '#lib/api';

	let { name, locked = false, active = false } = $props();

	let data = $state(null);
	let selected = $state({});
	let error = $state('');
	let message = $state('');
	let busy = $state(false);

	const gb = (mb) => (mb >= 1000 ? `${(mb / 1000).toLocaleString('it-IT', { maximumFractionDigits: 1 })} GB` : `${Math.round(mb)} MB`);
	async function load() {
		error = '';
		try {
			data = await api(`/projects/${name}/disk`);
			selected = {};
		} catch (e) {
			error = e.message;
		}
	}
	const chosen = $derived((data?.cleanable ?? []).filter((c) => selected[c.id]));
	const largest = $derived(Math.max(1, ...(data?.parts ?? []).map((p) => p.mb)));

	async function clean() {
		const list = chosen.map((c) => `• ${c.label} (${gb(c.mb)}): ${c.effect}`).join('\n');
		if (!confirm(`Cancellare definitivamente?\n\n${list}`)) return;
		busy = true;
		error = message = '';
		try {
			const r = await api(`/projects/${name}/disk/clean`, { method: 'POST', body: { ids: chosen.map((c) => c.id) } });
			message = `Liberati ${gb(r.freed_mb)}.`;
			await load();
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}
</script>

<details class="card" ontoggle={(e) => e.currentTarget.open && load()}>
	<summary style="cursor: pointer"><strong>Spazio su disco</strong></summary>
	<div class="grid" style="margin-top: 12px; gap: 8px">
		{#if data}
			<div class="small">Il progetto occupa <strong>{gb(data.total_mb)}</strong> <span class="mut">· liberi sul disco {data.free_gb} GB</span></div>
			<table class="small">
				<tbody>
					{#each data.parts as p}
						<tr>
							<td>{p.label}</td>
							<td class="barcell"><i style="width: {(100 * p.mb) / largest}%"></i></td>
							<td class="num">{gb(p.mb)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if data.cleanable.length && !locked}
				<div class="small"><strong>Si può liberare</strong> <span class="mut">(video, frame, modello finale ed esportazioni non si toccano)</span></div>
				{#each data.cleanable as c}
					<label class="row small" style="gap: 8px; align-items: flex-start">
						<input type="checkbox" bind:checked={selected[c.id]} disabled={active} />
						<span><strong>{c.label}</strong> · {gb(c.mb)}<br /><span class="mut">{c.effect}</span></span>
					</label>
				{/each}
				<div>
					<button class="danger" disabled={!chosen.length || busy || active} onclick={clean}>Cancella selezionati</button>
					{#if active}<span class="small mut">Disponibile a progetto fermo.</span>{/if}
				</div>
			{/if}
		{:else if !error}
			<div class="small mut">Calcolo…</div>
		{/if}
		{#if message}<div class="small" style="color: var(--ok)">{message}</div>{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
</details>

<style>
	table { border-collapse: collapse; width: 100%; }
	td { padding: 3px 8px 3px 0; }
	.barcell { width: 50%; }
	.barcell i { display: block; height: 10px; min-width: 2px; background: var(--acc); border-radius: 0 4px 4px 0; }
	.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
