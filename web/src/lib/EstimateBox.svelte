<script>
	import { fmtHours } from '#lib/api';

	let { estimate, title = 'Tempi stimati', note = '' } = $props();

	const LABEL = {
		probe: 'Analisi dei video',
		frames: 'Estrazione dei frame',
		features: 'Feature COLMAP',
		matching: 'Matching COLMAP',
		mapping: 'Ricostruzione delle camere',
		refine: 'Bundle adjustment',
		convert: 'Preparazione del dataset',
		test: 'Test rapido di qualità',
		train: 'Training dello splat',
		export: 'Esportazione e pulizia'
	};
</script>

{#if estimate}
	{#each estimate.warnings ?? [] as w}
		<div class="banner warn small"><strong>Attenzione.</strong> {w}</div>
	{/each}
	<div class="card grid" style="gap: 6px">
		<div class="row">
			<h3 class="grow">{title}</h3>
			<span class="chip">{estimate.frames} frame</span>
		</div>
		<div class="small">
			Totale circa <strong>{fmtHours(estimate.total.est)}</strong>
			<span class="mut">(tra {fmtHours(estimate.total.low)} e {fmtHours(estimate.total.high)})</span>
		</div>
		{#if note}<div class="small mut">{note}</div>{/if}
		<details>
			<summary class="small" style="cursor: pointer">Fase per fase</summary>
			<table class="small" style="border-collapse: collapse; width: 100%; margin-top: 6px">
				<thead>
					<tr style="text-align: left; color: var(--mut)"><th>Fase</th><th>Stima</th><th>Intervallo</th><th></th></tr>
				</thead>
				<tbody>
					{#each Object.entries(estimate.stages) as [id, s]}
						{#if s.est > 0}
							<tr style="border-top: 1px solid var(--line)">
								<td>{LABEL[id] ?? id}</td>
								<td><strong>{fmtHours(s.est)}</strong></td>
								<td class="mut">{fmtHours(s.low)} – {fmtHours(s.high)}</td>
								<td><span class="chip {s.basis === 'misurato' ? 'ok' : 'warn'}">{s.basis}</span></td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</details>
		<div class="small mut">
			Tarata sulle misure fatte su una {estimate.reference}; su altro hardware cambia. «Misurato» = nell'intervallo dei nostri giri reali;
			«estrapolato» = oltre, quindi più incerto (COLMAP cresce più che proporzionalmente con i frame).
		</div>
	</div>
{/if}
