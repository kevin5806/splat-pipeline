<script>
	import { api, apiUrl, viewerUrl } from '#lib/api';

	// data: { current, versions } come lo restituisce l'API
	let { name, data, locked = false, onchange = () => {} } = $props();

	let error = $state('');
	let busy = $state(false);

	const rows = $derived([...(data?.current ? [data.current] : []), ...[...(data?.versions ?? [])].reverse()]);
	const reference = $derived(data?.current ?? data?.versions?.at(-1));
	const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '–');
	const num = (v, digits) => (v == null ? '–' : v.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
	const best = (key, lower = false) => {
		const values = rows.map((r) => r[key]).filter((v) => v != null);
		return values.length > 1 ? (lower ? Math.min(...values) : Math.max(...values)) : null;
	};
	const top = $derived({ psnr: best('psnr'), ssim: best('ssim'), lpips: best('lpips', true) });
	const download = (file) => apiUrl(`/projects/${name}/files/${file}`);

	const it = (v) => v.toLocaleString('it-IT');
	const QUALITY = { standard: 'standard', high: 'alta' };
	const signed = (v, digits) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toLocaleString('it-IT', { maximumFractionDigits: digits })}`;

	/** Cosa è cambiato rispetto alla versione subito prima: impostazioni, poi risultato. */
	function changes(row, older) {
		if (!older) return '';
		const out = [];
		if (row.quality && older.quality && row.quality !== older.quality) out.push(`qualità ${QUALITY[older.quality]} → ${QUALITY[row.quality]}`);
		if (row.steps && older.steps && row.steps !== older.steps) out.push(`passi ${it(older.steps)} → ${it(row.steps)}`);
		// senza il valore nel file di configurazione il ciclo era quello predefinito di Nerfstudio
		const a = older.reset_every ?? 3000;
		const b = row.reset_every ?? 3000;
		if (a !== b) out.push(`ciclo di aggiunta del dettaglio ${it(a)} → ${it(b)}`);
		if (row.views && older.views && row.views !== older.views) out.push(`foto di controllo ${older.views} → ${row.views}: punteggi non confrontabili`);
		else if (row.psnr != null && older.psnr != null) out.push(`PSNR ${signed(row.psnr - older.psnr, 2)} dB`);
		if (row.gaussians && older.gaussians) out.push(`gaussiane ${signed(row.gaussians - older.gaussians, 0)}`);
		return out.join(' · ');
	}

	async function run(fn) {
		error = '';
		busy = true;
		try {
			await fn();
			onchange();
		} catch (e) {
			error = e.message;
		} finally {
			busy = false;
		}
	}
	function remove(r) {
		if (!confirm(`Eliminare definitivamente la versione ${r.number} (splat, confronto e modello)?`)) return;
		run(() => api(`/projects/${name}/versions/${r.number}`, { method: 'DELETE' }));
	}
	function annotate(r) {
		const note = prompt(`Nota per la versione ${r.number}`, r.note ?? '');
		if (note != null) run(() => api(`/projects/${name}/versions/${r.number}`, { method: 'PATCH', body: { note } }));
	}
</script>

{#if data?.versions?.length}
	<div class="card grid" style="gap: 8px">
		<h3>Versioni del modello</h3>
		<div class="small mut">
			Ogni volta che rifai il training, il modello di prima resta qui. Nessuna versione viene sovrascritta. PSNR e SSIM: più alto è meglio;
			LPIPS: più basso è meglio. Il grassetto segna il valore migliore. I punteggi si confrontano solo a parità di foto di controllo (colonna
			«foto di controllo»).
		</div>
		<div class="scroll">
			<table class="small">
				<thead>
					<tr>
						<th>Versione</th>
						<th>Training</th>
						<th class="n">Gaussiane</th>
						<th class="n">PSNR</th>
						<th class="n">SSIM</th>
						<th class="n">LPIPS</th>
						<th class="n">Foto di controllo</th>
						<th class="n">File</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each rows as r, i (r.number)}
						{@const diff = changes(r, rows[i + 1])}
						<tr>
							<td>
								<strong>{r.number}</strong>
								{#if r.current}<span class="chip ok">attuale</span>{/if}
								<div class="mut">{when(r.created)}</div>
								{#if r.note}<div>{r.note}</div>{/if}
							</td>
							<td>
								{r.quality === 'high' ? 'alta' : r.quality ? 'standard' : '–'}
								<div class="mut">{r.steps ? `${r.steps.toLocaleString('it-IT')} passi` : ''}{r.reset_every ? ` · ciclo ${r.reset_every.toLocaleString('it-IT')}` : ''}</div>
							</td>
							<td class="n">{r.gaussians ? r.gaussians.toLocaleString('it-IT') : '–'}</td>
							<td class="n" class:best={r.psnr === top.psnr}>{num(r.psnr, 2)}</td>
							<td class="n" class:best={r.ssim === top.ssim}>{num(r.ssim, 3)}</td>
							<td class="n" class:best={r.lpips === top.lpips}>{num(r.lpips, 3)}</td>
							<td class="n">{r.views ?? '–'}</td>
							<td class="n">{r.size_mb != null ? `${r.size_mb} MB` : '–'}</td>
							<td class="actions">
								{#if r.file}
									<a href={viewerUrl(name, r.file)} target="_blank" rel="noopener">viewer</a>
									{#if reference?.file && reference.file !== r.file}
										<a href="{viewerUrl(name, reference.file)}&compare={encodeURIComponent(r.file)}" target="_blank" rel="noopener">confronta</a>
									{/if}
									<a href={download(r.file)} download="splat_v{r.number}.ply">scarica</a>
								{/if}
								{#if !r.current && !locked}
									<button class="link" disabled={busy} onclick={() => annotate(r)}>nota</button>
									<button class="link danger" disabled={busy} onclick={() => remove(r)}>elimina</button>
								{/if}
							</td>
						</tr>
						{#if diff}
							<tr class="diff"><td colspan="9" class="mut">↑ rispetto alla versione {rows[i + 1].number}: {diff}</td></tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
		{#if !data.current}<div class="small mut">La versione nuova comparirà quando il training e l’esportazione saranno finiti.</div>{/if}
		{#if error}<div class="banner bad small">{error}</div>{/if}
	</div>
{/if}

<style>
	.scroll { overflow-x: auto; }
	table { border-collapse: collapse; width: 100%; }
	th, td { text-align: left; padding: 6px 10px 6px 0; border-top: 1px solid var(--line); vertical-align: top; }
	th { border-top: 0; color: var(--mut); font-weight: 500; }
	.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
	.best { font-weight: 700; }
	.diff td { border-top: 0; padding-top: 0; font-size: 12px; }
	.actions { white-space: nowrap; }
	.actions a, .actions .link { margin-right: 8px; }
	.link { background: none; border: 0; padding: 0; color: var(--acc); }
	.link.danger { color: var(--bad); }
</style>
