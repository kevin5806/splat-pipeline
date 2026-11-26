<script>
	import { fmtClock, apiUrl } from '#lib/api';

	// quality: { test?: {...}, final?: {...} } come lo restituisce l'API; videos: i video del progetto
	let { name, quality, videos = [], gate = null } = $props();

	// il modello attuale, poi le versioni dalla più recente, poi il test rapido
	const label = (k) => (k === 'final' ? 'Modello attuale' : k === 'test' ? 'Test rapido' : `Versione ${k.slice(1)}`);
	const order = (k) => (k === 'final' ? 0 : k === 'test' ? 100000 : 50000 - Number(k.slice(1)));
	const available = $derived(
		Object.keys(quality ?? {})
			.filter((k) => quality[k]?.views?.length)
			.sort((a, b) => order(a) - order(b))
	);
	let chosen = $state('');
	const kind = $derived(available.includes(chosen) ? chosen : (available[0] ?? ''));
	const data = $derived(quality?.[kind]);
	let showAll = $state(false);
	const kept = $derived((data?.views ?? []).filter((v) => v.file).sort((a, b) => a.psnr - b.psnr));
	const shown = $derived(showAll ? kept : kept.slice(0, 6));
	const videoName = (id) => {
		const i = videos.findIndex((v) => v.id === id);
		return videos.length > 1 ? `video ${i + 1}` : 'video';
	};
	const GROUP = { worst: 'tra le peggiori', middle: 'tipica', best: 'tra le migliori' };
</script>

{#if available.length}
	<div class="card grid" style="gap: 10px">
		<div class="row">
			<h3 class="grow">Confronto: foto vera e modello</h3>
			{#if available.length > 1}
				{#each available as k}
					<button class:primary={k === kind} onclick={() => (chosen = k)}>{label(k)}</button>
				{/each}
			{:else}
				<span class="chip">{label(kind)}</span>
			{/if}
		</div>
		<div class="small">
			PSNR medio <strong>{data.mean} dB</strong> su {data.count} viste che il training non usa
			<span class="mut">· metà sopra {data.median} · il 10% peggiore sotto {data.p10} · il 10% migliore sopra {data.p90}</span>
			{#if kind === 'test' && gate != null}<span class="chip {data.mean >= gate ? 'ok' : 'warn'}">soglia {gate}</span>{/if}
		</div>
		<div class="small mut">
			In ogni immagine: <strong>a sinistra la foto vera, a destra il modello</strong>. Se forme e contorni combaciano ma il modello è sfocato, le
			camere sono giuste e manca dettaglio o copertura; se il modello è irriconoscibile o spostato, lì le camere sono sbagliate o mancano.
		</div>
		<div class="views">
			{#each shown as v (v.n)}
				<figure>
					<a href={apiUrl(`/projects/${name}/quality/${kind}/${v.file}`)} target="_blank" rel="noopener">
						<img src={apiUrl(`/projects/${name}/quality/${kind}/${v.file}`)} alt="Foto e modello a {fmtClock(v.t)}" loading="lazy" />
					</a>
					<figcaption class="small">
						<strong>{videoName(v.video)} {fmtClock(v.t)}</strong> · PSNR {v.psnr} dB <span class="mut">· {GROUP[v.group]}</span>
					</figcaption>
				</figure>
			{/each}
		</div>
		{#if kept.length > 6}
			<div><button onclick={() => (showAll = !showAll)}>{showAll ? 'Mostra meno' : `Mostra tutte (${kept.length})`}</button></div>
		{/if}
	</div>
{/if}

<style>
	.views { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
	figure { margin: 0; }
	img { width: 100%; border-radius: 6px; display: block; }
</style>
