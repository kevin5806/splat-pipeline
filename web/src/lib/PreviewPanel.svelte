<script>
	import { apiUrl } from '#lib/api';

	// preview: { steps: [...], real: bool }
	let { name, preview, running = false } = $props();

	let picked = $state(null); // null = segui l'ultima
	const steps = $derived(preview?.steps ?? []);
	const index = $derived(picked == null ? steps.length - 1 : Math.min(picked, steps.length - 1));
	const step = $derived(steps[index]);
	const src = (file) => apiUrl(`/projects/${name}/preview/${file}`);
</script>

{#if steps.length}
	<div class="card grid" style="gap: 8px">
		<div class="row">
			<h3 class="grow">Anteprima del training</h3>
			<span class="chip">passo {step.toLocaleString('it-IT')}</span>
		</div>
		<div class="pair">
			{#if preview.real}
				<figure><img src={src('real.jpg')} alt="Foto vera" /><figcaption class="small mut">foto vera</figcaption></figure>
			{/if}
			<figure>
				<img src={src(`step_${String(step).padStart(6, '0')}.jpg`)} alt="Modello al passo {step}" />
				<figcaption class="small mut">modello</figcaption>
			</figure>
		</div>
		{#if steps.length > 1}
			<input
				type="range"
				min="0"
				max={steps.length - 1}
				value={index}
				aria-label="Passo dell'anteprima"
				oninput={(e) => (picked = +e.target.value === steps.length - 1 ? null : +e.target.value)}
			/>
		{/if}
		<div class="small mut">
			Sempre la stessa inquadratura, una che il training non usa. {running ? 'Si aggiorna ogni 1.000 passi.' : 'Trascina per rivedere come è migliorato.'}
		</div>
	</div>
{/if}

<style>
	.pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
	figure { margin: 0; }
	img { width: 100%; border-radius: 6px; display: block; }
</style>
