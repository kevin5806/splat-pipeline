<script>
	import { apiUrl, fmtClock } from '#lib/api';

	// preview: { angles: [{ suffix, real, steps, video, t }] }
	let { name, preview, running = false } = $props();

	let picked = $state(null); // posizione sulla barra; null = segui l'ultima
	let chosen = $state(''); // inquadratura (suffisso dei file)
	let all = $state(false); // tutte le inquadrature insieme

	const angles = $derived(preview?.angles ?? []);
	const angle = $derived(angles.find((a) => a.suffix === chosen) ?? angles[0]);
	const steps = $derived(angle?.steps ?? []);
	const index = $derived(picked == null ? steps.length - 1 : Math.min(picked, steps.length - 1));
	const step = $derived(steps[index]);
	const src = (file) => apiUrl(`/projects/${name}/preview/${file}`);
	const frame = (a, s) => src(`step_${String(s).padStart(6, '0')}${a.suffix}.jpg`);
	// un'inquadratura può non avere ancora quel passo: si mostra il più vicino prima
	const at = (a) => [...a.steps].reverse().find((s) => s <= step) ?? a.steps[0];
	const label = (a, i) => `Inquadratura ${i + 1}${a.video ? ` · video ${a.video}, ${fmtClock(a.t)}` : ''}`;
</script>

{#if steps.length}
	<div class="card grid" style="gap: 8px">
		<div class="row">
			<h3 class="grow">Anteprima del training</h3>
			<span class="chip">passo {step.toLocaleString('it-IT')}</span>
		</div>
		{#if angles.length > 1}
			<div class="row" style="gap: 6px">
				{#each angles as a, i}
					<button
						class="small"
						class:primary={!all && a.suffix === angle.suffix}
						onclick={() => {
							chosen = a.suffix;
							all = false;
						}}>{label(a, i)}</button
					>
				{/each}
				<button class="small" class:primary={all} onclick={() => (all = !all)}>Tutte insieme</button>
			</div>
		{/if}
		{#if all}
			<div class="mosaic">
				{#each angles as a, i}
					<figure>
						<button class="thumb" onclick={() => ((chosen = a.suffix), (all = false))} aria-label="Apri {label(a, i)}">
							<img src={frame(a, at(a))} alt="Modello, {label(a, i)}" loading="lazy" />
						</button>
						<figcaption class="small mut">{label(a, i)}</figcaption>
					</figure>
				{/each}
			</div>
		{:else}
			<div class="pair">
				{#if angle.real}
					<figure><img src={src(`real${angle.suffix}.jpg`)} alt="Foto vera" /><figcaption class="small mut">foto vera</figcaption></figure>
				{/if}
				<figure>
					<img src={frame(angle, step)} alt="Modello al passo {step}" />
					<figcaption class="small mut">modello</figcaption>
				</figure>
			</div>
		{/if}
		{#if steps.length > 1}
			<label class="small row" style="gap: 8px">
				<span class="mut">{steps[0].toLocaleString('it-IT')}</span>
				<input
					class="grow"
					type="range"
					min="0"
					max={steps.length - 1}
					value={index}
					aria-label="Scorri tra i passi del training"
					oninput={(e) => (picked = +e.target.value === steps.length - 1 ? null : +e.target.value)}
				/>
				<span class="mut">{steps[steps.length - 1].toLocaleString('it-IT')}</span>
			</label>
		{/if}
		<div class="small mut">
			Trascina la barra per scorrere tra i {steps.length} passaggi salvati. {running ? 'Se ne aggiunge uno ogni 1.000 passi. ' : ''}Le
			inquadrature sono prese dalle foto di controllo, che restano fuori dal training per dare il voto al modello anche senza anteprima:
			guardare l'anteprima non toglie nessuna foto in più.
		</div>
	</div>
{/if}

<style>
	.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
	.mosaic { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
	figure { margin: 0; }
	img { width: 100%; border-radius: 6px; display: block; }
	.thumb { padding: 0; border: 0; background: none; width: 100%; }
</style>
